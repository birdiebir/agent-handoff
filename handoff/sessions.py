"""列出三家最近的對話，以及查某資料夾最近一段。"""
import glob
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import expand, is_under, norm_path, HandoffError
from . import transcripts as T


@dataclass
class SessionInfo:
    cli: str
    session_id: str
    path: str
    cwd: str
    mtime: float
    title: str = ""
    last_user: str = ""
    noninteractive: bool = False   # Codex exec 等（Claude 的 sdk-cli 另用 is_noninteractive 讀檔判斷）

    def mtime_str(self):
        return datetime.fromtimestamp(self.mtime).strftime("%m-%d %H:%M")


def _files(config, cli):
    """回傳 (檔案路徑, 略過的 .zst 數)。"""
    ad = config["adapters"][cli]
    if cli == "claude":
        return glob.glob(os.path.join(expand(ad["projects_dir"]), "*", "*.jsonl")), 0
    if cli == "codex":
        base = expand(ad["sessions_dir"])
        files = glob.glob(os.path.join(base, "*", "*", "*", "rollout-*.jsonl"))
        zst = len(glob.glob(os.path.join(base, "*", "*", "*", "rollout-*.zst")))
        return files, zst
    if cli == "agy":
        out = []
        for d in ad["brain_dirs"]:
            out += glob.glob(os.path.join(expand(d), "*", ".system_generated", "logs", "transcript.jsonl"))
        return out, 0
    return [], 0


def _codex_titles(config):
    """session_index.jsonl：id → thread_name（取最新一筆）。"""
    path = expand(config["adapters"]["codex"]["index_file"])
    titles = {}
    if not os.path.exists(path):
        return titles
    for o in T._iter_jsonl(path):
        if o.get("id") and o.get("thread_name"):
            titles[o["id"]] = o["thread_name"]
    return titles


def agy_workspace(config, session_id):
    """agy 互動模式的 history.jsonl 記了 workspace＋conversationId（同一段取最新一筆）；-p 模式不記，查不到回空字串。"""
    best_ts, ws = None, ""
    for path in config["adapters"]["agy"].get("history_files", []):
        path = expand(path)
        if not os.path.exists(path):
            continue
        for o in T._iter_jsonl(path):
            if o.get("conversationId") == session_id and isinstance(o.get("workspace"), str):
                ts = o.get("timestamp") if isinstance(o.get("timestamp"), (int, float)) else 0
                if best_ts is None or ts >= best_ts:   # 多份 history 比時間，不靠讀取順序
                    best_ts, ws = ts, o["workspace"]
    return ws


def _info_from(cli, path, config, codex_titles=None, tool_chars=100):
    t = T.read_transcript(cli, path, tool_chars)
    if t.is_subagent:
        return None
    title = t.title
    if cli == "codex" and codex_titles:
        title = codex_titles.get(t.session_id, title)
    cwd = t.cwd
    if cli == "agy" and not cwd:
        # agy 紀錄沒有 cwd 欄位，還沒用過工具時從 Cwd 參數也推不到
        cwd = agy_workspace(config, t.session_id)
    last = T.one_line(t.last_user_text(), 40)
    return SessionInfo(cli, t.session_id, path, cwd, os.path.getmtime(path), title, last, t.noninteractive)


def is_noninteractive(info, head_lines=30):
    """不是使用者在互動的那段：Claude 的 `claude -p`（entrypoint=sdk-cli）、Codex 的 `exec`（source=exec）。

    在 Claude 分頁裡跑 `claude -p` 測試，SessionStart hook 也會把它登記到同一個分頁，
    不跳過的話 /exit 後打 handoff 會接到測試對話（2026-10-21 實測踩到）。
    """
    if getattr(info, "noninteractive", False):
        return True
    if info.cli != "claude" or not info.path:
        return False
    return T.head_has_sdk_cli(info.path, head_lines)


def list_sessions(config, hours=None, now=None, clis=None):
    """最近 hours 小時有更新的對話，新到舊。回傳 (清單, 備註列表)。"""
    hours = config.get("list_hours", 24) if hours is None else hours
    now = now or time.time()
    cutoff = now - hours * 3600
    notes, out = [], []
    titles = _codex_titles(config) if "codex" in config["adapters"] else {}
    for cli in clis or config["chain"]:
        files, zst = _files(config, cli)
        if zst:
            notes.append(f"codex 有 {zst} 份 .zst 壓縮紀錄未列入（尚未支援）")
        for f in files:
            try:
                if os.path.getmtime(f) < cutoff:
                    continue
                info = _info_from(cli, f, config, titles)
            except (OSError, T.UnsupportedTranscript):
                continue
            if info and not info.noninteractive:   # codex exec 不列入
                out.append(info)
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out, notes


def find_session(config, session_id, cli=None):
    """用 session id 找對話（不分 CLI 時三家都找）。找不到回 None。"""
    titles = _codex_titles(config)
    for c in ([cli] if cli else config["chain"]):
        files, _ = _files(config, c)
        for f in files:
            name = Path(f).stem if c != "agy" else Path(f).parent.parent.parent.name
            if session_id in name:
                return _info_from(c, f, config, titles)
    return None


def latest_for_cwd(config, cwd, hours=None, now=None):
    """某資料夾（含其子資料夾）最近一段對話，跨三家取最新。"""
    hours = config.get("cwd_lookup_hours", 168) if hours is None else hours
    infos, _ = list_sessions(config, hours=hours, now=now)
    for s in infos:
        if s.cwd and is_under(s.cwd, cwd):
            return s
    return None


def format_list(infos):
    lines = []
    for i, s in enumerate(infos, 1):
        lines.append(
            f"{i:>2}. [{s.cli:<6}] {s.mtime_str()}  {s.cwd or '(cwd 未知)'}\n"
            f"      {s.session_id}  {s.title or '(無標題)'}\n"
            f"      最後使用者訊息：{s.last_user or '(無)'}"
        )
    return "\n".join(lines)
