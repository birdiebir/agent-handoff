"""`claude -p` 對話（entrypoint=sdk-cli）交接時要跳過。"""
from handoff.cli import is_noninteractive
from handoff.sessions import SessionInfo


def _info(tmp_path, cli, lines):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return SessionInfo(cli=cli, session_id="x", path=str(p), cwd="", mtime=0.0)


def test_sdk_cli_is_noninteractive(tmp_path):
    info = _info(tmp_path, "claude", ['{"type":"user","entrypoint":"sdk-cli","message":{}}'])
    assert is_noninteractive(info)


def test_interactive_cli_kept(tmp_path):
    info = _info(tmp_path, "claude", ['{"type":"user","entrypoint":"cli","message":{}}'])
    assert not is_noninteractive(info)


def test_other_cli_never_skipped(tmp_path):
    info = _info(tmp_path, "codex", ['{"entrypoint":"sdk-cli"}'])
    assert not is_noninteractive(info)


def test_missing_file_kept(tmp_path):
    info = SessionInfo(cli="claude", session_id="x", path=str(tmp_path / "nope.jsonl"), cwd="", mtime=0.0)
    assert not is_noninteractive(info)
