# tail-log.ps1 — Live-tail backend log, keep window open
$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
$LauncherDir = Join-Path $Root "data\v1-launcher"

# Find latest backend log file (retry up to 8 seconds)
$logFile = $null
for ($i = 0; $i -lt 16; $i++) {
    $logFile = Get-ChildItem -Path (Join-Path $LauncherDir "backend-*.log") -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($logFile) { break }
    Start-Sleep -Milliseconds 500
}

if ($logFile) {
    Write-Host "[LOG] Dang theo doi: $($logFile.Name)" -ForegroundColor Green
    Write-Host "----------------------------------------------------------------------" -ForegroundColor DarkGray
    try {
        Get-Content -Path $logFile.FullName -Wait -Tail 30 -ErrorAction Stop
    }
    catch {
        Write-Host "`n[LOG] Da ngung theo doi log." -ForegroundColor Yellow
    }
}
else {
    Write-Host "[LOG] Khong tim thay file log trong: $LauncherDir" -ForegroundColor Yellow
    Write-Host "He thong van dang chay. Mo http://127.0.0.1:5173 de su dung." -ForegroundColor Cyan
}
# Script ends here — control returns to the .bat file which has its own `pause >nul`
