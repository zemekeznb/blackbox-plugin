"""Deterministic, source-backed observations. No model calls or repair verdicts."""
from datetime import datetime
import hashlib
import json
import re

from tas_notes_core import NotesError, canonical

RULE_VERSION = 'observed-errors-2'
COVERAGE = ('仅识别 Bash 返回中的完整 Python traceback 或指定 PowerShell 诊断格式；'
            '格式命中不是已验证故障，打印日志也可能命中。未验证的退出状态、内部重试及拒绝可能漏记。'
            '空返回不表示成功；没有匹配记录不代表没有问题。')
RETENTION = '当前只收紧展示：已启用项目的普通输入、工具事件和最后回复仍可能保存在本机。'
MAX_SCAN = 500
PAGE_SIZE = 100


def response_segments(content):
    """Return independent text segments, never rejoin across a truncation gap."""
    value = content.get('tool_response')
    marked = 'tool_response' in content.get('_coverage', {}).get('truncated_fields', [])
    if isinstance(value, str) and not marked:
        return [('full', value, False)]
    # Older previews hold incomplete JSON-encoded values. They cannot safely be
    # treated as source text. Only the explicit raw-string head/tail shape works.
    if (not isinstance(value, dict) or value.get('truncated') is not True
            or type(value.get('original_length')) is not int
            or not isinstance(value.get('preview'), str) or not isinstance(value.get('tail'), str)):
        return []
    head, tail = value['preview'], value['tail']
    # JSON escaping can trigger the storage budget while raw-text windows still
    # overlap. Overlap is harmless because each segment is inspected separately.
    if value['original_length'] <= 0 or max(len(head), len(tail)) > value['original_length']:
        return []
    # Discard cut lines: even a plausible exception footer may be incomplete.
    head = head[:head.rfind('\n') + 1]
    tail = tail[tail.find('\n') + 1:] if '\n' in tail else ''
    return [('head', head, True), ('tail', tail, True)]


def python_traceback(body):
    """Find a header, a real frame line, and an unindented exception footer."""
    lines = body.splitlines()
    start, frame = None, False
    for index, line in enumerate(lines):
        if line == 'Traceback (most recent call last):':
            start, frame = index, False
        elif start is not None:
            if re.match(r'^\s+File "[^\r\n]+", line \d+(?:, in .+)?$', line):
                frame = True
            elif frame and re.fullmatch(r'(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?::.*)?', line):
                return line.split(':', 1)[0], '\n'.join(lines[start:index + 1])
            elif line and not line[0].isspace():
                # Unrelated output between a header and its footer is not a
                # complete trace; do not bridge separate diagnostic fragments.
                start, frame = None, False
    return None


def detect(content):
    """Only inspect returned terminal output, never prompts or tool arguments."""
    if content.get('tool_name') != 'Bash':
        return None
    for segment, body, truncated in response_segments(content):
        hit = None
        # Keep the previously observed PowerShell format contract. Python's
        # traceback grammar is broader, but still only an observation of text.
        if (re.match(r'^ResourceUnavailable:\s*\r?\nLine \|', body)
                and re.search(r"\| Program '[^\r\n]+' failed to run:", body)):
            hit = {'rule': 'powershell-process-start', 'title': '工具返回进程启动错误',
                   'evidence': body}
        trace = python_traceback(body)
        if trace and hit is None:
            name, evidence = trace
            hit = {'rule': 'python-exception', 'title': '工具返回 ' + name, 'evidence': evidence}
        if hit:
            hit['signal'] = {'kind': 'diagnostic_format', 'source_field': 'tool_response',
                             'format': hit['rule'], 'segment': segment, 'truncated': truncated,
                             'independently_verified': False}
            return hit
    return None


def target_key(content):
    """Shell commands are opaque: only identical full inputs may be linked."""
    if content.get('_coverage', {}).get('capture_gap_before'):
        return None
    tool, args = content.get('tool_name'), content.get('tool_input')
    if not isinstance(tool, str) or not isinstance(args, dict) or not args:
        return None
    if ('tool_input' in content.get('_coverage', {}).get('truncated_fields', [])
            or args.get('truncated') is True or '[REDACTED]' in canonical(args)):
        return None
    # Do not guess shell paths, nested tool identities or changed parameters.
    return hashlib.sha256(canonical([tool, args]).encode('utf-8')).hexdigest()


def positive(value, default):
    if value is None or value == '':
        return default
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) <= 2**63 - 1:
        raise NotesError('invalid event cursor')
    return int(value)


def listing(store, project='', tool='', start='', end='', before=None, through=None):
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
    upper = positive(before, 2**63 - 1)
    items, scanned = [], 0
    with store.connect() as db:
        db.execute('BEGIN')
        latest = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        zero_snapshot = (type(through) is int and through == 0) or through == '0'
        snapshot = 0 if zero_snapshot else positive(through, latest)
        if isinstance(through, bool) or snapshot > latest:
            raise NotesError('invalid snapshot')
        rows = db.execute('''SELECT e.seq,e.captured,e.content,i.project
            FROM events e
            JOIN investigations i ON i.id=e.investigation
            WHERE e.kind='PostToolUse' AND e.seq<? AND e.seq<=?
            ORDER BY e.seq DESC LIMIT ?''', (upper, snapshot, MAX_SCAN + 1)).fetchall()
        # Rows are materialized from one snapshot; release the read lock before
        # parsing potentially large tool output so capture writes can proceed.
        db.commit()
        for row in rows[:MAX_SCAN]:
            scanned += 1
            content = json.loads(row['content'])
            name = content.get('tool_name', '')
            stamp = datetime.fromisoformat(row['captured'])
            if (not isinstance(name, str) or project.casefold() not in row['project'].casefold()
                    or tool.casefold() not in name.casefold()
                    or (dates[0] and stamp < dates[0]) or (dates[1] and stamp >= dates[1])):
                continue
            hit = detect(content)
            if not hit:
                continue
            items.append({'id': str(row['seq']), 'title': hit['title'], 'tool': name,
                          'signal': hit['signal'],
                          'project': row['project'], 'captured': row['captured']})
            if len(items) == PAGE_SIZE:
                break
    return {'items': items, 'next_before': str(rows[scanned - 1]['seq']) if len(rows) > scanned else None,
            'snapshot_seq': snapshot, 'scanned_count': scanned, 'has_newer': latest > snapshot,
            'index_pending': False, 'rule_version': RULE_VERSION, 'coverage': COVERAGE, 'retention': RETENTION}


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
        latest = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
        bound = positive(through, latest)
        if not seq <= bound <= latest:
            raise NotesError('invalid snapshot')
        rows = db.execute('SELECT * FROM events WHERE investigation=? AND seq>? AND seq<=? ORDER BY seq',
                          (first['investigation'], seq, bound)).fetchall()
        db.commit()
    linked, key = [], target_key(content)
    capture_gap = bool(content.get('_coverage', {}).get('capture_gap_before'))
    first_turn = content.get('turn_id')
    first_turn = first_turn if isinstance(first_turn, str) and first_turn else None
    for row in rows:
        if row['kind'] in ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd'):
            break
        other = json.loads(row['content'])
        if other.get('_coverage', {}).get('capture_gap_before'):
            capture_gap = True
            break
        turn = other.get('turn_id')
        turn = turn if isinstance(turn, str) and turn else None
        if turn and turn != first_turn:
            break
        if row['kind'] != 'PostToolUse' or not key:
            continue
        if first_turn and not turn:
            # Missing round identity after a known one cannot establish that a
            # later repeat belongs to the same round.
            break
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
        body += ['', '## 相同输入再次调用 · 来源 #' + str(row['seq']), '',
                 '> 依据：同一会话的已捕获边界内，相同工具及完整参数；不是处理意图或修复关系的证明。', '',
                 fenced(operation(other)), '', '### 返回结果', '',
                 fenced(other.get('tool_response', '未捕获返回'))]
        if not other.get('tool_response'):
            body += ['', '> 空返回不表示成功。']
    if not linked:
        body += ['', '> 未捕获到符合边界规则的相同输入再次调用。']
    body += ['', '## 已保存来源', '']
    for event in events:
        body += ['### 来源 #' + str(event['seq']), '', 'SHA-256：' + event['digest'], '',
                 fenced(json.loads(event['content'])), '']
    body += ['', '## 覆盖与保留说明', '', COVERAGE, '', RETENTION,
             '', '最多附上 20 次相同输入调用；遇到下一次匹配错误、新用户输入、助手结束、回合变化或会话中断/结束即停止。',
             '不同参数的处理、跨会话操作及未投递事件不会被自动补入。',
             '来源是经过字段选择、脱敏及可能截断的保存副本；不等同于完整宿主原始事件。',
             '来源可能包含敏感信息；下载不上传，分享前请自行检查。']
    if any(json.loads(e['content']).get('_coverage', {}).get('truncated_fields') for e in events):
        body += ['', '> 部分来源字段已截断，请以来源标记为准。']
    if capture_gap:
        body += ['', '> 当前会话存在已知采集缺口，不能证明跨缺口的回合和操作关系；已停止自动关联。']
    if any('\ufffd' in e['content'] for e in events):
        body += ['', '> 来源含编码替换字符，原始文字可能已丢失。']
    return {'id': str(seq), 'title': hit['title'], 'project': first['project'],
            'tool': content.get('tool_name'), 'captured': first['captured'],
            'source_seq': bound, 'rule_version': RULE_VERSION, 'events': events,
            'signal': hit['signal'], 'association': 'same-input-repeat',
            'capture_gap': capture_gap,
            'markdown': '\n'.join(body) + '\n'}
