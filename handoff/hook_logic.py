"""Claude hook 的邏輯（hook 腳本只是薄殼，方便測試）。

- session_start(data, cli, environ, sd)：登記「WT_SESSION -> 對話」。
- prompt_submit(data, ...)：攔截 /handoff、/ho；其他提示只在「已交出去」時加提醒。
回傳值：None＝什麼都不輸出；dict＝要印成 JSON 的 hook 輸出。
"""
import io
import re
from datetime import datetime

from . import state as ST

NO_SESSION = "<no-session>"   # 沒有 session_id 時用的查詢鍵（不會對到任何標記）
JUNK = r"\s﻿​"
PROMPT_RE = re.compile(rf"^[{JUNK}]*/(?:handoff|ho)(?:[{JUNK}]+([^{JUNK}]+))?[{JUNK}]*$", re.I)
# Codex 的 skill 用 $名稱 叫出來；TUI 可能不放行未知的 /指令，所以 Codex 另外認 $handoff／$ho
PROMPT_RE_DOLLAR = re.compile(rf"^[{JUNK}]*[/$](?:handoff|ho)(?:[{JUNK}]+([^{JUNK}]+))?[{JUNK}]*$", re.I)


def session_start(data, cli="claude", environ=None, sd=None, now=None):
    import os
    environ = os.environ if environ is None else environ
    wt = environ.get("WT_SESSION")
    if not wt:
        return False
    if environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":   # `claude -p` 不是使用者的互動對話，不登記
        return False
    return ST.register(sd or ST.state_dir(), wt, cli, data.get("session_id"), data.get("cwd"),
                       data.get("transcript_path"), now)


def parse_prompt(prompt, allow_dollar=False):
    """回傳 (是不是 /handoff, 目標或 None)。allow_dollar：Codex 也認 $handoff。"""
    m = (PROMPT_RE_DOLLAR if allow_dollar else PROMPT_RE).match(prompt or "")
    if not m:
        return False, None
    return True, (m.group(1) or "").lower() or None


def reminder(data, sd, now=None):
    """已交給別家、而且這個 session 就是被交出去的那段：提醒（不擋）。"""
    a = ST.read_active(sd, now, claude_session=data.get("session_id") or NO_SESSION)
    if not a or a.get("holder") == "claude":
        return None
    if data.get("session_id") not in (a.get("source_session"), a.get("claude_session")):
        return None
    msg = (f"【handoff 提醒】{a['holder']} 從 {a['since'].replace('T', ' ')} 接手中（這段對話已交給它）。"
           f"先讀接力卡 {a.get('card')} 的「接手方紀錄」，並用 git status／git diff 驗收它改了什麼，再動手；"
           "確認接回請打 /handoff claude，或先結束對方視窗。")
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}


def block(reason):
    return {"decision": "block", "reason": reason}


def prompt_submit(data, config=None, now=None, launch_fn=None, cli="claude"):
    from .config import HandoffError, load_config
    config = config or load_config()
    sd = ST.state_dir(config)
    is_ho, target = parse_prompt(data.get("prompt"), allow_dollar=cli == "codex")
    if not is_ho:
        return reminder(data, sd, now) if cli == "claude" else None   # 提醒只給被交出去的 Claude 對話
    sid, cwd = data.get("session_id"), data.get("cwd")
    if target == "claude" and cli == "claude":
        # 在原本的 Claude 視窗確認接回：只清標記，不開新視窗
        ST.clear_active(sd, sid or NO_SESSION)   # 只清自己這段的標記
        ST.set_baton(config, "claude", "在 Claude 視窗確認接回")
        return block("已確認接回 Claude，接手標記已清除、主線令牌收回 Claude。請先用 git status／git diff 驗收別家改了什麼，再繼續。")
    if target and target not in config["chain"]:
        return block(f"handoff 沒有執行：不認得的目標 {target}（可用：{', '.join(config['chain'])}）")
    from . import cli as cli_mod
    from . import transcripts as T
    argv = ([target] if target else []) + ["--from", cli] + (["--session", sid] if sid else [])
    err = io.StringIO()
    try:
        plan = cli_mod.run(argv + ["--launch", "tab"], config=config, now=now, err=err, cwd_now=cwd,
                       launch_fn=launch_fn)
    except (HandoffError, T.UnsupportedTranscript) as e:
        hint = "（在這裡請打 /handoff claude）" if cli != "claude" and "沒有下一家" in str(e) else ""
        return block(f"handoff 沒有執行：{e}{hint}")
    except Exception as e:  # 使用者明確打了 /handoff，失敗要讓他看到
        return block(f"handoff 失敗：{type(e).__name__}: {e}")
    tail = ("請在這個視窗打 /exit，免得額度重置後 Claude 自動繼續跟它撞檔。" if cli == "claude"
            else f"請在這個視窗結束 {cli}（/exit），免得兩邊同時動同一批檔案。")
    return block(f"已交給 {plan['target']}（新分頁）。接力卡：{plan['card']}。" + tail)
