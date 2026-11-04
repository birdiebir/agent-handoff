"""SessionStart hook：登記「Windows Terminal 分頁 -> 對話」。Claude 與 Codex 共用。

用法：python -X utf8 -I session_start.py [--cli claude|codex]
任何錯誤都安靜結束、exit 0，絕不能擋住 CLI 開機。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    try:
        cli = "codex" if "codex" in sys.argv[1:] else "claude"
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace").strip() or "{}")   # 容忍 BOM（PowerShell 轉手 stdin 會加）
        from handoff.hook_logic import session_start
        session_start(data, cli)
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    main()
    sys.exit(0)
