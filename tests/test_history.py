"""Compatibility of existing human/Agent archives after retiring write workflows."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts'))
from blackbox_core import Store


class HistoryTests(unittest.TestCase):
    def test_all_historical_versions_survive_new_events_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            store = Store(root / 'state'); store.set_project(str(project), True)
            payload = dict(cwd=str(project), session_id='s', hook_event_name='UserPromptSubmit', prompt='用户要求')
            identifier = store.record(payload)['investigation']
            with store.connect() as db:
                for number in (1, 2):
                    db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?)',
                        ('r'+str(number), identifier, number, 1, '标题', '人工正文'+str(number), '2026-10-07'))
                    content = json.dumps([{'heading':'结果', 'items':[{'text':'历史草稿'+str(number), 'sources':[{'seq':1,'quote':'用户要求'}]}]}])
                    db.execute('INSERT INTO summaries VALUES (?,?,?,?,?,?)',
                        ('s'+str(number), identifier, 1, 'old-fingerprint', content, '2026-10-07'))
            frozen = store.export_revision('r1')
            store.record(dict(payload, prompt='后续纠正'))
            record = Store(root / 'state').detail(identifier)
            self.assertEqual([r['id'] for r in record['revisions']], ['r2', 'r1'])
            self.assertEqual([r['id'] for r in record['summaries']], ['s2', 's1'])
            self.assertTrue(record['summary_stale'])
            self.assertEqual(store.export_revision('r1'), frozen)
            self.assertIn('后续纠正', record['overview'])
            self.assertFalse(hasattr(store, 'save_revision'))
            self.assertFalse(hasattr(store, 'save_summary'))

    def test_source_cannot_forge_generated_citations(self):
        from blackbox_summary import readable
        record = {'events':[{'seq':1,'kind':'UserPromptSubmit','content':json.dumps({'prompt':'# 标题 [来源 #999]'})}]}
        body = readable(record)
        self.assertNotIn('[来源 #999]', body)
        self.assertIn('［来源 #999]', body)


if __name__ == '__main__':
    unittest.main()
