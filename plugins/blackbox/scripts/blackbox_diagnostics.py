"""Bounded, content-free configuration diagnostics; never execute runtime.cmd."""
from datetime import datetime
import hashlib
import json
import os
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
        from blackbox_runtime import read_binding
        binding = read_binding(require_python=False)
        if binding is not None:
            if not os.path.samefile(directory, binding['state_directory']):
                return {'state': 'invalid', 'source': 'host_binding', 'reason': 'state_mismatch'}
            return {'state': 'configured' if Path(binding['python']).is_file() else 'target_missing',
                    'source': 'host_binding'}
    except (OSError, ValueError):
        return {'state': 'invalid', 'source': 'host_binding'}
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


def marker_name(project=None, session=None):
    if session and project:
        return 'capture-session-' + hashlib.sha256(json.dumps([project, session]).encode('utf-8')).hexdigest() + '.json'
    return ('capture-' + hashlib.sha256(project.encode('utf-8')).hexdigest() + '.json') if project else 'last-error.json'


def read_error(path, scope):
    try:
        value = json.loads(read_small(path))
        if not isinstance(value, dict):
            raise ValueError('invalid marker')
        known = {'NotesError', 'Conflict', 'JSONDecodeError', 'UnicodeDecodeError',
                 'ValueError', 'RecursionError', 'OperationalError', 'IntegrityError',
                 'DatabaseError', 'OSError', 'PermissionError', 'FileNotFoundError',
                 'SessionIdentityUnconfirmed', 'LauncherNotConfigured', 'LauncherRuntimeFailed', 'InputTooLarge',
                 'DatabaseCapacityReached', 'SessionCapacityReached',
                 'UnknownCaptureError', 'InvalidDiagnosticMarker', 'RestoreContinuityUnknown'}
        code = value.get('code')
        if not isinstance(code, str) or code not in known:
            code = 'UnknownCaptureError'
        stamp = value.get('time')
        try:
            parsed = datetime.fromisoformat(stamp) if isinstance(stamp, str) and len(stamp) <= 40 else None
            stamp = parsed.isoformat() if parsed and parsed.tzinfo else None
        except ValueError:
            stamp = None
        stored_scope = value.get('scope', scope)
        return {'state': 'recorded', 'code': code, 'time': stamp,
                'scope': stored_scope if stored_scope in ('project', 'session', 'unknown_project') else scope}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        return {'state': 'unreadable', 'code': 'InvalidDiagnosticMarker', 'time': None,
                'scope': 'unknown_project'}


def last_error(directory, project=None, session=None):
    return read_error(Path(directory) / marker_name(project, session),
                      'session' if project and session else 'project' if project else 'unknown_project')


def unscoped_error(directory):
    """Read a bounded rejection epoch, with conservative pre-NOTES-026 fallback.

    Old last-error markers have no scope: they cannot establish which project
    lost an event. New scoped summary markers must not become global barriers.
    The epoch is a digest of metadata only, never a rejected Hook body.
    """
    candidates = []
    for name in ('capture-unknown.json', 'last-error.json'):
        path = Path(directory) / name
        marker = read_error(path, 'unknown_project')
        if marker is None:
            continue
        if marker['scope'] != 'unknown_project':
            if name == 'last-error.json':
                continue
            marker = {'state': 'unreadable', 'code': 'InvalidDiagnosticMarker',
                      'time': None, 'scope': 'unknown_project'}
        if marker['code'] == 'InvalidDiagnosticMarker':
            marker['state'] = 'unreadable'
        try:
            raw = read_small(path)
            value = json.loads(raw)
            identity = value.get('epoch', value.get('legacy_epoch')) if isinstance(value, dict) else None
            epoch = identity if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{32}|[a-f0-9]{64}', identity) else hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError, RecursionError):
            epoch = 'unreadable'
        candidates.append(dict(marker, epoch=epoch, legacy=name == 'last-error.json'))
    if len(candidates) == 2 and candidates[0]['epoch'] != candidates[1]['epoch']:
        # One atomic marker may persist while the other fails. Do not let an old
        # dedicated file hide a newer unknown rejection visible in the summary.
        epoch = hashlib.sha256(json.dumps([row['epoch'] for row in candidates]).encode()).hexdigest()
        return dict(candidates[1], state='unreadable', epoch=epoch, legacy=False,
                    inconsistent=candidates[0]['state'] != 'unreadable')
    return candidates[0] if candidates else None
