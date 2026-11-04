"""讀三家 CLI 的對話紀錄，轉成統一的訊息清單。

規則（研究報告 3.2）：
- 只留使用者與助理的文字；
- 工具呼叫留一行摘要（工具名＋檔名或指令前 N 字），工具輸出丟掉；
- 跳過系統注入內容；
- 只取最後一次 compact 之後；
- 工作資料夾讀 jsonl 裡的 cwd 欄位，不用資料夾代號反推。
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TOOL_SUMMARY_CHARS = 100


class UnsupportedTranscript(Exception):
    """例如 .zst 壓縮的 Codex 紀錄。"""


@dataclass
class Message:
    role: str          # user / assistant / tool
    text: str
    ts: str = ""


@dataclass
class Transcript:
    cli: str
    session_id: str
    path: str
    cwd: str = None
    start: str = ""
    end: str = ""
    title: str = ""
    messages: list = field(default_factory=list)
    compacted: bool = False       # 是否有 compact 截斷過
    notes: list = field(default_factory=list)
    is_subagent: bool = False     # Codex 的 guardian／子代理對話，不列入清單
    noninteractive: bool = False  # Codex `exec`（source=="exec"）等非互動對話

    def user_messages(self):
        return [m for m in self.messages if m.role == "user"]

    def last_user_text(self):
        us = self.user_messages()
        return us[-1].text if us else ""


def _iter_jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def head_has_sdk_cli(path, head_lines=30):
    """Claude 紀錄開頭幾行有 entrypoint=sdk-cli（`claude -p`）就回 True；讀不到回 False。"""
    if not path:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= head_lines:
                    break
                if '"entrypoint":"sdk-cli"' in line or '"entrypoint": "sdk-cli"' in line:
                    return True
    except OSError:
        return False
    return False


def one_line(s, limit):
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _tool_line(name, detail, limit):
    d = one_line(detail, limit) if detail else ""
    return f"[工具] {name}" + (f"：{d}" if d else "")


def _pick_detail(args, keys):
    if not isinstance(args, dict):
        return str(args) if args else ""
    for k in keys:
        v = args.get(k)
        if v:
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    for v in args.values():
        if isinstance(v, str) and v:
            return v
    return ""


# ---------------------------------------------------------------- Claude
CLAUDE_INJECTED_PREFIXES = (
    "<command-name>", "<command-message>", "<command-args>", "<local-command",
    "<system-reminder>", "Caveat:", "<task-notification>", "[Request interrupted",
)
_CMD_NAME = re.compile(r"<command-name>\s*(\S+?)\s*</command-name>")
_CMD_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
CLAUDE_DETAIL_KEYS = ("file_path", "command", "path", "pattern", "url", "query", "description", "prompt")


def read_claude(path, tool_chars=TOOL_SUMMARY_CHARS):
    path = str(path)
    t = Transcript("claude", Path(path).stem, path)
    for o in _iter_jsonl(path):
        typ = o.get("type")
        ts = o.get("timestamp") or ""
        if typ == "ai-title":
            t.title = o.get("aiTitle") or t.title
            continue
        if typ == "system" and o.get("subtype") == "compact_boundary":
            t.messages = []
            t.compacted = True
            continue
        if typ not in ("user", "assistant"):
            continue
        if o.get("isSidechain"):
            continue
        if o.get("cwd") and not t.cwd:
            t.cwd = o["cwd"]
        if ts:
            t.start = t.start or ts
            t.end = ts
        if o.get("isMeta") or o.get("isCompactSummary"):
            continue
        origin = o.get("origin")
        if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
            continue
        content = (o.get("message") or {}).get("content")
        if typ == "user":
            texts = []
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            for x in texts:
                x = x.strip()
                cmd = _CMD_NAME.search(x)
                if cmd:   # 斜線指令：留「/指令 參數」一行（/exit 等純操作略過）
                    if cmd.group(1) not in ("/exit", "/clear", "/compact", "/handoff"):
                        a = _CMD_ARGS.search(x)
                        t.messages.append(Message("user", (cmd.group(1) + " " + (a.group(1).strip() if a else "")).strip(), ts))
                    continue
                if x and not x.startswith(CLAUDE_INJECTED_PREFIXES):
                    t.messages.append(Message("user", x, ts))
        else:
            if isinstance(content, str):
                if content.strip():
                    t.messages.append(Message("assistant", content.strip(), ts))
                continue
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    t.messages.append(Message("assistant", b["text"].strip(), ts))
                elif b.get("type") == "tool_use":
                    d = _pick_detail(b.get("input"), CLAUDE_DETAIL_KEYS)
                    t.messages.append(Message("tool", _tool_line(b.get("name", "?"), d, tool_chars), ts))
    return t


# ---------------------------------------------------------------- Codex
CODEX_INJECTED_PREFIXES = (
    "# AGENTS.md instructions", "<environment_context>", "<user_instructions>",
    "<turn_aborted>", "<permissions", "<INSTRUCTIONS>", "<collaboration_mode>",
    "[external_agent_tool_result]",
)


def read_codex(path, tool_chars=TOOL_SUMMARY_CHARS):
    path = str(path)
    if path.endswith(".zst"):
        raise UnsupportedTranscript("Codex 壓縮紀錄（.zst）尚未支援，已跳過")
    t = Transcript("codex", "", path)
    for o in _iter_jsonl(path):
        typ = o.get("type")
        ts = o.get("timestamp") or ""
        p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        if typ == "session_meta":
            t.session_id = p.get("id") or t.session_id
            t.cwd = p.get("cwd") or t.cwd
            t.start = p.get("timestamp") or ts
            t.is_subagent = bool(p.get("parent_thread_id")) or p.get("thread_source") not in (None, "user")
            t.noninteractive = p.get("source") == "exec"
            continue
        if ts:
            t.end = ts
        if typ == "compacted":
            t.messages = []
            t.compacted = True
            continue
        if typ != "response_item":
            continue
        pt = p.get("type")
        if pt == "message":
            role = p.get("role")
            if role not in ("user", "assistant"):
                continue
            for b in p.get("content") or []:
                x = (b.get("text") or "").strip() if isinstance(b, dict) else ""
                if not x:
                    continue
                if role == "user" and x.startswith(CODEX_INJECTED_PREFIXES):
                    continue
                if x.startswith("[external_agent_tool_call"):
                    t.messages.append(Message("tool", one_line(x, tool_chars), ts))
                    continue
                if x.startswith("[external_agent_tool_result]"):
                    continue
                t.messages.append(Message(role, x, ts))
        elif pt in ("function_call", "custom_tool_call"):
            raw = p.get("arguments") if pt == "function_call" else p.get("input")
            detail = raw
            if isinstance(raw, str):
                try:
                    detail = _pick_detail(json.loads(raw), ("command", "cmd", "path", "file_path", "query"))
                except (json.JSONDecodeError, TypeError):
                    detail = raw
                    if not isinstance(detail, str):
                        detail = ""
            t.messages.append(Message("tool", _tool_line(p.get("name", "?"), detail, tool_chars), ts))
    if not t.session_id:
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path)
        t.session_id = m.group(1) if m else Path(path).stem
    return t


def codex_session_meta(path):
    """只讀第一行 session_meta（列清單用，避免整檔解析）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            o = json.loads(f.readline())
    except (OSError, json.JSONDecodeError):
        return {}
    return o.get("payload") or {} if o.get("type") == "session_meta" else {}


# ---------------------------------------------------------------- agy
AGY_DETAIL_KEYS = ("CommandLine", "AbsolutePath", "TargetFile", "Query", "query", "Url", "url", "SearchPath")
_USER_REQ = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)


def _agy_args(args):
    """agy 的 tool_calls args 值是 JSON 編碼過的字串，解開。"""
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                pass
        out[k] = v
    return out


def read_agy(path, tool_chars=TOOL_SUMMARY_CHARS):
    path = str(path)
    sid = Path(path).parent.parent.parent.name
    t = Transcript("agy", sid, path)
    for o in _iter_jsonl(path):
        typ = o.get("type")
        ts = o.get("created_at") or ""
        if ts:
            t.start = t.start or ts
            t.end = ts
        if typ == "USER_INPUT":
            c = o.get("content") or ""
            m = _USER_REQ.search(c)
            x = (m.group(1) if m else c.split("<ADDITIONAL_METADATA>")[0]).strip()
            if x:
                t.messages.append(Message("user", x, ts))
        elif typ == "PLANNER_RESPONSE":
            c = (o.get("content") or "").strip()
            if c:
                t.messages.append(Message("assistant", c, ts))
            for tc in o.get("tool_calls") or []:
                args = _agy_args(tc.get("args"))
                if not t.cwd and isinstance(args.get("Cwd"), str):
                    t.cwd = args["Cwd"]
                d = _pick_detail(args, AGY_DETAIL_KEYS)
                t.messages.append(Message("tool", _tool_line(tc.get("name", "?"), d, tool_chars), ts))
        # GENERIC（工具輸出）、SYSTEM_MESSAGE 一律丟掉
    if not t.cwd:
        t.notes.append("agy 紀錄沒有 cwd 欄位，工作資料夾無法判斷")
    return t


READERS = {"claude": read_claude, "codex": read_codex, "agy": read_agy}


def read_transcript(cli, path, tool_chars=TOOL_SUMMARY_CHARS):
    return READERS[cli](path, tool_chars)
