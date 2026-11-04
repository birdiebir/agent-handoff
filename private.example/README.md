# private.example

`private\` 是本機狀態（不進 git）：
- `state\registry.json`：`WT_SESSION -> [{cli, session_id, cwd, transcript_path, time}]`，SessionStart hook 自動寫，每分頁最近 5 筆、整份最多 200 筆。
- `state\active.json`：交給非 Claude 時寫入的接手標記，超過 24 小時視為過期。

這些檔案都是自動產生，不用手動建立。範本見 `state\`。

另外，`guard/scan.py`（commit／push 前的個資掃描）讀 `private\blocklist.txt`，範本見 `blocklist.txt`；沒有這份檔，掃描會直接擋下。
