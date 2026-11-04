"""登記表、pick_source、prompt_submit、active 標記、launch。全部用暫存檔。"""
import base64
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from conftest import CLAUDE_ID, CODEX_ID, claude_rows, make_codex, write_jsonl
from handoff import cli, hook_logic as H, launch as L, state as ST


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.setenv("HANDOFF_STATE_DIR", str(tmp_path / "state"))


NOW = datetime(2026, 10, 21, 12, 0, 0)
X = "D:\\x"


# ---- 登記表 ----
def test_register_and_lookup_newest_first(tmp_path):
    sd = tmp_path / "s"
    ST.register(sd, "WT1", "claude", "a", X, "t1", NOW)
    ST.register(sd, "WT1", "codex", "b", X, "t2", NOW + timedelta(seconds=5))
    assert [e["session_id"] for e in ST.lookup(sd, "WT1")] == ["b", "a"]
    assert ST.lookup(sd, "WT-other") == [] and ST.lookup(sd, None) == []


def test_register_same_session_dedup(tmp_path):
    sd = tmp_path / "s"
    ST.register(sd, "W", "claude", "a", "c", "t", NOW)
    ST.register(sd, "W", "claude", "b", "c", "t", NOW + timedelta(seconds=1))
    ST.register(sd, "W", "claude", "a", "c", "t", NOW + timedelta(seconds=2))
    assert [e["session_id"] for e in ST.lookup(sd, "W")] == ["a", "b"]


def test_register_per_tab_and_total_limit(tmp_path):
    sd = tmp_path / "s"
    for i in range(8):
        ST.register(sd, "W", "claude", f"s{i}", "c", "t", NOW + timedelta(seconds=i))
    assert [e["session_id"] for e in ST.lookup(sd, "W")] == ["s7", "s6", "s5", "s4", "s3"]
    sd2 = tmp_path / "s2"
    for i in range(50):
        for j in range(5):
            ST.register(sd2, f"W{i}", "claude", f"s{i}-{j}", "c", "t", NOW + timedelta(seconds=i * 10 + j))
    reg = json.loads((sd2 / "registry.json").read_text(encoding="utf-8"))
    assert sum(len(v) for v in reg.values()) == 200
    assert "W0" not in reg and "W49" in reg  # 最舊的分頁先被丟


def test_register_requires_wt_and_id(tmp_path):
    assert ST.register(tmp_path, "", "claude", "a", "c", "t") is False
    assert ST.register(tmp_path, "W", "claude", "", "c", "t") is False


def test_session_start_hook_logic(tmp_path):
    data = {"session_id": "abc", "cwd": X, "transcript_path": "T"}
    assert H.session_start(data, "claude", environ={}, sd=tmp_path) is False
    assert H.session_start(data, "codex", environ={"WT_SESSION": "W"}, sd=tmp_path, now=NOW) is True
    e = ST.lookup(tmp_path, "W")[0]
    assert e["cli"] == "codex" and e["session_id"] == "abc" and e["transcript_path"] == "T"


def test_session_start_script_never_fails(tmp_path):
    script = os.path.join(os.path.dirname(__file__), "..", "hooks", "session_start.py")
    for stdin in ["", "not json", "{}", '{"session_id": 5}']:
        r = subprocess.run([sys.executable, "-I", script], input=stdin, text=True, capture_output=True,
                           env={**os.environ, "WT_SESSION": "W", "HANDOFF_STATE_DIR": str(tmp_path)}, timeout=10)
        assert r.returncode == 0 and r.stdout == ""


def test_prompt_submit_script_silent_for_normal_prompt(tmp_path):
    script = os.path.join(os.path.dirname(__file__), "..", "hooks", "prompt_submit.py")
    for stdin in ["", "garbage", json.dumps({"prompt": "你好", "session_id": "x", "cwd": X})]:
        r = subprocess.run([sys.executable, "-I", script], input=stdin, text=True, capture_output=True,
                           env={**os.environ, "HANDOFF_STATE_DIR": str(tmp_path)}, timeout=10)
        assert r.returncode == 0 and r.stdout == ""


# ---- pick_source ----
def test_pick_source_prefers_wt_registry(env):
    cfg, p = env
    # 同資料夾兩段 Claude：B 較新（資料夾最近一段會選 B），但這個分頁登記的是 A
    A, B = CLAUDE_ID, "99999999-2222-3333-4444-555555555555"
    fa = write_jsonl(p["claude_projects"] / "D--x" / f"{A}.jsonl", claude_rows(p["plain"], A))
    fb = write_jsonl(p["claude_projects"] / "D--x" / f"{B}.jsonl", claude_rows(p["plain"], B))
    t = os.path.getmtime(fa) + 100
    os.utime(fb, (t, t))
    ST.register(ST.state_dir(cfg), "WTX", "claude", A, p["plain"], str(fa), NOW)
    buf = io.StringIO()
    cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"], wt_session="WTX", err=buf)
    assert A in buf.getvalue() and B not in buf.getvalue()
    # 查不到登記才退回資料夾最近一段；24 小時內同資料夾有兩段互動對話 -> 不猜、請用 --list
    with pytest.raises(cli.HandoffError) as e:
        cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"], wt_session="WT-none", err=io.StringIO())
    assert "--list" in str(e.value) and "--session" in str(e.value)


def test_pick_source_uses_env_wt_session(env, monkeypatch):
    cfg, p = env
    A, B = CLAUDE_ID, "99999999-2222-3333-4444-555555555555"
    fa = write_jsonl(p["claude_projects"] / "D--x" / f"{A}.jsonl", claude_rows(p["plain"], A))
    fb = write_jsonl(p["claude_projects"] / "D--x" / f"{B}.jsonl", claude_rows(p["plain"], B))
    t = os.path.getmtime(fa) + 100
    os.utime(fb, (t, t))
    ST.register(ST.state_dir(cfg), "ENVTAB", "claude", A, p["plain"], str(fa), NOW)
    monkeypatch.setenv("WT_SESSION", "ENVTAB")
    buf = io.StringIO()
    cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"], err=buf)
    assert A in buf.getvalue()


def test_pick_source_registry_stale_falls_back(env, claude_file):
    cfg, p = env
    ST.register(ST.state_dir(cfg), "W", "claude", "deadbeef-gone", p["plain"], "x", NOW)
    buf = io.StringIO()
    cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"], wt_session="W", err=buf)
    assert CLAUDE_ID in buf.getvalue()


# ---- prompt 比對 ----
@pytest.mark.parametrize("text,expect", [
    ("/handoff", (True, None)), ("/handoff codex", (True, "codex")), ("/ho", (True, None)),
    ("  /ho agy ", (True, "agy")), ("/HANDOFF Codex", (True, "codex")),
    ("請幫我 /handoff", (False, None)), ("handoff", (False, None)), ("/handoffs", (False, None)),
    ("/handoff codex 順便", (False, None)), ("/handoff\n﻿", (True, None)), ("﻿/ho codex\n", (True, "codex")), ("", (False, None)), (None, (False, None)),
])
def test_parse_prompt(text, expect):
    assert H.parse_prompt(text) == expect


# ---- prompt_submit ----
def test_prompt_submit_block_and_launch(env, claude_file):
    cfg, p = env
    launched = []
    out = H.prompt_submit({"prompt": "/handoff", "session_id": CLAUDE_ID, "cwd": p["plain"]}, config=cfg,
                          now=NOW, launch_fn=lambda plan: launched.append(plan) or "wt")
    assert set(out) == {"decision", "reason"} and out["decision"] == "block"
    assert "已交給 codex（新分頁）" in out["reason"] and "接力卡：" in out["reason"] and "/exit" in out["reason"]
    assert len(launched) == 1 and launched[0]["exe"] == "codex" and launched[0]["cwd"] == p["plain"]
    a = ST.read_active(ST.state_dir(cfg), NOW)
    assert a["holder"] == "codex" and a["source_session"] == CLAUDE_ID and a["claude_session"] == CLAUDE_ID
    assert os.path.exists(a["card"])


def test_prompt_submit_alias_and_target(env, claude_file):
    cfg, p = env
    out = H.prompt_submit({"prompt": "/ho codex", "session_id": CLAUDE_ID, "cwd": p["plain"]}, config=cfg,
                          now=NOW, launch_fn=lambda plan: "wt")
    assert out["decision"] == "block" and "codex" in out["reason"]


def test_prompt_submit_errors_are_blocked_with_message(env, claude_file):
    cfg, p = env
    out = H.prompt_submit({"prompt": "/handoff foo", "session_id": CLAUDE_ID, "cwd": p["plain"]}, config=cfg,
                          launch_fn=lambda plan: "wt")
    assert out["decision"] == "block" and "不認得" in out["reason"]
    out = H.prompt_submit({"prompt": "/handoff", "session_id": "nope", "cwd": p["plain"]}, config=cfg,
                          launch_fn=lambda plan: "wt")
    assert out["decision"] == "block" and "找不到 session" in out["reason"]


def test_prompt_submit_claude_clears_active(env):
    cfg, p = env
    sd = ST.state_dir(cfg)
    ST.write_active(sd, "codex", "C", CLAUDE_ID, CLAUDE_ID, X, datetime.now())
    out = H.prompt_submit({"prompt": "/handoff claude", "session_id": CLAUDE_ID, "cwd": X}, config=cfg)
    assert out["decision"] == "block" and ST.read_active(sd) is None


def test_normal_prompt_silent_without_active(env):
    cfg, _ = env
    assert H.prompt_submit({"prompt": "寫個函式", "session_id": CLAUDE_ID, "cwd": X}, config=cfg) is None


# ---- active 提醒 ----
def test_active_reminder_for_original_session(env):
    cfg, _ = env
    ST.write_active(ST.state_dir(cfg), "codex", "D:\\card.md", CLAUDE_ID, CLAUDE_ID, X, NOW)
    out = H.prompt_submit({"prompt": "繼續", "session_id": CLAUDE_ID, "cwd": X}, config=cfg,
                          now=NOW + timedelta(hours=1))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit" and "decision" not in out
    assert "codex" in ctx and "2026-10-21 12:00" in ctx and "D:\\card.md" in ctx and "/handoff claude" in ctx


def test_active_reminder_other_session_and_claude_holder_silent(env):
    cfg, _ = env
    sd = ST.state_dir(cfg)
    ST.write_active(sd, "codex", "c", CLAUDE_ID, CLAUDE_ID, X, NOW)
    assert H.prompt_submit({"prompt": "hi", "session_id": "other", "cwd": "x"}, config=cfg, now=NOW) is None
    ST.write_active(sd, "claude", "c", CLAUDE_ID, CLAUDE_ID, X, NOW)
    assert H.prompt_submit({"prompt": "hi", "session_id": CLAUDE_ID, "cwd": "x"}, config=cfg, now=NOW) is None


def test_active_expires_after_24h(env):
    cfg, _ = env
    ST.write_active(ST.state_dir(cfg), "codex", "c", CLAUDE_ID, CLAUDE_ID, X, NOW)
    q = {"prompt": "hi", "session_id": CLAUDE_ID, "cwd": "x"}
    assert H.prompt_submit(q, config=cfg, now=NOW + timedelta(hours=23)) is not None
    assert H.prompt_submit(q, config=cfg, now=NOW + timedelta(hours=25)) is None


def test_cli_run_sets_active_and_claude_clears(env):
    cfg, p = env
    make_codex(p, p["plain"])
    sd = ST.state_dir(cfg)
    cli.run(["claude", "--session", CODEX_ID], config=cfg, now=NOW)
    assert ST.read_active(sd, NOW) is None  # 回 Claude：清除
    cli.run(["agy", "--session", CODEX_ID], config=cfg, now=NOW)
    assert ST.read_active(sd, NOW)["holder"] == "agy"
    cli.run(["claude", "--session", CODEX_ID], config=cfg, now=NOW)
    assert ST.read_active(sd, NOW) is None


def test_dry_run_does_not_write_active(env, claude_file):
    cfg, _ = env
    cli.run(["--dry-run", "--session", CLAUDE_ID], config=cfg)
    assert ST.read_active(ST.state_dir(cfg)) is None


# ---- launch ----
def test_launch_quote_and_encode():
    assert L.ps_quote("a'b") == "'a''b'" and L.ps_quote("\u2018x") == "'\u2018\u2018x'"
    plan = {"exe": "codex", "args": ["resume", "T", "第一行\n第二行 'q' ; 分號"], "cwd": "D:\\中文 資料夾"}
    cmd = L.build_ps_command(plan)
    assert cmd.startswith("Set-Location -LiteralPath 'D:\\中文 資料夾' -ErrorAction Stop; & 'codex' 'resume' 'T'")
    argv = L.build_wt_argv(plan, "wt.exe")
    assert argv[:7] == ["wt.exe", "-w", "0", "new-tab", "-d", "D:\\中文 資料夾", "powershell"]
    assert argv[7:9] == ["-NoExit", "-EncodedCommand"]
    assert base64.b64decode(argv[9]).decode("utf-16-le") == cmd and ";" not in argv[9]


def test_launch_tab_prefers_wt_falls_back_to_powershell():
    calls = []
    plan = {"exe": "codex", "args": ["x"], "cwd": X}
    assert L.launch_tab(plan, popen=lambda a, **k: calls.append((a, k)), which=lambda n: "C:\\wt.exe") == "wt"
    assert calls[0][0][0] == "C:\\wt.exe"
    calls.clear()
    assert L.launch_tab(plan, popen=lambda a, **k: calls.append((a, k)), which=lambda n: None) == "powershell"
    assert calls[0][0][0] == "powershell" and calls[0][1]["cwd"] == X


def test_cli_launch_flag_calls_launcher(env, claude_file):
    cfg, _ = env
    got = []
    plan = cli.run(["--session", CLAUDE_ID, "--launch", "tab"], config=cfg,
                   launch_fn=lambda pl: got.append(pl) or "wt")
    assert plan["launched"] == "wt" and got and got[0]["exe"] == "codex"
