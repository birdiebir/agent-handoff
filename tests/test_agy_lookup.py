"""agy 對話找不到 cwd 的兩條退路。

agy 的 transcript 沒有 cwd 欄位，只能從工具呼叫的 Cwd 參數推；對話還沒用過工具時，
在 agy 裡打 /handoff 會報「這個資料夾沒有任何對話」。
"""
import io
import os
import sys
import time

import pytest

from conftest import AGY_ID, write_jsonl
from handoff import cli, launch as L, sessions as S

ROWS_NO_TOOL = [
    {"type": "USER_INPUT", "created_at": "2026-10-29T06:37:11Z",
     "content": "<USER_REQUEST>\n只回覆 OK\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nlocal time\n</ADDITIONAL_METADATA>"},
    {"type": "PLANNER_RESPONSE", "created_at": "2026-10-29T06:37:12Z", "content": "OK", "tool_calls": []},
]


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.setenv("HANDOFF_STATE_DIR", str(tmp_path / "state"))


def _agy(env, sid=AGY_ID):
    cfg, p = env
    cfg["adapters"]["agy"]["history_files"] = [str(p["home"] / "agy" / "history.jsonl")]
    return write_jsonl(p["agy_brain"] / sid / ".system_generated" / "logs" / "transcript.jsonl", ROWS_NO_TOOL)


def test_agy_cwd_from_history_file(env):
    cfg, p = env
    f = _agy(env)
    write_jsonl(p["home"] / "agy" / "history.jsonl", [
        {"display": "舊的", "timestamp": 1, "workspace": "C:\\old", "conversationId": AGY_ID},
        {"display": "只回覆 OK", "timestamp": 2, "workspace": p["plain"], "conversationId": AGY_ID},
        {"display": "別段", "timestamp": 3, "workspace": "C:\\other", "conversationId": "zzz"},
    ])
    info = S.find_session(cfg, AGY_ID, "agy")
    assert info.cwd == p["plain"]          # 同一段取最新一筆
    buf = io.StringIO()
    cli.run(["claude", "--from", "agy", "--dry-run"], config=cfg, cwd_now=p["plain"], err=buf)
    assert AGY_ID in buf.getvalue()
    assert os.path.exists(f)


def test_agy_recent_fallback_when_no_cwd(env):
    cfg, p = env
    _agy(env)                               # 沒有 history、沒有工具呼叫 -> cwd 未知
    buf = io.StringIO()
    cli.run(["claude", "--from", "agy", "--dry-run"], config=cfg, cwd_now=p["plain"], err=buf)
    out = buf.getvalue()
    assert AGY_ID in out and "剛更新" in out


def test_agy_recent_fallback_only_for_fresh_and_from_agy(env):
    cfg, p = env
    f = _agy(env)
    old = time.time() - 3600
    os.utime(f, (old, old))                 # 一小時前的對話不算「正在打 /handoff 的那段」
    with pytest.raises(cli.HandoffError):
        cli.run(["claude", "--from", "agy", "--dry-run"], config=cfg, cwd_now=p["plain"], err=io.StringIO())
    os.utime(f, None)
    with pytest.raises(cli.HandoffError):   # 沒有 --from agy 不套用（Claude／Codex 有自己的 cwd）
        cli.run(["claude", "--dry-run"], config=cfg, cwd_now=p["plain"], err=io.StringIO())


def test_launch_tab_hidden_desktop_spawns_on_default():
    """agy 的指令跑在看不見的桌面（exebox-…）：wt 要改在 WinSta0\\Default 開，不然使用者看不到分頁（10-29 實測）。"""
    plan = {"exe": "claude", "args": ["x"], "cwd": "C:\\x"}
    for name, expect in (("exebox-7TTZ", "default"), ("Default", "popen"), ("", "popen")):
        got = []
        assert L.launch_tab(plan, popen=lambda a, **k: got.append("popen"), which=lambda n: "C:\\wt.exe",
                            desktop=lambda: name, spawn_default=lambda a: got.append("default")) == "wt"
        assert got == [expect]


@pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 有桌面名稱")
def test_current_desktop_reads_a_name():
    assert L.current_desktop()


def test_history_cwd_goes_into_card_and_newest_history_wins(env):
    cfg, p = env
    _agy(env)
    other = str(p["home"] / "agy" / "history2.jsonl")
    cfg["adapters"]["agy"]["history_files"].append(other)
    write_jsonl(p["home"] / "agy" / "history.jsonl",
                [{"timestamp": 200, "workspace": p["plain"], "conversationId": AGY_ID}])
    write_jsonl(other, [{"timestamp": 100, "workspace": "D:/old", "conversationId": AGY_ID}])
    assert S.agy_workspace(cfg, AGY_ID) == p["plain"]
    buf = io.StringIO()
    cli.run(["claude", "--from", "agy", "--dry-run"], config=cfg, cwd_now=p["plain"], err=buf)
    assert f"工作資料夾：{p['plain']}" in buf.getvalue()


def test_recent_fallback_refuses_when_two_fresh(env):
    cfg, p = env
    _agy(env)
    _agy(env, sid="22222222-3aa8-478e-84d6-840ffdddf760")
    with pytest.raises(cli.HandoffError) as e:
        cli.run(["claude", "--from", "agy", "--dry-run"], config=cfg, cwd_now=p["plain"], err=io.StringIO())
    assert "無法確定" in str(e.value)
