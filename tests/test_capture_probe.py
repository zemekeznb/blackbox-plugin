from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts'))
from blackbox_core import Store
from blackbox_capture_probe import CaptureProbe, TOOL


class CaptureProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        self.store = Store(self.root / 'state')
        self.store.set_project(str(self.project), True)
        self.log = self.store.directory / 'capture-probe.jsonl'

    def arm(self, **changes):
        config = dict(enabled=True, project=str(self.project.resolve()),
                      expires=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())
        config.update(changes)
        (self.store.directory / 'capture-probe.json').write_text(json.dumps(config), encoding='utf-8')

    def test_disabled_expired_and_wrong_project_never_log(self):
        CaptureProbe(self.store.directory)
        for change in ({'enabled': False}, {'project': 'other'}, {'expires': '2000-01-01T00:00:00+00:00'},
                       {'expires': (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}):
            self.arm(**change)
            with patch('pathlib.Path.cwd', return_value=self.project): CaptureProbe(self.store.directory)
        self.assertFalse(self.log.exists())

    def test_target_error_has_stages_but_never_body(self):
        self.arm()
        payload = dict(cwd=str(self.project), session_id='synthetic', hook_event_name='PostToolUse',
                       tool_name=TOOL, tool_input={'case':'error', 'private':'PRIVATE_SENTINEL'},
                       tool_response={'isError': True, 'content':[{'type':'text','text':'PRIVATE_SENTINEL'}]})
        with patch('pathlib.Path.cwd', return_value=self.project):
            self.assertEqual(self.store.record_stream(io.BytesIO(json.dumps(payload).encode()))['status'],'recorded')
        text = self.log.read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE_SENTINEL', text)
        rows = [json.loads(l) for l in text.splitlines()]
        self.assertEqual([r['phase'] for r in rows], ['entry','received','complete'])
        self.assertTrue(rows[1]['is_error']); self.assertEqual(rows[2]['status'],'recorded')

    def test_bad_json_logs_rejection_without_echo_and_still_raises(self):
        self.arm()
        with patch('pathlib.Path.cwd', return_value=self.project), self.assertRaises(ValueError):
            self.store.record_stream(io.BytesIO(b'PRIVATE_SENTINEL'))
        text = self.log.read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE_SENTINEL',text)
        self.assertEqual([json.loads(l)['phase'] for l in text.splitlines()], ['entry','rejected'])

    def test_capacity_and_broken_config_do_not_break_capture(self):
        self.arm()
        self.log.write_text('x'*65536)
        with patch('pathlib.Path.cwd', return_value=self.project): CaptureProbe(self.store.directory)
        self.assertEqual(self.log.stat().st_size,65536)
        (self.store.directory/'capture-probe.json').write_text('[]')
        with patch('pathlib.Path.cwd', return_value=self.project): CaptureProbe(self.store.directory)


if __name__ == '__main__': unittest.main()
