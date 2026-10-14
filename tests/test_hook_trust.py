import sys
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts'))
import blackbox_hooks as hooks


class TrustTests(unittest.TestCase):
    def rows(self):
        return [dict(pluginId='blackbox@personal', eventName=e, enabled=True,
                     trustStatus='trusted') for e in hooks.REQUIRED]

    def test_complete_trusted(self):
        self.assertEqual(hooks.classify(self.rows()), 'trusted')

    def test_one_untrusted_blocks(self):
        rows = self.rows(); rows[0]['trustStatus'] = 'untrusted'
        self.assertEqual(hooks.classify(rows), 'hooks_untrusted')

    def test_disabled_and_missing(self):
        rows = self.rows(); rows[0]['enabled'] = False
        self.assertEqual(hooks.classify(rows), 'hooks_disabled')
        self.assertEqual(hooks.classify(rows[1:]), 'hooks_missing')

    def test_legacy_trust_does_not_count(self):
        rows = self.rows()
        for row in rows: row['pluginId'] = 'tas-notes@personal'
        self.assertEqual(hooks.classify(rows), 'hooks_missing')

    def test_transport_failure_is_unknown(self):
        with patch.object(hooks.HookInspector, '_lookup', return_value={}):
            self.assertEqual(hooks.inspect(['X']), {'X': 'hooks_unknown'})

    def test_project_results_do_not_leak(self):
        with patch.object(hooks.HookInspector, '_lookup', return_value={'A':'trusted','B':'hooks_untrusted'}):
            self.assertEqual(hooks.inspect(['A','B','C']),
                             {'A':'trusted','B':'hooks_untrusted','C':'hooks_unknown'})

    def test_status_blocks_waiting_and_preserves_project_authorization(self):
        from blackbox_core import Store
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            store = Store(root / 'state'); store.set_project(str(project), True)
            (store.directory / 'runtime.cmd').write_text('@"' + sys.executable + '" %*\n', encoding='ascii')
            with patch('blackbox_hooks.inspect', return_value={str(project): 'hooks_untrusted'}):
                value = store.status()
                self.assertEqual(value['projects'][0]['capture_state'], 'hooks_untrusted')
                self.assertTrue(value['projects'][0]['enabled'])
                self.assertEqual(value['capture_health']['state'], 'hooks_untrusted')
            with patch('blackbox_hooks.inspect', return_value={str(project): 'trusted'}):
                self.assertEqual(store.status()['projects'][0]['capture_state'], 'awaiting_event')

    def test_managed_hooks_and_changed_definition(self):
        rows = self.rows(); rows[0]['trustStatus'] = 'managed'
        self.assertEqual(hooks.classify(rows), 'trusted')
        rows[0]['trustStatus'] = 'modified'
        self.assertEqual(hooks.classify(rows), 'hooks_untrusted')

if __name__ == '__main__':
    unittest.main()
