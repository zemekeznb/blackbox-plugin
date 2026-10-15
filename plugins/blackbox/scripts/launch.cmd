@echo off
setlocal DisableDelayedExpansion
set "BLACKBOX_RUNTIME_SCRIPT=%~dp0blackbox_runtime.ps1"
set "BLACKBOX_RUNTIME_MODE=hook"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "try { & ([ScriptBlock]::Create([IO.File]::ReadAllText($env:BLACKBOX_RUNTIME_SCRIPT))) } catch { [Console]::Error.WriteLine('Blackbox Plugin: runtime_launcher_unreadable; host runtime unavailable.'); exit 1 }"
if errorlevel 1 >&2 echo Blackbox Plugin: LauncherRuntimeFailed; recording was not started.
rem Recording must not block the user's agent turn. MCP returns real failures.
exit /b 0
