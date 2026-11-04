"""審查修正（2026-10-21）：登記過濾與淘汰、hook 逾時與早退、啟動失敗不寫標記、
active 依 session 分開、接力卡回推 Claude session、agy 工作關鍵字、install 的 `|| true`。"""
import io
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import CLAUDE_ID, CODEX_ID, codex_rows, claude_rows, write_jsonl
from handoff import card as C, cli, hook_logic as H, sessions as S, state as ST, transcripts as T
from handoff.adapters import ADAPTERS, codex as codex_ad
from handoff.config import HandoffError, load_config

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
import install as I  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, 0)
X = "D:\\x"
OTHER = "99999999-2222-3333-4444-555555555555"


# ---------------------------------------------------------------- 1. 登記過濾與淘汰
def test_session_start_skips_sdk_cli_env(tmp_path):
    data = {"session_id": "abc", "cwd": X, "transcript_path": "T"}
    env = {"WT_SESSION": "W", "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}
    assert H.session_start(data, "claude", environ=env, sd=tmp_path, now=NOW) is False
    assert ST.lookup(tmp_path, "W") == []
    env["CLAUDE_CODE_ENTRYPOINT"] = "cli"
    assert H.session_start(data, "claude", environ=env, sd=tmp_path, now=NOW) is True


def test_register_evicts_noninteractive_first(tmp_path):
    """5 筆 claude -p 不能把互動對話擠掉（實證：分頁 101c6e53 曾 5 筆全是 sdk-cli）。"""
    sd = tmp_path / "s"
    ST.register(sd, "W", "claude", "human-a", X, "t", NOW)
    ST.register(sd, "W", "claude", "human-b", X, "t", NOW + timedelta(seconds=1))
    for i in range(7):
        ST.register(sd, "W", "claude", f"sdk{i}", X, "t", NOW + timedelta(seconds=10 + i), interactive=False)
    ids = [e["session_id"] for e in ST.lookup(sd, "W")]
    assert "human-a" in ids and "human-b" in ids and len(ids) == 5
    assert ids == ["sdk6", "sdk5", "sdk4", "human-b", "human-a"]


def test_register_all_interactive_drops_oldest(tmp_path):
    sd = tmp_path / "s"
    for i in range(7):
        ST.register(sd, "W", "claude", f"s{i}", X, "t", NOW + timedelta(seconds=i))
    assert [e["session_id"] for e in ST.lookup(sd, "W")] == ["s6", "s5", "s4", "s3", "s2"]


def test_register_total_limit_drops_noninteractive_first(tmp_path):
    sd = tmp_path / "s"
    ST.register(sd, "OLD", "claude", "human", X, "t", NOW)  # 最舊但是互動的
    for i in range(5):
        ST.register(sd, f"N{i}", "claude", f"sdk{i}", X, "t", NOW + timedelta(seconds=10 + i), interactive=False)
    ST.register(sd, "NEW", "claude", "human2", X, "t", NOW + timedelta(seconds=99), max_total=3)
    reg = json.loads((sd / "registry.json").read_text(encoding="utf-8"))
    assert sum(len(v) for v in reg.values()) == 3 and "OLD" in reg and "NEW" in reg


def test_purge_noninteractive_cleans_and_backs_up(tmp_path):
    sdk = tmp_path / "sdk.jsonl"
    sdk.write_text('{"type":"user","entrypoint":"sdk-cli"}\n', encoding="utf-8")
    human = tmp_path / "human.jsonl"
    human.write_text('{"type":"user","entrypoint":"cli"}\n', encoding="utf-8")
    sd = tmp_path / "s"
    ST.register(sd, "W", "claude", "h", X, str(human), NOW)
    ST.register(sd, "W", "claude", "s1", X, str(sdk), NOW + timedelta(seconds=1))
    ST.register(sd, "W", "claude", "s2", X, "gone.jsonl", NOW + timedelta(seconds=2), interactive=False)
    ST.register(sd, "ONLYSDK", "claude", "s3", X, str(sdk), NOW)
    before = (sd / "registry.json").read_bytes()
    assert ST.purge_noninteractive(sd, backup_suffix="20261021") == 3
    assert (sd / "registry.json.bak-20261021").read_bytes() == before
    reg = json.loads((sd / "registry.json").read_text(encoding="utf-8"))
    assert list(reg) == ["W"] and [e["session_id"] for e in reg["W"]] == ["h"]
    assert ST.purge_noninteractive(sd) == 0   # 再跑一次沒東西可清


def _codex_exec(p, sid, cwd):
    rows = codex_rows(cwd, sid)
    rows[0]["payload"]["source"] = "exec"
    return write_jsonl(p["codex_sessions"] / "2026" / "10" / "21" / f"rollout-2026-10-21T14-52-11-{sid}.jsonl", rows)


def test_codex_exec_excluded_from_list_and_fallback(env):
    cfg, p = env
    fa = write_jsonl(p["claude_projects"] / "D--x" / f"{CLAUDE_ID}.jsonl", claude_rows(p["plain"], CLAUDE_ID))
    fx = _codex_exec(p, CODEX_ID, p["plain"])
    t = os.path.getmtime(fa) + 100
    os.utime(fx, (t, t))   # exec 比互動對話新
    infos, _ = S.list_sessions(cfg)
    assert [i.session_id for i in infos] == [CLAUDE_ID]
    # 直接指定 id 仍找得到（使用者明確要求），但標成非互動
    got = S.find_session(cfg, CODEX_ID, "codex")
    assert got is not None and S.is_noninteractive(got)
    buf = io.StringIO()
    cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"], err=buf)
    assert CLAUDE_ID in buf.getvalue() and CODEX_ID not in buf.getvalue()


def test_codex_source_cli_is_kept(env):
    cfg, p = env
    rows = codex_rows(p["plain"], CODEX_ID)
    rows[0]["payload"]["source"] = "cli"
    write_jsonl(p["codex_sessions"] / "2026" / "10" / "21" / f"rollout-x-{CODEX_ID}.jsonl", rows)
    assert [i.session_id for i in S.list_sessions(cfg)[0]] == [CODEX_ID]


def test_fallback_multiple_recent_requires_list(env):
    cfg, p = env
    write_jsonl(p["claude_projects"] / "D--x" / f"{CLAUDE_ID}.jsonl", claude_rows(p["plain"], CLAUDE_ID))
    write_jsonl(p["claude_projects"] / "D--x" / f"{OTHER}.jsonl", claude_rows(p["plain"], OTHER))
    with pytest.raises(HandoffError) as e:
        cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"], err=io.StringIO())
    msg = str(e.value)
    assert "--list" in msg and "--session" in msg and CLAUDE_ID in msg and OTHER in msg
    # 明確指定就不受影響
    plan = cli.run(["--dry-run", "--session", OTHER], config=cfg, cwd_now=p["plain"], err=io.StringIO())
    assert plan["cwd"] == p["plain"]


def test_fallback_old_session_does_not_count_as_ambiguous(env):
    cfg, p = env
    fa = write_jsonl(p["claude_projects"] / "D--x" / f"{CLAUDE_ID}.jsonl", claude_rows(p["plain"], CLAUDE_ID))
    write_jsonl(p["claude_projects"] / "D--x" / f"{OTHER}.jsonl", claude_rows(p["plain"], OTHER))
    old = time.time() - 48 * 3600
    os.utime(fa, (old, old))   # 兩天前：不算 24 小時內的候選
    buf = io.StringIO()
    cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"], err=buf)
    assert OTHER in buf.getvalue()


def test_fallback_ignores_sdk_cli_when_counting(env):
    cfg, p = env
    write_jsonl(p["claude_projects"] / "D--x" / f"{CLAUDE_ID}.jsonl", claude_rows(p["plain"], CLAUDE_ID))
    rows = claude_rows(p["plain"], OTHER)
    for r in rows:
        r["entrypoint"] = "sdk-cli"
    write_jsonl(p["claude_projects"] / "D--x" / f"{OTHER}.jsonl", rows)
    buf = io.StringIO()
    cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"], err=buf)
    assert CLAUDE_ID in buf.getvalue()


# ---------------------------------------------------------------- 2. Codex 匯入：hook 逾時、早退、send 失敗
class FakeStdin:
    def __init__(self, fail=False):
        self.fail, self.sent = fail, []

    def write(self, s):
        if self.fail:
            raise BrokenPipeError("pipe closed")
        self.sent.append(s)

    def flush(self):
        pass

    def close(self):
        if self.fail:
            raise BrokenPipeError("pipe closed")


class FakeProc:
    def __init__(self, lines=(), exit_code=None, fail_write=False, block=False):
        self.stdin = FakeStdin(fail_write)
        self.code = exit_code
        self.terminated = False
        self._ev = threading.Event()
        self.stdout = self._gen(list(lines), block)

    def _gen(self, lines, block):
        yield from lines
        if block:
            self._ev.wait(10)

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self._ev.set()


def _patch_popen(monkeypatch, proc):
    monkeypatch.setattr(codex_ad.subprocess, "Popen", lambda *a, **k: proc)


def test_hook_import_timeout_value(monkeypatch, env):
    cfg, _ = env
    got = []
    monkeypatch.setattr(codex_ad, "import_claude_session",
                        lambda src, cwd, exe, timeout, ledger: got.append(timeout) or "TID")
    src = S.SessionInfo("claude", CLAUDE_ID, "p", X, 0)

    class Card:
        path, source_cli, claude_session = "C:/c.md", "claude", CLAUDE_ID
    assert cfg["adapters"]["codex"]["hook_import_timeout_sec"] == 15
    ADAPTERS["codex"].build_launch(Card(), src, X, cfg, hook=True)
    ADAPTERS["codex"].build_launch(Card(), src, X, cfg, hook=False)
    cfg["adapters"]["codex"]["hook_import_timeout_sec"] = 7
    ADAPTERS["codex"].build_launch(Card(), src, X, cfg, hook=True)
    del cfg["adapters"]["codex"]["hook_import_timeout_sec"]
    ADAPTERS["codex"].build_launch(Card(), src, X, cfg, hook=True)   # 沒設定：預設 15
    assert got == [15, 60, 7, 15]


def test_launch_tab_uses_hook_timeout(monkeypatch, env, claude_file):
    cfg, _ = env
    got = []
    monkeypatch.setattr(codex_ad, "import_claude_session",
                        lambda src, cwd, exe, timeout, ledger: got.append(timeout) or "TID")
    cli.run(["--session", CLAUDE_ID, "--launch", "tab"], config=cfg, launch_fn=lambda pl: "wt")
    cli.run(["--session", CLAUDE_ID], config=cfg)
    assert got == [15, 60]


def test_import_times_out(monkeypatch):
    proc = FakeProc(block=True)
    _patch_popen(monkeypatch, proc)
    t0 = time.time()
    with pytest.raises(codex_ad.ImportFailed) as e:
        codex_ad.import_claude_session("a.jsonl", X, "codex", timeout=1)
    assert "逾時" in str(e.value) and time.time() - t0 < 5 and proc.terminated


def test_import_detects_early_exit(monkeypatch):
    proc = FakeProc(lines=[], exit_code=1)   # app-server 一啟動就死
    _patch_popen(monkeypatch, proc)
    t0 = time.time()
    with pytest.raises(codex_ad.ImportFailed) as e:
        codex_ad.import_claude_session("a.jsonl", X, "codex", timeout=30)
    assert "提早結束" in str(e.value) and time.time() - t0 < 5   # 不用等滿 30 秒


def test_import_send_oserror_becomes_importfailed(monkeypatch):
    proc = FakeProc(block=True, fail_write=True)
    _patch_popen(monkeypatch, proc)
    with pytest.raises(codex_ad.ImportFailed) as e:
        codex_ad.import_claude_session("a.jsonl", X, "codex", timeout=30)
    assert "傳送" in str(e.value) and proc.terminated


def test_import_failure_falls_back_to_card_only(monkeypatch, env):
    """send 失敗 -> ImportFailed -> build_launch 退回 `-C <cwd> <提示>`（只靠接力卡）。"""
    cfg, _ = env
    _patch_popen(monkeypatch, FakeProc(block=True, fail_write=True))
    src = S.SessionInfo("claude", CLAUDE_ID, "p", X, 0)

    class Card:
        path, source_cli, claude_session = "C:/c.md", "claude", CLAUDE_ID
    plan = ADAPTERS["codex"].build_launch(Card(), src, X, cfg, hook=True)
    assert plan["args"][:4] == ["-p", "handoff", "-C", X] and any("匯入失敗" in n for n in plan["notes"])


def test_import_success_path_still_works(monkeypatch):
    """假 app-server：initialize 回覆、import 完成通知 -> 回傳 thread id。"""
    lines = [json.dumps({"id": 1, "result": {}}) + "\n",
             json.dumps({"method": "externalAgentConfig/import/completed", "params": {"itemTypeResults": [
                 {"itemType": "SESSIONS", "successes": [{"target": "NEW-TID"}], "failures": []}]}}) + "\n"]
    proc = FakeProc(lines=lines, block=True)
    _patch_popen(monkeypatch, proc)
    assert codex_ad.import_claude_session("a.jsonl", X, "codex", timeout=10) == "NEW-TID"


# ---------------------------------------------------------------- 3. 啟動失敗不寫 active
def test_launch_failure_does_not_write_active(env, claude_file, monkeypatch):
    cfg, _ = env
    monkeypatch.setattr(codex_ad, "import_claude_session", lambda *a, **k: "TID")

    def boom(plan):
        raise OSError("wt 起不來")
    with pytest.raises(OSError):
        cli.run(["--session", CLAUDE_ID, "--launch", "tab"], config=cfg, now=NOW, launch_fn=boom)
    sd = ST.state_dir(cfg)
    assert ST.read_active(sd, NOW) is None and not ST.active_dir(sd).exists()


def test_launch_failure_in_hook_is_blocked_without_marker(env, claude_file, monkeypatch):
    cfg, p = env
    monkeypatch.setattr(codex_ad, "import_claude_session", lambda *a, **k: "TID")

    def boom(plan):
        raise OSError("wt 起不來")
    out = H.prompt_submit({"prompt": "/handoff", "session_id": CLAUDE_ID, "cwd": p["plain"]}, config=cfg,
                          now=NOW, launch_fn=boom)
    assert out["decision"] == "block" and "handoff 失敗" in out["reason"]
    assert ST.read_active(ST.state_dir(cfg), NOW) is None


def test_launch_success_writes_active_after_launch(env, claude_file, monkeypatch):
    cfg, _ = env
    monkeypatch.setattr(codex_ad, "import_claude_session", lambda *a, **k: "TID")
    seen = []
    sd = ST.state_dir(cfg)
    cli.run(["--session", CLAUDE_ID, "--launch", "tab"], config=cfg, now=NOW,
            launch_fn=lambda pl: seen.append(ST.read_active(sd, NOW)) or "wt")
    assert seen == [None]   # 啟動當下還沒寫
    assert ST.read_active(sd, NOW)["holder"] == "codex"


def test_ps_command_stops_when_set_location_fails():
    from handoff import launch as L
    cmd = L.build_ps_command({"exe": "codex", "args": ["x"], "cwd": "D:\\no"})
    assert cmd.split(";")[0].endswith("-ErrorAction Stop") and "& 'codex'" in cmd.split(";")[1]


# ---------------------------------------------------------------- 4. 接力卡回推 Claude session、active 分開存
def _codex_with_prompt(p, cfg, card_path, sid="cccccccc-0000-0000-0000-000000000000"):
    rows = codex_rows(p["plain"], sid)
    rows.append({"type": "response_item", "timestamp": "2026-10-21T07:00:00Z", "payload": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text":
            f"你是從 claude 交接過來的接手方（codex）。先讀接力卡 {card_path}，了解來源對話最後做到哪裡。\n遵守規則"}]}})
    f = write_jsonl(p["codex_sessions"] / "2026" / "10" / "21" / f"rollout-x-{sid}.jsonl", rows)
    return T.read_transcript("codex", str(f))


def _write_card(cfg, name, claude_id, cwd, mtime):
    d = Path(cfg["card_dir"])
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(f"# 接力卡\n\n- 工作資料夾：{cwd}\n- Claude session：{claude_id}\n", encoding="utf-8")
    os.utime(f, (mtime, mtime))
    return f


def test_claude_id_from_card_path_beats_newest_card(env):
    cfg, p = env
    now = time.time()
    mine = _write_card(cfg, "2026-10-21_1000_claude.md", CLAUDE_ID, p["plain"], now - 1000)
    _write_card(cfg, "2026-10-21_1100_claude.md", OTHER, p["plain"], now)   # 同資料夾較新，但不是這段的
    tr = _codex_with_prompt(p, cfg, mine)
    assert C.find_inherited_claude_id(cfg["card_dir"], p["plain"]) == OTHER   # 舊做法會選錯
    card = C.make_card(tr, cfg, now=NOW, git_status=None, write=False)
    assert card.claude_session == CLAUDE_ID


def test_claude_id_falls_back_to_newest_card(env):
    cfg, p = env
    _write_card(cfg, "2026-10-21_1100_claude.md", OTHER, p["plain"], time.time())
    tr = _codex_with_prompt(p, cfg, str(Path(cfg["card_dir"]) / "not-exist.md"))
    assert C.make_card(tr, cfg, now=NOW, git_status=None, write=False).claude_session == OTHER


def test_claude_id_ignores_card_outside_card_dir(env):
    cfg, p = env
    outside = p["tmp"] / "elsewhere" / "x.md"
    outside.parent.mkdir()
    outside.write_text(f"- Claude session：{CLAUDE_ID}\n", encoding="utf-8")
    tr = _codex_with_prompt(p, cfg, outside)
    assert C.make_card(tr, cfg, now=NOW, git_status=None, write=False).claude_session is None


def test_active_stored_per_claude_session(env):
    cfg, _ = env
    sd = ST.state_dir(cfg)
    ST.write_active(sd, "codex", "cardA", CLAUDE_ID, CLAUDE_ID, X, NOW)
    ST.write_active(sd, "agy", "cardB", OTHER, OTHER, X, NOW + timedelta(minutes=1))
    assert sorted(f.name for f in ST.active_dir(sd).glob("*.json")) == sorted([f"{CLAUDE_ID}.json", f"{OTHER}.json"])
    assert ST.read_active(sd, NOW, claude_session=CLAUDE_ID)["holder"] == "codex"
    assert ST.read_active(sd, NOW, claude_session=OTHER)["holder"] == "agy"
    assert ST.read_active(sd, NOW, claude_session="nobody") is None


def test_reminder_only_reads_own_active(env):
    cfg, _ = env
    sd = ST.state_dir(cfg)
    ST.write_active(sd, "codex", "cardA", CLAUDE_ID, CLAUDE_ID, X, NOW)
    ST.write_active(sd, "agy", "cardB", OTHER, OTHER, X, NOW)
    a = H.prompt_submit({"prompt": "繼續", "session_id": CLAUDE_ID, "cwd": X}, config=cfg, now=NOW)
    b = H.prompt_submit({"prompt": "繼續", "session_id": OTHER, "cwd": X}, config=cfg, now=NOW)
    ctx_a = a["hookSpecificOutput"]["additionalContext"]
    ctx_b = b["hookSpecificOutput"]["additionalContext"]
    assert "codex" in ctx_a and "cardA" in ctx_a and "agy" not in ctx_a
    assert "agy" in ctx_b and "cardB" in ctx_b and "codex" not in ctx_b
    assert H.prompt_submit({"prompt": "繼續", "session_id": "third", "cwd": X}, config=cfg, now=NOW) is None


def test_handoff_claude_clears_only_own_marker(env):
    cfg, _ = env
    sd = ST.state_dir(cfg)
    ST.write_active(sd, "codex", "cardA", CLAUDE_ID, CLAUDE_ID, X, NOW)
    ST.write_active(sd, "agy", "cardB", OTHER, OTHER, X, NOW)
    out = H.prompt_submit({"prompt": "/handoff claude", "session_id": CLAUDE_ID, "cwd": X}, config=cfg, now=NOW)
    assert out["decision"] == "block"
    assert ST.read_active(sd, NOW, claude_session=CLAUDE_ID) is None
    assert ST.read_active(sd, NOW, claude_session=OTHER)["holder"] == "agy"
    # 沒有 session_id 的 /handoff claude 不會誤清別人的
    H.prompt_submit({"prompt": "/handoff claude", "cwd": X}, config=cfg, now=NOW)
    assert ST.read_active(sd, NOW, claude_session=OTHER)["holder"] == "agy"


def test_legacy_single_active_is_migrated(env):
    cfg, _ = env
    sd = ST.state_dir(cfg)
    Path(sd).mkdir(parents=True, exist_ok=True)
    legacy = ST.legacy_active_path(sd)
    legacy.write_text(json.dumps({"holder": "codex", "since": NOW.strftime("%Y-%m-%dT%H:%M:%S"), "card": "old",
                                  "claude_session": CLAUDE_ID, "source_session": CLAUDE_ID, "cwd": X}), encoding="utf-8")
    a = ST.read_active(sd, NOW, claude_session=CLAUDE_ID)
    assert a["holder"] == "codex" and a["card"] == "old"
    assert not legacy.exists() and (ST.active_dir(sd) / f"{CLAUDE_ID}.json").exists()
    out = H.prompt_submit({"prompt": "hi", "session_id": CLAUDE_ID, "cwd": X}, config=cfg, now=NOW)
    assert "old" in out["hookSpecificOutput"]["additionalContext"]


def test_legacy_active_cleared_by_handoff_claude(env):
    cfg, _ = env
    sd = ST.state_dir(cfg)
    Path(sd).mkdir(parents=True, exist_ok=True)
    ST.legacy_active_path(sd).write_text(json.dumps({"holder": "codex", "since": NOW.strftime("%Y-%m-%dT%H:%M:%S"),
        "card": "old", "claude_session": CLAUDE_ID, "source_session": CLAUDE_ID, "cwd": X}), encoding="utf-8")
    H.prompt_submit({"prompt": "/handoff claude", "session_id": CLAUDE_ID, "cwd": X}, config=cfg, now=NOW)
    assert ST.read_active(sd, NOW) is None and not ST.legacy_active_path(sd).exists()


def test_clear_active_without_session_clears_all(env):
    cfg, _ = env
    sd = ST.state_dir(cfg)
    ST.write_active(sd, "codex", "a", CLAUDE_ID, CLAUDE_ID, X, NOW)
    ST.write_active(sd, "agy", "b", OTHER, OTHER, X, NOW)
    assert ST.clear_active(sd) is True and ST.read_active(sd, NOW) is None


# ---------------------------------------------------------------- 5. agy 工作關鍵字、cwd 退回值
def test_default_config_has_work_keywords():
    kws = load_config()["work_keywords"]
    for k in ("ClientAlpha", "ClientBeta", "ProjectOrion", "confidential"):
        assert k in kws


@pytest.mark.parametrize("kw", ["ClientAlpha", "ClientBeta", "ProjectOrion", "confidential"])
def test_keyword_blocks_agy_explicit_and_auto(env, kw):
    cfg, p = env
    text = f"今天要處理{kw}的案子"
    with pytest.raises(HandoffError) as e:
        cli.choose_target("claude", p["plain"], cfg, explicit="agy", text=text)
    assert kw in str(e.value) and "work_keywords" in str(e.value)
    with pytest.raises(HandoffError):      # 備援順序自動跳過 agy -> 沒有下一家
        cli.choose_target("codex", p["plain"], cfg, text=text)
    assert cli.choose_target("claude", p["plain"], cfg, text=text) == "codex"   # codex 不受影響


def test_keyword_case_insensitive_and_clean_text_passes(env):
    cfg, p = env
    with pytest.raises(HandoffError):
        cli.choose_target("claude", p["plain"], cfg, explicit="agy", text="clientbeta 上線")
    assert cli.choose_target("claude", p["plain"], cfg, explicit="agy", text="整理音樂檔案") == "agy"


def test_user_can_edit_keywords_and_override(env):
    cfg, p = env
    cfg["work_keywords"] = ["自訂詞"]
    assert cli.choose_target("claude", p["plain"], cfg, explicit="agy", text="ClientAlpha") == "agy"
    with pytest.raises(HandoffError):
        cli.choose_target("claude", p["plain"], cfg, explicit="agy", text="有自訂詞")
    cfg["adapters"]["agy"]["allow_work_projects"] = True
    assert cli.choose_target("claude", p["plain"], cfg, explicit="agy", text="有自訂詞") == "agy"


def test_agy_cwd_fallback_refused(env):
    cfg, p = env
    with pytest.raises(HandoffError) as e:
        cli.choose_target("claude", p["plain"], cfg, explicit="agy", cwd_fallback=True)
    assert "退回值" in str(e.value)
    assert cli.choose_target("claude", p["plain"], cfg, cwd_fallback=True) == "codex"
    with pytest.raises(HandoffError):
        cli.choose_target("codex", p["plain"], cfg, cwd_fallback=True)


def test_run_keyword_in_last_messages_blocks_agy_and_writes_nothing(env):
    cfg, p = env
    rows = claude_rows(p["plain"])
    rows[1]["message"]["content"] = "舊的需求 OLD"
    rows.append({"type": "user", "cwd": p["plain"], "timestamp": "2026-10-21T03:00:00Z", "origin": {"kind": "human"},
                 "message": {"content": "幫我看ProjectOrion會議的簡報"}})
    write_jsonl(p["claude_projects"] / "D--x" / f"{CLAUDE_ID}.jsonl", rows)
    with pytest.raises(HandoffError) as e:
        cli.run(["agy", "--session", CLAUDE_ID], config=cfg)
    assert "ProjectOrion" in str(e.value) and not os.path.exists(cfg["card_dir"])
    # 同一段交給 codex 沒問題
    assert cli.run(["codex", "--session", CLAUDE_ID, "--dry-run"], config=cfg)["target"] == "codex"


def test_run_keyword_in_assistant_text_also_blocks(env):
    cfg, p = env
    rows = claude_rows(p["plain"])
    rows.append({"type": "assistant", "cwd": p["plain"], "timestamp": "2026-10-21T03:00:00Z",
                 "message": {"content": [{"type": "text", "text": "這是 ClientBeta 的素材"}]}})
    write_jsonl(p["claude_projects"] / "D--x" / f"{CLAUDE_ID}.jsonl", rows)
    with pytest.raises(HandoffError):
        cli.run(["agy", "--session", CLAUDE_ID, "--dry-run"], config=cfg)


def test_run_without_cwd_never_goes_to_agy(env):
    cfg, p = env
    rows = [{"type": "user", "timestamp": "2026-10-21T03:00:00Z", "origin": {"kind": "human"},
             "message": {"content": "整理音樂"}}]   # 沒有 cwd 欄位
    write_jsonl(p["claude_projects"] / "D--x" / f"{CLAUDE_ID}.jsonl", rows)
    with pytest.raises(HandoffError) as e:
        cli.run(["agy", "--session", CLAUDE_ID, "--dry-run"], config=cfg, cwd_now=p["plain"])
    assert "退回值" in str(e.value)


def test_agy_build_launch_screens_card_text(env):
    cfg, p = env

    class Card:
        path, source_cli, claude_session, text = "C:/c.md", "claude", CLAUDE_ID, "含 ClientAlpha 字樣"
    with pytest.raises(HandoffError):
        ADAPTERS["agy"].build_launch(Card(), None, p["plain"], cfg)


# ---------------------------------------------------------------- 6. install 的 `|| true`
PY = "C:/Python/python.exe"


def _all_cmds(entries):
    return [h for g in entries.values() for h in g["hooks"]]


def test_install_commands_end_with_or_true():
    for h in _all_cmds(I.claude_entries(PY)):
        assert h["command"].endswith(" || true")
    cx = _all_cmds(I.codex_entries(PY))[0]
    assert cx["command"].endswith(" || true")
    assert "||" not in cx["commandWindows"]   # PowerShell 5.1 不支援 ||


def test_is_ours_accepts_both_forms():
    base = '"C:/Python/python.exe" -X utf8 -I "/opt/agent-handoff/hooks/session_start.py" --cli claude'
    assert I.is_ours(base) and I.is_ours(base + " || true") and I.is_ours(base + " || true  ")
    assert not I.is_ours('node other.js || true') and not I.is_ours("") and not I.is_ours(None)
    assert I.strip_tail(base + " || true") == base and I.strip_tail(base) == base


def test_merge_replaces_old_form_without_duplicates():
    old_cmd = I.hook_cmd(PY, "prompt_submit.py", safe=False)   # 舊版安裝：沒有 || true
    cfg = {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "node keep.js"}]},
        {"hooks": [{"type": "command", "command": old_cmd, "timeout": 25}]}]}}
    new = I.merge_hooks(cfg, I.claude_entries(PY))
    cmds = [h["command"] for g in new["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert cmds.count("node keep.js") == 1 and len([c for c in cmds if "prompt_submit.py" in c]) == 1
    assert [c for c in cmds if "prompt_submit.py" in c][0].endswith("|| true")
    assert I.merge_hooks(new, I.claude_entries(PY)) == new   # 冪等


def test_strip_removes_both_forms():
    for safe in (True, False):
        cfg = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "mine"}]},
                                          {"hooks": [{"type": "command", "command": I.hook_cmd(PY, "session_start.py", safe=safe)}]}]}}
        assert I.strip_hooks(cfg)["hooks"]["SessionStart"] == [{"hooks": [{"type": "command", "command": "mine"}]}]


def test_install_dry_run_json_contains_or_true(tmp_path, capsys):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    assert I.main(["--dry-run", "--home", str(tmp_path), "--python", PY]) == 0
    assert "|| true" in capsys.readouterr().out
