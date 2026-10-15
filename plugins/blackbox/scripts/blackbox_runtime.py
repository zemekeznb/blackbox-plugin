"""Explicit host runtime binding; never discover another interpreter or data store."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import stat
import sys
import tempfile
import uuid

MAX_BINDING_BYTES = 16384


class RuntimeBindingError(ValueError):
    """A bounded public error; configuration values never become error messages."""

    def __init__(self, code, source=None):
        super().__init__(code)
        self.public_code = code
        if source is not None:
            self.errno = getattr(source, 'errno', None)
            self.winerror = getattr(source, 'winerror', None)


def binding_path():
    home = os.environ.get('CODEX_HOME')
    if home is None:
        profile = os.environ.get('USERPROFILE') or str(Path.home())
        home = str(Path(profile) / '.codex')
    base = _absolute(home)
    return base / 'blackbox' / 'runtime.json'


def _absolute(value):
    if (not isinstance(value, str) or not value or len(value) > 32760
            or any(ord(character) < 32 for character in value)):
        raise RuntimeBindingError('runtime_binding_invalid')
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeBindingError('runtime_binding_invalid')
    # A runtime binding is local. Device paths and network shares are not launch
    # targets; JSON string values are data, including Unicode and shell symbols.
    if os.name == 'nt' and (value.replace('/', '\\').startswith('\\\\')
                            or len(path.drive) != 2 or path.drive[1:] != ':'
                            or any(character in value[2:] for character in '<>:"|?*')):
        raise RuntimeBindingError('runtime_binding_invalid')
    if '..' in path.parts or '.' in path.parts:
        raise RuntimeBindingError('runtime_binding_invalid')
    return path


def _existing(value, *, directory=False):
    path = _absolute(value)
    code = 'runtime_state_unavailable' if directory else 'runtime_unavailable'
    try:
        mode = path.stat().st_mode
        valid = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
        if not valid or (not directory and os.name == 'nt' and path.suffix.lower() != '.exe'):
            raise RuntimeBindingError(code)
    except OSError as error:
        raise RuntimeBindingError(code, error) from None
    return path


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeBindingError('runtime_binding_invalid')
        value[key] = item
    return value


def _validate(value, *, require_python=True):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'python', 'state_directory'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise RuntimeBindingError('runtime_binding_invalid')
    executable = _absolute(value['python'])
    if os.name == 'nt' and executable.suffix.lower() != '.exe':
        raise RuntimeBindingError('runtime_binding_invalid')
    if require_python:
        _existing(value['python'])
    directory = _existing(value['state_directory'], directory=True)
    try:
        database = (directory / 'notes.sqlite3').lstat()
        if not stat.S_ISREG(database.st_mode) or getattr(database, 'st_file_attributes', 0) & 0x400:
            raise RuntimeBindingError('runtime_state_unavailable')
    except OSError as error:
        raise RuntimeBindingError('runtime_state_unavailable', error) from None
    return value


def _binding_absent(target):
    """Windows can report path-not-found for a bad ancestor; inspect that case."""
    candidate = target
    while True:
        try:
            candidate.lstat()
        except FileNotFoundError:
            if candidate == candidate.parent:
                raise RuntimeBindingError('runtime_binding_unreadable')
            candidate = candidate.parent
            continue
        if candidate == target or not candidate.is_dir():
            raise RuntimeBindingError('runtime_binding_invalid')
        return True


def read_binding(*, require_python=True):
    """Return None only when the binding file is absent, never on an access error."""
    target = binding_path()
    try:
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise RuntimeBindingError('runtime_binding_invalid')
        with target.open('rb') as stream:
            raw = stream.read(MAX_BINDING_BYTES + 1)
    except FileNotFoundError:
        try:
            if _binding_absent(target):
                return None
        except OSError as error:
            raise RuntimeBindingError('runtime_binding_unreadable', error) from None
    except OSError as error:
        raise RuntimeBindingError('runtime_binding_unreadable', error) from None
    if len(raw) > MAX_BINDING_BYTES:
        raise RuntimeBindingError('runtime_binding_invalid')
    try:
        return _validate(json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object), require_python=require_python)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise RuntimeBindingError('runtime_binding_invalid') from None


def configured_state():
    value = read_binding(require_python=False)
    return Path(value['state_directory']) if value is not None else None


@contextmanager
def _binding_lock(parent):
    """Serialize explicit setup; the OS releases this lock if setup exits early."""
    with (parent / 'runtime.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeBindingError('runtime_binding_busy', error) from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def write_binding(state, python):
    """Persist an explicitly selected physical store; never silently switch it."""
    value = _validate({'schema_version': 1, 'python': str(python), 'state_directory': str(state)})
    body = (json.dumps(value, ensure_ascii=True, indent=2) + '\n').encode('utf-8')
    if len(body) > MAX_BINDING_BYTES:
        raise RuntimeBindingError('runtime_binding_invalid')
    target = binding_path()
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _binding_lock(target.parent):
            # A stale interpreter can be repaired, but only for the same store.
            previous = read_binding(require_python=False)
            if previous is not None and not os.path.samefile(previous['state_directory'], value['state_directory']):
                raise RuntimeBindingError('runtime_binding_conflict')
            descriptor, name = tempfile.mkstemp(prefix='.runtime-', suffix='.json', dir=target.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
    except OSError as error:
        raise RuntimeBindingError('runtime_binding_write_failed', error) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return value


def _mark_hook_failure(directory):
    temporary = None
    try:
        directory = _existing(str(directory), directory=True)
        database = (directory / 'notes.sqlite3').lstat()
        if not stat.S_ISREG(database.st_mode) or getattr(database, 'st_file_attributes', 0) & 0x400:
            return
        target = directory / 'last-error.json'
        try:
            existing = target.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existing.st_mode) or getattr(existing, 'st_file_attributes', 0) & 0x400:
                return
        marker = {'code': 'LauncherRuntimeFailed', 'scope': 'unknown_project',
                  'time': datetime.now(timezone.utc).isoformat(), 'epoch': uuid.uuid4().hex}
        descriptor, name = tempfile.mkstemp(prefix='.launcher-error-', suffix='.tmp', dir=directory)
        temporary = Path(name)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(marker, stream)
        os.replace(temporary, target)
        temporary = None
    except (OSError, RuntimeBindingError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    marker_state = None
    try:
        if arguments not in (['mcp'], ['hook']):
            raise RuntimeBindingError('runtime_launch_mode_invalid')
        value = read_binding(require_python=False)
        if value is None:
            raise RuntimeBindingError('runtime_not_configured')
        # Do not silently override a caller's explicit state choice or use a
        # different store in an automatic host process.
        override = os.environ.get('BLACKBOX_STATE_DIR') or os.environ.get('TAS_NOTES_STATE_DIR')
        if override:
            try:
                matches = os.path.samefile(_absolute(override), value['state_directory'])
            except OSError:
                matches = False
            if not matches:
                raise RuntimeBindingError('runtime_state_conflict')
        if arguments[0] == 'hook':
            marker_state = Path(value['state_directory'])
        _existing(value['python'])
        if not os.path.samefile(sys.executable, value['python']):
            raise RuntimeBindingError('runtime_interpreter_mismatch')
        os.environ['BLACKBOX_STATE_DIR'] = value['state_directory']
        scripts = Path(__file__).resolve().parent
        sys.path.insert(0, str(scripts))
        script = scripts / ('blackbox_mcp.py' if arguments[0] == 'mcp' else 'blackbox.py')
        sys.argv = [str(script)] + ([] if arguments[0] == 'mcp' else ['hook'])
        runpy.run_path(str(script), run_name='__main__')
        return 0
    except RuntimeBindingError as error:
        if marker_state is not None:
            _mark_hook_failure(marker_state)
        print('Blackbox Plugin: ' + error.public_code + '; host runtime unavailable.', file=sys.stderr)
        return 1
    except (OSError, ImportError):
        if marker_state is not None:
            _mark_hook_failure(marker_state)
        print('Blackbox Plugin: runtime_launch_failed; host runtime unavailable.', file=sys.stderr)
        return 1
    except SystemExit as error:
        if error.code not in (None, 0) and marker_state is not None:
            _mark_hook_failure(marker_state)
        raise


if __name__ == '__main__':
    raise SystemExit(main())
