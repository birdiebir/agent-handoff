import json
import os

import pytest

from conftest import CLAUDE_ID, CODEX_ID, make_agy, make_codex, write_jsonl
from handoff import cli
from handoff.adapters import ADAPTERS, codex as codex_ad
from handoff.config import HandoffError, is_work_path
from handoff.sessions import SessionInfo


class FakeCard:
    path = "C:/cards/x.md"
    source_cli = "claude"
    claude_session = CLAUDE_ID


# ---------- 目標選擇 ----------
def test_choose_target_order(env):
    cfg, p = env
    assert cli.choose_target("claude", p["plain"], cfg) == "codex"
    assert cli.choose_target("codex", p["plain"], cfg) == "agy"
    with pytest.raises(HandoffError):
        cli.choose_target("agy", p["plain"], cfg)          # 沒有下一家


def test_work_project_skips_agy(env):
    cfg, p = env
    assert is_work_path(p["work"], cfg) is True
    assert cli.choose_target("claude", p["work"], cfg) == "codex"
    with pytest.raises(HandoffError) as e:
        cli.choose_target("codex", p["work"], cfg)          # 跳過 agy 後沒有下一家
    assert "沒有下一家" in str(e.value)


def test_explicit_agy_rejected_for_work(env):
    cfg, p = env
    with pytest.raises(HandoffError) as e:
        cli.choose_target("claude", p["work"], cfg, explicit="agy")
    assert "工作專案" in str(e.value)
    assert cli.choose_target("claude", p["plain"], cfg, explicit="agy") == "agy"


def test_agy_allow_work_flag(env):
    cfg, p = env
    cfg["adapters"]["agy"]["allow_work_projects"] = True
    assert cli.choose_target("claude", p["work"], cfg, explicit="agy") == "agy"


def test_explicit_back_to_claude_and_errors(env):
    cfg, p = env
    assert cli.choose_target("codex", p["work"], cfg, explicit="claude") == "claude"
    with pytest.raises(HandoffError):
        cli.choose_target("claude", p["plain"], cfg, explicit="claude")
    with pytest.raises(HandoffError):
        cli.choose_target("claude", p["plain"], cfg, explicit="gemini")


def test_agy_unknown_cwd_refused(env):
    cfg, _ = env
    assert cli.agy_blocked(None, cfg)


# ---------- adapter ----------
def test_claude_adapter_resume_and_continue():
    cfg = __import__("handoff.config", fromlist=["load_config"]).load_config()
    plan = ADAPTERS["claude"].build_launch(FakeCard(), None, "D:\\x", cfg)
    assert plan["args"][:2] == ["--resume", CLAUDE_ID] and plan["cwd"] == "D:\\x"
    assert "C:/cards/x.md" in plan["args"][2] and "git diff" in plan["args"][2]
    c = FakeCard()
    c.claude_session = None
    assert ADAPTERS["claude"].build_launch(c, None, "D:\\x", cfg)["args"][0] == "--continue"


def test_prompt_contains_rules(env):
    cfg, _ = env
    prompt = ADAPTERS["codex"].build_launch(FakeCard(), None, "D:\\x", cfg)["args"][-1]
    for must in ("C:/cards/x.md", "AGENTS.md", "PROJECT.md", "TODO.md",
                 "不刪檔", "push", "接手方紀錄", "主線令牌", "確認令牌"):
        assert must in prompt
    assert "不准改" not in prompt   # 令牌交給 Codex，共用檔可以寫


def test_prompt_without_baton_keeps_forbidden(env):
    cfg, _ = env
    cfg = dict(cfg)
    cfg.pop("baton_script", None)
    prompt = ADAPTERS["codex"].build_launch(FakeCard(), None, "D:/x", cfg)["args"][-1]
    assert "不准改" in prompt and "git push" in prompt


def test_codex_native_import_success(monkeypatch, env):
    cfg, _ = env
    calls = []
    monkeypatch.setattr(codex_ad, "import_claude_session",
                        lambda src, cwd, exe, timeout, ledger: calls.append((src, cwd)) or "NEW-THREAD-ID")
    src = SessionInfo("claude", CLAUDE_ID, "C:/a/b.jsonl", "D:\\x", 0)
    plan = ADAPTERS["codex"].build_launch(FakeCard(), src, "D:\\x", cfg)
    assert calls == [("C:/a/b.jsonl", "D:\\x")]
    assert plan["args"][:4] == ["-p", "handoff", "resume", "NEW-THREAD-ID"] and plan["exe"] == "codex"


def test_codex_import_failure_falls_back(monkeypatch, env):
    cfg, _ = env

    def boom(*a, **k):
        raise codex_ad.ImportFailed("壞了")
    monkeypatch.setattr(codex_ad, "import_claude_session", boom)
    src = SessionInfo("claude", CLAUDE_ID, "C:/a/b.jsonl", "D:\\x", 0)
    plan = ADAPTERS["codex"].build_launch(FakeCard(), src, "D:\\x", cfg)
    assert plan["args"][:4] == ["-p", "handoff", "-C", "D:\\x"] and any("壞了" in n for n in plan["notes"])


def test_codex_no_import_from_non_claude(monkeypatch, env):
    cfg, _ = env
    monkeypatch.setattr(codex_ad, "import_claude_session", lambda *a, **k: pytest.fail("不該匯入"))
    src = SessionInfo("agy", "x", "p", "D:\\x", 0)
    assert ADAPTERS["codex"].build_launch(FakeCard(), src, "D:\\x", cfg)["args"][:3] == ["-p", "handoff", "-C"]


def test_codex_dry_run_does_not_import(monkeypatch, env):
    cfg, _ = env
    monkeypatch.setattr(codex_ad, "import_claude_session", lambda *a, **k: pytest.fail("dry-run 不該匯入"))
    src = SessionInfo("claude", CLAUDE_ID, "p", "D:\\x", 0)
    plan = ADAPTERS["codex"].build_launch(FakeCard(), src, "D:\\x", cfg, dry_run=True)
    assert plan["args"][:3] == ["-p", "handoff", "resume"]


def test_parse_completed_and_ledger(tmp_path):
    ok = {"itemTypeResults": [{"itemType": "SESSIONS", "failures": [], "successes": [{"itemType": "SESSIONS", "target": "TID"}]}]}
    assert codex_ad.parse_completed(ok, "x") == "TID"
    bad = {"itemTypeResults": [{"itemType": "SESSIONS", "successes": [], "failures": [{"failureStage": "s", "message": "m"}]}]}
    with pytest.raises(codex_ad.ImportFailed):
        codex_ad.parse_completed(bad, "x")
    led = tmp_path / "l.json"
    prefix = chr(92) * 2 + "?" + chr(92)
    src = str(tmp_path / "s.jsonl")
    led.write_text(json.dumps({"records": [
        {"source_path": prefix + src, "imported_thread_id": "OLD", "imported_at": 1},
        {"source_path": prefix + src, "imported_thread_id": "NEWER", "imported_at": 2}]}), encoding="utf-8")
    assert codex_ad.ledger_lookup(str(led), src) == "NEWER"


def test_agy_adapter_refuses_work(env):
    cfg, p = env
    with pytest.raises(HandoffError):
        ADAPTERS["agy"].build_launch(FakeCard(), None, p["work"], cfg)
    plan = ADAPTERS["agy"].build_launch(FakeCard(), None, p["plain"], cfg)
    assert plan["args"][0] == "-i" and plan["exe"] == "agy"


# ---------- 端到端（run） ----------
def test_run_dry_run_no_files_written(claude_file, env, monkeypatch):
    cfg, p = env
    monkeypatch.setattr(codex_ad, "import_claude_session", lambda *a, **k: pytest.fail("不該匯入"))
    plan = cli.run(["--dry-run", "--session", CLAUDE_ID], config=cfg)
    assert plan["exe"] == "codex" and plan["cwd"] == p["plain"] and plan["args"][:3] == ["-p", "handoff", "resume"]
    assert not os.path.exists(cfg["card_dir"])            # 沒寫卡


def test_run_writes_card_and_imports(claude_file, env, monkeypatch):
    cfg, p = env
    monkeypatch.setattr(codex_ad, "import_claude_session", lambda *a, **k: "TID")
    plan = cli.run(["--session", CLAUDE_ID], config=cfg)
    cards = list(os.scandir(cfg["card_dir"]))
    assert len(cards) == 1 and cards[0].name.endswith("_claude.md")
    assert plan["args"][:4] == ["-p", "handoff", "resume", "TID"]


def test_run_work_project_agy_rejected_and_no_card(env):
    cfg, p = env
    write_jsonl(p["claude_projects"] / "w" / f"{CLAUDE_ID}.jsonl",
                [{"type": "user", "cwd": p["work"], "message": {"content": "hi"}}])
    with pytest.raises(HandoffError):
        cli.run(["agy", "--session", CLAUDE_ID], config=cfg)
    assert not os.path.exists(cfg["card_dir"])


def test_run_list_interactive(claude_file, env, monkeypatch):
    cfg, _ = env
    plan = cli.run(["--list", "--dry-run"], config=cfg, input_fn=lambda prompt: "1")
    assert plan["exe"] == "codex"
    with pytest.raises(HandoffError):
        cli.run(["--list", "--dry-run"], config=cfg, input_fn=lambda prompt: "")


def test_run_default_uses_cwd(claude_file, env):
    cfg, p = env
    plan = cli.run(["--dry-run"], config=cfg, cwd_now=p["plain"])
    assert plan["cwd"] == p["plain"]
    with pytest.raises(HandoffError):
        cli.run(["--dry-run"], config=cfg, cwd_now=str(p["tmp"] / "elsewhere"))


def test_codex_profile_empty_means_no_flag(monkeypatch, env):
    cfg, _ = env
    cfg["adapters"]["codex"]["profile"] = ""
    monkeypatch.setattr(codex_ad, "import_claude_session", lambda *a, **k: "TID")
    src = SessionInfo("claude", CLAUDE_ID, "p", "D:/x", 0)
    assert ADAPTERS["codex"].build_launch(FakeCard(), src, "D:/x", cfg)["args"][:2] == ["resume", "TID"]
    assert ADAPTERS["codex"].build_launch(FakeCard(), None, "D:/x", cfg)["args"][0] == "-C"
    del cfg["adapters"]["codex"]["profile"]
    assert ADAPTERS["codex"].build_launch(FakeCard(), None, "D:/x", cfg)["args"][0] == "-C"


def test_codex_profile_custom_name(env):
    cfg, _ = env
    cfg["adapters"]["codex"]["profile"] = "mine"
    assert ADAPTERS["codex"].build_launch(FakeCard(), None, "D:/x", cfg)["args"][:2] == ["-p", "mine"]


def test_chain_json_default_profile_is_handoff():
    from handoff.config import load_config
    assert load_config()["adapters"]["codex"]["profile"] == "handoff"
