import json
import os
import time
from datetime import datetime

from conftest import CLAUDE_ID, CODEX_ID, AGY_ID, make_agy, make_codex, write_jsonl
from handoff import card as C
from handoff import sessions as S
from handoff import transcripts as T
from handoff.transcripts import Message


def test_list_sessions_three_clis(claude_file, env):
    cfg, p = env
    make_codex(p, p["plain"])
    make_codex(p, p["plain"], sid="aaaaaaaa-0000-0000-0000-000000000001", subagent=True)   # 不應列出
    make_agy(p)
    write_jsonl(p["codex_sessions"].parent / "session_index.jsonl", [{"id": CODEX_ID, "thread_name": "Codex 名稱"}])
    infos, notes = S.list_sessions(cfg, hours=24 * 365 * 5)
    by = {i.cli: i for i in infos}
    assert set(by) == {"claude", "codex", "agy"} and len(infos) == 3
    assert by["claude"].title == "測試標題" and by["claude"].last_user == "請改 foo.py"
    assert by["codex"].title == "Codex 名稱" and by["codex"].cwd == p["plain"]
    assert by["agy"].session_id == AGY_ID
    assert all(i.session_id for i in infos)


def test_list_respects_hours(claude_file, env):
    cfg, _ = env
    old = time.time() - 48 * 3600
    os.utime(claude_file, (old, old))
    assert S.list_sessions(cfg, hours=24)[0] == []
    assert len(S.list_sessions(cfg, hours=72)[0]) == 1


def test_zst_counted_in_notes(env):
    cfg, p = env
    d = p["codex_sessions"] / "2026" / "10" / "01"
    d.mkdir(parents=True)
    (d / "rollout-a.jsonl.zst").write_bytes(b"x")
    # glob 為 rollout-*.zst：檔名需以 .zst 結尾
    (d / "rollout-b.zst").write_bytes(b"x")
    _, notes = S.list_sessions(cfg, hours=24)
    assert any("zst" in n for n in notes)


def test_find_session_and_cwd_lookup(claude_file, env):
    cfg, p = env
    make_codex(p, p["plain"])
    assert S.find_session(cfg, CLAUDE_ID).cli == "claude"
    assert S.find_session(cfg, CODEX_ID, cli="codex").cwd == p["plain"]
    assert S.find_session(cfg, "nonexistent") is None
    # 碼同資料夾時取最新
    newer = time.time() + 100
    for f in p["codex_sessions"].rglob("rollout-*.jsonl"):
        os.utime(f, (newer, newer))
    assert S.latest_for_cwd(cfg, p["plain"]).cli == "codex"
    assert S.latest_for_cwd(cfg, str(p["tmp"] / "other")) is None


def make_transcript(n_users=15, long=False):
    t = T.Transcript("claude", CLAUDE_ID, "x", cwd="C:\\not\\a\\repo\\zzz", start="s", end="e")
    for i in range(n_users):
        t.messages.append(Message("user", f"問題{i}"))
        t.messages.append(Message("assistant", ("很長" * 5000) if long else f"回答{i}"))
    return t


def test_select_messages_user_limit():
    picked = C.select_messages(make_transcript(15).messages, {"max_user_messages": 10, "max_chars": 10**6})
    users = [x for m, x in picked if m.role == "user"]
    assert len(users) == 10 and users[-1] == "問題14" and users[0] == "問題5"


def test_select_messages_char_limit_and_truncate():
    picked = C.select_messages(make_transcript(5, long=True).messages,
                               {"max_user_messages": 10, "max_chars": 15000, "max_message_chars": 3000})
    assert sum(len(x) for _, x in picked) <= 15000 + 3100
    assert all(len(x) <= 3000 + 10 for _, x in picked)
    assert picked[-1][0].role == "assistant"          # 最新的一定在


def test_make_card_writes_and_format(env):
    cfg, p = env
    t = make_transcript(3)
    t.cwd = p["plain"]
    now = datetime(2026, 10, 21, 16, 30)
    card = C.make_card(t, cfg, now=now, git_status="M foo.py")
    assert os.path.basename(card.path) == "2026-10-21_1630_claude.md"
    txt = open(card.path, encoding="utf-8").read()
    assert f"- 工作資料夾：{p['plain']}" in txt and f"- Claude session：{CLAUDE_ID}" in txt
    assert "M foo.py" in txt and "## 接手方紀錄" in txt and "問題2" in txt
    # 同分鐘再產生一張：不覆蓋
    card2 = C.make_card(t, cfg, now=now, git_status=None)
    assert card2.path != card.path and os.path.exists(card.path)


def test_make_card_dry_run_writes_nothing(env):
    cfg, p = env
    t = make_transcript(2)
    t.cwd = p["plain"]
    card = C.make_card(t, cfg, now=datetime(2026, 10, 21, 16, 30), git_status=None, write=False)
    assert not os.path.exists(card.path)
    assert not os.path.exists(cfg["card_dir"])


def test_inherit_claude_session_from_earlier_card(env):
    cfg, p = env
    t = make_transcript(2)
    t.cwd = p["plain"]
    C.make_card(t, cfg, now=datetime(2026, 10, 21, 16, 30), git_status=None)     # claude 卡
    t2 = T.Transcript("codex", CODEX_ID, "x", cwd=p["plain"])
    t2.messages = [Message("user", "繼續")]
    c2 = C.make_card(t2, cfg, now=datetime(2026, 10, 21, 17, 0), git_status=None)
    assert c2.claude_session == CLAUDE_ID                                         # 接力鏈保留最早的 Claude id
    assert f"- Claude session：{CLAUDE_ID}" in c2.text


def test_git_status_not_repo_returns_none(tmp_path):
    assert C.run_git_status(str(tmp_path)) is None
