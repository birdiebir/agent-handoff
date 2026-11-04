import pytest

from conftest import (AGY_ID, CLAUDE_ID, CODEX_ID, claude_rows, make_agy, make_codex, write_jsonl)
from handoff import transcripts as T


def texts(t):
    return [(m.role, m.text) for m in t.messages]


def test_claude_basic(claude_file, env):
    t = T.read_claude(claude_file)
    assert t.session_id == CLAUDE_ID
    assert t.cwd == env[1]["plain"]          # 讀 cwd 欄位
    assert t.title == "測試標題"
    roles = [m.role for m in t.messages]
    assert roles == ["user", "assistant", "user", "assistant", "tool", "assistant"]
    joined = "\n".join(m.text for m in t.messages)
    for bad in ("SECRET", "思考不要出現", "meta 注入", "system-reminder", "子代理不要出現"):
        assert bad not in joined
    tool = [m for m in t.messages if m.role == "tool"][0]
    assert tool.text == "[工具] Edit：C:/x/foo.py"          # 工具名＋檔名
    assert t.start == "2026-10-21T01:00:00Z" and t.end.startswith("2026-10-21T02:00")


def test_claude_compact_truncates(env, tmp_path):
    p = write_jsonl(tmp_path / "c.jsonl", claude_rows(env[1]["plain"], with_compact=True))
    t = T.read_claude(p)
    assert t.compacted
    joined = "\n".join(m.text for m in t.messages)
    assert "OLD" not in joined                  # compact 之前的丟掉
    assert "compact 摘要" not in joined
    assert t.user_messages()[0].text == "請改 foo.py"


def test_claude_tool_summary_truncated(tmp_path):
    rows = [{"type": "assistant", "cwd": "C:/a", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "x" * 500}}]}}]
    t = T.read_claude(write_jsonl(tmp_path / "c.jsonl", rows))
    line = t.messages[0].text
    assert line.startswith("[工具] Bash：") and len(line) < 130 and line.endswith("…")


def test_codex(env):
    p = make_codex(env[1], env[1]["plain"])
    t = T.read_codex(p)
    assert t.session_id == CODEX_ID
    assert t.cwd == env[1]["plain"]
    assert t.compacted
    assert [m.role for m in t.messages] == ["user", "tool", "assistant"]
    assert t.messages[1].text == "[工具] shell：['pytest', '-q']" or "pytest" in t.messages[1].text
    joined = "\n".join(m.text for m in t.messages)
    for bad in ("SECRET", "developer 注入", "AGENTS.md instructions", "environment_context", "OLD"):
        assert bad not in joined
    assert not t.is_subagent


def test_codex_subagent_flag(env):
    p = make_codex(env[1], env[1]["plain"], sid="aaaaaaaa-0000-0000-0000-000000000001", subagent=True)
    assert T.read_codex(p).is_subagent


def test_codex_zst_skipped(tmp_path):
    f = tmp_path / "rollout-x.jsonl.zst"
    f.write_bytes(b"x")
    with pytest.raises(T.UnsupportedTranscript):
        T.read_codex(f)


def test_agy(env):
    p = make_agy(env[1])
    t = T.read_agy(p)
    assert t.session_id == AGY_ID
    assert t.cwd == "X:\\demo\\proj"      # 從 tool call 的 Cwd 取
    assert [m.role for m in t.messages] == ["user", "assistant", "tool", "assistant"]
    assert t.messages[0].text == "查一下額度"
    assert t.messages[2].text == "[工具] run_command：dir /w"
    joined = "\n".join(m.text for m in t.messages)
    assert "SECRET" not in joined and "系統注入" not in joined and "ADDITIONAL_METADATA" not in joined


def test_agy_no_cwd_noted(tmp_path):
    rows = [{"type": "USER_INPUT", "created_at": "t", "content": "<USER_REQUEST>hi</USER_REQUEST>"}]
    d = tmp_path / "brain" / "abc" / ".system_generated" / "logs"
    t = T.read_agy(write_jsonl(d / "transcript.jsonl", rows))
    assert t.cwd is None and t.notes


def test_claude_slash_command_kept_as_one_line(tmp_path):
    rows = [
        {"type": "user", "cwd": "C:/a", "message": {"content": "<command-message>x</command-message><command-name>/save</command-name><command-args>95%</command-args>"}},
        {"type": "user", "cwd": "C:/a", "message": {"content": "<command-name>/exit</command-name>"}},
    ]
    t = T.read_claude(write_jsonl(tmp_path / "c.jsonl", rows))
    assert [m.text for m in t.messages] == ["/save 95%"]
