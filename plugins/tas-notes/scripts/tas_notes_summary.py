"""Read-only rendering of existing archives; no summary generation or writes."""
import json


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
              '- **理由与依据**：历史材料未明确提供的理由保持未知。',
              '- **用户验收**：需由你核对并说明；人工保存也不自动表示验收通过。']
    return '\n\n'.join(parts)


def render_summary(summary):
    parts = ['## Agent 整理草稿', '历史 Agent 草稿；引用仅表明材料存在，不证明解释正确；未自动判定用户验收。']
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
