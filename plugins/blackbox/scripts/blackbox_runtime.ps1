# Invoked as fixed local plugin code by the CMD shims. Binding values are data;
# no execution-policy setting, PATH change, elevation or interpreter discovery.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$stage = 'runtime_launch_failed'
$markerState = $null

function Quote-Argument([string] $Value) {
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Assert-LocalAbsolute([object] $Value) {
    if ($Value -isnot [string] -or $Value.Length -eq 0 -or $Value.Length -gt 32760 -or
        $Value -match '[\x00-\x1f]' -or $Value -notmatch '^[A-Za-z]:[\\/]' -or
        $Value -match '(^|[\\/])\.\.?([\\/]|$)' -or $Value.Substring(2) -match '[<>:"|?*]') {
        throw 'runtime_binding_invalid'
    }
}

function Run-Child([string] $Executable, [string[]] $Arguments) {
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.UseShellExecute = $false
    # No redirected streams: preserve the inherited MCP/Hook bytes and avoid
    # PowerShell's native-output decoding/re-encoding or pipeline buffering.
    $start.Arguments = ($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
    $child = [System.Diagnostics.Process]::Start($start)
    try {
        $child.WaitForExit()
        return $child.ExitCode
    } finally {
        $child.Dispose()
    }
}

function Record-HookFailure([string] $State) {
    # Best effort only in the already verified bound store. Never create a
    # directory, follow a marker link, or write a diagnostic for MCP startup.
    $temporary = $null
    try {
        $directoryAttributes = [IO.File]::GetAttributes($State)
        $database = [IO.Path]::Combine($State, 'notes.sqlite3')
        $databaseAttributes = [IO.File]::GetAttributes($database)
        if (($directoryAttributes -band [IO.FileAttributes]::Directory) -eq 0 -or
            ($directoryAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($databaseAttributes -band [IO.FileAttributes]::Directory) -ne 0 -or
            ($databaseAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return }
        $marker = [IO.Path]::Combine($State, 'last-error.json')
        if ([IO.File]::Exists($marker) -or [IO.Directory]::Exists($marker)) {
            $markerAttributes = [IO.File]::GetAttributes($marker)
            if (($markerAttributes -band [IO.FileAttributes]::Directory) -ne 0 -or
                ($markerAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return }
        }
        $epoch = [Guid]::NewGuid().ToString('N')
        $temporary = [IO.Path]::Combine($State, '.launcher-error-' + $epoch + '.tmp')
        $stamp = [DateTime]::UtcNow.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
        $body = '{"code":"LauncherRuntimeFailed","scope":"unknown_project","time":"' + $stamp + '","epoch":"' + $epoch + '"}'
        [IO.File]::WriteAllText($temporary, $body, (New-Object Text.UTF8Encoding($false)))
        if ([IO.File]::Exists($marker)) {
            [IO.File]::Replace($temporary, $marker, $null)
        } else {
            [IO.File]::Move($temporary, $marker)
        }
        $temporary = $null
    } catch {
        # stderr still reports the startup failure if the marker is unwritable.
    } finally {
        if ($null -ne $temporary) { try { [IO.File]::Delete($temporary) } catch {} }
    }
}

try {
    $mode = $env:BLACKBOX_RUNTIME_MODE
    if ($mode -cne 'mcp' -and $mode -cne 'hook') { throw 'runtime_launch_mode_invalid' }
    $scripts = [IO.Path]::GetDirectoryName($env:BLACKBOX_RUNTIME_SCRIPT)
    $bindingHome = $env:CODEX_HOME
    if ($null -eq $bindingHome) { $bindingHome = [IO.Path]::Combine($env:USERPROFILE, '.codex') }
    Assert-LocalAbsolute $bindingHome
    $binding = [IO.Path]::Combine($bindingHome, 'blackbox', 'runtime.json')
    $stage = 'runtime_binding_unreadable'
    $stream = $null
    $missing = $false
    try {
        $attributes = [IO.File]::GetAttributes($binding)
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($attributes -band [IO.FileAttributes]::Directory) -ne 0) {
            $stage = 'runtime_binding_invalid'
            throw $stage
        }
        $stream = [IO.File]::Open($binding, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    } catch {
        $reason = $_.Exception
        while ($null -ne $reason.InnerException) { $reason = $reason.InnerException }
        if ($reason -is [IO.FileNotFoundException] -or $reason -is [IO.DirectoryNotFoundException]) {
            # A missing JSON file is compatible with old installs. An existing
            # malformed ancestor (file or dangling link) is not a missing setup.
            $ancestor = [IO.Path]::GetDirectoryName($binding)
            while (-not [IO.Directory]::Exists($ancestor)) {
                try {
                    $ancestorAttributes = [IO.File]::GetAttributes($ancestor)
                    throw 'runtime_binding_invalid'
                } catch {
                    $ancestorError = $_.Exception
                    while ($null -ne $ancestorError.InnerException) { $ancestorError = $ancestorError.InnerException }
                    if ($ancestorError -isnot [IO.FileNotFoundException] -and
                        $ancestorError -isnot [IO.DirectoryNotFoundException]) { throw }
                }
                $parent = [IO.Path]::GetDirectoryName($ancestor)
                if ([string]::IsNullOrEmpty($parent) -or $parent -eq $ancestor) { throw 'runtime_binding_invalid' }
                $ancestor = $parent
            }
            $missing = $true
        } else { throw }
    }
    if ($missing) {
        if ($mode -ceq 'hook') {
            # Compatibility only when no host binding exists. A broken binding
            # must never enter this path or create/use another state directory.
            $legacy = [IO.Path]::Combine($scripts, 'launch-legacy.cmd')
            $cmd = [IO.Path]::Combine($env:SystemRoot, 'System32', 'cmd.exe')
            $start = New-Object System.Diagnostics.ProcessStartInfo
            $start.FileName = $cmd
            $start.UseShellExecute = $false
            $start.Arguments = '/d /s /c ""' + $legacy + '""'
            $child = [System.Diagnostics.Process]::Start($start)
            try { $child.WaitForExit(); exit $child.ExitCode } finally { $child.Dispose() }
        }
        $stage = 'runtime_not_configured'
        throw $stage
    }
    try {
        if ($stream.Length -gt 16384) { $stage = 'runtime_binding_invalid'; throw $stage }
        $decoder = New-Object System.Text.UTF8Encoding($false, $true)
        $reader = New-Object IO.StreamReader($stream, $decoder, $false)
        try { $raw = $reader.ReadToEnd() } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
    $stage = 'runtime_binding_invalid'
    $value = ConvertFrom-Json -InputObject $raw
    if ($null -eq $value -or $value -is [array]) { throw $stage }
    $keys = @($value.PSObject.Properties.Name)
    $jsonStrings = [regex]::Matches($raw, '"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9A-Fa-f]{4}))*"')
    $keyCount = @($jsonStrings | Where-Object { $raw.Substring($_.Index + $_.Length).TrimStart().StartsWith(':') }).Count
    if ($keys.Count -ne 3 -or $keys -cnotcontains 'schema_version' -or
        $keys -cnotcontains 'python' -or $keys -cnotcontains 'state_directory' -or
        $keyCount -ne 3 -or $value.schema_version -isnot [int] -or $value.schema_version -ne 1) { throw $stage }
    Assert-LocalAbsolute $value.python
    Assert-LocalAbsolute $value.state_directory
    if ([IO.Path]::GetExtension($value.python) -ine '.exe') { throw $stage }
    $stage = 'runtime_state_unavailable'
    if (-not [IO.Directory]::Exists($value.state_directory)) { throw $stage }
    $stateAttributes = [IO.File]::GetAttributes($value.state_directory)
    $databaseAttributes = [IO.File]::GetAttributes([IO.Path]::Combine($value.state_directory, 'notes.sqlite3'))
    if (($stateAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($databaseAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($databaseAttributes -band [IO.FileAttributes]::Directory) -ne 0) { throw $stage }
    $override = $env:BLACKBOX_STATE_DIR
    if ([string]::IsNullOrEmpty($override)) { $override = $env:TAS_NOTES_STATE_DIR }
    # Python performs the authoritative physical same-file check. Only allow a
    # shell-side failure marker when absence/equality of an override is certain.
    if ($mode -ceq 'hook' -and ([string]::IsNullOrEmpty($override) -or
        [string]::Equals($override, $value.state_directory, [StringComparison]::OrdinalIgnoreCase))) {
        $markerState = $value.state_directory
    }
    $stage = 'runtime_unavailable'
    if (-not [IO.File]::Exists($value.python)) { throw $stage }
    $stage = 'runtime_launch_failed'
    $entry = [IO.Path]::Combine($scripts, 'blackbox_runtime.py')
    $exitCode = Run-Child $value.python @('-I', '-B', $entry, $mode)
    if ($exitCode -ne 0 -and $null -ne $markerState) { Record-HookFailure $markerState }
    exit $exitCode
} catch {
    if ($null -ne $markerState) { Record-HookFailure $markerState }
    [Console]::Error.WriteLine('Blackbox Plugin: ' + $stage + '; host runtime unavailable.')
    exit 1
}
