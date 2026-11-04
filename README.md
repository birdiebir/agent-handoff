# agent-handoff

當 Claude Code、Codex CLI、Antigravity CLI（agy）其中一家的額度用完時，自動從對話紀錄產生「接力卡」，並把正在做的 session 交給下一家接手。備援順序預設 `claude -> codex -> agy`，首要永遠是 Claude。接力卡由程式產生，不用人寫。

提供兩種入口：

- PowerShell 指令 `handoff`（別名 `ho`）。
- 在 Claude／Codex 裡直接打 `/handoff`（hook 攔截，不經過模型）；agy 走 skill。

## 需求

- Windows 10／11、Windows Terminal（`wt.exe`）、PowerShell 5.1 以上。**Mac／Linux 未支援。**
- Python 3.10 以上（只用標準函式庫；跑測試要 `pytest`）。
- 至少裝了其中兩家 CLI（`claude`、`codex`、`agy`）。
- 資料夾請維持名稱 `agent-handoff`（安裝腳本靠路徑裡的 `agent-handoff/hooks/` 辨認自己加的 hook）。

## 安裝

```
git clone <this-repo> agent-handoff
cd agent-handoff
python -X utf8 -m pytest -q                 # 確認測試通過
```

1. 把 `profile_snippet.ps1` 的內容貼進 PowerShell profile（`notepad $PROFILE`），把 `<path-to-agent-handoff>` 換成實際資料夾，或設環境變數 `HANDOFF_DIR`。
2. 要在 Claude／Codex／agy 裡打 `/handoff`：

```
python install\install.py --dry-run     # 先看會改哪些檔
python install\install.py --apply       # 安裝（改前備份成 *.bak-<時間>-handoff）
python install\install.py --uninstall   # 只移除自己加的
```

會改 `~\.claude\settings.json`、`~\.claude\commands\handoff.md`、`~\.codex\hooks.json`、`~\.gemini\config\skills\handoff` 與 `ho`。Codex 的 hook 裝好後要在 Codex 裡用 `/hooks` 信任一次。`install\codex_hooks.json` 只是草稿，正式內容由 install 產生。

## 用法

```
handoff                  # 目前資料夾最近一段 -> 備援順序的下一家
handoff codex            # 指定目標（claude 回主力）
handoff --list           # 列最近 24 小時的對話，打數字選
handoff --session <id>   # 指定要交出的那一段
handoff --from claude    # 限定來源 CLI
handoff --dry-run        # 只印計畫：不匯入、不寫卡
```

沒有 profile 時可直接 `python handoff.py ...`；最後一行 stdout 是 JSON `{"exe":…,"args":[…],"cwd":…}`，說明文字走 stderr。`--launch tab` 會自己在同一個 Windows Terminal 視窗開新分頁。

## 設定

設定讀取順序（後者蓋前者）：

1. `config/chain.example.json`：中性預設（進 git，請不要放個人資料）。
2. `private/chain.local.json`：本機設定，不進 git。可只寫要改的欄位，dict 遞迴合併、list 整個取代。檔案位置可用環境變數 `HANDOFF_LOCAL_CONFIG` 改。
3. 環境變數：`HANDOFF_CARD_DIR`（接力卡資料夾）、`HANDOFF_BATON_SCRIPT`（主線令牌腳本）。

設定方式：

```
mkdir private
copy config\chain.example.json private\chain.local.json
# 刪掉不想改的欄位，只留要覆蓋的，例如：
# { "card_dir": "D:/notes/handoff-cards", "work_paths": ["D:/work"] }
```

主要欄位：

| 欄位 | 說明 |
|---|---|
| `chain` | 備援順序 |
| `card_dir` | 接力卡存放資料夾（預設 `~/handoff-cards`） |
| `work_paths`、`work_keywords` | 交給 agy 的過濾：cwd 在這些路徑底下、或接力卡內容含這些關鍵字時，不交給 agy |
| `adapters.agy.allow_work_projects` | 預設 `false`；設 `true` 就不做上述過濾 |
| `baton_script` | 選用。多個 CLI 共用「誰是主線」令牌時，交給 Codex／交回 Claude 會呼叫 `python <腳本> take <claude\|codex> --by handoff --reason <原因>`；留空就不轉移 |
| `baton_prompt` | 選用。令牌交給 Codex 時的提示，`{forbidden}` 會換成 `shared_files_forbidden` |
| `shared_files_forbidden` | 接手方預設不准改的共用檔名單 |
| `rules_labels` | 選用。覆蓋各 CLI「遵守全域規則」那句文字 |
| `adapters.*` | 各 CLI 的執行檔、對話紀錄位置、Codex 匯入逾時、Codex profile |

接力卡檔名 `YYYY-MM-DD_HHmm_<來源>.md`。狀態（分頁登記表、接手標記）寫在 `private/state/`，範本見 `private.example/`。

## 限制

- 只支援 Windows；用 `wt.exe` 開分頁。
- agy 沒有可攔截使用者提示的 hook，只能走 skill，要經過模型：額度用完時改在 PowerShell 打 `handoff claude`。
- Claude -> Codex 用 Codex 原生匯入（app-server，只匯入對話），失敗退回只給接力卡。
- 在 Claude 額度用完時 `/handoff` 的 hook 是否照樣攔截，尚未長期驗證。
- 接力卡只擷取最後幾輪對話文字（工具輸出省略），接手方請用 `git status`／`git diff` 驗證實際狀態。
- agy 預設不接工作專案（`adapters.agy.allow_work_projects=false`）；依自己的情境調整 `work_paths`／`work_keywords`，或打開這個開關。

細節見 `CLAUDE.md`、`docs/開發日誌.md`。

## 授權

MIT，見 `LICENSE`。
