"""install.py：合併不弄丟現有 hook、uninstall 只移除自己的、dry-run 不寫檔。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
import install as I  # noqa: E402

EXISTING = {
    "model": "x",
    "hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "node autosave_threshold_hook.js", "timeout": 5}]}],
        "SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": "python compact_hook.py"}]}],
        "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "node autosave.js"}]}],
    },
}
PY = "C:/Python/python.exe"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / ".claude" / "settings.json").write_text(json.dumps(EXISTING, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return h


def cmds(cfg, event):
    return [x["command"] for g in cfg["hooks"].get(event, []) for x in g["hooks"]]


def test_merge_keeps_existing_and_adds_ours():
    new = I.merge_hooks(EXISTING, I.claude_entries(PY))
    assert "node autosave_threshold_hook.js" in cmds(new, "UserPromptSubmit")
    assert any("prompt_submit.py" in c for c in cmds(new, "UserPromptSubmit"))
    assert "python compact_hook.py" in cmds(new, "SessionStart")
    assert any("session_start.py" in c for c in cmds(new, "SessionStart"))
    assert new["hooks"]["PostToolUse"] == EXISTING["hooks"]["PostToolUse"] and new["model"] == "x"
    assert EXISTING["hooks"]["SessionStart"][0]["matcher"] == "compact"  # 沒改到輸入


def test_merge_is_idempotent():
    once = I.merge_hooks(EXISTING, I.claude_entries(PY))
    assert once == I.merge_hooks(once, I.claude_entries(PY))


def test_strip_removes_only_ours():
    assert I.strip_hooks(I.merge_hooks(EXISTING, I.claude_entries(PY))) == EXISTING


def test_strip_drops_empty_event():
    assert I.strip_hooks(I.merge_hooks({}, I.codex_entries(PY))) == {"hooks": {}}


def test_apply_backs_up_and_writes(home):
    done = I.apply_actions(I.plan_actions(home, PY))
    st = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert any("prompt_submit.py" in c for c in cmds(st, "UserPromptSubmit"))
    baks = list((home / ".claude").glob("settings.json.bak-*-handoff"))
    assert len(baks) == 1 and json.loads(baks[0].read_text(encoding="utf-8")) == EXISTING
    assert (home / ".claude" / "commands" / "handoff.md").exists()
    cx = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "commandWindows" in cx["hooks"]["SessionStart"][0]["hooks"][0]
    assert not list((home / ".codex").glob("*.bak-*"))  # 新建的檔沒東西可備份
    assert done


def test_codex_existing_is_backed_up_and_merged(home):
    (home / ".codex").mkdir()
    other = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}
    (home / ".codex" / "hooks.json").write_text(json.dumps(other), encoding="utf-8")
    I.apply_actions(I.plan_actions(home, PY))
    cx = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert cx["hooks"]["Stop"] == other["hooks"]["Stop"] and "SessionStart" in cx["hooks"]
    assert len(list((home / ".codex").glob("hooks.json.bak-*-handoff"))) == 1


def test_uninstall_only_removes_ours(home):
    I.apply_actions(I.plan_actions(home, PY))
    p = home / ".claude" / "settings.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "mine"}]}]  # 安裝後使用者自己加的
    p.write_text(json.dumps(cfg), encoding="utf-8")
    I.apply_actions(I.plan_actions(home, PY, uninstall=True))
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "mine"}]}]
    assert cmds(back, "UserPromptSubmit") == ["node autosave_threshold_hook.js"]
    assert cmds(back, "SessionStart") == ["python compact_hook.py"]
    assert not (home / ".claude" / "commands" / "handoff.md").exists()


def test_uninstall_keeps_edited_command_file(home):
    I.apply_actions(I.plan_actions(home, PY))
    f = home / ".claude" / "commands" / "handoff.md"
    f.write_text("我改過的", encoding="utf-8")
    I.apply_actions(I.plan_actions(home, PY, uninstall=True))
    assert f.read_text(encoding="utf-8") == "我改過的"


def test_dry_run_writes_nothing(home, capsys):
    before = (home / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert I.main(["--dry-run", "--home", str(home), "--python", PY]) == 0
    assert (home / ".claude" / "settings.json").read_text(encoding="utf-8") == before
    assert not (home / ".claude" / "commands").exists() and not (home / ".codex").exists()
    out = capsys.readouterr().out
    assert "[會改]" in out and "未寫入" in out
