"""Deterministic, source-backed observations. No model calls or repair verdicts."""
from datetime import datetime
import hashlib
import json
import re
from tas_notes_shell_errors import shell_diagnostic, direct_program, literal_content_read, content_read_diagnostic, absolute_content_read, content_read_path
from tas_notes_business_errors import business_error
from tas_notes_curl_errors import curl_error
from tas_notes_http_errors import http_error
from tas_notes_curl_result import curl_result

from tas_notes_core import NotesError, canonical

RULE_VERSION = 'observed-errors-17'
COVERAGE = ('识别 Bash 返回中的完整 Python traceback、指定 PowerShell 诊断格式，'
            '支持同一命令显式模块限定绑定的读取别名；普通 cat/gc/type 的有限读取语法按歧义排除正文诊断，'
            '只保留完整匹配目标的指定读取错误（简单相对路径需整段路径后缀匹配）；不证明别名身份，隐式重定义可能漏记。'
            '指定单输出流 curl --write-out 尾部 JSON 支持传输失败和 HTTP 状态；不猜测拆分旧混合文本。'
            '以及直接调用 Git/Node.js 时的指定 Git 诊断/完整 Node.js 异常栈；'
            '以及已保存的完整 MCP 结果对象中布尔值 isError=true；'
            '直接 curl.exe JSON POST 的完整 HTTP 2xx/4xx/5xx 响应支持 JSON-RPC 2.0 单条/批量错误，'
            '以及专用媒体类型下的 GraphQL 错误（含部分数据）；明确请求 GraphQL 时也支持普通 JSON 的 2xx 返回。'
            '指定直接 curl.exe 调用还识别完整独立的主机解析、连接失败和超时诊断；不推断超时阶段。'
            '指定带响应头的 curl.exe 返回也识别单个 HTTP 4xx/5xx 错误状态，保留正文，不推断业务含义或责任。'
            '支持指定 fail 选项、重定向及中间响应链；只观察最终状态，curl (22) 与状态不重复列出。'
            '另识别指定完整 curl TLS/证书验证、响应不完整、连接重置、重定向超限和空回复诊断。'
            '指定诊断可位于 HTTP 响应之前，或仅含合法中间响应/已跟随重定向头的链尾；'
            '不从最终响应正文中寻找诊断，不根据声明长度切割已解码正文。'
            '明确直接 curl.exe 的返回不回退为 Python/PowerShell 正文错误；保留宿主进程启动失败格式。'
            '受限字面量 Get-Content 读取不将正文 traceback 视为 Python 执行异常；'
            '绝对路径读取只接受与目标一致的指定 PowerShell 缺失路径诊断；相对路径、别名、自指日志和进程启动格式仍有歧义。'
            '指定 GraphQL SSE 单操作完整 next/complete 序列支持明确 errors；不支持任意 SSE、复用连接或增量载荷。'
            '不解释任意 success:false，不把批量中的错误推断为整批失败。'
            '格式命中或工具自报错误不是独立验证的故障。Bash 打印日志也可能命中。'
            '当前宿主部分失败调用未投递可见记录；未送达的错误、未验证的退出状态、内部重试及拒绝可能漏记。'
            '空返回不表示成功；没有匹配记录不代表没有问题。')
RETENTION = '已启用项目中，经宿主确认的用户会话（含归档）的普通输入、工具事件和最后回复可能保存在本机；内部、临时及未确认身份的事件不保存。'
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


def is_mcp_tool(name):
    return isinstance(name, str) and re.fullmatch(r'mcp__[^\s]+__[^\s]+', name) is not None


def mcp_error(content):
    """Observe an explicit MCP result flag, never infer failure from result text.

    Live successful MCP deliveries establish the result-object shape. Controlled
    isError=true calls currently lack Hook delivery on the tested desktop path;
    support here is conditional on receipt, not a claim to recover missing calls.
    """
    name = content.get('tool_name')
    if not is_mcp_tool(name):
        return None
    result = content.get('tool_response')
    if (not isinstance(result, dict) or result.get('isError') is not True
            or result.get('truncated') is True
            or 'tool_response' in content.get('_coverage', {}).get('truncated_fields', [])):
        return None
    blocks = result.get('content')
    if (not isinstance(blocks, list)
            or any(not isinstance(block, dict) or not isinstance(block.get('type'), str)
                   or (block['type'] == 'text' and not isinstance(block.get('text'), str))
                   for block in blocks)):
        return None
    return {'rule': 'mcp-is-error', 'title': name + ' 返回错误',
            'evidence': json.dumps(result, ensure_ascii=False, indent=2),
            'signal': {'kind': 'tool_reported_error', 'source_field': 'tool_response.isError',
                       'format': 'mcp-is-error', 'segment': 'full', 'truncated': False,
                       'independently_verified': False}}


def detect(content):
    """Inspect tool-specific returned evidence, never prompts or tool arguments."""
    if content.get('tool_name') != 'Bash':
        return mcp_error(content)
    structured = curl_result(content)
    if structured:
        return structured
    network = curl_error(content)
    if network:
        return network
    business = business_error(content)
    if business:
        return business
    http = http_error(content)
    if http:
        return http
    native_curl = direct_program(content, ('curl.exe',))
    content_read = literal_content_read(content)
    ambiguous_read = content_read_path(content, ambiguous_alias=True) is not None
    for segment, body, truncated in response_segments(content):
        hit = None
        # Keep the previously observed PowerShell format contract. Python's
        # traceback grammar is broader, but still only an observation of text.
        if (re.match(r'^ResourceUnavailable:\s*\r?\nLine \|', body)
                and re.search(r"\| Program '[^\r\n]+' failed to run:", body)):
            hit = {'rule': 'powershell-process-start', 'title': '工具返回进程启动错误',
                   'evidence': body}
        # A literal native curl call owns its HTTP payload. Do not reinterpret
        # a fetched traceback/PowerShell example as a local runtime diagnostic.
        # The host's actual process-start failure above remains observable.
        if native_curl and hit is None:
            continue
        if ambiguous_read and hit is None:
            # A traceback may be file contents. Do not call it an execution
            # error solely because an unresolved alias returned those bytes.
            if not truncated:
                hit = content_read_diagnostic(content, body, ambiguous_alias=True)
            if hit is None:
                continue
        if content_read and hit is None and not truncated:
            hit = content_read_diagnostic(content, body)
        trace = None if content_read else python_traceback(body)
        if trace and hit is None:
            name, evidence = trace
            hit = {'rule': 'python-exception', 'title': '工具返回 ' + name, 'evidence': evidence}
        if hit is None and not absolute_content_read(content):
            hit = shell_diagnostic(content, body)
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
    if (not isinstance(tool, str) or not isinstance(args, dict)
            or (not args and not is_mcp_tool(tool))):
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


def context_filter(project_path='', investigation=''):
    # These are exact stored identifiers, not paths to resolve or newly authorize.
    for value, limit in ((project_path, 32767), (investigation, 200)):
        if not isinstance(value, str) or len(value) > limit:
            raise NotesError('invalid context filter')


def contexts(store, project_path=''):
    """Read scalar navigation metadata without loading prompts or event bodies."""
    context_filter(project_path)
    with store.connect() as db:
        db.execute('BEGIN')
        projects = [{'path': row['path'], 'enabled': bool(row['enabled']),
                     'last_captured': row['last_captured']}
                    for row in db.execute('''
                        SELECT p.path,p.enabled,MAX(e.captured) AS last_captured
                        FROM projects p LEFT JOIN investigations i ON i.project=p.path
                        LEFT JOIN events e ON e.investigation=i.id
                        GROUP BY p.path ORDER BY MAX(e.seq) DESC,p.path''')]
        sessions = [dict(row) for row in db.execute('''
            SELECT i.id,i.session,MIN(e.captured) AS first_captured,
                MAX(e.captured) AS last_captured
            FROM investigations i JOIN events e ON e.investigation=i.id
            WHERE i.project=? GROUP BY i.id
            ORDER BY MAX(e.seq) DESC,i.id''', (project_path,))] if project_path else []
        from tas_notes_sessions import archived_ids
        archived = archived_ids(db)
        for session in sessions:
            session['archived'] = session['id'] in archived
        db.commit()
    return {'projects': projects, 'sessions': sessions}


def saved_sessions(store, identifiers):
    """Resolve only known, event-bearing records; never accept arbitrary host IDs."""
    if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 20
            or any(not isinstance(value, str) or not value or len(value) > 200
                   for value in identifiers)):
        raise NotesError('invalid session selection')
    identifiers = list(dict.fromkeys(identifiers))
    with store.connect() as db:
        rows = db.execute('''SELECT i.id,i.session,i.project FROM investigations i
            WHERE i.id IN (''' + ','.join('?' for _ in identifiers) + ''')
            AND EXISTS(SELECT 1 FROM events e WHERE e.investigation=i.id)''', identifiers).fetchall()
    result = {row['id']: {'session': row['session'], 'project_path': row['project']} for row in rows}
    if len(result) != len(identifiers):
        raise NotesError('unknown session selection')
    return result


def listing(store, project='', tool='', start='', end='', before=None, through=None,
            project_path='', investigation='', _include_total=True, anomaly_type='', _count_only=False):
    for value in (project, tool, anomaly_type):
        if not isinstance(value, str) or len(value) > 200:
            raise NotesError('invalid filter')
    context_filter(project_path, investigation)
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
        rows = db.execute('''SELECT e.seq,e.captured,e.content,i.project,i.session,i.id AS investigation
            FROM events e
            JOIN investigations i ON i.id=e.investigation
            WHERE e.kind='PostToolUse' AND e.seq<? AND e.seq<=?
                AND (?='' OR i.project=?) AND (?='' OR i.id=?)
            ORDER BY e.seq DESC LIMIT ?''', (upper, snapshot, project_path, project_path,
                                           investigation, investigation, MAX_SCAN + 1)).fetchall()
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
            if not hit or (anomaly_type and exception_type(hit) != anomaly_type):
                continue
            items.append({'id': str(row['seq']), 'title': hit['title'], 'tool': name,
                          'signal': hit['signal'], 'anomaly_type': exception_type(hit),
                          'investigation': row['investigation'], 'session': row['session'],
                          'project': row['project'], 'captured': row['captured']})
            if len(items) == PAGE_SIZE and not _count_only:
                break
    result = {'items': items, 'next_before': str(rows[scanned - 1]['seq']) if len(rows) > scanned else None,
            'snapshot_seq': snapshot, 'scanned_count': scanned, 'has_newer': latest > snapshot,
            'index_pending': False, 'rule_version': RULE_VERSION, 'coverage': COVERAGE, 'retention': RETENTION}
    if _include_total:
        # A scan window is an implementation limit, not a user-visible empty
        # page. Continue empty display windows at the same frozen snapshot.
        # Internal count calls remain bounded and do not recursively continue.
        while not result['items'] and result['next_before'] is not None:
            next_page = listing(store, project, tool, start, end, before=result['next_before'],
                                through=snapshot, project_path=project_path,
                                investigation=investigation, _include_total=False, anomaly_type=anomaly_type)
            next_page['scanned_count'] += result['scanned_count']
            next_page['has_newer'] = next_page['has_newer'] or result['has_newer']
            result = next_page
        # Count the filtered snapshot independently of the display cursor.
        # Any display windows skipped above contained zero matches, so starting
        # from the resulting first page cannot omit or double-count a match.
        # Each bounded batch releases its read lock before parsing output.
        page = result if before is None else listing(
            store, project, tool, start, end, through=snapshot,
            project_path=project_path, investigation=investigation, _include_total=False, anomaly_type=anomaly_type, _count_only=True)
        # Reuse the required total scan to fill a page of matching anomalies.
        # Raw scan windows must not create partial display pages or empty tails.
        total, display, has_more = 0, [], False
        while True:
            total += len(page['items'])
            for item in page['items']:
                if int(item['id']) < upper:
                    if len(display) < PAGE_SIZE:
                        display.append(item)
                    else:
                        has_more = True
            result['has_newer'] = result['has_newer'] or page['has_newer']
            if page['next_before'] is None:
                break
            page = listing(store, project, tool, start, end, before=page['next_before'],
                           through=snapshot, project_path=project_path,
                           investigation=investigation, _include_total=False, anomaly_type=anomaly_type, _count_only=True)
        result['items'] = display
        result['next_before'] = display[-1]['id'] if has_more else None
        result['total_count'] = total
        enrich_list_items(store, result['items'], snapshot)
    return result


def path_summary(path, suffix=''):
    """Display the leaf without filesystem access; preserve evidence in the title."""
    full = path + suffix
    # Special traceback locations and URI-like inputs are not local paths.
    if (path.startswith('<') and path.endswith('>')) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*://', path):
        leaf = path
    else:
        stripped = path.rstrip('/\\')
        leaf = re.split(r'[/\\]', stripped)[-1] if stripped else path
        if stripped and path[-1:] in ('/', '\\'):
            leaf += path[-1]
    display = (leaf[:159] + '…' if len(leaf) > 160 else leaf) + suffix
    return display, full[:4096] + ('…' if len(full) > 4096 else '')


def source_summary_parts(content):
    """Short card text and original location; neither is a diagnosis."""
    hit = detect(content)
    evidence = hit.get('evidence', '') if hit else ''
    if isinstance(evidence, str):
        paths = re.findall(r"(?:FileNotFoundError|PermissionError):[^\n]*?[\"']([^\"'\n]+)[\"']\s*$", evidence)
        if paths:
            return path_summary(paths[-1])
        frames = re.findall(r'File "([^"\n]+)", line (\d+)', evidence)
        if frames:
            path, line = frames[-1]
            return path_summary(path, f' · line {line}')
    value = content.get('tool_input')
    if isinstance(value, dict):
        for field in ('file_path', 'path'):
            if isinstance(value.get(field), str) and value[field].strip():
                return path_summary(value[field].strip())
    command = operation(content)
    if isinstance(command, str) and command.strip():
        text = '执行：' + command.strip().splitlines()[0][:150]
        return text, text
    return '', ''


def enrich_list_items(store, items, snapshot):
    # Read only as far as the requested sources need, releasing every batch's
    # read transaction before parsing. New arrivals are excluded by snapshot.
    bounds = {}
    for item in items:
        key = item['investigation']
        bounds[key] = min(bounds.get(key, int(item['id'])), int(item['id']))
    wanted = {key: {int(item['id']) for item in items if item['investigation'] == key} for key in bounds}
    indexed = {key: list_repeat_index(repeat_rows(store, key, lower, snapshot), wanted[key])
               for key, lower in bounds.items()}
    # Session deletion removes all its events atomically. If it happened during
    # the bounded reads, none of its partial counts may be presented as final.
    present = set()
    if items:
        ids = [int(item['id']) for item in items]
        with store.connect() as db:
            present = {row[0] for row in db.execute(
                'SELECT seq FROM events WHERE seq IN (' + ','.join('?' for _ in ids) + ')', ids)}
    for item in items:
        contents, counts = indexed[item['investigation']]
        seq = int(item['id'])
        content = contents.get(seq)
        item['source_summary'] = ''
        item['source_summary_full'] = ''
        item['repeat_count'] = None  # A concurrent deletion is not proof of zero repeats.
        if content is None or seq not in present:
            continue
        item['source_summary'], item['source_summary_full'] = source_summary_parts(content)
        item['repeat_count'] = counts[seq]


def repeat_rows(store, investigation, lower, snapshot):
    """Bounded keyset reads; no cursor or read lock lives across a yield."""
    while lower <= snapshot:
        with store.connect() as db:
            db.execute('BEGIN')
            rows = db.execute(
                'SELECT seq,kind,content FROM events WHERE investigation=? AND seq>=? AND seq<=? ORDER BY seq LIMIT ?',
                (investigation, lower, snapshot, MAX_SCAN)).fetchall()
            from tas_notes_cleanup import with_boundaries
            expanded = with_boundaries(db, investigation, rows, lower, rows[-1]['seq']) if rows else []
            db.commit()
        if not rows:
            return
        yield from expanded
        lower = rows[-1]['seq'] + 1
        if len(rows) < MAX_SCAN:
            return


def list_repeat_index(rows, wanted=None):
    """Single forward pass, retaining only requested sources and active counts.

    Active sources are grouped by turn and exact input key. Boundaries discard
    groups; matching tool calls advance only their own sources, up to the same
    error/20-call cutoff as linked_events. SQLite rows are materialized before
    each batch so parsing never extends the database read lock.
    """
    remaining = set(wanted) if wanted is not None else None
    contents, counts, active = {}, {}, {}
    for row in rows:
        if remaining is not None and not remaining and not active:
            break
        seq, kind = row['seq'], row['kind']
        requested = remaining is None or seq in remaining
        content = json.loads(row['content'])
        coverage = content.get('_coverage', {})
        turn = content.get('turn_id')
        turn = turn if isinstance(turn, str) and turn else None
        hard = (kind in ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd')
                or coverage.get('capture_boundary_version') != 1
                or coverage.get('capture_gap_before'))
        if hard:
            active.clear()
        elif turn:
            active = {turn: active[turn]} if turn in active else {}
        elif kind == 'PostToolUse':
            active = {None: active[None]} if None in active else {}
        key = target_key(content) if requested or (active and kind == 'PostToolUse') else None
        if not hard and kind == 'PostToolUse' and key:
            group = active.get(turn)
            matches = group.get(key, []) if group else []
            if matches:
                error = bool(detect(content))
                pending = []
                for source in matches:
                    counts[source] += 1
                    if not error and counts[source] < 20:
                        pending.append(source)
                if pending:
                    group[key] = pending
                else:
                    del group[key]
                    if not group:
                        del active[turn]
        # Current boundaries constrain earlier sources, not the current source.
        if requested:
            contents[seq] = content
            counts[seq] = 0
            if remaining is not None:
                remaining.discard(seq)
            if key and coverage.get('capture_boundary_version') == 1:
                active.setdefault(turn, {}).setdefault(key, []).append(seq)
        if remaining is not None and not remaining and not active:
            break
    return contents, counts


def exception_type(hit):
    rule = hit['rule']
    if rule == 'python-exception':
        return hit['title'].removeprefix('工具返回 ')[:200]
    if rule == 'node-exception':
        return 'Node.js 异常'
    if rule.startswith('powershell-'):
        return 'PowerShell 错误'
    if rule.startswith('git-'):
        return 'Git 错误'
    if 'graphql' in rule:
        return 'GraphQL 错误'
    if 'json-rpc' in rule or 'jsonrpc' in rule:
        return 'JSON-RPC 错误'
    if 'http' in rule:
        return 'HTTP 错误状态'
    if rule.startswith('curl-'):
        return '网络与传输错误'
    if rule == 'mcp-is-error':
        return '工具报告错误'
    return '其他明确错误'


def anomaly_types(store, investigation, through=None):
    saved_sessions(store, [investigation])
    counts = {}
    page = listing(store, investigation=investigation, through=through, _include_total=False)
    snapshot = page['snapshot_seq']
    while True:
        for item in page['items']:
            label = item['anomaly_type']
            counts[label] = counts.get(label, 0) + 1
        if page['next_before'] is None:
            break
        page = listing(store, investigation=investigation, through=snapshot,
                       before=page['next_before'], _include_total=False)
    workspace_has_anomalies = bool(counts) or anomaly_presence(store, snapshot)['has_anomalies']
    return {'types': [{'name': key, 'count': counts[key]} for key in sorted(counts)],
            'workspace_has_anomalies': workspace_has_anomalies,
            'snapshot_seq': snapshot}


def anomaly_presence(store, through=None):
    """Existence across saved local sessions, pinned to one snapshot."""
    page = listing(store, through=through, _include_total=False, _count_only=True)
    snapshot = page['snapshot_seq']
    while not page['items'] and page['next_before'] is not None:
        page = listing(store, through=snapshot, before=page['next_before'],
                       _include_total=False, _count_only=True)
    return {'has_anomalies': bool(page['items']), 'snapshot_seq': snapshot}


def fenced(value):
    value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    # Arbitrary raw output cannot break out of an exported Markdown code block.
    size = max([len(m[0]) for m in re.finditer(r'`+', value)] + [2]) + 1
    marker = '`' * size
    return marker + '\n' + value + '\n' + marker


def operation(content):
    value = content.get('tool_input', '未捕获输入')
    if content.get('tool_name') == 'Bash' and isinstance(value, dict):
        for field in ('command', 'cmd'):
            if isinstance(value.get(field), str):
                return value[field]
    return value


def linked_events(content, rows):
    linked, key = [], target_key(content)
    legacy_association_boundary = content.get('_coverage', {}).get('capture_boundary_version') != 1
    if legacy_association_boundary:
        key = None
    capture_gap = bool(content.get('_coverage', {}).get('capture_gap_before'))
    capture_gap_scope = content.get('_coverage', {}).get('capture_gap_scope') if capture_gap else None
    capture_gap_reason = content.get('_coverage', {}).get('capture_gap_reason') if capture_gap else None
    first_turn = content.get('turn_id')
    first_turn = first_turn if isinstance(first_turn, str) and first_turn else None
    for row in rows:
        if row['kind'] in ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd'):
            break
        other = json.loads(row['content'])
        if other.get('_coverage', {}).get('capture_boundary_version') != 1:
            legacy_association_boundary = True
            break
        if other.get('_coverage', {}).get('capture_gap_before'):
            capture_gap = True
            capture_gap_scope = other.get('_coverage', {}).get('capture_gap_scope')
            capture_gap_reason = other.get('_coverage', {}).get('capture_gap_reason')
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
    return linked, capture_gap, capture_gap_scope, capture_gap_reason, legacy_association_boundary


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
        from tas_notes_cleanup import with_boundaries
        rows = with_boundaries(db, first['investigation'], rows, seq+1, bound)
        db.commit()
    linked, capture_gap, capture_gap_scope, capture_gap_reason, legacy_association_boundary = linked_events(content, rows)
    events = [dict(first)] + linked
    body = ['# ' + hit['title'], '', '> 按工具返回格式或明确错误标记识别的观察记录；不是独立验证的 Bug 或修复结论。',
            '', '## 记录范围', '', '项目：', '', fenced(first['project']), '',
            '会话：', '', fenced(first['session']), '',
            '- 会话记录 ID：' + first['investigation'],
            '- 记录时间（本机落盘）：' + first['captured'],
            '- 来源事件：#' + str(seq), '- 规则：' + RULE_VERSION + '/' + hit['rule'],
            '- 快照截至事件：#' + str(bound), '', '## 执行操作', '',
            fenced(operation(content)), '', '## 错误返回', '', fenced(hit['evidence'])]
    readable_outputs = [hit['evidence']]
    for row in linked:
        other = json.loads(row['content'])
        readable_outputs.append(other.get('tool_response', '未捕获返回'))
        body += ['', '## 相同输入再次调用 · 来源 #' + str(row['seq']), '',
                 '> 依据：同一会话的已捕获边界内，相同工具及完整参数；不是处理意图或修复关系的证明。', '',
                 fenced(operation(other)), '', '### 返回结果', '',
                 fenced(other.get('tool_response', '未捕获返回'))]
        if not other.get('tool_response'):
            body += ['', '> 空返回不表示成功。']
    body += ['', '## 技术详情 · 已保存来源', '']
    for event in events:
        body += ['### 来源 #' + str(event['seq']), '', 'SHA-256：' + event['digest'], '',
                 fenced(json.loads(event['content'])), '']
    body += ['', '## 阅读提示', '',
             '这是一份已保存事件的局部快照，来源经过有限脱敏且可能截断；没有记录不代表没有异常。',
             '相同输入的后续调用仅作上下文，不构成修复结论。分享前请检查敏感信息；导出不会上传。']
    if any(json.loads(e['content']).get('_coverage', {}).get('truncated_fields') for e in events):
        body += ['', '> 部分来源字段已截断，请以来源标记为准。']
    if capture_gap:
        if capture_gap_scope == 'unknown_project' and capture_gap_reason == 'restored_store':
            body += ['', '> 备份恢复后的采集连续性未知，已停止跨恢复边界自动关联；这不证明发生过采集错误或丢失了事件。']
        elif capture_gap_scope == 'unknown_project' and capture_gap_reason == 'diagnostic_unreadable':
            body += ['', '> 采集诊断不可读或不一致，无法确定事件连续性，已保守停止自动关联；这不证明当前会话丢失了事件。']
        elif capture_gap_scope == 'unknown_project':
            body += ['', '> 存在无法确定所属项目或会话的采集拒收；为避免跨缺口误关联，已保守停止自动关联。这不证明当前会话丢失了事件。']
        else:
            body += ['', '> 当前会话存在已知采集缺口，不能证明跨缺口的回合和操作关系；已停止自动关联。']
    if legacy_association_boundary:
        body += ['', '> 旧来源未保存完整采集边界，未自动关联后续调用；来源仍按原样保留。这不证明旧会话实际丢失了事件。']
    if any('\ufffd' in fenced(output) for output in readable_outputs):
        body += ['', '> 部分文字可能显示不完整。']
    return {'id': str(seq), 'title': hit['title'], 'project': first['project'],
            'investigation': first['investigation'], 'session': first['session'],
            'tool': content.get('tool_name'), 'captured': first['captured'],
            'source_seq': bound, 'rule_version': RULE_VERSION, 'events': events,
            'signal': hit['signal'], 'association': 'same-input-repeat',
            'capture_gap': capture_gap,
            'capture_gap_scope': capture_gap_scope,
            'capture_gap_reason': capture_gap_reason,
            'legacy_association_boundary': legacy_association_boundary,
            'markdown': '\n'.join(body) + '\n'}
