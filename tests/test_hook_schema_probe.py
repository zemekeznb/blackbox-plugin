import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from probe_hook_schema import record


class HookSchemaProbe(unittest.TestCase):
    def test_scope_expiry_and_no_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            c={'enabled':True,'expires':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
               'project':'D:/selected','commands':{'success':'probe'}}
            p={'cwd':'D:/selected','hook_event_name':'PostToolUse','tool_name':'Bash',
               'tool_input':{'command':'probe'},'tool_response':'PRIVATE_SENTINEL','unknown':{'stderr':'PRIVATE_SENTINEL'}}
            (root/'config.json').write_text(json.dumps(c))
            for change in ({'cwd':'D:/other'},{'tool_name':'Other'},{'tool_input':{'command':'other'}}):
                record(root,json.dumps(p|change).encode())
            self.assertFalse((root/'schema.jsonl').exists())
            record(root,json.dumps(p).encode());raw=(root/'schema.jsonl').read_text()
            self.assertNotIn('PRIVATE_SENTINEL',raw)
            self.assertEqual(json.loads(raw)['fields']['unknown'],{'stderr':'str'})
            c['expires']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            (root/'config.json').write_text(json.dumps(c));record(root,json.dumps(p).encode())
            self.assertEqual((root/'schema.jsonl').read_text(),raw)

    def test_forwards_exact_bytes_and_exit_even_without_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);child=root/'child.py'
            child.write_text('import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\nsys.exit(7)\n')
            wrapper=Path(__file__).resolve().parents[1]/'tools/probe_hook_schema.py'
            for data in (b'\xffinvalid\x00',b'{"tool_response":"PRIVATE_SENTINEL"}\r\n'):
                r=subprocess.run([sys.executable,str(wrapper),tmp,str(child)],input=data,capture_output=True,timeout=12)
                self.assertEqual(r.stdout,data);self.assertEqual(r.returncode,7)
            self.assertFalse((root/'schema.jsonl').exists())
