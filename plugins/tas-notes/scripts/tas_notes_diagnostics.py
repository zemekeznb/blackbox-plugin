"""Bounded, content-free configuration diagnostics; never execute runtime.cmd."""
from datetime import datetime
import json
from pathlib import Path
import re


def read_small(path):
    if path.is_symlink():
        raise ValueError('symlink')
    with path.open('rb') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError('oversized')
    return raw


def runtime_status(directory):
    try:
        raw = read_small(Path(directory) / 'runtime.cmd').decode('ascii')
        match = re.fullmatch(r'@"([^"\r\n%!^&|<>]+)" %\*\r?\n?', raw)
        if not match or not Path(match[1]).is_absolute():
            return {'state': 'invalid'}
        # Existence is not an execution test, version check or hook-health check.
        return {'state': 'configured' if Path(match[1]).is_file() else 'target_missing'}
    except FileNotFoundError:
        return {'state': 'missing'}
    except (OSError, ValueError):
        return {'state': 'invalid'}


def last_error(directory):
    try:
        value = json.loads(read_small(Path(directory) / 'last-error.json'))
        if not isinstance(value, dict):
            raise ValueError('invalid marker')
        known = {'NotesError', 'Conflict', 'JSONDecodeError', 'UnicodeDecodeError',
                 'ValueError', 'RecursionError', 'OperationalError', 'IntegrityError',
                 'DatabaseError', 'OSError', 'PermissionError', 'FileNotFoundError',
                 'LauncherNotConfigured', 'LauncherRuntimeFailed'}
        code = value.get('code')
        if not isinstance(code, str) or code not in known:
            code = 'UnknownCaptureError'
        stamp = value.get('time')
        try:
            parsed = datetime.fromisoformat(stamp) if isinstance(stamp, str) and len(stamp) <= 40 else None
            stamp = parsed.isoformat() if parsed and parsed.tzinfo else None
        except ValueError:
            stamp = None
        return {'state': 'recorded', 'code': code, 'time': stamp, 'scope': 'unknown_project'}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        return {'state': 'unreadable', 'code': 'InvalidDiagnosticMarker', 'time': None,
                'scope': 'unknown_project'}
