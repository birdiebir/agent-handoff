"""Codex CLI adapter。

來源是 Claude 時，用 app-server 的 externalAgentConfig/import（只勾 SESSIONS）原生匯入，
拿到新 thread id 後 `codex resume <id> "<提示>"`；匯入失敗退回 `codex -C <cwd> "<提示>"` 靠接力卡。
"""
import json
import os
import queue
import shutil
import subprocess
import threading
import time

from ..config import expand
from .base import handoff_prompt

NAME = "codex"


class ImportFailed(Exception):
    pass


def _strip_unc(p):
    return p[4:] if p.startswith(chr(92) * 2 + "?" + chr(92)) else p


def parse_completed(params, src_path):
    """從 completed 通知取 SESSIONS 的新 thread id（success.target）；有 failure 就丟例外。"""
    target = None
    for r in (params or {}).get("itemTypeResults", []):
        if r.get("itemType") != "SESSIONS":
            continue
        for f in r.get("failures", []):
            raise ImportFailed(f"{f.get('failureStage')}: {f.get('message')}")
        for s in r.get("successes", []):
            target = s.get("target") or target
    return target


def ledger_lookup(ledger_path, src_path):
    """匯入登記檔：找 source_path 對得上、imported_at 最新的 imported_thread_id。"""
    try:
        with open(ledger_path, encoding="utf-8") as f:
            recs = json.load(f).get("records", [])
    except (OSError, json.JSONDecodeError):
        return None
    want = os.path.normcase(os.path.normpath(src_path))
    best = None
    for r in recs:
        sp = os.path.normcase(os.path.normpath(_strip_unc(r.get("source_path", ""))))
        if sp == want and (best is None or r.get("imported_at", 0) >= best.get("imported_at", 0)):
            best = r
    return best.get("imported_thread_id") if best else None


def import_claude_session(src_path, cwd, exe="codex", timeout=60, ledger_path=None):
    """啟動 app-server 匯入一段 Claude 對話，回傳新 Codex thread id。失敗丟 ImportFailed。"""
    exe_path = shutil.which(exe) or exe
    try:
        p = subprocess.Popen([exe_path, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                             encoding="utf-8", shell=False)
    except OSError as e:
        raise ImportFailed(f"無法啟動 codex app-server：{e}")
    q = queue.Queue()
    threading.Thread(target=lambda: [q.put(l) for l in p.stdout], daemon=True).start()

    def send(o):
        try:
            p.stdin.write(json.dumps(o, ensure_ascii=False) + "\n")
            p.stdin.flush()
        except (OSError, ValueError) as e:   # BrokenPipe 等：app-server 已經掛了
            raise ImportFailed(f"傳送給 app-server 失敗：{e}")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "agent-handoff", "version": "0.1"},
            "capabilities": {"experimentalApi": True}}})
        deadline = time.time() + timeout
        sent = False
        while time.time() < deadline:
            try:
                o = json.loads(q.get(timeout=0.25))
            except queue.Empty:
                if p.poll() is not None and q.empty():
                    raise ImportFailed(f"app-server 提早結束（exit code {p.poll()}）")
                continue
            except json.JSONDecodeError:
                continue
            m = o.get("method") or ""
            if o.get("id") == 1 and not sent:
                if o.get("error"):
                    raise ImportFailed(f"initialize 失敗：{o['error']}")
                send({"method": "initialized"})
                send({"id": 2, "method": "externalAgentConfig/import", "params": {"migrationItems": [{
                    "itemType": "SESSIONS", "description": "agent-handoff", "cwd": None,
                    "details": {"plugins": [], "sessions": [{"path": src_path, "cwd": cwd, "title": None}],
                                "mcpServers": [], "hooks": [], "subagents": [], "commands": []}}]}})
                sent = True
            elif o.get("id") == 2 and o.get("error"):
                raise ImportFailed(f"import 失敗：{o['error']}")
            elif "externalAgentConfig" in m and m.endswith("completed"):
                tid = parse_completed(o.get("params"), src_path)
                tid = tid or (ledger_lookup(ledger_path, src_path) if ledger_path else None)
                if not tid:
                    raise ImportFailed("匯入完成但找不到新 thread id")
                return tid
        raise ImportFailed(f"匯入逾時（{timeout} 秒）")
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        p.terminate()


def profile_args(config):
    """設定的 profile（~/.codex/<名稱>.config.toml）→ 全域選項 `-p <名稱>`；空字串或沒設就不帶。"""
    prof = (config["adapters"]["codex"].get("profile") or "").strip()
    return ["-p", prof] if prof else []


def import_timeout(config, hook=False):
    """匯入逾時秒數。hook 路徑（--launch tab，使用者在 Claude 裡等 hook 回應）壓短，退回只靠接力卡。"""
    ad = config["adapters"]["codex"]
    if hook:
        return ad.get("hook_import_timeout_sec", 15)
    return ad.get("import_timeout_sec", 60)


def build_launch(card, source_session, cwd, config, dry_run=False, hook=False):
    ad = config["adapters"]["codex"]
    prompt = handoff_prompt(card, NAME, config)
    notes = []
    pre = profile_args(config)
    fallback = {"exe": ad["exe"], "args": pre + ["-C", cwd, prompt], "cwd": cwd}
    if source_session is not None and has_native_import(source_session.cli, config):
        if dry_run:
            notes.append("計畫：用 app-server 原生匯入 Claude 對話（dry-run 不匯入）；失敗會退回只給接力卡")
            return {"exe": ad["exe"], "args": pre + ["resume", "<匯入後的新 thread id>", prompt], "cwd": cwd, "notes": notes}
        try:
            tid = import_claude_session(source_session.path, cwd, ad["exe"], import_timeout(config, hook),
                                        expand(ad.get("import_ledger", "")) or None)
            notes.append(f"已原生匯入，Codex thread {tid}")
            return {"exe": ad["exe"], "args": pre + ["resume", tid, prompt], "cwd": cwd, "notes": notes}
        except ImportFailed as e:
            notes.append(f"原生匯入失敗，退回只給接力卡：{e}")
    else:
        notes.append("來源不是可原生匯入的 CLI，靠接力卡")
    fallback["notes"] = notes
    return fallback


def transcript_location(config):
    return expand(config["adapters"]["codex"]["sessions_dir"]) + r"\YYYY\MM\DD\rollout-*.jsonl"


def has_native_import(source_cli, config):
    ad = config["adapters"]["codex"]
    return bool(ad.get("native_import")) and source_cli in ad.get("native_import_from", [])
