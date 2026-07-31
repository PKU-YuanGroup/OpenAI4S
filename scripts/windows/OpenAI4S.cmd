@echo off
rem OpenAI4S -- double-clickable entry point.
rem
rem All of the work is in openai4s.ps1, next to this file. This wrapper exists
rem because a .ps1 is not double-clickable: Explorer opens it in an editor, and
rem the default execution policy blocks it even from a prompt. -ExecutionPolicy
rem Bypass applies to this one process only and changes nothing machine-wide.
setlocal
set "OPENAI4S_LAUNCHER=%~dp0openai4s.ps1"
if not exist "%OPENAI4S_LAUNCHER%" (
  echo.
  echo OpenAI4S: openai4s.ps1 is missing next to this file.
  echo Re-download the release archive and unzip the whole thing.
  echo.
  pause
  exit /b 1
)
powershell.exe -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%OPENAI4S_LAUNCHER%" %*
exit /b %ERRORLEVEL%
