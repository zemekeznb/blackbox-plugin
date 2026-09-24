@echo off
setlocal DisableDelayedExpansion
set "notes_state=%LOCALAPPDATA%\TAS Notes"
if defined TAS_NOTES_STATE_DIR set "notes_state=%TAS_NOTES_STATE_DIR%"
if not exist "%notes_state%\runtime.cmd" goto missing
call "%notes_state%\runtime.cmd" "%~dp0tas_notes.py" hook
if errorlevel 1 >"%notes_state%\last-error.json" echo {"code":"LauncherRuntimeFailed"}
exit /b 0
:missing
if not exist "%notes_state%" mkdir "%notes_state%" 2>nul
>"%notes_state%\last-error.json" echo {"code":"LauncherNotConfigured"}
>&2 echo TAS Notes: configure the Python runtime first.
exit /b 0
