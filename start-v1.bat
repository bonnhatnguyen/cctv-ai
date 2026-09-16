@echo off
title CCTV AI - Giam Sat Ro Tien [KHONG DONG CUA SO NAY]
setlocal
set "ROOT=%~dp0"

echo ======================================================================
echo   DANG KHOI DONG HE THONG CCTV AI (BACKEND + FRONTEND + AI CAMERA)
echo ======================================================================

call powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\start-v1.ps1" %*

if ERRORLEVEL 1 (
    echo.
    echo ======================================================================
    echo   [LOI] KHONG THE KHOI DONG HE THONG CCTV AI!
    echo   Kiem tra lai .venv, port 8000/5173, hoac chay stop.bat truoc.
    echo ======================================================================
    echo.
    echo Nhan phim bat ky de dong cua so nay...
    pause >nul
    goto :EOF
)

echo.
echo ======================================================================
echo   CCTV AI - HE THONG DA SAN SANG!
echo.
echo   * Web UI:  http://127.0.0.1:5173
echo   * API:     http://127.0.0.1:8000
echo   * Tat:     Chay file stop.bat
echo ======================================================================
echo.
echo [CUA SO NAY GIU MO DE XEM LOG - SE KHONG TU DONG TAT]
echo Nhan Ctrl+C de ngung xem log (cua so van giu mo).
echo ---------------------------------------------------------------
echo.

call powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\tail-log.ps1"

echo.
echo ---------------------------------------------------------------
echo LOG DA DUNG. He thong van dang chay ngam binh thuong.
echo De tat hoan toan: chay file stop.bat
echo ---------------------------------------------------------------
echo.
echo Nhan phim bat ky de dong cua so nay...
pause >nul
