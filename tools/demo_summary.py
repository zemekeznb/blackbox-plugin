"""Synthetic modeling review fixture, not evidence of real model generation."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store
from tas_notes_summary import SECTIONS
from tas_notes_web import serve

root = Path(tempfile.mkdtemp(prefix='tas-notes-summary-'))
project = root / 'project'
project.mkdir()
store = Store(root / 'state')
store.set_project(str(project), True)
events = [('UserPromptSubmit', {'prompt': '【合成示例】制作红屋顶小屋。'}),
          ('PostToolUse', {'tool_name': 'blender.execute', 'tool_input': {'command': 'synthetic_boolean()'},
                           'tool_response': '【合成示例】布尔操作失败。'}),
          ('Stop', {'last_assistant_message': '【合成示例】改用独立屋顶，尚未检查渲染效果。'}),
          ('UserPromptSubmit', {'prompt': '【合成示例】屋顶还是太高。<img src=x onerror=alert(1)>'})]
for kind, fields in events:
    identifier = store.record(dict(cwd=str(project), session_id='synthetic', hook_event_name=kind, **fields))['investigation']
source = store.summary_source(identifier)
items = [
    [('用户希望制作红屋顶小屋。', 1, '制作红屋顶小屋')],
    [('工具报告布尔操作失败。', 2, '布尔操作失败'),
     ('Agent 表示改用独立屋顶，渲染效果尚未检查。', 3, '改用独立屋顶')],
    [],
    [('用户随后指出屋顶还是太高，不能视作已验收。', 4, '屋顶还是太高')],
    [('渲染效果仍待检查。', 3, '尚未检查渲染效果')]]
payload = {k: source[k] for k in ('record_id', 'source_seq', 'fingerprint', 'expected_summary')}
payload['sections'] = [dict(heading=h, items=[dict(text=t, sources=[dict(seq=n, quote=q)]) for t,n,q in rows])
                       for h, rows in zip(SECTIONS, items)]
store.save_summary(payload)
store.set_project(str(project), False)
print('SYNTHETIC FIXTURE: hand-authored summary; not model-generation evidence.', flush=True)
serve(store)
