param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$PluginRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$StateDir
)
$ErrorActionPreference = 'Stop'
$notesSource = (Resolve-Path -LiteralPath $PluginRoot).Path
$notesManifest = Get-Content -LiteralPath (Join-Path $notesSource '.codex-plugin/plugin.json') -Raw | ConvertFrom-Json
if ($notesManifest.name -ne 'tas-notes') { throw 'Not a Blackbox Plugin plugin.' }
$notesPython = (& $Python -c 'import sys; print(getattr(sys,"_base_executable",sys.executable))').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Python is unavailable.' }
$notesPythonw = Join-Path (Split-Path $notesPython -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $notesPythonw -PathType Leaf)) { throw 'pythonw.exe is unavailable.' }
$notesState = (& $notesPython -c 'import sys; sys.path.insert(0,sys.argv[1]); from tas_notes_launcher import resolve_state_directory; print(resolve_state_directory(sys.argv[2] if len(sys.argv)>2 and sys.argv[2] else None, create=True))' (Join-Path $notesSource 'scripts') $StateDir).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $notesState -PathType Container)) { throw 'Cannot resolve recording state directory.' }
$notesBuild = (& $notesPython -c 'import sys; sys.path.insert(0,sys.argv[1]); from tas_notes_launcher import build_id; print(build_id())' (Join-Path $notesSource 'scripts')).Trim()
if ($LASTEXITCODE -ne 0 -or $notesBuild -notmatch '^[a-f0-9]{64}$') { throw 'Cannot identify reviewer build.' }
$notesApp = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) "TAS Notes/reviewer/$notesBuild"
$notesShortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Blackbox Plugin.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Blackbox Plugin.lnk')
)
$notesShell = New-Object -ComObject WScript.Shell
foreach ($notesShortcutPath in $notesShortcutPaths) {
    if (Test-Path -LiteralPath $notesShortcutPath) {
        $notesExisting = $notesShell.CreateShortcut($notesShortcutPath)
        if ($notesExisting.Description -ne 'Blackbox Plugin local review launcher') {
            throw "A different shortcut already exists: $notesShortcutPath"
        }
    }
}
# Copy only application code/assets, never databases, configuration or event data.
foreach ($notesFolder in @('scripts','assets')) {
    $notesDestination = Join-Path $notesApp $notesFolder
    New-Item -ItemType Directory -Path $notesDestination -Force | Out-Null
    foreach ($notesFile in (Get-ChildItem -LiteralPath (Join-Path $notesSource $notesFolder) -File)) {
        if ($notesFile.Extension -notin @('.py','.js','.css','.html')) { continue }
        $notesTarget = Join-Path $notesDestination $notesFile.Name
        if (Test-Path -LiteralPath $notesTarget) {
            # build_id normalizes CRLF. Compare the same bytes here, otherwise
            # reinstalling a Git checkout with another newline policy fails.
            & $notesPython -c 'import sys; sys.path.insert(0,sys.argv[1]); from tas_notes_launcher import normalized_file_bytes; sys.exit(0 if normalized_file_bytes(sys.argv[2]) == normalized_file_bytes(sys.argv[3]) else 1)' (Join-Path $notesSource 'scripts') $notesTarget $notesFile.FullName
            if ($LASTEXITCODE -ne 0) {
                throw 'Installed build differs; refusing to overwrite it.'
            }
        } else { Copy-Item -LiteralPath $notesFile.FullName -Destination $notesTarget }
    }
}
# Preserve release metadata so the copied app reports the same version/build.
$notesManifestDirectory = Join-Path $notesApp '.codex-plugin'
New-Item -ItemType Directory -Path $notesManifestDirectory -Force | Out-Null
$notesManifestTarget = Join-Path $notesManifestDirectory 'plugin.json'
if (Test-Path -LiteralPath $notesManifestTarget) {
    $notesInstalledManifest = Get-Content -LiteralPath $notesManifestTarget -Raw | ConvertFrom-Json
    if ($notesInstalledManifest.name -ne $notesManifest.name -or $notesInstalledManifest.version -ne $notesManifest.version) {
        throw 'Installed release metadata differs; refusing to overwrite it.'
    }
} else {
    Copy-Item -LiteralPath (Join-Path $notesSource '.codex-plugin/plugin.json') -Destination $notesManifestTarget
}
# A packaged Codex process may write LocalAppData into its MSIX LocalCache.
# Explorer cannot see that virtual overlay: persist the real file-handle path.
$notesEntry = (& $notesPython -c 'import sys; sys.path.insert(0,sys.argv[1]); from tas_notes_launcher import physical_file_path; print(physical_file_path(sys.argv[2]))' (Join-Path $notesSource 'scripts') (Join-Path $notesApp 'scripts/open_notes.py')).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $notesEntry -PathType Leaf)) { throw 'Cannot resolve installed launcher.' }
$notesApp = Split-Path (Split-Path $notesEntry -Parent) -Parent
$notesInstalledBuild = (& $notesPython -c 'import sys; sys.path.insert(0,sys.argv[1]); from tas_notes_launcher import build_id; print(build_id())' (Join-Path $notesApp 'scripts')).Trim()
if ($LASTEXITCODE -ne 0 -or $notesInstalledBuild -ne $notesBuild) { throw 'Installed build verification failed.' }
foreach ($notesShortcutPath in $notesShortcutPaths) {
    $notesShortcut = $notesShell.CreateShortcut($notesShortcutPath)
    $notesShortcut.TargetPath = $notesPythonw
    $notesShortcut.Arguments = '"' + $notesEntry + '" --state-dir "' + $notesState + '"'
    $notesShortcut.WorkingDirectory = $notesApp
    $notesShortcut.Description = 'Blackbox Plugin local review launcher'
    # pythonw already avoids a console; do not request a minimized launch.
    $notesShortcut.WindowStyle = 1
    $notesShortcut.Save()
    # COM Save() succeeding alone does not prove that Windows has a usable link.
    $notesVerified = $notesShell.CreateShortcut($notesShortcutPath)
    if ($notesVerified.TargetPath -ne $notesPythonw -or
        $notesVerified.Arguments -ne $notesShortcut.Arguments -or
        $notesVerified.WorkingDirectory -ne $notesApp -or
        $notesVerified.WindowStyle -ne 1 -or
        -not (Test-Path -LiteralPath $notesVerified.TargetPath -PathType Leaf)) {
        throw "Shortcut verification failed: $notesShortcutPath"
    }
}
[pscustomobject]@{ shortcuts=$notesShortcutPaths; application=$notesApp; python=$notesPythonw; state_directory=$notesState; build=$notesBuild; version=$notesManifest.version } | ConvertTo-Json
