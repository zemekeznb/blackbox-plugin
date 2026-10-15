"""Legacy inputs only. New installations and outputs use Blackbox names."""
import os
from pathlib import Path
import subprocess

LEGACY_PLUGIN = 'tas-notes'
LEGACY_STATE_NAME = 'TAS Notes'
LEGACY_STATE_ENV = 'TAS_NOTES_STATE_DIR'
LEGACY_SERVICE = 'tas-notes-review'


def default_state():
    from blackbox_core import NotesError
    override = os.environ.get('BLACKBOX_STATE_DIR') or os.environ.get(LEGACY_STATE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    from blackbox_runtime import configured_state
    configured = configured_state()
    if configured is not None:
        return configured
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise NotesError('LOCALAPPDATA is unavailable; specify --state-dir')
    current, legacy = Path(base) / 'Blackbox', Path(base) / LEGACY_STATE_NAME
    if current.exists() and legacy.exists() and current.resolve() != legacy.resolve():
        raise NotesError('Both Blackbox and legacy state directories exist. Choose --state-dir explicitly; automatic merging is disabled.')
    return current if current.exists() or not legacy.exists() else legacy


def make_alias(legacy, current):
    if os.name == 'nt':
        # Fixed PowerShell code; paths are data in child-only environment vars.
        environment = dict(os.environ, BLACKBOX_ALIAS=str(legacy), BLACKBOX_TARGET=str(current))
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        process = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
                                  "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:BLACKBOX_ALIAS -Target $env:BLACKBOX_TARGET | Out-Null"],
                                 env=environment, capture_output=True, timeout=20)
        if process.returncode:
            raise OSError('Legacy path alias creation failed')
    else:
        legacy.symlink_to(current, target_is_directory=True)


def migrate_default(directory):
    """Rename only the resolved default legacy directory; never merge or rewrite DBs.

    Call at explicit runtime setup, before opening Store. Custom paths stay put.
    Windows rejects moving open database handles; failure leaves the old path usable.
    """
    from blackbox_core import NotesError
    from blackbox_launcher import resolve_state_directory, read_registry, probe_instance
    directory = Path(directory).resolve()
    if os.environ.get('BLACKBOX_STATE_DIR') or os.environ.get(LEGACY_STATE_ENV):
        return directory
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        return directory
    legacy_logical = Path(base) / LEGACY_STATE_NAME
    expected = resolve_state_directory(legacy_logical)
    if directory != expected or directory.name != LEGACY_STATE_NAME or not directory.exists():
        return directory
    current = directory.with_name('Blackbox')
    # Both paths must be direct siblings of the checked physical default parent.
    if directory.parent != current.parent or current.exists() or current.is_symlink():
        raise NotesError('Blackbox destination already exists. Resolve state-directory conflict before migration.')
    if directory.is_symlink() or (hasattr(directory, 'is_junction') and directory.is_junction()):
        raise NotesError('Legacy state is redirected; choose its existing directory explicitly instead of moving it.')
    instance = read_registry(directory / 'review-instance.json')
    if instance and probe_instance(instance, directory):
        raise NotesError('Stop the existing Blackbox reading service before migrating its data directory, then retry setup.')
    try:
        directory.rename(current)
    except OSError:
        raise NotesError('Legacy data is in use or cannot be moved. Close the reading service and retry; data was not changed.') from None
    try:
        make_alias(directory, current)
    except (OSError, subprocess.TimeoutExpired):
        # If an alias was created despite a late process failure, accept only an
        # exact link to this target. Otherwise rollback the directory rename.
        if directory.exists() and directory.resolve() == current.resolve():
            return current
        if not directory.exists() and not directory.is_symlink():
            current.rename(directory)
        raise NotesError('Could not preserve the legacy path. Migration did not complete; inspect the two directories before retrying.') from None
    return current
