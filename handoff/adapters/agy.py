"""Antigravity CLI（agy）adapter：沒有原生匯入，靠接力卡。

adapters.agy.allow_work_projects=false（預設）時，工作專案一律拒絕：
- cwd 在 work_paths 底下；
- cwd 未知，或只是退回「執行 handoff 時的資料夾」（不是來源對話真正的工作資料夾）；
- 接力卡內容（最後幾則使用者訊息與助理文字）含 work_keywords 任一關鍵字。
"""
from ..config import HandoffError, expand, is_work_path, work_keyword_hit
from .base import handoff_prompt

NAME = "agy"


def check_allowed(cwd, config, text=None, cwd_fallback=False):
    """工作專案（或 cwd 未知／只是退回值、內容含工作關鍵字）不能交給 agy；不允許時丟 HandoffError。"""
    if config["adapters"]["agy"].get("allow_work_projects", False):
        return
    w = is_work_path(cwd, config)
    if w is None:
        raise HandoffError("agy 設定為不接工作專案，而來源對話的工作資料夾未知，無法確認不是工作專案，已拒絕。")
    if cwd_fallback:
        raise HandoffError("agy 設定為不接工作專案，而來源紀錄沒有工作資料夾（目前的資料夾只是退回值），"
                           "無法確認不是工作專案，已拒絕。")
    if w:
        raise HandoffError(
            f"agy 設定為不接工作專案，不能交給它（{cwd} 在工作路徑底下）。"
            "請改交 claude 或 codex；若確定要放行，在 private/chain.local.json 設 adapters.agy.allow_work_projects。")
    hit = work_keyword_hit(text, config)
    if hit:
        raise HandoffError(
            f"agy 設定為不接工作專案，而接力卡內容含工作關鍵字「{hit}」，不能交給它。"
            "請改交 claude 或 codex；關鍵字清單是設定檔的 work_keywords。")


def build_launch(card, source_session, cwd, config, dry_run=False, hook=False):
    check_allowed(cwd, config, text=getattr(card, "text", None))
    ad = config["adapters"]["agy"]
    prompt = handoff_prompt(card, NAME, config)
    return {"exe": ad["exe"], "args": ["-i", prompt], "cwd": cwd,
            "notes": ["agy 沒有原生匯入，只靠接力卡"]}


def transcript_location(config):
    return "; ".join(expand(d) + r"\<id>\.system_generated\logs\transcript.jsonl"
                     for d in config["adapters"]["agy"]["brain_dirs"])


def has_native_import(source_cli, config):
    return False
