"""設定讀取順序：config 範例預設 < 本機設定檔 < 環境變數。"""
import json

import pytest

from handoff import config as C


def _write(p, d):
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_example_only_when_no_local(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDOFF_LOCAL_CONFIG", str(tmp_path / "none.json"))
    cfg = C.load_config()
    assert cfg["card_dir"] == "~/handoff-cards" and cfg["baton_script"] == ""
    assert cfg["chain"] == ["claude", "codex", "agy"]


def test_local_overrides_and_deep_merges(tmp_path, monkeypatch):
    loc = _write(tmp_path / "l.json", {"card_dir": "X:/cards", "adapters": {"agy": {"allow_work_projects": True}}})
    monkeypatch.setenv("HANDOFF_LOCAL_CONFIG", str(loc))
    cfg = C.load_config()
    assert cfg["card_dir"] == "X:/cards"
    assert cfg["adapters"]["agy"]["allow_work_projects"] is True
    assert cfg["adapters"]["agy"]["exe"] == "agy"          # 沒寫的欄位保留範例值
    assert cfg["adapters"]["codex"]["profile"] == "handoff"


def test_env_beats_local(tmp_path, monkeypatch):
    loc = _write(tmp_path / "l.json", {"card_dir": "X:/cards", "baton_script": "X:/b.py"})
    monkeypatch.setenv("HANDOFF_LOCAL_CONFIG", str(loc))
    monkeypatch.setenv("HANDOFF_CARD_DIR", "Y:/env-cards")
    monkeypatch.setenv("HANDOFF_BATON_SCRIPT", "Y:/env-baton.py")
    cfg = C.load_config()
    assert cfg["card_dir"] == "Y:/env-cards" and cfg["baton_script"] == "Y:/env-baton.py"


def test_lists_are_replaced_not_appended(tmp_path, monkeypatch):
    loc = _write(tmp_path / "l.json", {"work_keywords": ["only-this"]})
    monkeypatch.setenv("HANDOFF_LOCAL_CONFIG", str(loc))
    assert C.load_config()["work_keywords"] == ["only-this"]


def test_explicit_path_replaces_local_and_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDOFF_LOCAL_CONFIG", str(_write(tmp_path / "a.json", {"card_dir": "A"})))
    b = _write(tmp_path / "b.json", {"card_dir": "B"})
    assert C.load_config(b)["card_dir"] == "B"
    with pytest.raises(FileNotFoundError):
        C.load_config(tmp_path / "missing.json")


def test_custom_baton_prompt_and_rules_label(env):
    from handoff.adapters.base import handoff_prompt

    class Card:
        source_cli, path = "claude", "C:/c.md"
    cfg, _ = env
    cfg = dict(cfg, baton_prompt="TOKEN {forbidden} END", rules_labels={"codex": "MY RULES"})
    p = handoff_prompt(Card(), "codex", cfg)
    assert "遵守MY RULES。" in p and "TOKEN PROJECT.md、TODO.md END" in p
