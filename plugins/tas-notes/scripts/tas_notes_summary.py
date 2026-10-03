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
    value = value.replace('[来源 #', '［来源 #')
    return value[:limit] + ('…（摘录，完整内容见来源）' if len(value) > limit else '')


def readable(record):
    events = record['events']
    count = lambda kind: sum(e['kind'] == kind for e in events)
    parts = ['## 过程概览',
             '- **记录范围**：%s 次用户输入 · %s 次工具操作 · %s 次 Agent 陈述。' %
             (count('UserPromptSubmit'), count('PostToolUse'), count('Stop')),
             '- **整理方式**：按事件组织的自动概览；尚未生成语义总结。',
             '- **当前结论**：未自动判定解决或验收；未提供的理由仍未知。',
             '## 工作经过']
    turn = 0
    for event in events:
        c = json.loads(event['content'])
        ref = f"[来源 #{event['seq']}]"
        if isinstance(c.get('prompt'), str):
            turn += 1
            prompt = c['prompt']
            if '\n## My request:' in prompt:
                prompt = prompt.split('\n## My request:', 1)[1]
            parts += [f'### 第 {turn} 轮 · 要求与进展',
                      f'**用户要求 / 反馈** {ref}', '> ' + excerpt(prompt, 700)]
        if event['kind'] == 'PostToolUse':
            name = excerpt(c.get('tool_name'), 80) or '未命名工具'
            parts.append(f'- **操作**：{name}。输入与返回见来源。{ref}')
            if c.get('error'):
                parts.append(f'- **异常信号**：来源带有错误字段，需核对影响。{ref}')
            elif not any(c.get(k) not in (None, '', {}, []) for k in ('tool_response', 'tool_result')):
                parts.append(f'- **结果缺口**：工具返回缺失或为空，不能判断成功。{ref}')
            else:
                parts.append(f'- **观察**：已捕获工具返回；未自动核实成功或失败。{ref}')
        elif c.get('error'):
            parts.append(f'- **异常信号**：来源带有错误字段，不能推断整个任务结果。{ref}')
        if isinstance(c.get('last_assistant_message'), str):
            parts += [f'**Agent 反馈（待核实）** {ref}', '> ' + excerpt(c['last_assistant_message'], 1200)]
        if event['kind'] in ('Interrupt', 'PreCompact', 'PostCompact'):
            label = {'Interrupt': '任务中断', 'PreCompact': '上下文压缩前', 'PostCompact': '上下文压缩后'}[event['kind']]
            detail = '本条没有可用的正文或原因' if not any(c.get(k) for k in ('message', 'reason')) else '详情见来源'
            parts.append(f'- **过程节点**：{label}；{detail}。{ref}')
        if '\ufffd' in event['content']:
            parts.append(f'- **覆盖缺口**：材料可能已发生编码丢失。{ref}')
        if c.get('_coverage', {}).get('truncated_fields'):
            parts.append(f'- **覆盖缺口**：材料因大小限制截断。{ref}')
    parts += ['## 待确认',
              '- **结果**：本版未提取结构化退出状态，Agent 的完成陈述不等于独立验证。',
              '- **理由与依据**：使用“获取 Codex 整理指令”生成有来源的总结；缺失的理由不能编造。',
              '- **用户验收**：需由你核对并说明；人工保存也不自动表示验收通过。']
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
        safe_quote = ' '.join(quote.split()).replace('[来源 #', '［来源 #')
        parts.append(f"- [来源 #{seq}] 「{safe_quote}」")
    return '\n\n'.join(parts)
