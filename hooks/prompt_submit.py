"""UserPromptSubmit hook：在 Claude／Codex 裡打 /handoff、/ho 就在本機交接並開新分頁，不經過模型。

用法：python -X utf8 -I prompt_submit.py [--cli claude|codex]（預設 claude）。
Codex 的 stdin 欄位同 Claude（session_id／cwd／prompt），block 格式也相同（decision:block＋reason）。

其他提示：只在「這段對話已交給別家」時輸出 additionalContext 提醒，否則什麼都不印。
失敗時不擋 Claude（非 /handoff 的提示一律 exit 0）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    try:
        for s in (sys.stdout, sys.stderr):
            s.reconfigure(encoding="utf-8")
        cli = "codex" if "codex" in sys.argv[1:] else "claude"
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace").strip() or "{}")   # 容忍 BOM（PowerShell 轉手 stdin 會加）
        from handoff.hook_logic import prompt_submit
        out = prompt_submit(data, cli=cli)
        if out:
            # ensure_ascii：Codex 在 Windows 經 PowerShell 轉手 stdout，非 ASCII 會被主控台編碼弄壞
            sys.stdout.write(json.dumps(out, ensure_ascii=True))
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    main()
    sys.exit(0)
