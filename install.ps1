# Maintenance/compatibility installer for an already extracted package.
# New users: use either supported entrypoint in README.md / release/INSTALL.md.
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$StateDir,
    [string]$Codex = 'codex',
    [switch]$RuntimeOnly,
    [switch]$ReplaceSource,
    [switch]$CheckOnly,
    [switch]$SkipShortcuts,
    [switch]$InstallShortcuts
)
$ErrorActionPreference = 'Stop'

try {
    if ($env:OS -ne 'Windows_NT') { throw 'This Alpha installer supports Windows only.' }
    if ($SkipShortcuts -and $InstallShortcuts) { throw 'Choose either -SkipShortcuts or -InstallShortcuts.' }
    $notesRoot = Join-Path $PSScriptRoot 'plugins/blackbox'
    $notesCheck = Join-Path $notesRoot 'scripts/blackbox_preflight.py'
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
    if ($RuntimeOnly -and $ReplaceSource) { throw '-ReplaceSource requires full installation, not -RuntimeOnly.' }
    $notesHostArguments = @((Join-Path $notesRoot 'scripts/blackbox_install.py'), '--root', $PSScriptRoot, '--codex', $Codex)
    if ($ReplaceSource) { $notesHostArguments += '--replace-source' }
    if (-not $RuntimeOnly) {
        $notesHostRaw = & $notesPython @notesHostArguments
        $notesHostExit = $LASTEXITCODE
        $notesHost = ($notesHostRaw -join "`n") | ConvertFrom-Json
        if ($notesHostExit -ne 0 -or -not $notesHost.ok) { throw $notesHost.error }
        Write-Host "Codex: $($notesHost.codex)"
        Write-Host "Codex home: $($notesHost.codex_home)"
    }
    if ($CheckOnly) {
        Write-Host 'Check only: no runtime, project, shortcut or plugin registration changed. Codex inventory commands may create host cache/log files.'
        exit 0
    }
    $notesEntry = Join-Path $notesRoot 'scripts/blackbox.py'
    & $notesPython $notesEntry --state-dir $notesResult.state_directory configure-runtime
    if ($LASTEXITCODE -ne 0) {
        throw 'Runtime configuration failed. Check state-directory write access and Python path. Existing project choices were not changed.'
    }
    if (-not $RuntimeOnly) {
        $notesHostRaw = & $notesPython @notesHostArguments --register
        $notesHostExit = $LASTEXITCODE
        $notesHost = ($notesHostRaw -join "`n") | ConvertFrom-Json
        if ($notesHostExit -ne 0 -or -not $notesHost.ok) {
            throw "Runtime configured; plugin registration not verified. $($notesHost.error) Existing records are retained. Retry this installer after resolving the error."
        }
        Write-Host "Plugin registration and payload verified: $($notesHost.version)"
    }
    & $notesPython (Join-Path $notesRoot 'scripts/blackbox_hooks.py')
    Write-Host 'Hook trust is separate from installation. If not trusted, start Codex and use /hooks to review blackbox@personal. Recording cannot start until trusted.'
    if ($InstallShortcuts) {
        & (Join-Path $notesRoot 'scripts/install_shortcuts.ps1') -Python $notesPython -PluginRoot $notesRoot -StateDir $notesResult.state_directory
    } else {
        Write-Host 'Shortcuts were not requested. Use -InstallShortcuts to create the optional local reader shortcuts.'
    }
    Write-Host 'Local runtime configured. No projects were enabled and no running reader was stopped.'
    if ($RuntimeOnly) { Write-Host 'Runtime-only mode: Codex plugin registration was not checked or changed.' }
    Write-Host "Plugin directory: $notesRoot"
    Write-Host 'Host MCP startup and actual Hook delivery are pending verification in Codex. Choose a project explicitly before recording.'
    Write-Host 'Keep this extracted directory in its stable location for marketplace updates. Reopen Codex if its plugin list has not refreshed.'
    exit 0
} catch {
    Write-Host "Blackbox setup failed: $($_.Exception.Message)"
    exit 1
}
