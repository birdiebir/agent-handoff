"""共用 fixture：用假 jsonl 造三家紀錄，全部放 tmp_path，不碰真實紀錄。"""
import copy
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handoff.config import load_config  # noqa: E402

CLAUDE_ID = "11111111-2222-3333-4444-555555555555"
CODEX_ID = "01a15d3b-53b0-7233-8b89-549deb3493d0"
AGY_ID = "18af145c-3aa8-478e-84d6-840ffdddf760"


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def claude_rows(cwd, sid=CLAUDE_ID, with_compact=False):
    base = {"cwd": cwd, "sessionId": sid, "isSidechain": False}
    rows = [
        {"type": "ai-title", "aiTitle": "測試標題", "sessionId": sid},
        {**base, "type": "user", "timestamp": "2026-10-21T01:00:00Z", "origin": {"kind": "human"},
         "message": {"role": "user", "content": "舊的需求 OLD"}},
        {**base, "type": "assistant", "timestamp": "2026-10-21T01:00:05Z",
         "message": {"content": [{"type": "text", "text": "舊的回覆 OLD"}]}},
    ]
    if with_compact:
        rows.append({**base, "type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-21T01:05:00Z"})
        rows.append({**base, "type": "user", "isCompactSummary": True, "timestamp": "2026-10-21T01:05:01Z",
                     "message": {"content": "這是 compact 摘要不要出現"}})
    rows += [
        {**base, "type": "user", "timestamp": "2026-10-21T02:00:00Z", "origin": {"kind": "human"},
         "message": {"role": "user", "content": "請改 foo.py"}},
        {**base, "type": "user", "isMeta": True, "timestamp": "2026-10-21T02:00:01Z",
         "message": {"content": "meta 注入不要出現"}},
        {**base, "type": "user", "timestamp": "2026-10-21T02:00:02Z",
         "message": {"content": "<system-reminder>注入</system-reminder>"}},
        {**base, "type": "assistant", "timestamp": "2026-10-21T02:00:10Z",
         "message": {"content": [{"type": "thinking", "thinking": "思考不要出現"},
                                 {"type": "text", "text": "好，我來改"},
                                 {"type": "tool_use", "name": "Edit", "input": {"file_path": "C:/x/foo.py", "old_string": "a"}}]}},
        {**base, "type": "user", "timestamp": "2026-10-21T02:00:20Z",
         "message": {"content": [{"type": "tool_result", "content": "工具輸出SECRET不要出現"}]}},
        {**base, "type": "assistant", "timestamp": "2026-10-21T02:00:30Z",
         "message": {"content": [{"type": "text", "text": "改好了"}]}},
        {**base, "type": "assistant", "isSidechain": True, "timestamp": "2026-10-21T02:00:40Z",
         "message": {"content": [{"type": "text", "text": "子代理不要出現"}]}},
    ]
    return rows


def codex_rows(cwd, sid=CODEX_ID, subagent=False):
    meta = {"id": sid, "cwd": cwd, "timestamp": "2026-10-21T06:52:11Z", "thread_source": "user"}
    if subagent:
        meta["thread_source"] = "guardian_review"
        meta["parent_thread_id"] = "x"
    return [
        {"type": "session_meta", "timestamp": "2026-10-21T06:52:11Z", "payload": meta},
        {"type": "response_item", "timestamp": "2026-10-21T06:52:12Z",
         "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "developer 注入"}]}},
        {"type": "response_item", "timestamp": "2026-10-21T06:52:12Z",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "# AGENTS.md instructions for x"},
             {"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
        {"type": "response_item", "timestamp": "2026-10-21T06:52:13Z",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "舊 codex 需求 OLD"}]}},
        {"type": "compacted", "timestamp": "2026-10-21T06:53:00Z", "payload": {}},
        {"type": "response_item", "timestamp": "2026-10-21T06:54:00Z",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "跑一下測試"}]}},
        {"type": "response_item", "timestamp": "2026-10-21T06:54:01Z",
         "payload": {"type": "reasoning", "summary": [], "encrypted_content": "xxx"}},
        {"type": "response_item", "timestamp": "2026-10-21T06:54:02Z",
         "payload": {"type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["pytest", "-q"]})}},
        {"type": "response_item", "timestamp": "2026-10-21T06:54:03Z",
         "payload": {"type": "function_call_output", "output": "工具輸出SECRET不要出現"}},
        {"type": "response_item", "timestamp": "2026-10-21T06:54:04Z",
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "測試全過"}]}},
    ]


def agy_rows():
    return [
        {"type": "USER_INPUT", "created_at": "2026-10-20T04:01:39Z", "content":
            "<USER_REQUEST>\n查一下額度\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nlocal time\n</ADDITIONAL_METADATA>"},
        {"type": "PLANNER_RESPONSE", "created_at": "2026-10-20T04:01:40Z", "content": "我先搜尋",
         "tool_calls": [{"name": "run_command", "args": {"CommandLine": json.dumps("dir /w"), "Cwd": json.dumps("X:\\demo\\proj")}}]},
        {"type": "GENERIC", "created_at": "2026-10-20T04:01:45Z", "content": "工具輸出SECRET不要出現"},
        {"type": "SYSTEM_MESSAGE", "created_at": "2026-10-20T09:04:01Z", "content": "系統注入不要出現"},
        {"type": "PLANNER_RESPONSE", "created_at": "2026-10-20T04:02:00Z", "content": "查到了", "tool_calls": None},
    ]


FAKE_BATON = """import json, os, sys
a = sys.argv[1:]
if a and a[0] == "take":
    with open(os.environ["AGENT_BATON_FILE"], "w", encoding="utf-8") as f:
        json.dump({"holder": a[1]}, f)
    print("baton -> " + a[1])
"""


@pytest.fixture
def env(tmp_path):
    """建立假家目錄 + 對應 config。回傳 (config, paths dict)。"""
    cfg = copy.deepcopy(load_config())
    home = tmp_path / "home"
    cfg["card_dir"] = str(tmp_path / "cards")
    fake = tmp_path / "fake_baton.py"
    fake.write_text(FAKE_BATON, encoding="utf-8")
    cfg["baton_script"] = str(fake)
    cfg["work_paths"] = [str(tmp_path / "Work")]
    ad = cfg["adapters"]
    ad["claude"]["projects_dir"] = str(home / "claude" / "projects")
    ad["codex"]["sessions_dir"] = str(home / "codex" / "sessions")
    ad["codex"]["index_file"] = str(home / "codex" / "session_index.jsonl")
    ad["codex"]["import_ledger"] = str(home / "codex" / "ledger.json")
    ad["agy"]["brain_dirs"] = [str(home / "agy" / "brain")]
    # 測試預設「agy 不接工作專案」，不跟著本機設定變；要測允許的情況在各測試裡自己打開
    ad["agy"]["allow_work_projects"] = False
    plain = str(tmp_path / "proj")
    work = str(tmp_path / "Work" / "案件A")
    paths = {
        "home": home, "plain": plain, "work": work, "tmp": tmp_path,
        "claude_projects": home / "claude" / "projects",
        "codex_sessions": home / "codex" / "sessions",
        "agy_brain": home / "agy" / "brain",
    }
    return cfg, paths


@pytest.fixture
def claude_file(env):
    cfg, p = env
    return write_jsonl(p["claude_projects"] / "D--x-proj" / f"{CLAUDE_ID}.jsonl", claude_rows(p["plain"]))


def make_codex(p, cwd, sid=CODEX_ID, subagent=False):
    return write_jsonl(p["codex_sessions"] / "2026" / "10" / "21" / f"rollout-2026-10-21T14-52-11-{sid}.jsonl",
                       codex_rows(cwd, sid, subagent))


def make_agy(p, sid=AGY_ID):
    return write_jsonl(p["agy_brain"] / sid / ".system_generated" / "logs" / "transcript.jsonl", agy_rows())


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch, tmp_path):
    """所有測試：狀態資料夾指到暫存、不受 WT_SESSION 影響，絕不碰真的 private/state。"""
    monkeypatch.delenv("WT_SESSION", raising=False)
    # 設定檔也隔離：不讀本機 private/chain.local.json，只用 config/chain.example.json
    monkeypatch.setenv("HANDOFF_LOCAL_CONFIG", str(tmp_path / "no-local.json"))
    monkeypatch.delenv("HANDOFF_CARD_DIR", raising=False)
    monkeypatch.delenv("HANDOFF_BATON_SCRIPT", raising=False)
    monkeypatch.setenv("HANDOFF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_BATON_FILE", str(tmp_path / "baton.json"))   # 主線令牌也隔離
