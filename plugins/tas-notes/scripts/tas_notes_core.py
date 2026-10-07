"""Opt-in local event storage and read-only historical archives."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from uuid import uuid4

MAX_INPUT = 512 * 1024
MAX_FIELD = 32 * 1024
MAX_SESSION_BYTES = 10 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
ERROR_RESERVE_BYTES = 512 * 1024
ERROR_RESERVE_EVENTS = 128
EVENTS = frozenset(('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'Stop',
                   'PreCompact', 'PostCompact', 'SessionEnd', 'Interrupt'))
FIELDS = ('prompt', 'last_assistant_message', 'tool_name', 'tool_input',
          'tool_response', 'tool_result', 'error', 'reason', 'source', 'message',
          'turn_id', 'tool_use_id', 'call_id', 'event_id', 'hook_event_id',
          'exit_code', 'exitCode', 'returncode', 'status', 'success', 'isError')
SENSITIVE = re.compile(r'authorization|password|passwd|secret|credential|api[_-]?key|access[_-]?token', re.I)


class NotesError(ValueError):
    pass


class Conflict(NotesError):
    pass


class CaptureLimit(NotesError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def text(value, field, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise NotesError(f'invalid {field}')
    return value


def project_path(value, require_exists=True):
    path = Path(text(value, 'project', 4096)).expanduser()
    if not path.is_absolute() or (require_exists and not path.is_dir()):
        raise NotesError('project must be an existing absolute directory')
    return str(path.resolve())


def clean(value, depth=0):
    if depth > 12:
        raise NotesError('event nesting exceeds limit')
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if SENSITIVE.search(str(k)) else clean(v, depth + 1)
                for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v, depth + 1) for v in value]
    if isinstance(value, str):
        return re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}', 'Bearer [REDACTED]', value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    raise NotesError('unsupported event value')


def normalize(payload, adapter_version=2):
    if not isinstance(payload, dict):
        raise NotesError('hook input must be an object')
    if len(canonical(payload).encode('utf-8')) > MAX_INPUT:
        raise CaptureLimit('InputTooLarge')
    kind = payload.get('hook_event_name')
    if not isinstance(kind, str) or kind not in EVENTS:
        raise NotesError('unsupported hook event')
    session = text(payload.get('session_id') or payload.get('thread_id'), 'session_id')
    if payload.get('session_id') and payload.get('thread_id') and payload['session_id'] != payload['thread_id']:
        raise NotesError('ambiguous session identity')
    project = project_path(payload.get('cwd'))
    content, truncated = {}, []
    for field in FIELDS if adapter_version == 2 else FIELDS[:10]:
        if field not in payload:
            continue
        value = clean(payload[field])
        encoded = canonical(value)
        field_size = len(value) if adapter_version == 2 and isinstance(value, str) else len(encoded)
        if field_size > MAX_FIELD:
            if adapter_version == 2 and isinstance(value, str) and field in ('tool_response', 'tool_result', 'error'):
                value = {'preview': value[:MAX_FIELD // 2], 'tail': value[-MAX_FIELD // 2:],
                         'truncated': True, 'original_length': len(value)}
            else:
                value = {'preview': encoded[:MAX_FIELD], 'truncated': True}
            truncated.append(field)
        content[field] = value
    content['_coverage'] = {
        'source': 'codex_hook', 'truncated_fields': truncated,
        'transcript_read': False, 'independently_verified': False,
    }
    if adapter_version == 2:
        content['_coverage'].update({
        'adapter_version': 2,
        'unknown_field_count': len(set(payload) - set(FIELDS) - {
            'hook_event_name', 'session_id', 'thread_id', 'cwd',
            'transcript_path', 'permission_mode'}),
        'status_contract': 'captured_without_interpretation',
        })
    # Host event IDs are preferred. Repeated events without stable IDs are retained.
    id_fields = ('event_id', 'hook_event_id', 'tool_use_id', 'call_id') if kind == 'PostToolUse' else ('event_id', 'hook_event_id')
    identifier = None
    for field in id_fields:
        if field in payload and payload[field] is not None:
            candidate = text(payload[field], field)
            if identifier is None:
                identifier = candidate
    if identifier is not None:
        identifier = text(identifier, 'event_id')
        key = hashlib.sha256(canonical([session, kind, identifier]).encode()).hexdigest()
    else:
        key = str(uuid4())
    return project, session, kind, key, canonical(content)


def default_state():
    override = os.environ.get('TAS_NOTES_STATE_DIR')
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise NotesError('LOCALAPPDATA is unavailable; specify --state-dir')
    return Path(base) / 'TAS Notes'


class Store:
    def __init__(self, directory, read_only=False):
        self.directory = Path(directory).expanduser().resolve()
        self.read_only = read_only
        if not read_only:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / 'notes.sqlite3'
        if self.database.is_symlink():
            raise NotesError('database symlinks are not supported')
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise NotesError('unsupported database version')
            if read_only:
                return
            # Existing complete stores open without DDL, including when full/read-only.
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='capture_usage'").fetchone():
                return
            db.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS projects (
                    path TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)));
                CREATE TABLE IF NOT EXISTS investigations (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(path),
                    session TEXT NOT NULL, created TEXT NOT NULL,
                    UNIQUE(project, session));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    investigation TEXT NOT NULL REFERENCES investigations(id),
                    event_key TEXT NOT NULL, kind TEXT NOT NULL,
                    content TEXT NOT NULL, digest TEXT NOT NULL, captured TEXT NOT NULL,
                    UNIQUE(investigation, event_key));
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT PRIMARY KEY, investigation TEXT NOT NULL REFERENCES investigations(id),
                    number INTEGER NOT NULL, source_seq INTEGER NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL,
                    UNIQUE(investigation, number));
                CREATE TABLE IF NOT EXISTS summaries (
                    id TEXT PRIMARY KEY, investigation TEXT NOT NULL REFERENCES investigations(id),
                    source_seq INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                    content TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS capture_usage (
                    investigation TEXT PRIMARY KEY REFERENCES investigations(id),
                    bytes INTEGER NOT NULL, count INTEGER NOT NULL);
                INSERT OR IGNORE INTO capture_usage
                    SELECT investigation,SUM(length(CAST(content AS BLOB))),COUNT(*)
                    FROM events GROUP BY investigation;
                CREATE INDEX IF NOT EXISTS events_investigation_seq ON events(investigation,seq);
                CREATE INDEX IF NOT EXISTS events_kind_seq ON events(kind,seq);
                CREATE TRIGGER IF NOT EXISTS capture_usage_insert AFTER INSERT ON events BEGIN
                    INSERT INTO capture_usage VALUES (NEW.investigation,length(CAST(NEW.content AS BLOB)),1)
                    ON CONFLICT(investigation) DO UPDATE SET
                        bytes=bytes+length(CAST(NEW.content AS BLOB)), count=count+1;
                END;
                CREATE TRIGGER IF NOT EXISTS capture_usage_delete AFTER DELETE ON events BEGIN
                    UPDATE capture_usage SET bytes=bytes-length(CAST(OLD.content AS BLOB)),count=count-1
                    WHERE investigation=OLD.investigation;
                END;
                PRAGMA user_version=1;
                COMMIT;
            ''')

    @contextmanager
    def connect(self):
        database = self.database.as_uri() + '?mode=ro' if self.read_only else self.database
        db = sqlite3.connect(database, uri=self.read_only, timeout=3, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    def set_project(self, path, enabled):
        path = project_path(path, require_exists=enabled)
        # Runtime data must remain outside an enabled project, even with an override.
        if self.directory == Path(path) or Path(path) in self.directory.parents:
            raise NotesError('state directory must be outside the recorded project')
        with self.connect() as db:
            db.execute('INSERT INTO projects VALUES (?,?) ON CONFLICT(path) DO UPDATE SET enabled=excluded.enabled',
                       (path, int(enabled)))
        return {'project': path, 'enabled': bool(enabled)}

    def record(self, payload):
        # Opted-out projects do not acquire a writer lock or process their bodies.
        if not isinstance(payload, dict):
            raise NotesError('hook input must be an object')
        project = project_path(payload.get('cwd'))
        with self.connect() as db:
            allowed = db.execute('SELECT enabled FROM projects WHERE path=?', (project,)).fetchone()
            if not allowed or not allowed['enabled']:
                return {'status': 'ignored', 'reason': 'project_not_enabled'}
        try:
            return self._record_enabled(payload)
        except (NotesError, OSError, sqlite3.Error, ValueError, RecursionError) as error:
            # Normalization/connection errors happen before the writer section.
            # Errors inside it already published their marker while serialized.
            if not getattr(error, '_capture_diagnostic_recorded', False):
                code = error.code if isinstance(error, CaptureLimit) else type(error).__name__
                session = payload.get('session_id') or payload.get('thread_id')
                diagnostic(self.directory, code, project, session if isinstance(session, str) else None)
            raise

    def _record_enabled(self, payload):
        project, session, kind, key, body = normalize(payload)
        from tas_notes_diagnostics import last_error
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                allowed = db.execute('SELECT enabled FROM projects WHERE path=?', (project,)).fetchone()
                if not allowed or not allowed['enabled']:
                    db.rollback()
                    return {'status': 'ignored', 'reason': 'project_not_enabled'}
                # Read gap state only after earlier writers have completed. A
                # rejected boundary publishes its marker before releasing this lock.
                if last_error(self.directory, project, session):
                    content = json.loads(body)
                    content['_coverage']['capture_gap_before'] = True
                    body = canonical(content)
                digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
                row = db.execute('SELECT id FROM investigations WHERE project=? AND session=?', (project, session)).fetchone()
                identifier = row['id'] if row else str(uuid4())
                if not row:
                    db.execute('INSERT INTO investigations VALUES (?,?,?,?)', (identifier, project, session, now()))
                previous = db.execute('SELECT digest,content FROM events WHERE investigation=? AND event_key=?', (identifier, key)).fetchone()
                if previous:
                    if previous['digest'] != digest:
                        old = json.loads(previous['content'])
                        # A replay after the adapter upgrade must not conflict merely
                        # because we now retain additional host metadata. Never rewrite old evidence.
                        legacy = not old.get('_coverage', {}).get('adapter_version')
                        unchanged = normalize(payload, adapter_version=1)[-1] == previous['content'] if legacy else False
                        if not legacy:
                            current = json.loads(body)
                            for value in (old, current):
                                value.get('_coverage', {}).pop('capture_gap_before', None)
                            unchanged = old == current
                        if not unchanged:
                            raise Conflict('host event ID reused with different content')
                    db.rollback()
                    return {'status': 'duplicate', 'investigation': identifier}
                if self.database.stat().st_size >= MAX_DATABASE_BYTES:
                    raise CaptureLimit('DatabaseCapacityReached')
                usage = db.execute('SELECT bytes,count FROM capture_usage WHERE investigation=?', (identifier,)).fetchone()
                size, count = (usage['bytes'], usage['count']) if usage else (0, 0)
                if size + len(body.encode('utf-8')) > MAX_SESSION_BYTES or count >= 5000:
                    from tas_notes_anomalies import detect
                    if (kind != 'PostToolUse' or not detect(json.loads(body))
                            or size + len(body.encode('utf-8')) > MAX_SESSION_BYTES + ERROR_RESERVE_BYTES
                            or count >= 5000 + ERROR_RESERVE_EVENTS):
                        raise CaptureLimit('SessionCapacityReached')
                db.execute('INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES (?,?,?,?,?,?)',
                           (identifier, key, kind, body, digest, now()))
                db.commit()
            except (NotesError, OSError, sqlite3.Error, ValueError, RecursionError) as error:
                # The filesystem marker survives SQLite rollback. Publish before
                # the connection releases its writer lock, not in record's outer catch.
                code = error.code if isinstance(error, CaptureLimit) else type(error).__name__
                diagnostic(self.directory, code, project, session)
                error._capture_diagnostic_recorded = True
                raise
        return {'status': 'recorded', 'investigation': identifier}

    def listing(self, query=''):
        if not isinstance(query, str) or len(query) > 200:
            raise NotesError('invalid search query')
        with self.connect() as db:
            rows = db.execute('''SELECT i.*, (SELECT COUNT(*) FROM events e WHERE e.investigation=i.id) AS event_count,
                    (SELECT MAX(seq) FROM events e WHERE e.investigation=i.id) AS source_seq,
                    (SELECT title FROM revisions r WHERE r.investigation=i.id ORDER BY number DESC LIMIT 1) AS title,
                    (SELECT content FROM events e WHERE e.investigation=i.id AND kind='UserPromptSubmit' ORDER BY seq LIMIT 1) AS first_prompt
                FROM investigations i ORDER BY (SELECT MAX(seq) FROM events WHERE investigation=i.id) DESC LIMIT 500''').fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            prompt = item.pop('first_prompt')
            item['display_title'] = item['title'] or prompt_title(prompt) or f"调查 {item['id'][:8]}"
            item['title_source'] = 'human' if item['title'] else 'first_prompt' if prompt_title(prompt) else 'fallback'
        return [r for r in items if query.casefold() in canonical(r).casefold()]

    def status(self):
        from tas_notes_diagnostics import runtime_status, last_error
        with self.connect() as db:
            db.execute('BEGIN')
            projects = [dict(r) for r in db.execute('''SELECT p.path, p.enabled,
                COUNT(DISTINCT i.id) AS record_count, COUNT(e.seq) AS event_count,
                MAX(e.seq) AS last_seq FROM projects p
                LEFT JOIN investigations i ON i.project=p.path
                LEFT JOIN events e ON e.investigation=i.id GROUP BY p.path ORDER BY p.path''')]
            for project in projects:
                project['enabled'] = bool(project['enabled'])
                seq = project.pop('last_seq')
                event = db.execute('SELECT kind,captured,seq FROM events WHERE seq=?', (seq,)).fetchone()
                project['last_recorded_event'] = dict(event) if event else None
                project['last_capture_error'] = last_error(self.directory, project['path'])
            # Retain a bounded metadata-only records list for CLI compatibility.
            records = [dict(r) for r in db.execute('''SELECT i.id,i.project,COUNT(e.seq) AS event_count,
                MAX(e.seq) AS source_seq FROM investigations i LEFT JOIN events e ON e.investigation=i.id
                GROUP BY i.id ORDER BY source_seq DESC LIMIT 500''')]
            db.commit()
        from tas_notes_launcher import runtime_metadata
        application = runtime_metadata(self)
        application['state_directory'] = application['state_dir']
        size = self.database.stat().st_size
        return {'status_version': 2, 'runtime': runtime_status(self.directory), 'projects': projects,
                'application': application,
                'storage': {'database_bytes': size, 'database_limit_bytes': MAX_DATABASE_BYTES,
                            'capacity_reached': size >= MAX_DATABASE_BYTES,
                            'session_limit_bytes': MAX_SESSION_BYTES,
                            'error_reserve_bytes': ERROR_RESERVE_BYTES},
                'record_count': sum(p['record_count'] for p in projects), 'records': records,
                'last_capture_error': last_error(self.directory),
                'host_integration': 'not_checked', 'continuous_capture': 'unknown'}

    def detail(self, identifier):
        identifier = text(identifier, 'record id')
        with self.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM investigations WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise NotesError('record not found')
            events = [dict(r) for r in db.execute('SELECT seq,kind,content,digest,captured FROM events WHERE investigation=? ORDER BY seq', (identifier,))]
            revisions = [dict(r) for r in db.execute('SELECT * FROM revisions WHERE investigation=? ORDER BY number DESC', (identifier,))]
            summaries = [dict(r) for r in db.execute('SELECT * FROM summaries WHERE investigation=? ORDER BY rowid DESC', (identifier,))]
            revision = revisions[0] if revisions else None
            summary = summaries[0] if summaries else None
            db.commit()
        result = dict(row)
        result.update(events=events, revision=dict(revision) if revision else None,
                      revisions=revisions, summaries=summaries,
                      source_seq=events[-1]['seq'] if events else 0)
        first = next((e['content'] for e in events if e['kind'] == 'UserPromptSubmit'), None)
        result['display_title'] = result['revision']['title'] if revision else prompt_title(first) or f"调查 {identifier[:8]}"
        from tas_notes_summary import render_summary
        result['summary'] = dict(summary) if summary else None
        result['summary_stale'] = bool(summary and summary['source_seq'] != result['source_seq'])
        result['overview'] = render_draft(result)
        result['draft'] = render_summary(result['summary']) if summary and not result['summary_stale'] else result['overview']
        return result

    def export_revision(self, revision_id):
        revision_id = text(revision_id, 'revision id')
        with self.connect() as db:
            row = db.execute('SELECT * FROM revisions WHERE id=?', (revision_id,)).fetchone()
        if not row:
            raise NotesError('reviewed revision not found')
        return (f"# {row['title']}\n\n"
                f"> 人工保存版本 {row['number']} · {row['created']}\n"
                f"> 记录 {row['investigation']} · 来源截至事件 {row['source_seq']}\n"
                '> 保存和导出不等于用户验收通过；后续调查可能改变结论。\n\n'
                + row['body'] + '\n')


def prompt_title(content):
    prompt = json.loads(content).get('prompt') if content else None
    if not isinstance(prompt, str):
        return None
    # Presentation only: preserve full original evidence, prefer the user request
    # after Codex's attachment/context wrapper when that exact marker exists.
    marker = '\n## My request:'
    if marker in prompt:
        prompt = prompt.split(marker, 1)[1]
    value = ' '.join(prompt.split())
    return value[:79] + '…' if len(value) > 80 else value or None


def render_draft(record):
    from tas_notes_summary import readable
    return readable(record)


def diagnostic(directory, code, project=None, session=None):
    """Atomic, bounded, content-free last failure marker; recording stays fail-open."""
    from tas_notes_diagnostics import marker_name
    name = marker_name(project, session)
    target = Path(directory) / name
    temporary = target.with_name(f'.diagnostic-{uuid4().hex}.tmp')
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(canonical({'time': now(), 'code': code}), encoding='utf-8')
        os.replace(temporary, target)
    except OSError:
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    if session and project:
        diagnostic(directory, code, project)
    elif project:
        diagnostic(directory, code)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Blackbox Plugin: local execution errors and read-only archives')
    parser.add_argument('--state-dir', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('enable', 'disable'):
        commands.add_parser(command).add_argument('project')
    commands.add_parser('hook')
    commands.add_parser('status')
    commands.add_parser('stop')
    backup_parser = commands.add_parser('backup')
    backup_parser.add_argument('destination', type=Path)
    restore_parser = commands.add_parser('restore')
    restore_parser.add_argument('source', type=Path)
    restore_parser.add_argument('destination', type=Path)
    commands.add_parser('configure-runtime')
    opener = commands.add_parser('open')
    opener.add_argument('--no-browser', action='store_true')
    server = commands.add_parser('serve')
    server_mode = server.add_mutually_exclusive_group()
    server_mode.add_argument('--background', action='store_true')
    server_mode.add_argument('--ready-file', type=Path, help=argparse.SUPPRESS)
    export = commands.add_parser('export')
    export.add_argument('revision_id')
    export.add_argument('destination', type=Path)
    args = parser.parse_args(argv)
    directory = None
    try:
        if args.command == 'restore':
            from tas_notes_backup import restore
            print(json.dumps(restore(args.source, args.destination), ensure_ascii=True, indent=2))
            return 0
        from tas_notes_launcher import resolve_state_directory
        directory = resolve_state_directory(args.state_dir)
        if args.command == 'hook' and not (directory / 'notes.sqlite3').is_file():
            # Installation alone has no authorization and need not create a database.
            return 0
        store = Store(directory, read_only=args.command in ('status', 'open', 'serve', 'export', 'backup', 'stop')
                      and (directory / 'notes.sqlite3').is_file())
        if args.command == 'configure-runtime':
            # The .cmd bootstrap does not require changing PowerShell execution policy.
            # Restrict cmd metacharacters and use an ASCII short path for Windows codepages.
            import ctypes
            executable = sys.executable
            if os.name == 'nt':
                buffer = ctypes.create_unicode_buffer(32768)
                length = ctypes.windll.kernel32.GetShortPathNameW(executable, buffer, len(buffer))
                executable = buffer.value if 0 < length < len(buffer) else executable
            if not executable.isascii() or any(c in executable for c in '%!^&|<>"\r\n'):
                raise NotesError('runtime requires an ASCII executable path without cmd metacharacters')
            target = Path(directory) / 'runtime.cmd'
            target.write_text('@"' + executable + '" %*\n', encoding='ascii')
            result = {'runtime_configured': True, 'python': sys.executable}
        elif args.command in ('enable', 'disable'):
            result = store.set_project(args.project, args.command == 'enable')
        elif args.command == 'hook':
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(raw) > MAX_INPUT:
                raise CaptureLimit('InputTooLarge')
            result = store.record(json.loads(raw.decode('utf-8-sig')))
            # Never inject captured text or additional instructions into the Agent.
            return 0
        elif args.command == 'backup':
            from tas_notes_backup import backup
            result = backup(store, args.destination)
        elif args.command == 'stop':
            from tas_notes_launcher import stop_reviewer
            result = stop_reviewer(store)
        elif args.command == 'open':
            from tas_notes_launcher import ensure_reviewer
            result = ensure_reviewer(store)
            if not args.no_browser:
                import webbrowser
                if not webbrowser.open(result['url']):
                    raise NotesError('default browser unavailable')
        elif args.command == 'serve':
            if args.background:
                from tas_notes_launcher import ensure_reviewer
                result = ensure_reviewer(store)
            else:
                from tas_notes_web import serve
                serve(store, args.ready_file)
                return 0
        elif args.command == 'export':
            body = store.export_revision(args.revision_id)
            with args.destination.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(body)
            result = {'exported': str(args.destination)}
        else:
            result = store.status()
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (NotesError, OSError, sqlite3.Error, ValueError, RecursionError) as error:
        # Do not print exception text: it can contain input, SQL or filesystem secrets.
        code = error.code if isinstance(error, CaptureLimit) else type(error).__name__
        if getattr(error, 'public_code', None) == 'reviewer_build_changed':
            print('Blackbox Plugin: reviewer_build_changed; run stop, then open. Preserve any old unsaved edits first.', file=sys.stderr)
        else:
            print(f'Blackbox Plugin: {code}; operation not completed.', file=sys.stderr)
        if args.command == 'hook' and directory is not None:
            diagnostic(directory, code)
        return 0 if args.command == 'hook' else 1
