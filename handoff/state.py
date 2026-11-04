"""本機狀態：分頁登記表 registry.json、接手標記 active.json。

放在 private/state/（不進 git）。這支模組只用標準函式庫、import 要快，
因為 SessionStart hook 每次開機都會載入它。
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PER_TAB = 5
MAX_TOTAL = 200
ACTIVE_TTL_HOURS = 24


def state_dir(config=None):
    """狀態資料夾：環境變數 HANDOFF_STATE_DIR > config["state_dir"] > <專案>\\private\\state。"""
    p = os.environ.get("HANDOFF_STATE_DIR") or (config or {}).get("state_dir")
    if p:
        return Path(os.path.expandvars(os.path.expanduser(str(p))))
    return ROOT / "private" / "state"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _now_str(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")


# ---------- 登記表 ----------

def registry_path(sd):
    return Path(sd) / "registry.json"


def _evict(lst, per_tab):
    """超過 per_tab 筆時先丟非互動的（最舊的先丟），沒有非互動的才丟最舊的互動項。"""
    lst = list(lst)
    while len(lst) > per_tab:
        idx = next((i for i, e in enumerate(lst) if e.get("interactive") is False), 0)
        del lst[idx]
    return lst


def register(sd, wt_session, cli, session_id, cwd, transcript_path, now=None,
             per_tab=PER_TAB, max_total=MAX_TOTAL, interactive=True):
    """登記「分頁 -> 對話」。同分頁同 session 只留最新一筆；每分頁留 per_tab 筆（非互動的先淘汰）；整份最多 max_total 筆。"""
    if not wt_session or not session_id:
        return False
    path = registry_path(sd)
    reg = _read_json(path)
    entry = {"cli": cli, "session_id": session_id, "cwd": cwd or "",
             "transcript_path": transcript_path or "", "time": _now_str(now),
             "interactive": bool(interactive)}
    lst = [e for e in reg.get(wt_session, []) if e.get("session_id") != session_id]
    lst.append(entry)
    reg[wt_session] = _evict(lst, per_tab)
    # 整份上限：所有分頁的紀錄攤平、非互動的先丟、再依時間丟最舊的
    flat = [(e.get("interactive") is not False, e.get("time", ""), k, i)
            for k, v in reg.items() for i, e in enumerate(v)]
    if len(flat) > max_total:
        flat.sort()
        drop = {(k, i) for _, _, k, i in flat[:len(flat) - max_total]}
        reg = {k: [e for i, e in enumerate(v) if (k, i) not in drop] for k, v in reg.items()}
        reg = {k: v for k, v in reg.items() if v}
    _write_json(path, reg)
    return True


def lookup(sd, wt_session):
    """某分頁的登記紀錄，新到舊。"""
    if not wt_session:
        return []
    lst = _read_json(registry_path(sd)).get(wt_session, [])
    return [e for e in reversed(lst) if isinstance(e, dict) and e.get("session_id")]


def purge_noninteractive(sd, backup_suffix=None):
    """一次性清理：移除登記表裡 `claude -p`（紀錄開頭 entrypoint=sdk-cli）或 interactive=false 的項目。

    backup_suffix 不為 None 時先備份成 registry.json.bak-<suffix>。回傳清掉的筆數。
    """
    from .transcripts import head_has_sdk_cli
    path = registry_path(sd)
    if not path.exists():
        return 0
    if backup_suffix:
        bak = path.with_name(path.name + f".bak-{backup_suffix}")
        bak.write_bytes(path.read_bytes())
    reg = _read_json(path)
    removed = 0
    out = {}
    for k, v in reg.items():
        keep = []
        for e in v:
            if e.get("interactive") is False or (e.get("cli") == "claude" and head_has_sdk_cli(e.get("transcript_path"))):
                removed += 1
            else:
                keep.append(e)
        if keep:
            out[k] = keep
    if removed:
        _write_json(path, out)
    return removed


# ---------- 接手標記（依 Claude session 分開存：active/<claude_session>.json） ----------

def active_dir(sd):
    return Path(sd) / "active"


def legacy_active_path(sd):
    return Path(sd) / "active.json"


def _key(claude_session):
    k = "".join(c for c in str(claude_session or "") if c.isalnum() or c in "-_")
    return k or "unknown"


def active_path(sd, claude_session=None):
    return active_dir(sd) / f"{_key(claude_session)}.json"


def _migrate_legacy(sd):
    """舊版單一 active.json 讀到就遷移到 active/<session>.json。"""
    legacy = legacy_active_path(sd)
    if not legacy.exists():
        return
    a = _read_json(legacy)
    try:
        if a.get("holder"):
            dst = active_path(sd, a.get("claude_session") or a.get("source_session"))
            if not dst.exists():
                _write_json(dst, a)
        os.remove(legacy)
    except OSError:
        pass


def write_active(sd, holder, card, claude_session, source_session, cwd, now=None):
    _migrate_legacy(sd)
    _write_json(active_path(sd, claude_session), {
        "holder": holder, "since": _now_str(now), "card": card,
        "claude_session": claude_session or "", "source_session": source_session or "", "cwd": cwd or ""})


def _iter_active(sd):
    d = active_dir(sd)
    if not d.is_dir():
        return
    for f in sorted(d.glob("*.json")):
        a = _read_json(f)
        if a.get("holder"):
            yield f, a


def read_active(sd, now=None, ttl_hours=ACTIVE_TTL_HOURS, claude_session=None):
    """讀接手標記。給 claude_session 就只看屬於這段的（檔名或 source_session 對得上）；沒給就取最新一筆。
    不存在、格式壞、超過 ttl 都回 None。"""
    _migrate_legacy(sd)
    best = None
    for f, a in _iter_active(sd):
        if claude_session and claude_session not in (f.stem, a.get("claude_session"), a.get("source_session")):
            continue
        try:
            since = datetime.strptime(a.get("since", ""), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        if (now or datetime.now()) - since > timedelta(hours=ttl_hours):
            continue
        if best is None or a["since"] > best["since"]:
            best = a
    return best


def clear_active(sd, claude_session=None):
    """清除接手標記。給 claude_session 只清屬於這段的；沒給就全清（含舊版 active.json）。"""
    _migrate_legacy(sd)
    cleared = False
    for f, a in list(_iter_active(sd)):
        if claude_session and claude_session not in (f.stem, a.get("claude_session"), a.get("source_session")):
            continue
        try:
            os.remove(f)
            cleared = True
        except OSError:
            pass
    return cleared


# ---------- 主線令牌：交給 codex／交回 claude 時轉移 ----------

def set_baton(config, holder, reason=""):
    """呼叫設定檔 baton_script 指定的主線令牌腳本（`take <holder>`）。只管 claude／codex（agy 不當主線）；沒設定 baton_script 或失敗都不擋交接，回傳說明文字。"""
    import subprocess
    import sys
    script = (config or {}).get("baton_script")
    if holder not in ("claude", "codex") or not script:
        return None
    script = os.path.expandvars(os.path.expanduser(str(script)))
    if not os.path.isfile(script):
        return f"找不到主線令牌腳本 {script}，令牌沒轉移"
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", "-I", script, "take", holder, "--by", "handoff", "--reason", reason],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return f"主線令牌轉移失敗：{e}"
    return (r.stdout or r.stderr or "").strip() or None
