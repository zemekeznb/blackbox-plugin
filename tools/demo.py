"""Synthetic UI smoke data; never reads or enables a real project."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store
from tas_notes_web import serve

parser = argparse.ArgumentParser()
parser.add_argument('--add-correction', action='store_true')
args = parser.parse_args()
project = ROOT / '.cache' / 'demo-project'
project.mkdir(parents=True, exist_ok=True)
store = Store(ROOT / '.cache' / 'demo-state')
store.set_project(str(project), True)
events = [
    ('UserPromptSubmit', {'prompt': '【合成演示】请排查重复提交订单的问题。'}),
    ('PostToolUse', {'tool_name': 'test', 'tool_input': '运行重复提交测试',
                    'tool_response': '【合成演示】方案 A：调整按钮禁用时机；测试仍失败。'}),
    ('PostToolUse', {'tool_name': 'test', 'tool_input': '为请求增加幂等键并测试',
                    'tool_response': '【合成演示】方案 B：测试通过。真实工程证据尚未提供。'}),
    ('Stop', {'last_assistant_message': '【合成演示】Agent 判断问题已修复；用户尚未验收。'}),
]
for index, (kind, fields) in enumerate(events):
    store.record(dict(cwd=str(project), session_id='synthetic-demo', hook_event_name=kind,
                      event_id=f'demo-{index}', **fields))
if args.add_correction:
    store.record(dict(cwd=str(project), session_id='synthetic-demo', hook_event_name='UserPromptSubmit',
                      event_id='demo-correction', prompt='【合成演示】还是有问题：断网重试时会重复创建订单。'))
    print('Synthetic correction appended.')
else:
    print('SYNTHETIC DEMO ONLY. This does not prove live Codex capture.', flush=True)
    serve(store)
