param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$PluginRoot = (Split-Path $PSScriptRoot -Parent)
)
$ErrorActionPreference = 'Stop'
$notesSource = (Resolve-Path -LiteralPath $PluginRoot).Path
$notesManifest = Get-Content -LiteralPath (Join-Path $notesSource '.codex-plugin/plugin.json') -Raw | ConvertFrom-Json
if ($notesManifest.name -ne 'tas-notes') { throw 'Not a TAS Notes plugin.' }
$notesPython = (& $Python -c 'import sys; print(getattr(sys,"_base_executable",sys.executable))').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Python is unavailable.' }
$notesPythonw = Join-Path (Split-Path $notesPython -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $notesPythonw -PathType Leaf)) { throw 'pythonw.exe is unavailable.' }
$notesBuild = (& $notesPython -c 'import sys; sys.path.insert(0,sys.argv[1]); from tas_notes_launcher import build_id; print(build_id())' (Join-Path $notesSource 'scripts')).Trim()
if ($LASTEXITCODE -ne 0 -or $notesBuild -notmatch '^[a-f0-9]{64}$') { throw 'Cannot identify reviewer build.' }
$notesApp = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) "TAS Notes/reviewer/$notesBuild"
$notesShortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'TAS Notes.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'TAS Notes.lnk')
)
$notesShell = New-Object -ComObject WScript.Shell
foreach ($notesShortcutPath in $notesShortcutPaths) {
    if (Test-Path -LiteralPath $notesShortcutPath) {
        $notesExisting = $notesShell.CreateShortcut($notesShortcutPath)
        if ($notesExisting.Description -ne 'TAS Notes local review launcher') {
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
            if ((Get-FileHash -LiteralPath $notesTarget).Hash -ne (Get-FileHash -LiteralPath $notesFile.FullName).Hash) {
                throw 'Installed build differs; refusing to overwrite it.'
            }
        } else { Copy-Item -LiteralPath $notesFile.FullName -Destination $notesTarget }
    }
}
foreach ($notesShortcutPath in $notesShortcutPaths) {
    $notesShortcut = $notesShell.CreateShortcut($notesShortcutPath)
    $notesShortcut.TargetPath = $notesPythonw
    $notesShortcut.Arguments = '"' + (Join-Path $notesApp 'scripts/open_notes.py') + '"'
    $notesShortcut.WorkingDirectory = $notesApp
    $notesShortcut.Description = 'TAS Notes local review launcher'
    $notesShortcut.WindowStyle = 7
    $notesShortcut.Save()
}
[pscustomobject]@{ shortcuts=$notesShortcutPaths; application=$notesApp; python=$notesPythonw } | ConvertTo-Json
