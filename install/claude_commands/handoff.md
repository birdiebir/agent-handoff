---
description: 把目前這段對話交接給下一家 CLI（codex／agy），或指定目標：/handoff codex
argument-hint: "[claude|codex|agy]"
---

這是 agent-handoff 的後備指令。正常情況下 UserPromptSubmit hook 會先攔截 `/handoff`、在本機直接處理，不會走到這裡；
你看到這份指令，代表 hook 沒裝好或沒攔到，請照下面做。

用 Bash 執行（原樣，不要改動參數；目標是 `$ARGUMENTS`，沒給就留空）：

```
python "{{HANDOFF_DIR}}/handoff.py" $ARGUMENTS --from claude --session "$CLAUDE_CODE_SESSION_ID" --launch tab
```

- 它會產生接力卡、做匯入，並在同一個 Windows Terminal 視窗開新分頁啟動目標 CLI。
- 如果 `$CLAUDE_CODE_SESSION_ID` 是空的，改成不帶 `--session`（它會用這個分頁的登記紀錄或資料夾最近一段）。
- 執行完只回報一行：交給誰、接力卡路徑；然後提醒使用者在這個視窗打 `/exit`（免得額度重置後 Claude 自動繼續，跟接手方撞檔）。
- 不要自己再做其他事、不要改檔、不要 git push。
