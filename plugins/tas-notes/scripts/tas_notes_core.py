"""Local opt-in recording and versioned review, independent of the TAS service."""
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
EVENTS = frozenset(('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'Stop',
                   'PreCompact', 'PostCompact', 'SessionEnd', 'Interrupt'))
FIELDS = ('prompt', 'last_assistant_message', 'tool_name', 'tool_input',
          'tool_response', 'tool_result', 'error', 'reason', 'source', 'message')
SENSITIVE = re.compile(r'authorization|password|passwd|secret|credential|api[_-]?key|access[_-]?token', re.I)


class NotesError(ValueError):
    pass


class Conflict(NotesError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def text(value, field, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise NotesError(f'invalid {field}')
    return value


def project_path(value):
    path = Path(text(value, 'project', 4096)).expanduser()
    if not path.is_absolute() or not path.is_dir():
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


def normalize(payload):
    if not isinstance(payload, dict):
        raise NotesError('hook input must be an object')
    if len(canonical(payload).encode('utf-8')) > MAX_INPUT:
        raise NotesError('event exceeds size limit')
    kind = payload.get('hook_event_name')
    if not isinstance(kind, str) or kind not in EVENTS:
        raise NotesError('unsupported hook event')
    session = text(payload.get('session_id') or payload.get('thread_id'), 'session_id')
    if payload.get('session_id') and payload.get('thread_id') and payload['session_id'] != payload['thread_id']:
        raise NotesError('ambiguous session identity')
    project = project_path(payload.get('cwd'))
    content, truncated = {}, []
    for field in FIELDS:
        if field not in payload:
            continue
        value = clean(payload[field])
        encoded = canonical(value)
        if len(encoded) > MAX_FIELD:
            value = {'preview': encoded[:MAX_FIELD], 'truncated': True}
            truncated.append(field)
        content[field] = value
    content['_coverage'] = {
        'source': 'codex_hook', 'truncated_fields': truncated,
        'transcript_read': False, 'independently_verified': False,
    }
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
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / 'notes.sqlite3'
        if self.database.is_symlink():
            raise NotesError('database symlinks are not supported')
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise NotesError('unsupported database version')
            db.executescript('''
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
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=3, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    def set_project(self, path, enabled):
        path = project_path(path)
        # Runtime data must remain outside an enabled project, even with an override.
        if self.directory == Path(path) or Path(path) in self.directory.parents:
            raise NotesError('state directory must be outside the recorded project')
        with self.connect() as db:
            db.execute('INSERT INTO projects VALUES (?,?) ON CONFLICT(path) DO UPDATE SET enabled=excluded.enabled',
                       (path, int(enabled)))
        return {'project': path, 'enabled': bool(enabled)}

    def record(self, payload):
        project, session, kind, key, body = normalize(payload)
        digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
        if self.database.stat().st_size > MAX_DATABASE_BYTES:
            raise NotesError('local database limit reached')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            allowed = db.execute('SELECT enabled FROM projects WHERE path=?', (project,)).fetchone()
            if not allowed or not allowed['enabled']:
                db.rollback()
                return {'status': 'ignored', 'reason': 'project_not_enabled'}
            row = db.execute('SELECT id FROM investigations WHERE project=? AND session=?', (project, session)).fetchone()
            identifier = row['id'] if row else str(uuid4())
            if not row:
                db.execute('INSERT INTO investigations VALUES (?,?,?,?)', (identifier, project, session, now()))
            previous = db.execute('SELECT digest FROM events WHERE investigation=? AND event_key=?', (identifier, key)).fetchone()
            if previous:
                if previous['digest'] != digest:
                    raise Conflict('host event ID reused with different content')
                db.rollback()
                return {'status': 'duplicate', 'investigation': identifier}
            size, count = db.execute('SELECT COALESCE(SUM(length(CAST(content AS BLOB))),0),COUNT(*) FROM events WHERE investigation=?', (identifier,)).fetchone()
            if size + len(body.encode('utf-8')) > MAX_SESSION_BYTES or count >= 5000:
                raise NotesError('session recording limit reached')
            db.execute('INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES (?,?,?,?,?,?)',
                       (identifier, key, kind, body, digest, now()))
            db.commit()
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
            # Retain a bounded metadata-only records list for CLI compatibility.
            records = [dict(r) for r in db.execute('''SELECT i.id,i.project,COUNT(e.seq) AS event_count,
                MAX(e.seq) AS source_seq FROM investigations i LEFT JOIN events e ON e.investigation=i.id
                GROUP BY i.id ORDER BY source_seq DESC LIMIT 500''')]
            db.commit()
        return {'status_version': 1, 'runtime': runtime_status(self.directory), 'projects': projects,
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
            revision = db.execute('SELECT * FROM revisions WHERE investigation=? ORDER BY number DESC LIMIT 1', (identifier,)).fetchone()
            db.commit()
        result = dict(row)
        result.update(events=events, revision=dict(revision) if revision else None,
                      source_seq=events[-1]['seq'] if events else 0)
        first = next((e['content'] for e in events if e['kind'] == 'UserPromptSubmit'), None)
        result['display_title'] = result['revision']['title'] if revision else prompt_title(first) or f"调查 {identifier[:8]}"
        result['draft'] = render_draft(result)
        return result

    def save_revision(self, identifier, title, body, source_seq, expected_revision):
        identifier = text(identifier, 'record id')
        title = text(title, 'title', 200)
        body = text(body, 'body', 200_000)
        if type(source_seq) is not int or type(expected_revision) is not int:
            raise NotesError('invalid revision cursor')
        if self.database.stat().st_size > MAX_DATABASE_BYTES:
            raise NotesError('local database limit reached')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE investigation=?', (identifier,)).fetchone()[0]
            version = db.execute('SELECT COALESCE(MAX(number),0) FROM revisions WHERE investigation=?', (identifier,)).fetchone()[0]
            if not current:
                raise NotesError('record not found')
            if source_seq != current or expected_revision != version:
                raise Conflict('record changed; refresh and reconcile before saving')
            revision_id = str(uuid4())
            db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?)',
                       (revision_id, identifier, version + 1, current, title, body, now()))
            db.commit()
        return {'id': revision_id, 'number': version + 1}

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
    value = ' '.join(prompt.split())
    return value[:79] + '…' if len(value) > 80 else value or None


def render_draft(record):
    parts = ['## 当前记录边界',
             '这是根据已捕获 Hook 材料生成的过程草稿，尚未经 Owner 审阅或验收。',
             '未读取会话原文件；未提供的理由和结果未知。以下内容是来源材料，不是独立验证结论。',
             '## 工作经过']
    counts = {kind: sum(e['kind'] == kind for e in record['events']) for kind in EVENTS}
    parts.insert(-1, f"已捕获：{counts['UserPromptSubmit']} 次用户输入 · {counts['PostToolUse']} 次工具操作 · "
                 f"{counts['Stop']} 次 Agent 最后陈述。按落盘顺序展示；不代表完整过程或验收状态。")
    kinds = {'SessionStart': '会话开始', 'UserPromptSubmit': '用户输入', 'PostToolUse': '工具操作',
             'Stop': 'Agent 最后陈述', 'PreCompact': '压缩前', 'PostCompact': '压缩后',
             'Interrupt': '用户中断', 'SessionEnd': '会话结束'}
    labels = {'prompt': '用户输入', 'last_assistant_message': 'Agent 最后陈述',
              'tool_name': '工具', 'tool_input': '工具输入', 'tool_response': '工具返回',
              'tool_result': '工具结果', 'error': '错误', 'reason': '宿主原因',
              'source': '宿主来源', 'message': '消息'}
    for event in record['events']:
        parts.append(f"### 事件 {event['seq']} · {kinds.get(event['kind'], event['kind'])}\n\n采集时间：{event['captured']}")
        content = json.loads(event['content'])
        if event['kind'] == 'PostToolUse':
            parts.append('**结果覆盖：本版未提取结构化退出状态，操作是否成功未知；请核对工具返回与外部证据。**')
            if not any(content.get(key) not in (None, '', {}, []) for key in ('tool_response', 'tool_result')):
                parts.append('**工具返回缺失或为空，不能据此判断成功。**')
        if event['kind'] in ('PreCompact', 'PostCompact', 'Interrupt') and not any(key in content for key in FIELDS):
            parts.append('仅捕获到事件发生；本条没有可用的正文或原因。')
        if '\ufffd' in canonical(content):
            parts.append('**材料含替换字符（�），可能已发生编码丢失；保留原样，不猜测还原。**')
        for key in FIELDS:
            if key not in content:
                continue
            value = content[key]
            display = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            fence = '`' * max(3, max((len(m.group()) + 1 for m in re.finditer(r'`+', display)), default=3))
            parts.append(f"**{labels[key]}**\n\n{fence}text\n{display}\n{fence}")
        truncated = content['_coverage']['truncated_fields']
        if truncated:
            parts.append('材料因大小限制截断：' + ', '.join(truncated))
    parts.extend(['## 理由与依据', '请根据上方来源补充；没有来源的解释保持未知。',
                  '## 结果与待确认', '回合结束不代表问题解决。请区分测试结果、Agent 判断和用户反馈。'])
    return '\n\n'.join(parts)


def diagnostic(directory, code):
    """Atomic, bounded, content-free last failure marker; recording stays fail-open."""
    target = Path(directory) / 'last-error.json'
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


def main(argv=None):
    parser = argparse.ArgumentParser(description='TAS Notes: local records and human review')
    parser.add_argument('--state-dir', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('enable', 'disable'):
        commands.add_parser(command).add_argument('project')
    commands.add_parser('hook')
    commands.add_parser('status')
    commands.add_parser('configure-runtime')
    commands.add_parser('serve')
    export = commands.add_parser('export')
    export.add_argument('revision_id')
    export.add_argument('destination', type=Path)
    args = parser.parse_args(argv)
    directory = None
    try:
        directory = args.state_dir or default_state()
        store = Store(directory)
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
                raise NotesError('hook input exceeds limit')
            result = store.record(json.loads(raw.decode('utf-8-sig')))
            # Never inject captured text or additional instructions into the Agent.
            return 0
        elif args.command == 'serve':
            from tas_notes_web import serve
            serve(store)
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
        print(f'TAS Notes: {type(error).__name__}; operation not completed.', file=sys.stderr)
        if args.command == 'hook' and directory is not None:
            diagnostic(directory, type(error).__name__)
        return 0 if args.command == 'hook' else 1
