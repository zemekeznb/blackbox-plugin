"""Readable local fallback and source-bound Agent drafts; no model/network calls."""
import hashlib
import json

SECTIONS = ('目标与要求', '尝试与调整', '理由与依据', '结果与反馈', '待确认与缺口')


def fingerprint(events):
    return hashlib.sha256(json.dumps([(e['seq'], e['digest']) for e in events],
                                     separators=(',', ':')).encode()).hexdigest()


def excerpt(value, limit=600):
    if not isinstance(value, str):
        return ''
    value = ' '.join(value.split())
    return value[:limit] + ('…（摘录，完整内容见来源）' if len(value) > limit else '')


def readable(record):
    parts = ['## 过程概览', '以下为自动摘录，尚未做语义整理。工具原文在来源中；未提供的理由未知，用户验收状态未判定。']
    turn = 0
    for event in record['events']:
        c = json.loads(event['content'])
        ref = f"[来源 #{event['seq']}]"
        # Keep visible speech even if the host attaches it to a different event kind.
        if isinstance(c.get('prompt'), str):
            turn += 1
            parts += [f'### 第 {turn} 轮 · 用户要求或反馈', f"{excerpt(c['prompt'], 1200)} {ref}"]
        if event['kind'] == 'PostToolUse':
            name = excerpt(c.get('tool_name'), 80) or '未命名工具'
            parts.append(f'- 调用了 {name}。结果尚未核实；输入和返回见来源。{ref}')
            if not any(c.get(k) not in (None, '', {}, []) for k in ('tool_response', 'tool_result')):
                parts.append(f'- 工具返回缺失或为空，不能判断成功。{ref}')
        if isinstance(c.get('last_assistant_message'), str):
            parts += ['### Agent 的说明（尚非独立验证）', f"{excerpt(c['last_assistant_message'], 1600)} {ref}"]
        if c.get('error'):
            parts.append(f'- 来源带有错误字段，请核对，不能由此推断整个任务结果。{ref}')
        if event['kind'] in ('Interrupt', 'PreCompact', 'PostCompact'):
            label = {'Interrupt': '任务中断', 'PreCompact': '上下文压缩前', 'PostCompact': '上下文压缩后'}[event['kind']]
            parts.append(f'- {label}；本条没有可用的正文或原因。{ref}' if not any(k in c for k in ('message', 'reason'))
                         else f'- {label}；详情见来源。{ref}')
        if '\ufffd' in event['content']:
            parts.append(f'- 材料可能已发生编码丢失，不猜测还原。{ref}')
        if c.get('_coverage', {}).get('truncated_fields'):
            parts.append(f'- 材料因大小限制截断，完整过程可能缺失。{ref}')
    parts += ['## 待确认', '本版未提取结构化退出状态；回合结束、工具调用和 Agent 的完成陈述均不等于用户验收。缺失的理由需核对，不能编造。']
    return '\n\n'.join(parts)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != '_coverage':
                yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def validate(payload, record):
    from tas_notes_core import NotesError, Conflict, text
    required = {'record_id', 'source_seq', 'fingerprint', 'expected_summary', 'sections'}
    if not isinstance(payload, dict) or set(payload) != required:
        raise NotesError('invalid summary fields')
    if payload['record_id'] != record['id'] or type(payload['source_seq']) is not int:
        raise NotesError('invalid summary record')
    if payload['source_seq'] != record['source_seq'] or payload['fingerprint'] != fingerprint(record['events']):
        raise Conflict('summary sources changed')
    expected = record['summary']['id'] if record.get('summary') else None
    if payload['expected_summary'] != expected:
        raise Conflict('summary changed')
    sections = payload['sections']
    if not isinstance(sections, list) or len(sections) != len(SECTIONS):
        raise NotesError('invalid sections')
    sources = {e['seq']: list(strings(json.loads(e['content']))) for e in record['events']}
    for section, heading in zip(sections, SECTIONS):
        if not isinstance(section, dict) or set(section) != {'heading', 'items'} or section['heading'] != heading:
            raise NotesError('invalid section')
        items = section['items']
        if not isinstance(items, list) or len(items) > 40:
            raise NotesError('invalid items')
        for item in items:
            if not isinstance(item, dict) or set(item) != {'text', 'sources'}:
                raise NotesError('invalid item')
            text(item['text'], 'summary text', 3000)
            # One paragraph per item: provenance cannot be detached by injected headings.
            if '\n' in item['text'] or '\r' in item['text'] or '[来源 #' in item['text']:
                raise NotesError('invalid summary paragraph')
            refs = item['sources']
            if not isinstance(refs, list) or not 1 <= len(refs) <= 12:
                raise NotesError('missing sources')
            for ref in refs:
                if not isinstance(ref, dict) or set(ref) != {'seq', 'quote'} or type(ref['seq']) is not int:
                    raise NotesError('invalid source')
                quote = text(ref['quote'], 'source quote', 1000)
                if not any(quote in s for s in sources.get(ref['seq'], [])):
                    raise NotesError('source quote does not match')
    return sections


def render_summary(summary):
    parts = ['## Agent 整理草稿', '尚待人工审阅。引用匹配仅证明材料存在，不证明解释正确；未自动判定用户验收。']
    citations = []
    for section in json.loads(summary['content']):
        parts.append('## ' + section['heading'])
        for item in section['items']:
            refs = ' '.join(f"[来源 #{r['seq']}]" for r in item['sources'])
            parts.append('- ' + item['text'] + ' ' + refs)
            for ref in item['sources']:
                pair = (ref['seq'], ref['quote'])
                if pair not in citations:
                    citations.append(pair)
        if not section['items']:
            parts.append('尚未整理出有来源支持的内容，需核对。')
    parts.append('## 来源短引')
    parts.append('以下为捕获材料中的短引，随人工版本一起审阅；不等于完整原文或独立核验。')
    for seq, quote in citations:
        parts.append(f"- [来源 #{seq}] 「{' '.join(quote.split())}」")
    return '\n\n'.join(parts)
