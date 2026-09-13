@echo off
title CCTV AI - DANG TAT HE THONG...
setlocal
set "ROOT=%~dp0"

if exist "%ROOT%scripts\stop-clean.ps1" (
    call powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\stop-clean.ps1"
) else (
    echo [CCTV AI] Dang tat tien trinh...
    call powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ports = @(8000, 8001, 8002, 5173, 5174); foreach ($p in $ports) { $pids = Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 4 } | Select-Object -ExpandProperty OwningProcess -Unique; if ($pids) { foreach ($id in $pids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue } } }"
)

echo Nhan phim bat ky de dong cua so nay...
pause >nul
