"""Read-only checks for an extracted Blackbox Alpha package (stdlib only)."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import platform
import re
import sys

MIN_PYTHON = (3, 11)
REQUIRED_FILES = (
    '.codex-plugin/plugin.json', '.mcp.json', 'hooks/hooks.json',
    'skills/blackbox-review/SKILL.md', 'skills/setup/SKILL.md',
    'scripts/blackbox.py', 'scripts/launch.cmd', 'scripts/launch-legacy.cmd',
    'scripts/mcp.cmd', 'scripts/blackbox_runtime.py', 'scripts/blackbox_runtime.ps1', 'scripts/open_notes.py',
    'scripts/install_shortcuts.ps1', 'scripts/blackbox_preflight.py',
    'scripts/blackbox_core.py', 'scripts/blackbox_launcher.py', 'scripts/blackbox_install.py', 'scripts/blackbox_compat.py',
    'scripts/blackbox_mcp.py', 'scripts/blackbox_web.py',
    'scripts/blackbox_backup.py', 'scripts/blackbox_codex.py',
    'scripts/blackbox_diagnostics.py', 'scripts/blackbox_capture_probe.py',
    'scripts/blackbox_anomalies.py', 'scripts/blackbox_summary.py',
    'scripts/blackbox_shell_errors.py', 'scripts/blackbox_business_errors.py',
    'scripts/blackbox_curl_errors.py', 'scripts/blackbox_curl_result.py',
    'scripts/blackbox_http_errors.py', 'scripts/blackbox_transport_errors.py',
    'scripts/blackbox_sse_errors.py',
    'assets/review.html', 'assets/review.css', 'assets/review.js',
    'assets/anomalies.html', 'assets/anomalies.js', 'assets/mcp-reader.js',
)


def check_package(root):
    errors = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append('Package file missing: ' + relative + '. Extract the complete release again.')
    version = None
    try:
        manifest = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8-sig'))
        version = manifest.get('version')
        if manifest.get('name') != 'blackbox' or not isinstance(version, str) or not re.fullmatch(
                r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+_-]+)?', version):
            raise ValueError()
    except (OSError, ValueError, AttributeError):
        errors.append('Plugin manifest is invalid; expected technical key blackbox and a release version.')
    try:
        mcp = json.loads((root / '.mcp.json').read_text(encoding='utf-8-sig'))
        server = mcp['mcpServers']['blackbox_reader']
        if (server.get('command') != 'cmd.exe' or server.get('args') != ['/d', '/c', 'scripts\\mcp.cmd']
                or server.get('cwd') != '.'):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        errors.append('MCP launch configuration is invalid; restore .mcp.json from this release.')
    try:
        hooks = json.loads((root / 'hooks/hooks.json').read_text(encoding='utf-8-sig'))['hooks']
        if not isinstance(hooks, dict) or not hooks.get('PostToolUse'):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        errors.append('Hook configuration is invalid; restore hooks/hooks.json from this release.')
    return version, errors


def check_release_manifest(root, version):
    """Verify package bytes without trusting manifest paths outside the extraction."""
    release_root = root.parent.parent
    manifest_path = release_root / 'release-manifest.json'
    if not manifest_path.exists():
        return 'source_checkout', []
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        if manifest.get('schema_version') != 1 or manifest.get('version') != version:
            raise ValueError()
        files = manifest['files']
        if not isinstance(files, dict):
            raise ValueError()
        required = {'plugins/blackbox/' + name for name in REQUIRED_FILES} | {'install.ps1'}
        if not required.issubset(files):
            raise ValueError()
        for name, metadata in files.items():
            relative = PurePosixPath(name)
            if (not name or '\\' in name or ':' in name or relative.is_absolute()
                    or any(part in ('.', '..') for part in relative.parts)):
                raise ValueError()
            path = release_root.joinpath(*relative.parts)
            resolved = path.resolve(strict=True)
            if release_root not in resolved.parents or not resolved.is_file():
                raise ValueError()
            if (not isinstance(metadata, dict) or not isinstance(metadata.get('size'), int)
                    or not isinstance(metadata.get('sha256'), str)
                    or not re.fullmatch('[a-f0-9]{64}', metadata['sha256'])):
                raise ValueError()
            if resolved.stat().st_size != metadata['size']:
                raise ValueError()
            digest = hashlib.sha256()
            with resolved.open('rb') as stream:
                for block in iter(lambda: stream.read(128 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest() != metadata['sha256']:
                raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 'release_manifest_invalid', [
            'Release manifest/version/file checksum verification failed. Re-extract a complete unmodified release.']
    return 'release_manifest_verified', []


def inspect_state(state_dir, root):
    # Importing the resolver does not create a store, launch a service or scan events.
    from blackbox_launcher import resolve_state_directory
    import sqlite3
    directory = resolve_state_directory(state_dir)
    release_root = root.parent.parent
    if directory == release_root or release_root in directory.parents:
        raise ValueError('Keep recording data outside the complete release/source directory; choose another -StateDir.')
    if any((ancestor / '.git').exists() for ancestor in (directory, *directory.parents)):
        raise ValueError('StateDir must be outside all Git working directories; choose another -StateDir.')
    if directory.exists() and not directory.is_dir():
        raise ValueError('StateDir is a file. Choose a directory for local recording data.')
    ancestor = directory
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK):
        raise ValueError('StateDir is not writable. Preserve the selected directory and diagnose the required access before retrying.')
    database = directory / 'notes.sqlite3'
    if database.exists() or database.is_symlink():
        if database.is_symlink() or not database.is_file():
            raise ValueError('The existing database is not a regular file; preserve it and resolve the database location before retrying.')
        try:
            connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=3)
            try:
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                current = connection.execute("SELECT 1 FROM sqlite_master WHERE name='capture_usage' AND type='table'").fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            raise ValueError('Existing database cannot be checked. Back it up and resolve access/corruption before retrying.') from None
        if version != 1 or not current:
            raise ValueError('Existing database needs a separate compatibility/migration review. '
                             'Back it up first; this installer will not migrate history.')
    return directory


def preflight(plugin_root, state_dir=None, require_shortcuts=False):
    root = Path(plugin_root).expanduser().resolve()
    version, errors = check_package(root)
    package_source, release_errors = check_release_manifest(root, version)
    errors.extend(release_errors)
    warnings = []
    result = {'ok': False, 'version': version, 'plugin_root': str(root),
              'python': sys.executable, 'python_version': platform.python_version(),
              'minimum_python': '3.11', 'platform': platform.system(),
              'minimum_windows': '10', 'windows_version': platform.version(),
              'state_directory': None, 'runtime_binding': 'not_written',
              'mcp_launch': 'configured_absolute_python', 'host_mcp': 'not_checked',
              'package_source': package_source,
              'host_plugin_registration': 'not_checked', 'host_capture': 'not_checked',
              'errors': errors, 'warnings': warnings}
    if platform.system() != 'Windows':
        errors.append('Alpha installer supports Windows only; no changes were made.')
    elif hasattr(sys, 'getwindowsversion') and sys.getwindowsversion().major < 10:
        errors.append('Windows 10 or newer is required for the Alpha installer.')
    if sys.version_info[:2] < MIN_PYTHON:
        errors.append('Python 3.11 or newer is required. Run install.ps1 -Python with a compatible python.exe.')
    try:
        for name in ('sqlite3', 'hashlib', 'http.server', 'ctypes'):
            importlib.import_module(name)
        if not hasattr(importlib.import_module('hashlib'), 'file_digest'):
            raise ImportError()
    except ImportError:
        errors.append('Selected Python lacks required standard-library modules. Install a complete Python 3.11+ distribution.')
    if require_shortcuts:
        base_python = Path(getattr(sys, '_base_executable', sys.executable))
        if not base_python.with_name('pythonw.exe').is_file():
            errors.append('Shortcuts require pythonw.exe beside Python; install complete Python or omit -InstallShortcuts.')
    try:
        directory = inspect_state(state_dir, root)
        result['state_directory'] = str(directory)
        warnings.append('Host setup must persist this physical state directory and selected Python with '
                        'configure-runtime --bind-host. Temporary PATH changes are not a host runtime configuration.')
    except (OSError, ValueError, ImportError) as error:
        errors.append(str(error) if isinstance(error, ValueError) else
                      'Cannot resolve StateDir. Preserve the selected directory and diagnose access before retrying; do not substitute an empty store.')
    warnings.append('Preflight checks this shell only. Codex plugin registration and real Hook delivery remain unverified.')
    result['ok'] = not errors
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--require-shortcuts', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    result = preflight(args.plugin_root, args.state_dir, args.require_shortcuts)
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    else:
        print('Blackbox Plugin preflight: ' + ('PASS' if result['ok'] else 'FAILED'))
        print('Version: ' + str(result['version']))
        for kind in ('errors', 'warnings'):
            for message in result[kind]:
                print(kind.upper() + ': ' + message)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
