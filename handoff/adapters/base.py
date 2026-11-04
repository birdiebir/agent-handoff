"""各家 adapter 共用：接手提示、介面說明。

每支 adapter 模組都提供：
    NAME                                     CLI 名稱
    build_launch(card, source_session, cwd, config, dry_run=False) -> {exe, args, cwd, notes}
    transcript_location(config) -> str       紀錄位置（給人看）
    has_native_import(source_cli, config) -> bool   是否能原生匯入來源 CLI 的對話
"""

# 可被設定檔 rules_labels 覆蓋
RULES_LABEL = {
    "claude": r"你的全域規則（~\.claude\CLAUDE.md 與專案 CLAUDE.md）",
    "codex": r"你的全域規則 ~\.codex\AGENTS.md",
    "agy": r"你的全域規則 ~\.gemini\GEMINI.md（若存在）與專案裡的 AGENTS.md／CLAUDE.md",
}


def handoff_prompt(card, target, config, claude_resume=False):
    forbidden = "、".join(config.get("shared_files_forbidden", []))
    lines = [
        f"你是從 {card.source_cli} 交接過來的接手方（{target}）。先讀接力卡 {card.path}，了解來源對話最後做到哪裡。",
    ]
    label = (config.get("rules_labels") or {}).get(target) or RULES_LABEL.get(target, "該 CLI 的全域規則")
    lines.append(f"遵守{label}。")
    if target == "codex" and config.get("baton_script"):
        # 交給 Codex 時主線令牌一起交出去，Codex 是主線、可以寫共用檔
        tpl = config.get("baton_prompt") or "主線令牌已交給你：可以寫共用檔（{forbidden}）、commit、push；寫之前先確認令牌還在你手上。"
        lines += [
            tpl.replace("{forbidden}", forbidden),
            "不刪檔、不對外送出任何東西（訊息、郵件、發文、上傳）。",
        ]
    else:
        lines += [
            f"共用檔（{forbidden}）不准改，需要改的內容寫在接力卡「接手方紀錄」段。",
            "不刪檔、不 git push、不對外送出任何東西。",
        ]
    if claude_resume:
        lines.append("你是回到主力：先用 git status／git diff 驗收別家在交接期間改了什麼，確認沒問題再繼續原本的工作。")
    else:
        lines.append("先用 git status／git diff 確認目前檔案狀態，再照對話最後的進度繼續。")
    lines.append("做完或要停時，把結果（做了什麼、改了哪些檔、跑了哪些驗證、卡在哪）寫進接力卡的「接手方紀錄」段。")
    return "\n".join(lines)
