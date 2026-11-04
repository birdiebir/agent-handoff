---
name: handoff
description: >-
  把目前這段 agy 對話交接給下一家 CLI（備援順序 claude -> codex -> agy，agy 之後沒有下一家，預設請改交 claude），
  或指定目標。使用者輸入 /handoff、/handoff claude、/handoff codex、/ho，或說「交接」「接力給 Claude／Codex」時使用。
---

# handoff（agent-handoff，agy 版）

agy 沒有可以攔截使用者提示的 hook，所以這個指令要靠你（模型）去執行一行指令。額度或模型用完時這個 skill 無法使用，
那種情況請使用者離開 agy 後在 PowerShell 打 `handoff claude`。

## 步驟

1. 目標是使用者在 `/handoff` 後面打的字（`claude`、`codex`、`agy` 其中之一）。沒打就用 `claude`（agy 是備援順序最後一家，沒有下一家）。
   使用者打的不是這三個字，就回覆「不認得的目標」，不要執行。
2. 用 shell 執行（原樣，只換掉目標；路徑可能含空白或非英文字元，要整串照抄）：

```
python "{{HANDOFF_DIR}}/handoff.py" <目標> --from agy --launch tab
```

3. 它會產生接力卡，並在同一個 Windows Terminal 視窗開新分頁啟動目標 CLI。成功時最後幾行 stderr 會寫「已在…啟動 <目標>」。
4. 只回報一行：交給誰、接力卡路徑（指令輸出裡的「接力卡：…」）；然後提醒使用者在這個視窗打 `/exit`，免得兩邊同時改同一批檔案。
5. 指令失敗（exit 非 0）就把錯誤訊息原文貼給使用者，不要重試、不要自己改參數。

## 規則

- 不要做其他事：不改檔、不刪檔、不 `git push`、不要自己讀或改接力卡。
- 工作專案（cwd 在設定檔 `work_paths` 底下，或內容含 `work_keywords`）不會交給 agy；若指令回報被拒絕，照實轉述，請使用者改交 Claude 或 Codex。
