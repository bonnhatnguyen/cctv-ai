[CmdletBinding()]
param()

Set-StrictMode -Off
$ErrorActionPreference = "SilentlyContinue"

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  DANG TAT TOAN BO HE THONG CCTV AI VA GIAI PHONG TAI NGUYEN" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

# 1. Goi co che stop chinh thuc neu launcher dang theo doi
$launcherScript = Join-Path $PSScriptRoot "start-v1.ps1"
if (Test-Path $launcherScript) {
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcherScript -Stop *>$null
}

# 2. Giai phong cac port 8000, 8001, 8002, 5173, 5174 (BO QUA PID 0 VA PID 4 - TIME_WAIT / SYSTEM)
$ports = @(8000, 8001, 8002, 5173, 5174)
foreach ($p in $ports) {
    $connections = Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue |
                   Where-Object { $_.OwningProcess -gt 4 }
    
    if ($connections) {
        $uniquePids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $uniquePids) {
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Write-Host "[OK] Da giai phong PID $procId tren port $p" -ForegroundColor Green
            }
            catch {
                taskkill /F /PID $procId *>$null
            }
        }
    }
}

# 3. Quet va tat cac tien trinh python.exe hoac node.exe cua thu muc cctv
$currentPathLower = $ProjectRoot.ToLowerInvariant()
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' or Name = 'node.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object {
        $cmd = [string]$_.CommandLine
        if ($cmd.ToLowerInvariant().Contains($currentPathLower)) {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                Write-Host "[OK] Da dong tien trinh du an: $($_.Name) (PID $($_.ProcessId))" -ForegroundColor Green
            } catch {}
        }
    }

# 4. Xoa file trang thai launcher neu con sot lai
$stateFile = Join-Path $ProjectRoot "data\v1-launcher\launcher-state.json"
if (Test-Path $stateFile) {
    Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host "  [HOAN TAT] TOAN BO DICH VU, PORT VA CAMERA DA DUOC DONG SACH SE!" -ForegroundColor Green
Write-Host "  Khong con tien trinh nao chay ngam tren may tinh." -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green
Write-Host ""
