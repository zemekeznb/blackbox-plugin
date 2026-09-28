"""Detached local reviewer with bounded readiness handshake, no autostart."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


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
