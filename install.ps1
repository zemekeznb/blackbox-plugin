[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$StateDir,
    [switch]$CheckOnly,
    [switch]$SkipShortcuts,
    [switch]$InstallShortcuts
)
$ErrorActionPreference = 'Stop'

try {
    if ($env:OS -ne 'Windows_NT') { throw 'This Alpha installer supports Windows only.' }
    if ($SkipShortcuts -and $InstallShortcuts) { throw 'Choose either -SkipShortcuts or -InstallShortcuts.' }
    $notesRoot = Join-Path $PSScriptRoot 'plugins/tas-notes'
    $notesCheck = Join-Path $notesRoot 'scripts/tas_notes_preflight.py'
    if (-not (Test-Path -LiteralPath $notesCheck -PathType Leaf)) {
        throw 'Package is incomplete. Extract the complete release and run its root install.ps1.'
    }
    $notesCommand = Get-Command -Name $Python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $notesCommand) {
        throw 'Python was not found. Install Python 3.11+ with PATH enabled, or pass -Python C:\path\python.exe.'
    }
    $notesPython = $notesCommand.Source
    $notesArguments = @($notesCheck, '--plugin-root', $notesRoot, '--json')
    if ($StateDir) { $notesArguments += @('--state-dir', $StateDir) }
    if ($InstallShortcuts) { $notesArguments += '--require-shortcuts' }
    # No Store, service, shortcuts or Codex configuration are created by this check.
    $notesRaw = & $notesPython @notesArguments
    $notesCheckExit = $LASTEXITCODE
    try { $notesResult = ($notesRaw -join "`n") | ConvertFrom-Json }
    catch { throw 'Python preflight could not run. Use a complete Python 3.11+ installation and re-extract the release.' }
    if (-not $notesResult -or $notesCheckExit -ne 0 -or -not $notesResult.ok) {
        foreach ($notesError in $notesResult.errors) { Write-Host "ERROR: $notesError" }
        throw 'Preflight failed; runtime configuration was not changed.'
    }
    Write-Host "Preflight passed: Blackbox Plugin $($notesResult.version)"
    Write-Host "Package source: $($notesResult.package_source)"
    Write-Host "Hook Python: $($notesResult.python) ($($notesResult.python_version))"
    Write-Host "MCP PATH Python: $($notesResult.mcp_path_python.executable)"
    Write-Host "State directory: $($notesResult.state_directory)"
    foreach ($notesWarning in $notesResult.warnings) { Write-Warning $notesWarning }
    if ($CheckOnly) {
        Write-Host 'Check only: no runtime, project, shortcut or Codex configuration changed.'
        exit 0
    }
    $notesEntry = Join-Path $notesRoot 'scripts/tas_notes.py'
    & $notesPython $notesEntry --state-dir $notesResult.state_directory configure-runtime
    if ($LASTEXITCODE -ne 0) {
        throw 'Runtime configuration failed. Check state-directory write access and Python path. Existing project choices were not changed.'
    }
    if ($InstallShortcuts) {
        & (Join-Path $notesRoot 'scripts/install_shortcuts.ps1') -Python $notesPython -PluginRoot $notesRoot -StateDir $notesResult.state_directory
    } else {
        Write-Host 'Shortcuts were not requested. Use -InstallShortcuts to create the optional local reader shortcuts.'
    }
    Write-Host 'Local runtime configured. No projects were enabled and no running reader was stopped.'
    Write-Host 'Codex plugin installation is a separate step: register this plugin using the supported Codex interface available in your host.'
    Write-Host "Plugin directory: $notesRoot"
    Write-Host 'Plugin registration, reader startup and actual Hook delivery have NOT been verified by this installer.'
    Write-Host 'Keep this extracted directory available; runtime setup does not register or copy the plugin into Codex.'
    exit 0
} catch {
    Write-Host "Blackbox setup failed: $($_.Exception.Message)"
    exit 1
}
