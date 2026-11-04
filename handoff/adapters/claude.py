"""Claude Code adapter：接回原本的 Claude 對話（沒有原生匯入）。"""
from ..config import expand
from .base import handoff_prompt

NAME = "claude"


def build_launch(card, source_session, cwd, config, dry_run=False, hook=False):
    ad = config["adapters"]["claude"]
    prompt = handoff_prompt(card, NAME, config, claude_resume=True)
    notes = []
    if card.claude_session:
        args = ["--resume", card.claude_session, prompt]
        notes.append(f"接回原本的 Claude 對話 {card.claude_session}")
    else:
        args = ["--continue", prompt]
        notes.append("接力鏈裡找不到 Claude session id，改用 --continue（同資料夾多視窗時可能接錯）")
    # cwd 一定切回原工作資料夾（resume 時新訊息的 cwd 會變成執行指令的資料夾）
    return {"exe": ad["exe"], "args": args, "cwd": cwd, "notes": notes}


def transcript_location(config):
    return expand(config["adapters"]["claude"]["projects_dir"]) + r"\<資料夾代號>\<session-id>.jsonl"


def has_native_import(source_cli, config):
    return False
