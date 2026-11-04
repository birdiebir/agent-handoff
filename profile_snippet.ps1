# === agent-handoff（貼進 PowerShell profile；把 <path-to-agent-handoff> 換成實際資料夾，或設環境變數 HANDOFF_DIR）===
# 用法：handoff [claude|codex|agy] [--list] [--session <id>] [--from <cli>] [--dry-run]
# 不帶目標：照備援順序（claude -> codex -> agy）交給下一家；handoff claude 回主力。
# 刪掉這一段就完全還原。
$HandoffDir = if ($env:HANDOFF_DIR) { $env:HANDOFF_DIR } else { '<path-to-agent-handoff>' }

function handoff {
    $env:PYTHONIOENCODING = 'utf-8'
    $prevEnc = [Console]::OutputEncoding
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    try {
        # 說明文字走 stderr 直接顯示；stdout 只有最後一行 JSON
        $out = python "$HandoffDir\handoff.py" @args
        $code = $LASTEXITCODE
    } finally {
        [Console]::OutputEncoding = $prevEnc
    }
    if ($code -ne 0 -or -not $out) { return }
    if ($args -contains '--dry-run') { Write-Host '[dry-run] 不執行。' -ForegroundColor Yellow; return }
    $json = @($out)[-1] | ConvertFrom-Json
    Push-Location $json.cwd
    try {
        # 舊 Claude 視窗開著時，額度重置會自動繼續；提醒先 /exit
        Write-Host "啟動：$($json.exe)（在 $($json.cwd)）。舊視窗若還開著，記得打 /exit。" -ForegroundColor Cyan
        & $json.exe @($json.args)
    } finally {
        Pop-Location
    }
}

Set-Alias ho handoff
# === agent-handoff 結束 ===
