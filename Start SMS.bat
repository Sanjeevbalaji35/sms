@echo off
rem Double-click to start SMS. The first start sets everything up, which takes a few minutes.
title SMS
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 start.py
  goto done
)
python -c "import sys" >nul 2>nul
if %errorlevel%==0 (
  python start.py
  goto done
)
echo.
echo   SMS needs Python, and it isn't installed yet. Installing it now...
echo.
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo   Done. Close this window and double-click "Start SMS" again.
:done
echo.
pause
