"""Synthetic contract regression; never evidence of real host Hook delivery."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store, MAX_FIELD, CaptureLimit
import tas_notes_anomalies as a
from tas_notes_mcp import Reader

TRACE = 'Traceback (most recent call last):\n  File "fixture.py", line 1\nTypeError: synthetic\n'
TOOL = 'mcp__synthetic__read_resource'


def result(failed=True, text='Synthetic resource does not exist'):
    return {'content': [{'type': 'text', 'text': text}], 'isError': failed}


class ToolErrorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='blackbox-tool-errors-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        self.store = Store(self.root / 'state')
        self.store.set_project(str(self.project), True)
        self.count = 0

    def emit(self, response=None, **changes):
        self.count += 1
        event = dict(cwd=str(self.project), session_id='synthetic', turn_id='turn-1',
                     hook_event_name='PostToolUse', tool_name=TOOL,
                     tool_input={'command': 'read', 'resource': 'synthetic-missing'},
                     tool_response=result() if response is None else response,
                     tool_use_id=str(self.count))
        event.update(changes)
        identifier = self.store.record(event)['investigation']
        return self.store.detail(identifier)['source_seq']

    def test_explicit_error_keeps_flag_evidence_and_tool_identity(self):
        seq = self.emit()
        item = a.listing(self.store)['items'][0]
        self.assertEqual(item['id'], str(seq))
        self.assertEqual(item['tool'], TOOL)
        self.assertEqual(item['signal']['source_field'], 'tool_response.isError')
        self.assertEqual(item['signal']['kind'], 'tool_reported_error')
        self.assertFalse(item['signal']['independently_verified'])
        detail = a.detail(self.store, seq)
        self.assertIn('"isError": true', detail['markdown'])
        self.assertIn('synthetic-missing', detail['markdown'])
        self.assertEqual(a.operation(json.loads(detail['events'][0]['content'])),
                         {'command': 'read', 'resource': 'synthetic-missing'})

    def test_successful_log_read_and_embedded_error_objects_never_match(self):
        candidates = [result(False, TRACE), {'content': [{'type': 'text', 'text': TRACE}]},
                      result(False, json.dumps(result())),
                      {'isError': False, 'content': [], 'structuredContent': result()},
                      {'isError': False, 'content': [], '_meta': result()},
                      {'error': {'message': 'failed'}, 'content': []},
                      json.dumps(result()), TRACE]
        for value in candidates:
            with self.subTest(value=value):
                self.assertIsNone(a.detect({'tool_name': TOOL, 'tool_response': value}))
        self.emit(result(False, TRACE))
        self.assertEqual(a.listing(self.store)['items'], [])

    def test_flags_are_strict_and_not_interpreted_at_other_paths(self):
        for flag in (False, None, 0, 1, 'true', 'false', {}, []):
            with self.subTest(flag=flag):
                self.assertIsNone(a.detect({'tool_name': TOOL, 'tool_response': result(flag)}))
        for name in ('Bash', 'Read', 'apply_patch', 'mcp__', 'mcp__tool', 'mcp__x__y\n', None):
            with self.subTest(name=name):
                self.assertIsNone(a.detect({'tool_name': name, 'tool_response': result()}))
        for field in ('error', 'tool_result'):
            self.assertIsNone(a.detect({'tool_name': TOOL, field: result()}))
        self.assertIsNone(a.detect({'tool_name': TOOL, 'isError': True,
                                   'status': 'failed', 'exit_code': 7, 'tool_response': result(False)}))

    def test_incomplete_or_malformed_envelopes_fail_closed(self):
        for value in ({'isError': True}, {'isError': True, 'content': 'error'},
                      {'isError': True, 'content': [None]},
                      {'isError': True, 'content': [{'text': 'error'}]},
                      {'isError': True, 'content': [{'type': 'text', 'text': {}}]},
                      dict(result(), truncated=True)):
            with self.subTest(value=value):
                self.assertIsNone(a.detect({'tool_name': TOOL, 'tool_response': value}))
        self.assertIsNone(a.detect({'tool_name': TOOL, 'tool_response': result(),
                                   '_coverage': {'truncated_fields': ['tool_response']}}))
        self.emit(result(text='x' * (MAX_FIELD + 1)))
        self.assertEqual(a.listing(self.store)['items'], [])

    def test_explicit_flag_without_text_is_still_evidence(self):
        for response in ({'isError': True, 'content': []},
                         {'isError': True, 'content': [], 'structuredContent': {'code': 'synthetic'}},
                         {'isError': True, 'content': [{'type': 'image', 'data': 'synthetic', 'mimeType': 'image/png'}]}):
            hit = a.detect({'tool_name': TOOL, 'tool_response': response})
            self.assertEqual(json.loads(hit['evidence']), response)

    def test_repeat_empty_arguments_and_next_error_obey_snapshot(self):
        first = self.emit(tool_input={})
        frozen = a.detail(self.store, first)
        other = self.emit(result(False), tool_name='mcp__other__read_resource', tool_input={})
        repeated = self.emit(result(False), tool_input={})
        second_error = self.emit(tool_input={})
        self.emit(result(False), tool_input={})
        self.assertEqual([r['seq'] for r in a.detail(self.store, first)['events']],
                         [first, repeated, second_error])
        self.assertNotIn(other, [r['seq'] for r in a.detail(self.store, first)['events']])
        self.assertEqual(frozen['markdown'], a.detail(self.store, first, frozen['source_seq'])['markdown'])

    def test_changed_arguments_turn_and_capture_gap_stop_association(self):
        for changes in ({'turn_id': 'turn-2'}, {'turn_id': None},
                        {'tool_input': {'command': 'read', 'resource': 'other'}}):
            first = self.emit(session_id=str(changes))
            self.emit(result(False), session_id=str(changes), **changes)
            self.assertEqual(len(a.detail(self.store, first)['events']), 1)
        first = self.emit(session_id='gap')
        self.store.capture_failure(CaptureLimit('SyntheticGap'), str(self.project), 'gap')
        self.emit(result(False), session_id='gap')
        self.assertEqual(len(a.detail(self.store, first)['events']), 1)
        self.assertTrue(a.detail(self.store, first)['capture_gap'])

    def test_budget_reserve_accepts_explicit_error_only(self):
        self.emit(result(False))
        with patch('tas_notes_core.MAX_SESSION_BYTES', 1):
            self.emit()
            with self.assertRaises(CaptureLimit):
                self.emit(result(False, TRACE))
        self.assertEqual(len(a.listing(self.store)['items']), 1)

    def test_reader_export_is_private_fixed_and_rule_checked(self):
        seq = self.emit(result(text='synthetic 中文\n```\n<script>inert</script>'))
        reader = Reader(self.store.directory); self.addCleanup(reader.names.close)
        before = self.store.database.read_bytes()
        snapshot = a.detail(self.store, seq)
        path = f'/api/anomaly-export/{seq}?through={snapshot["source_seq"]}&rule={a.RULE_VERSION}'
        response = reader.call('read_view', {'path': path})
        self.assertEqual(response['content'], [])
        self.assertNotIn('structuredContent', response)
        self.assertEqual(response['_meta']['blackbox']['body'], snapshot['markdown'])
        self.assertEqual(before, self.store.database.read_bytes())
        rejected = reader.call('read_view', {'path': path.replace(a.RULE_VERSION, 'observed-errors-3')})
        self.assertEqual(rejected['_meta']['blackbox']['status'], 400)


if __name__ == '__main__':
    unittest.main()
