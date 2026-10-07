"""Deterministic, source-backed observations. No model calls or repair verdicts."""
from datetime import datetime
import hashlib
import json
import re
import sqlite3
from uuid import uuid4

from tas_notes_core import NotesError, canonical, MAX_DATABASE_BYTES

RULE_VERSION = 'observed-errors-1'
COVERAGE = ('仅识别已验证的终端错误格式；未暴露的退出状态、内部重试及拒绝可能漏记。'
            '空返回不表示成功；没有匹配记录不代表没有问题。')
RETENTION = '当前只收紧展示：已启用项目的普通输入、工具事件和最后回复仍可能保存在本机。'


def response_text(content):
    value = content.get('tool_response')
    return value if isinstance(value, str) else None


def detect(content):
    """Only inspect returned terminal output, never prompts or tool arguments."""
    if content.get('tool_name') != 'Bash':
        return None
    body = response_text(content)
    if not body or 'tool_response' in content.get('_coverage', {}).get('truncated_fields', []):
        return None
    # Actual PowerShell diagnostic shape, not an arbitrary occurrence of 'error'.
    if (re.match(r'^ResourceUnavailable:\s*\r?\nLine \|', body)
            and re.search(r"\| Program '[^\r\n]+' failed to run:", body)):
        return {'rule': 'powershell-process-start', 'title': '工具返回进程启动错误',
                'evidence': body}
    # A traceback must occupy the returned output, with an exception footer.
    # Printing a traceback can still imitate this format: classify the returned
    # diagnostic, never claim an independently verified defect.
    if body.startswith('Traceback (most recent call last):'):
        footer = body.rstrip().splitlines()[-1]
        if re.match(r'^(?:[A-Za-z_]\w*\.)*(?:FileNotFoundError|JSONDecodeError|TimeoutExpired|PermissionError):', footer):
            return {'rule': 'python-exception', 'title': '工具返回 ' + footer.split(':', 1)[0],
                    'evidence': body}
    return None


def target_key(content):
    """Shell commands are opaque: only identical full inputs may be linked."""
    tool, args = content.get('tool_name'), content.get('tool_input')
    if not isinstance(tool, str) or not isinstance(args, dict) or not args:
        return None
    if content.get('_coverage', {}).get('truncated_fields') or '[REDACTED]' in canonical(args):
        return None
    # Do not guess shell paths, nested tool identities or changed parameters.
    return hashlib.sha256(canonical([tool, args]).encode('utf-8')).hexdigest()


def ensure_schema(store):
    with store.connect() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='anomaly_index_state'").fetchone():
            return
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='anomaly_index_state'").fetchone():
            db.commit()
            return
        # Reserved writer lock prevents concurrent changes during the snapshot.
        # Backup through a separate read connection avoids backing up a writing
        # connection. The base user_version stays 1 for older Hook collectors.
        backup = store.directory / ('before-anomalies-' + uuid4().hex + '.sqlite3')
        with store.connect() as source:
            destination = sqlite3.connect(backup)
            try:
                source.backup(destination)
            finally:
                destination.close()
        db.execute('CREATE TABLE anomaly_index_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), rule_version TEXT NOT NULL, scanned_seq INTEGER NOT NULL)')
        db.execute('CREATE TABLE anomalies (event_seq INTEGER PRIMARY KEY REFERENCES events(seq), rule TEXT NOT NULL, title TEXT NOT NULL)')
        db.execute('INSERT INTO anomaly_index_state VALUES (1,?,0)', (RULE_VERSION,))
        db.commit()


def sync(store):
    ensure_schema(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        state = db.execute('SELECT * FROM anomaly_index_state').fetchone()
        cursor = state['scanned_seq']
        if state['rule_version'] != RULE_VERSION:
            db.execute('DELETE FROM anomalies')  # Derived index only; sources untouched.
            cursor = 0
        rows = db.execute('SELECT seq,kind,content FROM events WHERE seq>? ORDER BY seq LIMIT 500', (cursor,)).fetchall()
        if rows and store.database.stat().st_size > MAX_DATABASE_BYTES:
            raise NotesError('database capacity reached; index not extended')
        for row in rows:
            hit = detect(json.loads(row['content'])) if row['kind'] == 'PostToolUse' else None
            if hit:
                db.execute('INSERT OR REPLACE INTO anomalies VALUES (?,?,?)', (row['seq'], hit['rule'], hit['title']))
            cursor = row['seq']
        db.execute('UPDATE anomaly_index_state SET rule_version=?,scanned_seq=?', (RULE_VERSION, cursor))
        pending = bool(db.execute('SELECT 1 FROM events WHERE seq>? LIMIT 1', (cursor,)).fetchone())
        db.commit()
    return pending


def positive(value, default):
    if value is None or value == '':
        return default
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) <= 2**63 - 1:
        raise NotesError('invalid event cursor')
    return int(value)


def listing(store, project='', tool='', start='', end='', before=None):
    for value in (project, tool):
        if not isinstance(value, str) or len(value) > 200:
            raise NotesError('invalid filter')
    dates = []
    for value in (start, end):
        try:
            if value:
                date = datetime.fromisoformat(value)
                if date.tzinfo is None:
                    raise ValueError()
                dates.append(date)
            else:
                dates.append(None)
        except (ValueError, TypeError):
            raise NotesError('invalid date') from None
    if all(dates) and dates[0] >= dates[1]:
        raise NotesError('invalid date range')
    pending = sync(store)
    upper = positive(before, 2**63 - 1)
    items = []
    with store.connect() as db:
        rows = db.execute('''SELECT a.*,e.captured,e.content,i.project,i.session,e.investigation
            FROM anomalies a JOIN events e ON e.seq=a.event_seq
            JOIN investigations i ON i.id=e.investigation
            WHERE a.event_seq<? ORDER BY a.event_seq DESC''', (upper,))
        for row in rows:
            content = json.loads(row['content'])
            name = content.get('tool_name', '')
            stamp = datetime.fromisoformat(row['captured'])
            if (project.casefold() not in row['project'].casefold() or tool.casefold() not in name.casefold()
                    or (dates[0] and stamp < dates[0]) or (dates[1] and stamp >= dates[1])):
                continue
            items.append({'id': str(row['event_seq']), 'title': row['title'], 'tool': name,
                          'project': row['project'], 'captured': row['captured']})
            if len(items) == 101:
                break
    return {'items': items[:100], 'next_before': items[99]['id'] if len(items) > 100 else None,
            'index_pending': pending, 'rule_version': RULE_VERSION, 'coverage': COVERAGE, 'retention': RETENTION}


def fenced(value):
    value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    # Arbitrary raw output cannot break out of an exported Markdown code block.
    size = max([len(m[0]) for m in re.finditer(r'`+', value)] + [2]) + 1
    marker = '`' * size
    return marker + '\n' + value + '\n' + marker


def operation(content):
    value = content.get('tool_input', '未捕获输入')
    if isinstance(value, dict):
        for field in ('command', 'cmd'):
            if isinstance(value.get(field), str):
                return value[field]
    return value


def detail(store, identifier, through=None):
    seq = positive(identifier, None)
    if seq is None:
        raise NotesError('missing anomaly id')
    with store.connect() as db:
        db.execute('BEGIN')
        first = db.execute('''SELECT e.*,i.project,i.session FROM events e
            JOIN investigations i ON i.id=e.investigation WHERE e.seq=?''', (seq,)).fetchone()
        if not first or first['kind'] != 'PostToolUse':
            raise NotesError('anomaly not found')
        content = json.loads(first['content'])
        hit = detect(content)
        if not hit:
            raise NotesError('anomaly not found')
        latest = db.execute('SELECT MAX(seq) FROM events WHERE investigation=?', (first['investigation'],)).fetchone()[0]
        bound = positive(through, latest)
        if not seq <= bound <= latest:
            raise NotesError('invalid snapshot')
        rows = db.execute('SELECT * FROM events WHERE investigation=? AND seq>? AND seq<=? ORDER BY seq',
                          (first['investigation'], seq, bound)).fetchall()
        db.commit()
    linked, key = [], target_key(content)
    for row in rows:
        if row['kind'] in ('Interrupt', 'SessionEnd'):
            break
        if row['kind'] != 'PostToolUse' or not key:
            continue
        other = json.loads(row['content'])
        if target_key(other) == key:
            linked.append(dict(row))
            if detect(other) or len(linked) == 20:
                break
    events = [dict(first)] + linked
    body = ['# ' + hit['title'], '', '> 按工具返回格式识别的观察记录；不是独立验证的 Bug 或修复结论。',
            '', '## 记录范围', '', '项目：', '', fenced(first['project']), '',
            '- 记录时间（本机落盘）：' + first['captured'],
            '- 来源事件：#' + str(seq), '- 规则：' + RULE_VERSION + '/' + hit['rule'],
            '- 快照截至事件：#' + str(bound), '', '## 执行操作', '',
            fenced(operation(content)), '', '## 错误返回', '', fenced(hit['evidence'])]
    for row in linked:
        other = json.loads(row['content'])
        body += ['', '## 后续相关操作 · 来源 #' + str(row['seq']), '',
                 '> 关联依据：同一会话、相同工具及完整参数；不表示已确认处理意图。', '',
                 fenced(operation(other)), '', '### 返回结果', '',
                 fenced(other.get('tool_response', '未捕获返回'))]
        if not other.get('tool_response'):
            body += ['', '> 空返回不表示成功。']
    if not linked:
        body += ['', '> 未捕获到符合关联规则的后续操作。']
    body += ['', '## 原始来源', '']
    for event in events:
        body += ['### 来源 #' + str(event['seq']), '', 'SHA-256：' + event['digest'], '',
                 fenced(json.loads(event['content'])), '']
    body += ['', '## 覆盖与保留说明', '', COVERAGE, '', RETENTION,
             '', '最多附上 20 次匹配操作；遇到下一次匹配失败或会话中断/结束即停止关联。',
             '不同参数的处理、跨会话操作及未投递事件不会被自动补入。',
             '来源可能包含敏感信息；下载不上传，分享前请自行检查。']
    if any(json.loads(e['content']).get('_coverage', {}).get('truncated_fields') for e in events):
        body += ['', '> 部分来源字段已截断，请以来源标记为准。']
    if any('\ufffd' in e['content'] for e in events):
        body += ['', '> 来源含编码替换字符，原始文字可能已丢失。']
    return {'id': str(seq), 'title': hit['title'], 'project': first['project'],
            'tool': content.get('tool_name'), 'captured': first['captured'],
            'source_seq': bound, 'rule_version': RULE_VERSION, 'events': events,
            'markdown': '\n'.join(body) + '\n'}
