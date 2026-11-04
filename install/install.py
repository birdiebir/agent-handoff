"""安裝 / 解除 agent-handoff 的 hook 與 /handoff 指令。

    python install\\install.py --dry-run     只印會改哪些檔、改成什麼，不寫入
    python install\\install.py --apply       實際安裝（改前備份成 *.bak-<時間>-handoff）
    python install\\install.py --uninstall   只移除自己加的那幾筆
    --home <資料夾>                           測試用，預設使用者家目錄

改的東西：
  ~\\.claude\\settings.json   加入 SessionStart、UserPromptSubmit 各一筆（保留既有所有 hook）
  ~\\.claude\\commands\\handoff.md   後備斜線指令
  ~\\.codex\\hooks.json   加入 SessionStart、UserPromptSubmit 各一筆（不存在就新建，存在就合併）
  ~\\.gemini\\config\\skills\\handoff、ho\\SKILL.md   agy 的 /handoff、/ho（agy 沒有可攔截提示的 hook，只能走 skill）
自己加的 hook 用指令裡含 agent-handoff 的 hooks 腳本路徑辨認。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ("session_start.py", "prompt_submit.py")


def fwd(p):
    return str(p).replace("\\", "/")


TAIL = " || true"   # hook 腳本萬一失敗（exit 非 0）也不要擋住 CLI；以 bash 執行的指令才能用


def strip_tail(cmd):
    c = (cmd or "").rstrip()
    return c[:-len("|| true")].rstrip() if c.endswith("|| true") else c


def is_ours(cmd):
    """有沒有 ` || true` 結尾兩種寫法都認得（舊版安裝的、手動補過的）。"""
    c = strip_tail(cmd).replace("\\", "/").lower()
    return "agent-handoff/hooks/" in c and any(s in c for s in SCRIPTS)


def hook_cmd(python, script, extra="", slash=True, safe=True):
    path = ROOT / "hooks" / script
    if slash:
        p, py = fwd(path), fwd(python)
    else:
        p, py = str(path), str(python)
    return f'"{py}" -X utf8 -I "{p}"' + (f" {extra}" if extra else "") + (TAIL if safe else "")


def claude_entries(python):
    return {
        "SessionStart": {"hooks": [{"type": "command", "timeout": 5,
                                    "command": hook_cmd(python, "session_start.py", "--cli claude")}]},
        "UserPromptSubmit": {"hooks": [{"type": "command", "timeout": 25,
                                        "command": hook_cmd(python, "prompt_submit.py")}]},
    }


def ps_guarded(python, script, extra=""):
    """Codex 的 commandWindows（PowerShell 5.1，不能用 ||）：python 或腳本不在就什麼都不做，不擋 Codex。"""
    py, path = str(python), str(ROOT / "hooks" / script)
    call = f'& "{py}" -X utf8 -I "{path}"' + (f" {extra}" if extra else "")
    return f'if ((Test-Path -LiteralPath "{py}") -and (Test-Path -LiteralPath "{path}")) {{ {call} }}'


def codex_entries(python):
    return {
        "SessionStart": {"hooks": [{
            "type": "command", "timeout": 5, "statusMessage": "agent-handoff 登記分頁",
            "command": hook_cmd(python, "session_start.py", "--cli codex"),
            # commandWindows 由 PowerShell 執行，5.1 不支援 `||`（會整行語法錯誤）；改用 if (Test-Path ...) 保護
            "commandWindows": ps_guarded(python, "session_start.py", "--cli codex")}]},
        "UserPromptSubmit": {"hooks": [{
            "type": "command", "timeout": 25, "statusMessage": "agent-handoff",
            "command": hook_cmd(python, "prompt_submit.py", "--cli codex"),
            "commandWindows": ps_guarded(python, "prompt_submit.py", "--cli codex")}]},
    }


AGY_SKILL_NAMES = ("handoff", "ho")


def render_agy_skill(name):
    text = (HERE / "agy_skills" / "handoff" / "SKILL.md").read_text(encoding="utf-8").replace(
        "{{HANDOFF_DIR}}", fwd(ROOT))
    return text.replace("name: handoff" + chr(10), f"name: {name}" + chr(10), 1)


def group_is_ours(group):
    return any(is_ours(h.get("command")) for h in group.get("hooks", []))


def merge_hooks(cfg, entries):
    """把 entries 加進 cfg["hooks"]；已有我們的就先移除再加（冪等）；其他 hook 原封不動。"""
    cfg = json.loads(json.dumps(cfg))
    hooks = cfg.setdefault("hooks", {})
    for event, group in entries.items():
        lst = [g for g in hooks.get(event, []) if not group_is_ours(g)]
        lst.append(group)
        hooks[event] = lst
    return cfg


def strip_hooks(cfg):
    """移除我們加的 group；event 變空就移除該 event。"""
    cfg = json.loads(json.dumps(cfg))
    hooks = cfg.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not group_is_ours(g)]
        if not hooks[event]:
            del hooks[event]
    return cfg


def dump(cfg):
    return json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"


def render_command_file():
    return (HERE / "claude_commands" / "handoff.md").read_text(encoding="utf-8").replace(
        "{{HANDOFF_DIR}}", fwd(ROOT))


def load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def plan_actions(home, python, uninstall=False):
    """回傳動作清單 [{path, kind, new_text|None(刪除), old_exists, summary}]，不寫入。"""
    home = Path(home)
    acts = []
    settings = home / ".claude" / "settings.json"
    cmdfile = home / ".claude" / "commands" / "handoff.md"
    codex = home / ".codex" / "hooks.json"

    cur = load_json(settings)
    if cur is None and not uninstall:
        cur = {}
    if cur is not None:
        new = strip_hooks(cur) if uninstall else merge_hooks(cur, claude_entries(python))
        acts.append({"path": settings, "new_text": dump(new), "old_exists": settings.exists(),
                     "changed": dump(new) != dump(cur) or not settings.exists(),
                     "summary": "移除 SessionStart、UserPromptSubmit 的 agent-handoff 項目" if uninstall
                     else "加入 SessionStart、UserPromptSubmit 各一筆（保留既有 hook）"})

    want = render_command_file()
    if uninstall:
        same = cmdfile.exists() and cmdfile.read_text(encoding="utf-8") == want
        acts.append({"path": cmdfile, "new_text": None, "old_exists": cmdfile.exists(),
                     "changed": same, "summary": "刪除 handoff.md（內容與安裝版相同才刪）" if same
                     else "handoff.md 不存在或已被改過，不動"})
    else:
        acts.append({"path": cmdfile, "new_text": want, "old_exists": cmdfile.exists(),
                     "changed": not cmdfile.exists() or cmdfile.read_text(encoding="utf-8") != want,
                     "summary": "新增 /handoff 後備指令"})

    cur = load_json(codex)
    if cur is None and not uninstall:
        cur = {}
    if cur is not None:
        new = strip_hooks(cur) if uninstall else merge_hooks(cur, codex_entries(python))
        acts.append({"path": codex, "new_text": dump(new), "old_exists": codex.exists(),
                     "changed": dump(new) != dump(cur) or not codex.exists(),
                     "summary": "移除 SessionStart、UserPromptSubmit 的 agent-handoff 項目" if uninstall
                     else "加入 SessionStart、UserPromptSubmit 各一筆（首次執行要在 Codex 的 /hooks 按信任）"})

    for name in AGY_SKILL_NAMES:
        f = home / ".gemini" / "config" / "skills" / name / "SKILL.md"
        want = render_agy_skill(name)
        same = f.exists() and f.read_text(encoding="utf-8") == want
        if uninstall:
            acts.append({"path": f, "new_text": None, "old_exists": f.exists(), "changed": same,
                         "summary": f"刪除 agy skill {name}（內容與安裝版相同才刪）" if same
                         else f"agy skill {name} 不存在或已被改過，不動"})
        else:
            acts.append({"path": f, "new_text": want, "old_exists": f.exists(), "changed": not same,
                         "summary": f"新增 agy skill {name}（agy 裡打 /{name}，由模型執行 handoff.py）"})
    return acts


def apply_actions(acts, now=None):
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    done = []
    for a in acts:
        if not a["changed"]:
            continue
        p = a["path"]
        if a["old_exists"]:
            bak = p.with_name(f"{p.name}.bak-{stamp}-handoff")
            bak.write_bytes(p.read_bytes())
            done.append(f"備份 {bak}")
        if a["new_text"] is None:
            p.unlink()
            done.append(f"刪除 {p}")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(a["new_text"], encoding="utf-8")
            done.append(f"寫入 {p}")
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    ap.add_argument("--home", default=str(Path.home()))
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    uninstall = args.uninstall
    acts = plan_actions(args.home, args.python, uninstall=uninstall)
    for a in acts:
        print(f"{'[會改]' if a['changed'] else '[不變]'} {a['path']}：{a['summary']}")
    if args.dry_run:
        print("\n--- 將加入的 hook ---")
        print(json.dumps({"claude": claude_entries(args.python), "codex": codex_entries(args.python)},
                         ensure_ascii=False, indent=2))
        print("\n(dry-run，未寫入任何檔案)")
        return 0
    for line in apply_actions(acts):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
