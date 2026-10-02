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
from contextlib import contextmanager


def build_id():
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for folder in ('scripts', 'assets'):
        for path in sorted((root / folder).glob('*')):
            if path.is_file() and path.suffix in ('.py', '.js', '.html', '.css'):
                digest.update(path.name.encode())
                digest.update(path.read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()


@contextmanager
def startup_lock(directory):
    from tas_notes_core import NotesError
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


def healthy(value, build):
    if not isinstance(value, dict) or type(value.get('pid')) is not int or value['pid'] <= 0:
        return False
    url = value.get('url')
    match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})/#token=([A-Za-z0-9_-]{43})', url or '') if isinstance(url, str) else None
    if not match or not 1 <= int(match[1]) <= 65535:
        return False
    connection = http.client.HTTPConnection('127.0.0.1', int(match[1]), timeout=1)
    try:
        connection.request('GET', '/api/health', headers={'Authorization': 'Bearer ' + match[2]})
        response = connection.getresponse()
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096:
            return False
        data = json.loads(body)
        return data == {'service': 'tas-notes-review', 'protocol': 1, 'pid': value['pid'], 'build': build}
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def ensure_reviewer(store):
    from tas_notes_core import NotesError
    registry = store.directory / 'review-instance.json'
    build = build_id()
    with startup_lock(store.directory):
        try:
            with registry.open('rb') as stream:
                saved = json.loads(stream.read(4097))
        except (OSError, ValueError):
            saved = None
        if healthy(saved, build):
            return dict(saved, reused=True)
        value = background(store)
        if not healthy(value, build):
            raise NotesError('reviewer health check failed')
        fd, name = tempfile.mkstemp(prefix='.review-instance-', dir=store.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream)
            os.replace(name, registry)
        finally:
            Path(name).unlink(missing_ok=True)
        return dict(value, reused=False)


def background(store):
    from tas_notes_core import NotesError
    fd, name = tempfile.mkstemp(prefix='.review-ready-', suffix='.json', dir=store.directory)
    os.close(fd)
    ready = Path(name)
    # Windows venv python.exe is a redirector with a different PID from its child.
    # This stdlib-only service can use the underlying interpreter directly.
    executable = getattr(sys, '_base_executable', sys.executable) if os.name == 'nt' else sys.executable
    args = [executable, str(Path(__file__).with_name('tas_notes.py')),
            '--state-dir', str(store.directory), 'serve', '--ready-file', str(ready)]
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == 'nt':
        options['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    child = None
    try:
        child = subprocess.Popen(args, **options)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise NotesError('reviewer failed to start')
            try:
                value = json.loads(ready.read_text(encoding='utf-8'))
                if value.get('pid') == child.pid and isinstance(value.get('url'), str):
                    return value
            except (ValueError, OSError):
                pass
            time.sleep(0.05)
        raise NotesError('reviewer readiness timeout')
    except BaseException:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        raise
    finally:
        ready.unlink(missing_ok=True)
