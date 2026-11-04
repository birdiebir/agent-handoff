# agent-handoff：三家 CLI 交接工具

在 Claude Code、Codex CLI、Antigravity CLI（agy）之間交接工作。備援順序 `claude -> codex -> agy`（`config\chain.example.json`，本機覆蓋用 `private\chain.local.json`），首要永遠是 Claude。使用說明見 `README.md`，開發歷程見 `docs\開發日誌.md`。

## 怎麼跑

```
python handoff.py [目標] [--list] [--session <id>] [--from <cli>] [--dry-run]
python -X utf8 -m pytest -q
```

流程：找出「這一段」（`--session`，或 `--list` 互動選，或目前資料夾最近一段、跨三家取最新）-> 選目標（參數，或照順序取下一家）-> 產生接力卡 -> 目標 adapter 取得啟動指令（Codex 可原生匯入）-> 最後一行 stdout 印 JSON `{exe,args,cwd}`，說明文字走 stderr。`--dry-run` 不匯入、不寫卡。

## 設定讀取順序

`config\chain.example.json`（預設，進 git）< `private\chain.local.json`（本機，不進 git；`HANDOFF_LOCAL_CONFIG` 可改位置）< 環境變數 `HANDOFF_CARD_DIR`、`HANDOFF_BATON_SCRIPT`。`--config <檔>` 取代本機檔。實作在 `handoff\config.py`。測試的 conftest 會把本機檔指到不存在的路徑，測試只吃範例預設。

## 安全規則

- `adapters.agy.allow_work_projects=false`（範例預設）時，以下不交給 agy：cwd 在 `work_paths` 底下；cwd 未知或只是退回 `cwd_now` 的值；接力卡內容（最後幾則使用者訊息與助理文字）含 `work_keywords` 任一關鍵字。
- 給接手方的提示固定包含：讀接力卡、遵守該 CLI 全域規則、共用檔不准改、不刪檔不 push、結果寫進接力卡「接手方紀錄」。設了 `baton_script` 時，交給 Codex 的提示改成可以寫共用檔（`baton_prompt`）；agy 永遠不拿令牌。
- 工作資料夾讀 jsonl 的 `cwd` 欄位，不用資料夾代號反推。
- 不要把個人路徑、關鍵字寫進 `config\chain.example.json` 或測試；放 `private\chain.local.json`。

## 檔案地圖

| 路徑 | 內容 |
|---|---|
| `handoff.py` | 入口（薄殼，呼叫 `handoff\cli.py`） |
| `handoff\cli.py` | 參數、來源選擇、目標選擇、串起全流程 |
| `handoff\transcripts.py` | 三種紀錄格式 -> 統一訊息清單 |
| `handoff\sessions.py` | 列最近對話、依 id／資料夾找 |
| `handoff\card.py` | 產生接力卡 Markdown |
| `handoff\adapters\` | claude／codex／agy 各一支，介面見 `base.py` 開頭 |
| `handoff\config.py` | 分層載入設定、工作路徑判斷 |
| `handoff\state.py`、`launch.py`、`hook_logic.py` | 登記表／接手標記／主線令牌、開新分頁、hook 邏輯 |
| `hooks\` | SessionStart、UserPromptSubmit hook 腳本 |
| `install\` | 安裝腳本、Claude 後備指令、agy skill、Codex hook 草稿 |
| `profile_snippet.ps1` | `handoff`／`ho` 函式 |
| `tests\` | pytest（假 jsonl fixture；Codex 匯入用 mock；令牌用假腳本） |
| `private\`、`private.example\` | 本機狀態與設定（不進 git）、範本 |

## Hook 運作

- **分頁登記**：SessionStart hook（`--cli claude|codex`）把 `WT_SESSION -> [{cli, session_id, cwd, transcript_path, time}]` 寫進 `private\state\registry.json`（每分頁 5 筆、整份 200 筆；非互動對話不登記）。`handoff` 沒給 `--session` 時先查這份，查不到才退回資料夾最近一段；24 小時內同資料夾有多段互動對話就不猜。hook 任何錯誤都 exit 0。
- **`/handoff [目標]`（或 `/ho`）**：UserPromptSubmit hook 比對提示，在本機規劃、用 `wt.exe -w 0 new-tab`（`-EncodedCommand`）開新分頁，回 `decision:block`，不經過模型。Codex 也認 `$handoff`、`$ho`。agy 沒有可攔截提示的 hook，走 skill。
- **Claude 優先**：交給非 Claude 時寫接手標記，依 Claude session 分開存（`private\state\active\<claude_session>.json`），原 Claude session 之後的提示會收到提醒（24 小時過期）；`/handoff claude` 只清自己那段。
- **安裝**：`install\install.py --dry-run|--apply|--uninstall`，改前備份，只加／只移除自己的 hook。

## 待實測

1. 額度用完時 `/handoff` 的 hook 是否照樣攔截。
2. 長對話（數 MB）匯進 Codex 的表現；agy `--conversation` 跨資料夾。
3. Codex hook 要在 Codex 裡用 `/hooks` 信任一次，install 的 hook 內容變了要重新信任。
