@echo off
setlocal
if not "%~1"=="" (
  echo daily.cmd accepts no options. Use the INPUT_PREPARATION menu.
  exit /b 2
)
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -I -B "%~dp0scripts\daily_operator.py"
) else (
  echo Installed .venv wheel required. Ask the maintainer to install the reviewed release.
  exit /b 2
)
exit /b %errorlevel%
