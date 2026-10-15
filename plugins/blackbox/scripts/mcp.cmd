@echo off
setlocal DisableDelayedExpansion
set "BLACKBOX_RUNTIME_SCRIPT=%~dp0blackbox_runtime.ps1"
set "BLACKBOX_RUNTIME_MODE=mcp"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "try { & ([ScriptBlock]::Create([IO.File]::ReadAllText($env:BLACKBOX_RUNTIME_SCRIPT))) } catch { [Console]::Error.WriteLine('Blackbox Plugin: runtime_launcher_unreadable; host runtime unavailable.'); exit 1 }"
exit /b %errorlevel%
