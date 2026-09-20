@echo off
setlocal
if not "%~1"=="" (
  echo daily.cmd accepts no options. Use the INPUT_PREPARATION menu.
  exit /b 2
)
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -B "%~dp0scripts\daily_operator.py"
) else (
  python -B "%~dp0scripts\daily_operator.py"
)
exit /b %errorlevel%
