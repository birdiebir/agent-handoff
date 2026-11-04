"""handoff 轉移主線令牌（用 conftest 產生的假令牌腳本，檔案隔離到 tmp）。"""
import json
import os

from handoff import state as ST


def _holder():
    with open(os.environ["AGENT_BATON_FILE"], encoding="utf-8") as f:
        return json.load(f)["holder"]


def test_set_baton_codex_then_claude(env):
    cfg, _ = env
    assert ST.set_baton(cfg, "codex", "t")
    assert _holder() == "codex"
    ST.set_baton(cfg, "claude", "t")
    assert _holder() == "claude"


def test_agy_never_takes_baton(env):
    cfg, _ = env
    assert ST.set_baton(cfg, "agy", "t") is None
    assert not os.path.exists(os.environ["AGENT_BATON_FILE"])


def test_missing_script_does_not_raise(env):
    cfg, _ = env
    cfg = dict(cfg, baton_script="/nope/baton.py")
    assert "找不到" in ST.set_baton(cfg, "codex", "t")
