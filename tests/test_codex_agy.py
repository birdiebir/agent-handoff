"""第三階段：Codex 的 UserPromptSubmit（/handoff）與 agy skill、install 合併／移除。全部用暫存資料夾。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CODEX_ID, make_codex
from handoff import hook_logic as H, state as ST

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
import install as I  # noqa: E402

HOOK = str(Path(__file__).resolve().parent.parent / "hooks" / "prompt_submit.py")
PY = "C:/Python/python.exe"


def codex_prompt(prompt, cwd, sid=CODEX_ID):
    # Codex UserPromptSubmit 的 stdin 欄位（官方文件）：session_id、transcript_path、cwd、hook_event_name、model、turn_id、prompt
    return {"session_id": sid, "transcript_path": None, "cwd": cwd, "hook_event_name": "UserPromptSubmit",
            "model": "gpt-x", "turn_id": "t1", "prompt": prompt}


# ---- prompt 比對 ----
@pytest.mark.parametrize("text,expect", [
    ("$handoff", (True, None)), ("$ho agy", (True, "agy")), ("/handoff claude", (True, "claude")),
    ("請 $handoff", (False, None)), ("$handoffs", (False, None)),
])
def test_parse_prompt_dollar_only_for_codex(text, expect):
    assert H.parse_prompt(text, allow_dollar=True) == expect


def test_claude_does_not_accept_dollar():
    assert H.parse_prompt("$handoff") == (False, None)


# ---- Codex hook 邏輯 ----
def test_codex_handoff_default_goes_to_agy(env):
    cfg, p = env
    make_codex(p, p["plain"])
    launched = []
    out = H.prompt_submit(codex_prompt("/handoff", p["plain"]), config=cfg, cli="codex",
                          launch_fn=lambda plan: launched.append(plan) or "wt")
    assert set(out) == {"decision", "reason"} and out["decision"] == "block"
    assert "已交給 agy（新分頁）" in out["reason"] and "codex" in out["reason"]
    assert len(launched) == 1 and launched[0]["exe"] == "agy"
    a = ST.read_active(ST.state_dir(cfg))
    assert a["holder"] == "agy" and a["source_session"] == CODEX_ID


def test_codex_work_project_has_no_next_hop_and_hints_claude(env):
    cfg, p = env
    make_codex(p, p["work"])
    launched = []
    out = H.prompt_submit(codex_prompt("/handoff", p["work"]), config=cfg, cli="codex",
                          launch_fn=lambda plan: launched.append(plan) or "wt")
    assert out["decision"] == "block" and "沒有下一家" in out["reason"] and "/handoff claude" in out["reason"]
    assert launched == [] and ST.read_active(ST.state_dir(cfg)) is None


def test_codex_unknown_cwd_has_no_next_hop(env):
    cfg, p = env
    out = H.prompt_submit(codex_prompt("/ho", p["plain"], sid="not-there"), config=cfg, cli="codex",
                          launch_fn=lambda plan: "wt")
    assert out["decision"] == "block" and "找不到 session" in out["reason"]


def test_codex_handoff_claude_launches_claude_not_just_clear(env):
    cfg, p = env
    make_codex(p, p["plain"])
    launched = []
    out = H.prompt_submit(codex_prompt("/handoff claude", p["plain"]), config=cfg, cli="codex",
                          launch_fn=lambda plan: launched.append(plan) or "wt")
    assert out["decision"] == "block" and "已交給 claude" in out["reason"]
    assert launched and launched[0]["exe"] == "claude"


def test_codex_dollar_alias_works(env):
    cfg, p = env
    make_codex(p, p["plain"])
    out = H.prompt_submit(codex_prompt("$ho codex", p["plain"]), config=cfg, cli="codex",
                          launch_fn=lambda plan: "wt")
    assert out["decision"] == "block" and "不用交給自己" in out["reason"]


def test_codex_unknown_target_and_normal_prompt(env):
    cfg, p = env
    out = H.prompt_submit(codex_prompt("/handoff foo", p["plain"]), config=cfg, cli="codex")
    assert out["decision"] == "block" and "不認得" in out["reason"]
    # 一般提示：什麼都不輸出，即使 Claude 那邊有接手標記
    ST.write_active(ST.state_dir(cfg), "codex", "C", "cid", "cid", p["plain"])
    assert H.prompt_submit(codex_prompt("幫我寫程式", p["plain"]), config=cfg, cli="codex") is None


# ---- 腳本（子行程）----
def run_hook(stdin, *args, tmp_path):
    return subprocess.run([sys.executable, "-X", "utf8", "-I", HOOK, *args], input=stdin, capture_output=True,
                          env={**os.environ, "HANDOFF_STATE_DIR": str(tmp_path)}, timeout=20)


def test_codex_script_block_output_is_ascii_json(tmp_path):
    stdin = json.dumps(codex_prompt("/handoff foo", "D:\\x")).encode("utf-8")
    r = run_hook(stdin, "--cli", "codex", tmp_path=tmp_path)
    assert r.returncode == 0
    raw = r.stdout.decode("ascii")   # 全 ASCII，經 PowerShell 轉手也不會壞
    out = json.loads(raw)
    assert out["decision"] == "block" and "不認得" in out["reason"]


def test_codex_script_tolerates_bom_and_garbage(tmp_path):
    r = run_hook(b"\xef\xbb\xbf" + json.dumps(codex_prompt("/handoff foo", "D:\\x")).encode(), "--cli", "codex",
                 tmp_path=tmp_path)
    assert json.loads(r.stdout)["decision"] == "block"
    for junk in (b"", b"garbage", json.dumps(codex_prompt("你好", "D:\\x")).encode()):
        r = run_hook(junk, "--cli", "codex", tmp_path=tmp_path)
        assert r.returncode == 0 and r.stdout == b""


# ---- install：Codex 項目 ----
def test_codex_entries_have_both_events_and_no_pipe_pipe_on_windows():
    e = I.codex_entries(PY)
    assert set(e) == {"SessionStart", "UserPromptSubmit"}
    h = e["UserPromptSubmit"]["hooks"][0]
    assert "prompt_submit.py" in h["command"] and "--cli codex" in h["command"] and h["command"].endswith("|| true")
    for ev in e.values():
        cw = ev["hooks"][0]["commandWindows"]
        assert "||" not in cw and "Test-Path" in cw and cw.startswith("if (")


def test_codex_merge_keeps_others_upgrades_old_install_and_uninstalls():
    other = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                       "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}}
    old = I.merge_hooks(other, {"SessionStart": I.codex_entries(PY)["SessionStart"]})   # 舊版只裝 SessionStart
    new = I.merge_hooks(old, I.codex_entries(PY))
    ups = [h["command"] for g in new["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert "echo mine" in ups and sum("prompt_submit.py" in c for c in ups) == 1
    assert len(new["hooks"]["SessionStart"]) == 1                # 沒重複加
    assert new == I.merge_hooks(new, I.codex_entries(PY))        # 冪等
    assert I.strip_hooks(new) == other


def test_is_ours_recognizes_codex_command_and_pipe_variants():
    for ev in I.codex_entries(PY).values():
        assert I.is_ours(ev["hooks"][0]["command"])


@pytest.mark.skipif(not shutil.which("powershell"), reason="需要 Windows PowerShell")
def test_commandwindows_missing_script_does_not_block(tmp_path):
    """腳本或 python 不存在：PowerShell 5.1 照樣 exit 0、不輸出（不擋 Codex 一般提示）。"""
    cmd = I.ps_guarded(sys.executable, "no_such_script.py", "--cli codex")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], input=b"{}",
                       capture_output=True, timeout=60)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""
    cmd = I.ps_guarded(str(tmp_path / "nope" / "python.exe"), "prompt_submit.py", "--cli codex")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], input=b"{}",
                       capture_output=True, timeout=60)
    assert r.returncode == 0 and r.stdout == b""


@pytest.mark.skipif(not shutil.which("powershell"), reason="需要 Windows PowerShell")
def test_commandwindows_real_script_passes_stdin_and_stdout(tmp_path):
    cmd = I.ps_guarded(sys.executable, "prompt_submit.py", "--cli codex")
    env = {**os.environ, "HANDOFF_STATE_DIR": str(tmp_path)}
    stdin = json.dumps(codex_prompt("/handoff foo", "D:\\x")).encode("utf-8")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], input=stdin, env=env,
                       capture_output=True, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)["decision"] == "block"


# ---- install：agy skill ----
@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".gemini" / "config").mkdir(parents=True)
    (h / ".gemini" / "GEMINI.md").write_text("既有規則", encoding="utf-8")
    return h


def skill_path(home, name):
    return home / ".gemini" / "config" / "skills" / name / "SKILL.md"


def test_agy_skills_installed_with_names_and_path(home):
    I.apply_actions(I.plan_actions(home, PY))
    for name in ("handoff", "ho"):
        t = skill_path(home, name).read_text(encoding="utf-8")
        assert t.startswith(f"---\nname: {name}\n") and "{{" not in t
        assert "--from agy --launch tab" in t and "agent-handoff/handoff.py" in t
    assert (home / ".gemini" / "GEMINI.md").read_text(encoding="utf-8") == "既有規則"   # 不動既有檔
    assert not list((home / ".gemini").rglob("*.bak-*"))                                 # 全新檔沒東西可備份


def test_agy_skill_reinstall_is_noop_and_edited_one_is_backed_up(home):
    I.apply_actions(I.plan_actions(home, PY))
    assert not any(a["changed"] for a in I.plan_actions(home, PY) if "SKILL.md" in str(a["path"]))
    f = skill_path(home, "handoff")
    f.write_text("我改過的", encoding="utf-8")
    I.apply_actions(I.plan_actions(home, PY))
    assert "agent-handoff" in f.read_text(encoding="utf-8")
    assert len(list(f.parent.glob("SKILL.md.bak-*-handoff"))) == 1


def test_agy_uninstall_removes_only_unmodified(home):
    I.apply_actions(I.plan_actions(home, PY))
    skill_path(home, "ho").write_text("我改過的", encoding="utf-8")
    I.apply_actions(I.plan_actions(home, PY, uninstall=True))
    assert not skill_path(home, "handoff").exists()
    assert skill_path(home, "ho").read_text(encoding="utf-8") == "我改過的"
    assert (home / ".gemini" / "GEMINI.md").exists()


def test_dry_run_lists_codex_and_agy_and_writes_nothing(home, capsys):
    before = sorted(str(p) for p in home.rglob("*"))
    assert I.main(["--dry-run", "--home", str(home), "--python", PY]) == 0
    assert sorted(str(p) for p in home.rglob("*")) == before
    out = capsys.readouterr().out
    assert "hooks.json" in out and "agy skill handoff" in out and "agy skill ho" in out and "未寫入" in out
