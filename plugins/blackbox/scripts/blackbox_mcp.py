"""Local MCP reader. Data stays in app-only metadata; mutations require explicit scoped actions."""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

# Also works with python -I (do not import from the working directory).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blackbox_core import Store, NotesError
from blackbox_launcher import resolve_state_directory, runtime_metadata, release_version, ensure_reviewer, instance_endpoint
from blackbox_web import ASSETS, ReaderSelection
from blackbox_codex import SessionMetadata, ProjectCatalog, session_labels, navigation_contexts
from blackbox_anomalies import listing, saved_sessions, detail, RULE_VERSION

URI = 'ui://blackbox/reader-v23.html'
# Published resources share the same bridge contract. Keep earlier references
# usable when host entrypoint discovery and the reader restart at different times.
RESOURCE_URIS = (URI, 'ui://blackbox/reader-v22.html', 'ui://blackbox/reader-v21.html', 'ui://blackbox/reader-v20.html', 'ui://blackbox/reader-v19.html', 'ui://blackbox/reader-v18.html', 'ui://blackbox/reader-v17.html', 'ui://blackbox/reader-v16.html', 'ui://blackbox/reader-v15.html', 'ui://blackbox/reader-v14.html', 'ui://blackbox/reader-v13.html', 'ui://blackbox/reader-v12.html', 'ui://blackbox/reader-v11.html', 'ui://blackbox/reader-v10.html', 'ui://blackbox/reader-v9.html', 'ui://blackbox/reader-v8.html', 'ui://blackbox/reader-v7.html', 'ui://blackbox/reader-v6.html', 'ui://blackbox/reader-v5.html', 'ui://blackbox/reader-v4.html', 'ui://blackbox/reader-v3.html', 'ui://blackbox/reader-v2.html', 'ui://blackbox/reader-v1.html')
MIME = 'text/html;profile=mcp-app'
LIMIT = 65536
MAX_EXPORT_COPIES = 100


def export_error(code):
    error = NotesError(code)
    error.public_code = code
    return error


def downloads_directory():
    """Resolve Windows' actual Downloads known folder, including redirection."""
    if sys.platform != 'win32':
        raise export_error('downloads_unavailable')
    class GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                    ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]
    pointer = ctypes.c_wchar_p()
    free = None
    try:
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        ole = ctypes.WinDLL('ole32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get = shell.SHGetKnownFolderPath
        get.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_wchar_p)]
        get.restype = ctypes.c_long
        free = ole.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        drive_type = kernel.GetDriveTypeW
        drive_type.argtypes = [ctypes.c_wchar_p]
        drive_type.restype = ctypes.c_uint32
        identifier = GUID.from_buffer_copy(uuid.UUID('374DE290-123F-4565-9164-39C4925E467B').bytes_le)
        result = get(ctypes.byref(identifier), 0, None, ctypes.byref(pointer))
        if result != 0 or not pointer.value:
            raise export_error('downloads_unavailable')
        folder = Path(pointer.value)
        def require_local(path):
            # Known folders may point to UNC shares or mapped network drives.
            # Accept only removable, fixed and RAM disks, never remote/unknown.
            if (not path.is_absolute() or str(path).replace('/', '\\').startswith('\\\\')
                    or drive_type(path.anchor) not in (2, 3, 6)):
                raise export_error('downloads_unavailable')
        require_local(folder)
        if not folder.is_dir():
            raise export_error('downloads_unavailable')
        resolved = folder.resolve(strict=True)
        require_local(resolved)
        return resolved
    except (OSError, ValueError, AttributeError, TypeError):
        raise export_error('downloads_unavailable') from None
    finally:
        if pointer and free is not None:
            free(ctypes.cast(pointer, ctypes.c_void_p))


def save_export_file(directory, stem, body, digest):
    """Create a new file only; existing files and links are never opened."""
    for copy in range(MAX_EXPORT_COPIES):
        name = stem + (f' ({copy})' if copy else '') + '.md'
        target = directory / name
        try:
            stream = target.open('x+b')
        except FileExistsError:
            continue
        except OSError:
            raise export_error('export_save_failed') from None
        try:
            with stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError('not a regular export file')
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(0)
                actual = stream.read(len(body) + 1)
                if len(actual) != len(body) or hashlib.sha256(actual).hexdigest() != digest:
                    raise OSError('export verification failed')
        except (OSError, ValueError):
            # A failed write may leave this newly created file. Do not unlink a
            # path that another process could have replaced, or claim success.
            raise export_error('export_save_failed') from None
        return {'path': str(target), 'name': name, 'sha256': digest, 'bytes': len(body)}
    raise export_error('export_name_exhausted')


def tool(name, title, properties, required=(), readonly=True, destructive=False):
    return {'name': name, 'title': title, 'description': title,
            'inputSchema': {'type': 'object', 'properties': properties,
                            'required': list(required), 'additionalProperties': False},
            'annotations': {'readOnlyHint': readonly, 'destructiveHint': destructive, 'openWorldHint': False},
            '_meta': {'ui': {'visibility': ['app']}}}


OPEN = tool('open_reader', 'Blackbox 执行记录', {})
OPEN['_meta']['ui']['resourceUri'] = URI
OPEN['_meta']['openai/ui'] = {'entrypoints': [{'type': 'global'}, {'type': 'thread'}]}
TOOLS = [OPEN,
         tool('cleanup', 'Preview or confirm local record cleanup', {'operation': {'type': 'string', 'enum': ['preview','apply','retry']}, 'value': {'type': 'object'}}, ['operation','value'], False, True),
         tool('manage_project', 'Change capture for a confirmed registered project',
              {'path': {'type': 'string', 'maxLength': 4096}, 'enabled': {'type': 'boolean'},
               'expected_enabled': {'type': 'boolean'}, 'accept_capture_scope': {'type': 'boolean'}},
              ['path', 'enabled', 'expected_enabled', 'accept_capture_scope'], False),
         tool('manage_session', 'Archive, restore or delete confirmed Blackbox session records',
              {'investigation': {'type': 'string', 'maxLength': 200},
               'action': {'type': 'string', 'enum': ['archive', 'restore', 'delete']},
               'snapshot': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}},
              ['investigation', 'action', 'snapshot'], False, True),
         tool('read_view', 'Read a local Blackbox view', {'path': {'type': 'string', 'maxLength': 8192}}, ['path']),
         tool('select_session', 'Remember the selected Blackbox session',
              {'investigation': {'type': 'string', 'maxLength': 512}}, ['investigation'], False),
         tool('open_browser_export', 'Open the saved Blackbox snapshot in the local browser reader',
              {'record_id': {'type': 'string', 'maxLength': 64},
               'through': {'type': 'integer', 'minimum': 1},
               'rule': {'type': 'string', 'maxLength': 128}}, ['record_id', 'through', 'rule'], False),
         tool('save_export', 'Save the previewed Blackbox snapshot to Downloads',
              {'record_id': {'type': 'string', 'maxLength': 64},
               'through': {'type': 'integer', 'minimum': 1},
               'rule': {'type': 'string', 'maxLength': 128},
               'preview_sha256': {'type': 'string', 'minLength': 64, 'maxLength': 64, 'pattern': '^[a-f0-9]{64}$'}},
              ['record_id', 'through', 'rule', 'preview_sha256'], False)]


def resource_html():
    html = (ASSETS / 'anomalies.html').read_text(encoding='utf-8')
    html = html.replace('<link rel="stylesheet" href="./review.css"><script src="./anomalies.js" defer></script>',
                        '<style>' + (ASSETS / 'review.css').read_text(encoding='utf-8') + '</style>')
    html = html.replace('<button id="shutdown" hidden>', '<button id="shutdown" hidden disabled>')
    html = html.replace('停止阅读服务不会停用项目采集。', '关闭此面板不会停用项目采集。')
    scripts = '\n'.join((ASSETS / name).read_text(encoding='utf-8') for name in ('mcp-reader.js', 'anomalies.js'))
    scripts = scripts.replace('__BLACKBOX_VERSION__', release_version())
    return html.replace('</body>', '<script>' + scripts.replace('</script', '<\\/script') + '</script></body>')


class Reader:
    def __init__(self, directory=None):
        self.directory = directory
        self.names = SessionMetadata()
        self.projects = ProjectCatalog()

    def read(self, path):
        if not isinstance(path, str) or len(path) > 8192:
            raise NotesError('invalid path')
        url = urlsplit(path)
        if url.scheme or url.netloc or url.fragment or '%' in url.path:
            raise NotesError('invalid path')
        query = parse_qs(url.query, keep_blank_values=True, max_num_fields=20)
        routes = {
            '/api/status': set(), '/api/reader-selection': set(),
            '/api/session-management': {'investigation'}, '/api/anomaly-types': {'investigation', 'through'},
            '/api/anomaly-contexts': {'project_path'}, '/api/session-names': {'investigation'},
            '/api/anomalies': {'project', 'tool', 'start', 'end', 'before', 'through', 'project_path', 'investigation', 'anomaly_type'},
        }
        is_detail = url.path.startswith(('/api/anomaly/', '/api/anomaly-export/'))
        allowed = {'through', 'rule'} if is_detail else routes.get(url.path)
        if allowed is None or set(query) - allowed or (url.path != '/api/session-names' and any(len(v) != 1 for v in query.values())):
            raise NotesError('invalid view')
        store = Store(resolve_state_directory(self.directory), read_only=True)
        one = {key: value[0] for key, value in query.items()}
        if url.path == '/api/status':
            value = store.status()
            runtime = runtime_metadata(store)
            value['application'] = dict(runtime, capabilities=[], state_directory=runtime['state_dir'])
            return value
        if url.path == '/api/session-management':
            from blackbox_sessions import inspect_session
            return inspect_session(store, one.get('investigation', ''))
        if url.path == '/api/anomaly-types':
            from blackbox_anomalies import anomaly_types
            return anomaly_types(store, one.get('investigation', ''), one.get('through'))
        if url.path == '/api/anomaly-contexts':
            return navigation_contexts(store, one.get('project_path', ''), self.projects)
        if url.path == '/api/reader-selection':
            return ReaderSelection(store).get()
        if url.path == '/api/session-names':
            if set(query) != {'investigation'}:
                raise NotesError('invalid session names')
            saved = saved_sessions(store, query['investigation'])
            return session_labels(self.names, {key: value['session'] for key, value in saved.items()})
        if url.path == '/api/anomalies':
            return listing(store, **one)
        if one.get('rule', RULE_VERSION) != RULE_VERSION:
            raise NotesError('rule changed')
        identifier = url.path.split('/')[3:]
        if len(identifier) != 1 or not identifier[0]:
            raise NotesError('invalid identifier')
        result = detail(store, identifier[0], one.get('through'))
        return result['markdown'] if url.path.startswith('/api/anomaly-export/') else result

    def export_snapshot(self, args):
        identifier, through, rule = args['record_id'], args['through'], args['rule']
        if (not isinstance(identifier, str) or not re.fullmatch(r'[1-9][0-9]{0,63}', identifier)
                or type(through) is not int or not 0 < through <= 2**63 - 1
                or not isinstance(rule, str) or not 0 < len(rule) <= 128 or rule != RULE_VERSION):
            raise NotesError('invalid snapshot request')
        store = Store(resolve_state_directory(self.directory), read_only=True)
        # Resolve the exact saved anomaly before starting any service. This also
        # rejects nonexistent records and cutoffs beyond the saved event boundary.
        snapshot = detail(store, identifier, through)
        if snapshot['source_seq'] != through or snapshot['rule_version'] != rule:
            raise NotesError('snapshot changed')
        return store, snapshot

    def save_export(self, args):
        expected = args['preview_sha256']
        if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
            raise NotesError('invalid preview hash')
        _, snapshot = self.export_snapshot(args)
        body = snapshot['markdown'].encode('utf-8')
        digest = hashlib.sha256(body).hexdigest()
        if digest != expected:
            raise export_error('preview_mismatch')
        directory = downloads_directory()
        return save_export_file(directory, f"anomaly-{snapshot['id']}-through-{snapshot['source_seq']}", body, digest)

    def open_browser_export(self, args):
        store, snapshot = self.export_snapshot(args)
        try:
            instance = ensure_reviewer(store)
            if instance_endpoint(instance) is None:
                raise NotesError('reviewer unavailable')
        except (NotesError, OSError, sqlite3.Error) as error:
            code = ('reviewer_build_changed' if getattr(error, 'public_code', None) == 'reviewer_build_changed'
                    else 'reviewer_unavailable')
            failure = NotesError(code)
            failure.public_code = code
            raise failure from None
        # The launcher validates an exact authenticated loopback URL. Preserve
        # its token in the fragment; no event content or file path is accepted.
        return {'url': instance['url'] + '&' + urlencode({
            'record': snapshot['id'], 'through': snapshot['source_seq'], 'preview': 1})}

    def call(self, name, args):
        if not isinstance(args, dict):
            raise NotesError('invalid arguments')
        if name == 'open_reader' and not args:
            return {'content': [{'type': 'text', 'text': 'Blackbox local reader opened.'}]}
        try:
            if name == 'read_view' and set(args) == {'path'}:
                value = self.read(args['path'])
            elif name == 'cleanup':
                from blackbox_cleanup import dispatch
                value = dispatch(Store(resolve_state_directory(self.directory)), args)
            elif name == 'manage_project':
                value = Store(resolve_state_directory(self.directory)).manage_project(args)
            elif name == 'manage_session':
                from blackbox_sessions import manage_session
                store = Store(resolve_state_directory(self.directory))
                value = manage_session(store, args)
            elif name == 'select_session' and set(args) == {'investigation'}:
                store = Store(resolve_state_directory(self.directory), read_only=True)
                value = ReaderSelection(store).set(args)
            elif name == 'open_browser_export' and set(args) == {'record_id', 'through', 'rule'}:
                value = self.open_browser_export(args)
            elif name == 'save_export' and set(args) == {'record_id', 'through', 'rule', 'preview_sha256'}:
                value = self.save_export(args)
            else:
                raise NotesError('invalid arguments')
            status = 200
        except NotesError as error:
            code = getattr(error, 'public_code', None) if name in ('open_browser_export', 'save_export') else None
            if code in ('reviewer_build_changed', 'reviewer_unavailable'):
                status, value = (409 if code == 'reviewer_build_changed' else 503), {'error': code}
            elif code in ('preview_mismatch', 'downloads_unavailable', 'export_save_failed', 'export_name_exhausted'):
                status, value = (409 if code == 'preview_mismatch' else 503), {'error': code}
            else:
                status, value = 400, {'error': 'invalid_request'}
        except (ValueError, TypeError):
            status, value = 400, {'error': 'invalid_request'}
        except (OSError, sqlite3.Error):
            status, value = 503, {'error': 'local_store_unavailable'}
        # Never put record content, identifiers, paths or titles in model-visible fields.
        metadata = {'status': status, 'body': value}
        if name == 'read_view' and status == 200 and isinstance(value, str):
            metadata['preview_sha256'] = hashlib.sha256(value.encode('utf-8')).hexdigest()
        return {'content': [], '_meta': {'blackbox': metadata}}

    def dispatch(self, method, params):
        if method == 'initialize':
            requested = params.get('protocolVersion')
            version = requested if requested in ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25') else '2025-11-25'
            return {'protocolVersion': version, 'capabilities': {'tools': {}, 'resources': {}},
                    'serverInfo': {'name': 'blackbox-reader', 'version': release_version()}}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': TOOLS}
        if method == 'resources/list':
            return {'resources': [{'uri': URI, 'name': 'Blackbox reader', 'mimeType': MIME}]}
        if method == 'resources/templates/list':
            return {'resourceTemplates': []}
        if method == 'resources/read':
            requested_uri = params.get('uri')
            if not isinstance(requested_uri, str) or requested_uri not in RESOURCE_URIS:
                raise NotesError('invalid resource')
            return {'contents': [{'uri': requested_uri, 'mimeType': MIME, 'text': resource_html(),
                    '_meta': {'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}}}]}
        if method == 'tools/call':
            return self.call(params.get('name'), params.get('arguments', {}))
        raise LookupError('unknown method')

    def handle(self, message):
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
        if 'id' not in message:
            return None
        reply = {'jsonrpc': '2.0', 'id': message['id']}
        try:
            params = message.get('params', {})
            if not isinstance(params, dict):
                raise NotesError('invalid params')
            reply['result'] = self.dispatch(message['method'], params)
        except (ValueError, TypeError):
            reply['error'] = {'code': -32602, 'message': 'Invalid params'}
        except LookupError:
            reply['error'] = {'code': -32601, 'message': 'Method not found'}
        except (OSError, sqlite3.Error):
            reply['error'] = {'code': -32603, 'message': 'Local reader unavailable'}
        return reply


def main():
    reader = Reader()
    try:
        while True:
            line = sys.stdin.buffer.readline(LIMIT + 1)
            if not line:
                return 0
            if len(line) > LIMIT:
                return 1
            try:
                reply = reader.handle(json.loads(line))
            except (ValueError, UnicodeError, RecursionError):
                reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
            if reply is not None:
                print(json.dumps(reply, ensure_ascii=True), flush=True)
    finally:
        reader.names.close()
        reader.projects.close()


if __name__ == '__main__':
    raise SystemExit(main())
