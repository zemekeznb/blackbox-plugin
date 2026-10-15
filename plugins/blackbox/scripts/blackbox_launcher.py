"""Detached local reviewer with bounded readiness handshake, no autostart."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import hashlib
import http.client
import re
import errno
from contextlib import contextmanager

_owned_children = {}


@contextmanager
def _os_error_stage(stage):
    """Attach a fixed internal stage without changing the original OS error."""
    try:
        yield
    except OSError as error:
        error._blackbox_stage = stage
        raise


def _win32_error(stage, error_code=None):
    import ctypes
    # Capture GetLastError before another Windows call can overwrite it. The
    # exception text is never printed by the CLI; the numeric codes are useful.
    code = ctypes.get_last_error() if error_code is None else error_code
    error = ctypes.WinError(code)
    error._blackbox_stage = stage
    return error


def physical_file_path(path):
    """Resolve MSIX file virtualization through an actual open file handle."""
    if os.name != 'nt':
        with _os_error_stage('physical_file_resolve'):
            return str(Path(path).resolve(strict=True))
    import ctypes
    import msvcrt
    api = ctypes.WinDLL('kernel32', use_last_error=True).GetFinalPathNameByHandleW
    api.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    api.restype = ctypes.c_uint32
    with _os_error_stage('physical_file_open'):
        stream = Path(path).open('rb')
    with stream:
        buffer = ctypes.create_unicode_buffer(32768)
        size = api(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
        if not size:
            raise _win32_error('physical_file_resolve')
        if size >= len(buffer):
            raise _win32_error('physical_file_resolve', 122)  # ERROR_INSUFFICIENT_BUFFER
        value = buffer.value
    if value.startswith('\\\\?\\UNC\\'):
        return '\\\\' + value[8:]
    return value[4:] if value.startswith('\\\\?\\') else value


def physical_directory_path(path):
    """Resolve an existing directory, including a packaged app's virtual overlay."""
    with _os_error_stage('physical_directory_resolve'):
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, 'state path is not a directory')
    if os.name != 'nt':
        return str(path)
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    final = kernel.GetFinalPathNameByHandleW
    final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final.restype = wintypes.DWORD
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise _win32_error('physical_directory_open')
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = final(handle, buffer, len(buffer), 0)
        if not size:
            raise _win32_error('physical_directory_resolve')
        if size >= len(buffer):
            raise _win32_error('physical_directory_resolve', 122)
        value = buffer.value
    finally:
        close(handle)
    if value.startswith('\\\\?\\UNC\\'):
        return '\\\\' + value[8:]
    return value[4:] if value.startswith('\\\\?\\') else value


def resolve_state_directory(directory=None, *, create=False):
    from blackbox_core import default_state
    path = Path(directory if directory is not None else default_state()).expanduser().resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    # MSIX may redirect files without redirecting the directory handle. An
    # existing database is authoritative; keep its runtime files beside it.
    database = path / 'notes.sqlite3'
    if database.is_file() and not database.is_symlink():
        return Path(physical_file_path(database)).parent
    return Path(physical_directory_path(path)) if path.exists() else path


def normalized_file_bytes(path):
    """Build and installer must agree about harmless Windows newline conversion."""
    return Path(path).read_bytes().replace(b'\r\n', b'\n')


def release_version(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    try:
        version = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8-sig'))['version']
        if isinstance(version, str) and re.fullmatch(r'[A-Za-z0-9.+_-]{1,100}', version):
            return version
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return 'unknown'


def build_id(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    digest.update(release_version(root).encode('ascii'))
    for folder in ('scripts', 'assets'):
        for path in sorted((root / folder).glob('*')):
            if path.is_file() and path.suffix in ('.py', '.js', '.html', '.css'):
                digest.update((folder + '/' + path.name).encode())
                digest.update(normalized_file_bytes(path))
    return digest.hexdigest()


def runtime_metadata(store):
    return {'service': 'blackbox-review', 'protocol': 1, 'pid': os.getpid(),
            'build': build_id(), 'version': release_version(),
            'state_dir': str(resolve_state_directory(store.directory)),
            'capabilities': ['shutdown']}


@contextmanager
def startup_lock(directory):
    from blackbox_core import NotesError
    # OS-owned byte lock is released on process exit; no stale PID lock removal.
    with (Path(directory) / 'review-start.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + 15
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise NotesError('reviewer startup busy')
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def instance_endpoint(value):
    """Parse only our exact loopback URL; registry content is never a kill command."""
    if not isinstance(value, dict) or type(value.get('pid')) is not int or value['pid'] <= 0:
        return None
    url = value.get('url')
    match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})/#token=([A-Za-z0-9_-]{43})', url or '') if isinstance(url, str) else None
    if not match or not 1 <= int(match[1]) <= 65535:
        return None
    return int(match[1]), match[2]


def probe_instance(value, expected_state=None):
    endpoint = instance_endpoint(value)
    if endpoint is None:
        return None
    port, token = endpoint
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
    try:
        connection.request('GET', '/api/health', headers={'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        body = response.read(16385)
        if response.status != 200 or len(body) > 16384:
            return None
        data = json.loads(body)
        from blackbox_compat import LEGACY_SERVICE
        if (not isinstance(data, dict) or data.get('service') not in ('blackbox-review', LEGACY_SERVICE)
                or type(data.get('protocol')) is not int or data['protocol'] != 1
                or type(data.get('pid')) is not int or data['pid'] != value['pid']
                or not isinstance(data.get('build'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', data['build'])):
            return None
        if value.get('build') is not None and value['build'] != data['build']:
            return None
        if ('state_dir' in data and not isinstance(data['state_dir'], str)
                or 'capabilities' in data and not isinstance(data['capabilities'], list)):
            return None
        # Old services predate state metadata. The unguessable token and exact PID
        # still identify those services, but they cannot be force-stopped.
        for state in (value.get('state_dir'), expected_state):
            if state is not None and data.get('state_dir') is not None:
                if os.path.normcase(str(state)) != os.path.normcase(str(data['state_dir'])):
                    return None
        return data
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def probe_with_retry(value, expected_state=None):
    for attempt in range(3):
        data = probe_instance(value, expected_state)
        if data is not None:
            return data
        if instance_endpoint(value) is None:
            break
        if attempt < 2:
            time.sleep(0.1 * (attempt + 1))
    return None


def read_registry(registry):
    try:
        with Path(registry).open('rb') as stream:
            return json.loads(stream.read(16385))
    except (OSError, ValueError):
        return None


def write_registry(registry, value):
    fd, name = tempfile.mkstemp(prefix='.review-instance-', dir=Path(registry).parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
        os.replace(name, registry)
    finally:
        Path(name).unlink(missing_ok=True)


def request_shutdown(value, expected_state=None):
    data = probe_instance(value, expected_state)
    if data is None:
        return {'stopped': False, 'reason': 'not_running'}
    if 'shutdown' not in data.get('capabilities', []):
        return {'stopped': False, 'reason': 'shutdown_not_supported', 'runtime': data}
    port, token = instance_endpoint(value)
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('POST', '/api/shutdown', body=b'', headers={
            'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096:
            return {'stopped': False, 'reason': 'shutdown_rejected', 'runtime': data}
        result = json.loads(body)
        if not isinstance(result, dict) or result.get('stopping') is not True:
            return {'stopped': False, 'reason': 'shutdown_rejected', 'runtime': data}
    except (OSError, ValueError, http.client.HTTPException):
        return {'stopped': False, 'reason': 'shutdown_failed', 'runtime': data}
    finally:
        connection.close()
    # Acknowledgement precedes shutdown in the HTTP handler. Do not report the
    # service stopped until its authenticated endpoint is no longer available.
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if probe_instance(value, expected_state) is None:
            owned = _owned_children.get(value['pid'])
            if owned is not None and owned[1] == value['url']:
                try:
                    owned[0].wait(timeout=2)
                except subprocess.TimeoutExpired:
                    return {'stopped': False, 'reason': 'shutdown_pending', 'runtime': data}
                _owned_children.pop(value['pid'], None)
            return {'stopped': True, 'reason': 'stopped', 'runtime': data}
        time.sleep(0.05)
    return {'stopped': False, 'reason': 'shutdown_pending', 'runtime': data}


def stop_reviewer(store):
    registry = store.directory / 'review-instance.json'
    with startup_lock(store.directory):
        saved = read_registry(registry)
        result = request_shutdown(saved, str(resolve_state_directory(store.directory)))
        if result['stopped']:
            # The same lock protects publication by this launcher. Never remove
            # a changed registry written by another version or external tool.
            if read_registry(registry) == saved:
                registry.unlink(missing_ok=True)
        return result


def ensure_reviewer(store):
    from blackbox_core import NotesError
    registry = store.directory / 'review-instance.json'
    build = build_id()
    with startup_lock(store.directory):
        saved = read_registry(registry)
        state = str(resolve_state_directory(store.directory))
        current = probe_with_retry(saved, state)
        if current is not None:
            if current['build'] != build:
                error = NotesError('reviewer_build_changed')
                error.public_code = 'reviewer_build_changed'
                raise error
            return dict(current, url=saved['url'], reused=True)
        value = background(store, publish=lambda ready: write_registry(registry, ready))
        return dict(value, reused=False)


def background(store, publish=None):
    from blackbox_core import NotesError
    state = str(resolve_state_directory(store.directory))
    for pid, (process, _) in list(_owned_children.items()):
        if process.poll() is not None:
            _owned_children.pop(pid, None)
    fd, name = tempfile.mkstemp(prefix='.review-ready-', suffix='.json', dir=store.directory)
    os.close(fd)
    ready = Path(name)
    # Windows venv python.exe is a redirector with a different PID from its child.
    # This stdlib-only service can use the underlying interpreter directly.
    executable = getattr(sys, '_base_executable', sys.executable) if os.name == 'nt' else sys.executable
    args = [executable, str(Path(__file__).with_name('blackbox.py')),
            '--state-dir', state, 'serve', '--ready-file', str(ready)]
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == 'nt':
        options['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    child, value = None, None
    try:
        child = subprocess.Popen(args, **options)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise NotesError('reviewer failed to start')
            try:
                candidate = json.loads(ready.read_text(encoding='utf-8'))
                if isinstance(candidate, dict) and candidate.get('pid') == child.pid and instance_endpoint(candidate):
                    value = candidate
                    metadata = probe_with_retry(value, state)
                    if metadata is None or metadata['build'] != build_id():
                        raise NotesError('reviewer health check failed')
                    value = dict(metadata, url=value['url'])
                    if publish is not None:
                        publish(value)
                    _owned_children[child.pid] = (child, value['url'])
                    return value
            except (ValueError, OSError):
                if value is not None:
                    raise
            time.sleep(0.05)
        raise NotesError('reviewer readiness timeout')
    except BaseException:
        if child and child.poll() is None:
            try:
                if value is not None and value.get('pid') == child.pid:
                    request_shutdown(value, state)
            finally:
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    # This is our still-owned Popen child, never an arbitrary registry
                    # PID. Termination is the last resort for a broken startup.
                    child.terminate()
                    child.wait(timeout=5)
        raise
    finally:
        ready.unlink(missing_ok=True)
