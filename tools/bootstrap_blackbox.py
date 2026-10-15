"""Blackbox setup helper for Codex; stdlib only, no Git or PATH edits required."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

VERSION = '0.4.0-alpha.6.46'
REPOSITORY = 'https://github.com/zemekeznb/blackbox-plugin'
MAX_BYTES = 32 * 1024 * 1024


class SetupError(Exception):
    pass


def active_codex():
    """Only return executable paths for running Codex app servers, never arguments."""
    if os.name != 'nt' or not os.environ.get('SystemRoot'):
        return []
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = ("@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'codex.exe' "
               "-and $_.CommandLine -match ' app-server ' } | "
               "Select-Object -ExpandProperty ExecutablePath -Unique) | ConvertTo-Json -Compress")
    try:
        result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=10,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        value = json.loads(result.stdout) if result.returncode == 0 else []
        return value if isinstance(value, list) else [value] if isinstance(value, str) else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def unique_executable(paths):
    found = {os.path.normcase(str(Path(p).resolve())): str(Path(p).resolve())
             for p in paths if p and Path(p).is_file() and Path(p).name.lower() == 'codex.exe'}
    if len(found) > 1:
        raise SetupError('Multiple Codex desktop executables found. Confirm the running host before installation.')
    return next(iter(found.values()), None)


def find_codex():
    active = unique_executable(active_codex())
    if active:
        return active
    configured = os.environ.get('CODEX_CLI_PATH')
    known = unique_executable([configured] if configured else [])
    if known:
        return known
    known = unique_executable([shutil.which('codex.exe')])
    if known:
        return known
    roots = [Path.home() / 'AppData/Local/OpenAI/Codex/bin']
    if os.environ.get('LOCALAPPDATA'):
        roots.append(Path(os.environ['LOCALAPPDATA']) / 'OpenAI/Codex/bin')
    candidates = []
    for root in roots:
        if root.is_dir():
            candidates += list(root.glob('*/codex.exe'))[:64]
    known = unique_executable(candidates)
    if not known:
        raise SetupError('Codex desktop executable could not be located. Open the desktop app and retry; do not install another CLI.')
    return known


def download(url, limit=MAX_BYTES):
    request = urllib.request.Request(url, headers={'User-Agent': 'Blackbox-Setup/' + VERSION})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise SetupError('Download exceeds the package size limit.')
    return data


def checked_files(data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise SetupError('Package SHA256 mismatch; no package code was executed.')
    files, folded = {}, set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 1000 or sum(item.file_size for item in entries) > MAX_BYTES:
            raise SetupError('Package contents exceed limits.')
        for item in entries:
            path = PurePosixPath(item.filename)
            if (not path.parts or path.is_absolute() or str(path) != item.filename
                    or any(p in ('.', '..') or ':' in p or p.endswith((' ', '.'))
                           or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
                           for p in path.parts)
                    or '\\' in item.filename or any(ord(c) < 32 for c in item.filename)
                    or item.filename.casefold() in folded
                    or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)):
                raise SetupError('Unsafe or duplicate package path.')
            folded.add(item.filename.casefold())
            files[item.filename] = archive.read(item)
    manifest = json.loads(files['release-manifest.json'])
    if manifest.get('version') != VERSION or set(files) != set(manifest['files']) | {'release-manifest.json', 'SHA256SUMS'}:
        raise SetupError('Package version or manifest does not match.')
    for name, info in manifest['files'].items():
        if len(files[name]) != info['size'] or hashlib.sha256(files[name]).hexdigest() != info['sha256']:
            raise SetupError('Package file does not match manifest.')
    # Reject file/directory collisions before writing anything.
    for name in files:
        if any(str(parent).casefold() in folded for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise SetupError('Package contains conflicting paths.')
    return files


def prepare(files, destination):
    destination = Path(destination).absolute()
    for parent in (destination, *destination.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise SetupError('Install directory must not contain links or junctions.')
    if destination.exists():
        for existing in destination.rglob('*'):
            if existing.is_symlink() or (hasattr(existing, 'is_junction') and existing.is_junction()):
                raise SetupError('Existing installation source contains a link or junction.')
            if existing.is_file() and existing.relative_to(destination).as_posix() not in files:
                raise SetupError('Existing installation source contains unexpected files; keep it intact and resolve the conflict.')
        for name in files:
            target = destination.joinpath(*PurePosixPath(name).parts)
            for part in (target, *target.parents):
                if part == destination:
                    break
                if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                    raise SetupError('Existing installation source contains a link or junction.')
        if any(not (destination / name).is_file() or (destination / name).is_symlink()
               or (destination / name).read_bytes() != body for name, body in files.items()):
            raise SetupError('Existing installation source differs; keep it intact and resolve the conflict before retrying.')
        return destination
    destination.mkdir(parents=True)
    for name, body in files.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(body)
    return destination


def helper(root, script, *arguments):
    result = subprocess.run([sys.executable, '-B', '-E', '-s', str(root / 'plugins/blackbox/scripts' / script),
                             *map(str, arguments)], capture_output=True, encoding='utf-8',
                            errors='replace', timeout=180, cwd=root, shell=False)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise SetupError(script + ' could not finish; child output omitted.') from None
    if result.returncode or not isinstance(value, dict) or value.get('ok') is False:
        # Only helpers' bounded public errors are shown, never raw host output.
        error = value.get('errors', value.get('error', 'Check failed.')) if isinstance(value, dict) else 'Invalid response.'
        raise SetupError(script + ': ' + str(error))
    return value


def install():
    if os.name != 'nt' or sys.version_info[:2] < (3, 11):
        raise SetupError('Windows and existing Python 3.11+ are required.')
    executable = find_codex()
    stem = 'blackbox-plugin-' + VERSION + '-candidate.zip'
    url = REPOSITORY + '/releases/download/v' + VERSION + '/' + stem
    checksum = download(url + '.sha256', 1024).decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{64}  ' + re.escape(stem), checksum):
        raise SetupError('Invalid checksum file.')
    files = checked_files(download(url), checksum.split()[0])
    root = prepare(files, Path.home() / 'Blackbox-install' / VERSION)
    checked = helper(root, 'blackbox_preflight.py', '--plugin-root', root / 'plugins/blackbox', '--json')
    helper(root, 'blackbox_install.py', '--root', root, '--codex', executable, '--replace-source')
    configured = helper(root, 'blackbox.py', '--state-dir', checked['state_directory'], 'configure-runtime', '--bind-host')
    if configured.get('runtime_configured') is not True or configured.get('host_bound') is not True:
        raise SetupError('Runtime configuration was not confirmed.')
    try:
        registered = helper(root, 'blackbox_install.py', '--root', root, '--codex', executable,
                            '--replace-source', '--register')
        if registered.get('registration') != 'verified':
            raise SetupError('Registration was not confirmed.')
    except SetupError as error:
        raise SetupError('Runtime configured; registration incomplete. Existing data retained. ' + str(error)) from None
    return {'installed': True, 'version': VERSION, 'installed_path': registered['installed_path'],
            'runtime_configured': True, 'host_bound': True,
            'state_directory': configured.get('state_directory', checked['state_directory']),
            'host_mcp': 'not_verified', 'sidebar': 'not_verified',
            'warnings': checked.get('warnings', []),
            'capture': 'not_verified', 'project_opt_in': 'not_changed',
            'hook_trust': 'not_changed', 'next': 'Read installed blackbox-setup, then open with blackbox-review. Reload Codex only if required.'}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        result = install()
    except (SetupError, OSError, ValueError, KeyError, subprocess.TimeoutExpired, zipfile.BadZipFile):
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, SetupError) else 'Setup failed; check the network and package, then retry. No installation success was confirmed.'
        print(json.dumps({'installed': False, 'error': message}))
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
