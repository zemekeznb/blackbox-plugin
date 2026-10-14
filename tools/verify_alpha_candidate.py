"""Verify a local Alpha archive and optionally smoke-test its isolated reader.

Archive checks establish consistency, not publisher identity. The smoke test
executes the selected archive's Python code; use only a trusted local candidate.
It never enables a project or claims that Codex delivered an actual Hook.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import zipfile


MAX_FILES = 2000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
METADATA = {'release-manifest.json', 'SHA256SUMS'}
PLUGIN = 'plugins/blackbox/'
HASH = re.compile(r'[a-f0-9]{64}\Z')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z')
RESERVED = re.compile(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z')


class VerificationError(Exception):
    """Content-free failure; do not print subprocess output or reader tokens."""


def safe_name(value, *, directory=False):
    if not isinstance(value, str) or not value or '\\' in value:
        raise VerificationError('Unsafe archive path.')
    name = value[:-1] if directory and value.endswith('/') else value
    parts = name.split('/')
    if any(not part or part in ('.', '..') or part.endswith(('.', ' '))
           or RESERVED.fullmatch(part) or re.search(r'[\x00-\x1f\x7f:<>"|?*]', part)
           for part in parts):
        raise VerificationError('Unsafe archive path.')
    return name


def object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError('Duplicate JSON key in archive metadata.')
        result[key] = value
    return result


def read_json(data):
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=object_without_duplicates)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise VerificationError('Invalid JSON archive metadata.') from exc


def verify_archive(archive):
    """Read a bounded archive without extracting or executing any member."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > MAX_FILES:
                raise VerificationError('Archive has too many entries.')
            names, files, total = {}, {}, 0
            for info in entries:
                name = safe_name(info.orig_filename, directory=info.is_dir())
                folded = name.casefold()
                if folded in names:
                    raise VerificationError('Duplicate or case-colliding archive path.')
                names[folded] = (name, info.is_dir())
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG):
                    raise VerificationError('Archive contains a link or special file.')
                if info.flag_bits & 1:
                    raise VerificationError('Encrypted archive entries are not supported.')
                if info.is_dir():
                    if info.file_size:
                        raise VerificationError('Archive directory has unexpected content.')
                    continue
                total += info.file_size
                if info.file_size > MAX_FILE_BYTES or total > MAX_ARCHIVE_BYTES:
                    raise VerificationError('Archive exceeds verification size limits.')
                files[name] = bundle.read(info)
            for folded in names:
                components = folded.split('/')
                for length in range(1, len(components)):
                    parent = names.get('/'.join(components[:length]))
                    if parent and not parent[1]:
                        raise VerificationError('Archive path conflicts with a parent file.')
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise VerificationError('Archive cannot be read or failed its ZIP integrity check.') from exc
    if not METADATA.issubset(files):
        raise VerificationError('Archive is missing release metadata.')
    manifest = read_json(files['release-manifest.json'])
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise VerificationError('Unsupported release manifest schema.')
    version, source = manifest.get('version'), manifest.get('source')
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise VerificationError('Invalid release version.')
    if (not isinstance(source, dict) or type(source.get('dirty')) not in (bool, type(None))
            or source.get('commit') is not None and (not isinstance(source['commit'], str)
                or not re.fullmatch(r'[a-fA-F0-9]{40,64}', source['commit']))):
        raise VerificationError('Invalid source identity metadata.')
    declared = manifest.get('files')
    if not isinstance(declared, dict) or not declared:
        raise VerificationError('Release manifest has no payload files.')
    for name, expected in declared.items():
        safe_name(name)
        if (name in METADATA or not isinstance(expected, dict)
                or not isinstance(expected.get('sha256'), str) or not HASH.fullmatch(expected['sha256'])
                or type(expected.get('size')) is not int or expected['size'] < 0):
            raise VerificationError('Invalid file metadata in release manifest.')
    if set(files) != set(declared) | METADATA:
        raise VerificationError('Archive files do not match the release manifest.')
    for name, expected in declared.items():
        data = files[name]
        if len(data) != expected['size'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise VerificationError('Payload size or SHA-256 does not match the release manifest.')
    try:
        checksums = files['SHA256SUMS'].decode('utf-8').splitlines()
    except UnicodeError as exc:
        raise VerificationError('Invalid SHA256SUMS encoding.') from exc
    seen = set()
    for line in checksums:
        match = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
        if not match:
            raise VerificationError('Invalid SHA256SUMS entry.')
        digest, name = match.groups()
        safe_name(name)
        if name in seen or name == 'SHA256SUMS' or name not in files:
            raise VerificationError('Unexpected or repeated SHA256SUMS entry.')
        seen.add(name)
        if hashlib.sha256(files[name]).hexdigest() != digest:
            raise VerificationError('SHA256SUMS integrity check failed.')
    if seen != set(files) - {'SHA256SUMS'}:
        raise VerificationError('SHA256SUMS does not cover every payload and the manifest.')
    return manifest, files


def extract_verified(files, destination):
    """Extract verified bytes ourselves; never trust ZipFile.extract path rules."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        safe_name(name)
        target = destination.joinpath(*name.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)


@contextmanager
def smoke_directory():
    temporary = tempfile.TemporaryDirectory(prefix='blackbox-alpha-')
    try:
        yield temporary.name
    finally:
        # HTTP shutdown can finish before Windows releases the service's cwd.
        # Retry only this owned temporary directory; persistent failure surfaces.
        for attempt in range(30):
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if attempt == 29:
                    raise
                time.sleep(0.1)


def smoke_test(manifest, files):
    if PLUGIN + 'scripts/blackbox.py' not in files:
        raise VerificationError('Candidate does not contain the expected Blackbox entry point.')
    with smoke_directory() as temporary:
        root = Path(temporary)
        package, state = root / '候选 插件 with spaces', root / '隔离 状态'
        extract_verified(files, package)
        entry = package / PLUGIN / 'scripts/blackbox.py'
        environment = dict(os.environ, BLACKBOX_STATE_DIR=str(state), PYTHONDONTWRITEBYTECODE='1')
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)

        def command(*args):
            try:
                result = subprocess.run([sys.executable, '-B', '-E', '-s', str(entry),
                    '--state-dir', str(state), *args], cwd=package, env=environment,
                    capture_output=True, timeout=25,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except (OSError, subprocess.SubprocessError) as exc:
                raise VerificationError(f'Isolated {args[0]} command could not complete.') from exc
            if result.returncode:
                raise VerificationError(f'Isolated {args[0]} command failed; child output omitted.')
            try:
                data = json.loads(result.stdout)
            except (ValueError, UnicodeError) as exc:
                raise VerificationError(f'Isolated {args[0]} returned invalid JSON.') from exc
            if not isinstance(data, dict):
                raise VerificationError(f'Isolated {args[0]} returned an unexpected result.')
            return data

        started = False
        try:
            command('configure-runtime')
            initial = command('status')
            if (initial.get('runtime', {}).get('state') != 'configured'
                    or initial.get('record_count') != 0 or initial.get('projects') != []):
                raise VerificationError('Fresh candidate state is unexpectedly configured or populated.')
            database = state / 'notes.sqlite3'
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            command('configure-runtime')
            repeated = command('status')
            if (hashlib.sha256(database.read_bytes()).hexdigest() != before
                    or repeated.get('record_count') != initial['record_count']):
                raise VerificationError('Repeating runtime configuration changed the recording database.')
            # Set before launching: a timeout or invalid response can still leave
            # a service behind, which must be stopped using this isolated state.
            started = True
            opened = command('open', '--no-browser')
            match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})/#token=([A-Za-z0-9_-]{43})', opened.get('url', ''))
            if not match or not 1 <= int(match[1]) <= 65535:
                raise VerificationError('Reader returned an invalid local endpoint.')
            endpoint = urlsplit(opened['url'])
            connection = http.client.HTTPConnection('127.0.0.1', endpoint.port, timeout=5)
            try:
                connection.request('GET', '/api/health')
                response = connection.getresponse()
                response.read()
                if response.status != 401:
                    raise VerificationError('Reader health endpoint accepted an unauthenticated request.')
                headers = {'Authorization': 'Bearer ' + match[2]}
                connection.request('GET', '/api/health', headers=headers)
                response = connection.getresponse()
                body = response.read(MAX_FILE_BYTES + 1)
                if response.status != 200 or len(body) > MAX_FILE_BYTES:
                    raise VerificationError('Reader authenticated health check failed.')
                health = read_json(body)
                if (not isinstance(health, dict) or health.get('version') != manifest['version']
                        or health.get('service') != 'blackbox-review'
                        or Path(health.get('state_dir', '')).resolve() != state.resolve()):
                    raise VerificationError('Reader version or isolated state does not match the candidate.')
                connection.request('GET', '/', headers=headers)
                response = connection.getresponse()
                page = response.read(MAX_FILE_BYTES + 1)
                if response.status != 200 or b'Blackbox' not in page or len(page) > MAX_FILE_BYTES:
                    raise VerificationError('Reader page smoke check failed.')
            finally:
                connection.close()
        finally:
            if started:
                stopped = command('stop')
                if not stopped.get('stopped') and stopped.get('reason') != 'not_running':
                    raise VerificationError('Isolated reader did not confirm shutdown.')
        return {'runtime_configuration': 'passed', 'repeat_configuration_preserved_database': True,
                'reader_health_and_page': 'passed', 'reader_shutdown': 'passed',
                'projects_enabled': 0, 'record_count': 0,
                'synthetic_hooks_used': False, 'real_host_acceptance': 'not_tested'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--verify-only', action='store_true',
                        help='Only verify archive integrity; do not extract or execute package code')
    args = parser.parse_args(argv)
    try:
        manifest, files = verify_archive(args.archive)
        result = {'archive_integrity': 'passed', 'version': manifest['version'],
                  'file_count': len(files), 'source': manifest['source'],
                  'publisher_authenticity': 'not_verified', 'real_host_acceptance': 'not_tested',
                  'publish_ready': False}
        result['isolated_smoke'] = 'not_run' if args.verify_only else smoke_test(manifest, files)
    except (VerificationError, OSError, ValueError, http.client.HTTPException) as exc:
        message = str(exc) if isinstance(exc, VerificationError) else 'Verification failed due to a local I/O or protocol error.'
        print(json.dumps({'archive_verification': 'failed', 'error': message,
                          'publish_ready': False}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
