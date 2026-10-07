"""Synthetic anomaly page for visual QA; never enables a real project."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store
from tas_notes_web import serve

root = Path(tempfile.mkdtemp(prefix='tas-notes-anomaly-preview-'))
project = root / '合成建模项目'
project.mkdir()
store = Store(root / 'state')
store.set_project(str(project), True)
for number, command, output in [
    (1, 'python export_model.py', 'Traceback (most recent call last):\n  File "export_model.py", line 8, in <module>\nFileNotFoundError: output/model.blend\n'),
    (2, 'python export_model.py', 'Export finished: output/model.blend'),
    (3, 'python read_material.py', 'Traceback (most recent call last):\n  File "read_material.py", line 3, in <module>\njson.decoder.JSONDecodeError: Expecting property name: line 1 column 2\n')]:
    store.record({'cwd':str(project),'session_id':'synthetic-preview','hook_event_name':'PostToolUse',
                  'tool_use_id':str(number),'tool_name':'Bash','tool_input':{'command':command},'tool_response':output})
store.set_project(str(project), False)
print('Synthetic preview only. State: '+str(store.directory), flush=True)
serve(store)
