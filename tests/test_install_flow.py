"""Registration decisions with synthetic CLI responses; never touch user Codex."""
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts/blackbox_install.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('bb_install', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class InstallFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'package with spaces'
        self.home = Path(self.temp.name) / 'codex'
        self.plugin = self.root / 'plugins/blackbox'
        self.cache = self.home / 'plugins/cache/personal/blackbox/1.0.0'
        self.write(self.root / '.agents/plugins/marketplace.json', {'name': 'personal', 'plugins': [{'name': 'blackbox'}]})
        self.write(self.plugin / '.codex-plugin/plugin.json', {'name': 'blackbox', 'version': '1.0.0'})
        self.write(self.cache / '.codex-plugin/plugin.json', {'name': 'blackbox', 'version': '1.0.0'})
        self.calls = []
        self.markets = []
        self.inventory = []
        self.failure = None
        self.env = patch.dict('os.environ', {'CODEX_HOME': str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.which = patch.object(mod.shutil, 'which', return_value='codex.exe')
        self.which.start()
        self.addCleanup(self.which.stop)
        self.capabilities = patch.object(mod, 'check_commands')
        self.capabilities.start()
        self.addCleanup(self.capabilities.stop)

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def cli(self, exe, *args):
        self.calls.append(args)
        if args[:2] == self.failure:
            raise mod.InstallError('synthetic failure')
        if args[:2] == ('marketplace', 'list'):
            return {'marketplaces': self.markets}
        if args[0] == 'list':
            return {'installed': self.inventory}
        if args[0] == 'add':
            self.inventory = [{'name': 'blackbox', 'pluginId': 'blackbox@personal', 'installed': True, 'enabled': True, 'version': '1.0.0'}]
            return {'pluginId': 'blackbox@personal', 'version': '1.0.0', 'installedPath': str(self.cache)}
        if args[0] == 'remove':
            self.inventory = [p for p in self.inventory if p.get('pluginId') != args[1]]
        return {}

    def run_install(self, **kwargs):
        with patch.object(mod, 'run_cli', side_effect=self.cli):
            return mod.install(self.root, **kwargs)

    def test_precheck_never_registers(self):
        self.assertEqual('not_run', self.run_install()['registration'])
        self.assertEqual([('marketplace', 'list', '--json'), ('list', '--json')], self.calls)

    def test_new_install_verifies_bytes_and_reports_host_pending(self):
        result = self.run_install(register=True)
        self.assertEqual('verified', result['registration'])
        self.assertEqual('not_tested', result['real_hook'])
        self.assertIn(('marketplace', 'add', str(self.root.resolve()), '--json'), self.calls)

    def test_repeat_same_root_does_not_remove_market(self):
        self.markets = [{'name': 'personal', 'root': str(self.root)}]
        self.run_install(register=True)
        self.assertFalse(any(c[:2] in [('marketplace', 'add'), ('marketplace', 'remove')] for c in self.calls))

    def test_other_source_requires_explicit_switch(self):
        self.markets = [{'name': 'personal', 'root': str(self.home)}]
        with self.assertRaisesRegex(mod.InstallError, 'ReplaceSource'):
            self.run_install(register=True)
        self.assertEqual(2, len(self.calls))

    def test_switch_refuses_market_with_other_plugins(self):
        self.markets = [{'name': 'personal', 'root': str(self.home)}]
        self.write(self.home / '.agents/plugins/marketplace.json', {'name': 'personal', 'plugins': [{'name': 'blackbox'}, {'name': 'other'}]})
        with self.assertRaisesRegex(mod.InstallError, 'other plugins'):
            self.run_install(register=True, replace_source=True)
        self.assertEqual(2, len(self.calls))

    def test_explicit_switch_uses_official_commands(self):
        self.markets = [{'name': 'personal', 'root': str(self.home)}]
        self.write(self.home / '.agents/plugins/marketplace.json', {'name': 'personal', 'plugins': [{'name': 'blackbox'}]})
        self.run_install(register=True, replace_source=True)
        self.assertIn(('marketplace', 'remove', 'personal', '--json'), self.calls)
        self.assertFalse(any(c[0] == 'remove' for c in self.calls))

    def test_other_installed_identity_blocks_duplicates(self):
        self.inventory = [{'name': 'blackbox', 'pluginId': 'blackbox@other', 'installed': True}]
        with self.assertRaisesRegex(mod.InstallError, 'duplicate Hooks'):
            self.run_install(register=True)

    def test_tampered_cache_is_not_success(self):
        self.write(self.cache / '.codex-plugin/plugin.json', {'version': 'wrong'})
        with self.assertRaisesRegex(mod.InstallError, 'payload'):
            self.run_install(register=True)

    def test_registration_failure_is_not_success(self):
        self.failure = ('add', 'blackbox@personal')
        with self.assertRaisesRegex(mod.InstallError, 'synthetic failure'):
            self.run_install(register=True)

    def test_missing_cli_does_not_query_or_register(self):
        with patch.object(mod.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(mod.InstallError, 'not found'):
                self.run_install(register=True)
        self.assertEqual([], self.calls)

    def test_invalid_inventory_not_treated_as_empty(self):
        with patch.object(mod, 'run_cli', return_value={}):
            with self.assertRaisesRegex(mod.InstallError, 'inventory'):
                mod.install(self.root, register=True)

    def test_missing_host_capability_stops_before_inventory(self):
        with patch.object(mod, 'check_commands', side_effect=mod.InstallError('unsupported')):
            with self.assertRaisesRegex(mod.InstallError, 'unsupported'):
                self.run_install(register=True)
        self.assertEqual([], self.calls)

    def test_wrong_cache_home_is_not_success(self):
        self.cache = self.root
        with self.assertRaisesRegex(mod.InstallError, 'unexpected home'):
            self.run_install(register=True)

    def test_legacy_plugin_is_retired_before_new_install(self):
        self.inventory = [{'name': 'tas-notes', 'pluginId': 'tas-notes@personal', 'installed': True, 'enabled': True}]
        result = self.run_install(register=True)
        self.assertTrue(result['legacy_plugin_retired'])
        self.assertLess(self.calls.index(('remove', 'tas-notes@personal', '--json')),
                        self.calls.index(('add', 'blackbox@personal', '--json')))

    def test_failed_legacy_removal_never_adds_new_plugin(self):
        self.inventory = [{'name': 'tas-notes', 'pluginId': 'tas-notes@personal', 'installed': True, 'enabled': True}]
        self.failure = ('remove', 'tas-notes@personal')
        with self.assertRaises(mod.InstallError):
            self.run_install(register=True)
        self.assertFalse(any(c[0] == 'add' for c in self.calls))

    def test_disabled_setting_preserves_all_other_config(self):
        path = self.home / 'config.toml'
        path.parent.mkdir(parents=True, exist_ok=True)
        original = 'model="fixture"\n[plugins."blackbox@personal"]\nenabled = true\n[plugins."other@market"]\nenabled = true\n'
        path.write_text(original, encoding='utf-8')
        mod.preserve_disabled(self.home)
        self.assertEqual(original.replace('enabled = true', 'enabled = false', 1), path.read_text(encoding='utf-8'))

    def test_failed_upgrade_retains_disabled_preference_for_retry(self):
        self.inventory = [{'name':'tas-notes','pluginId':'tas-notes@personal','installed':True,'enabled':False}]
        self.failure = ('add','blackbox@personal')
        with self.assertRaises(mod.InstallError):
            self.run_install(register=True)
        pending = self.home / 'blackbox-migration-preference.json'
        self.assertEqual({'preserve_disabled':True},json.loads(pending.read_text()))
        self.failure = None
        def preserve(_):
            self.inventory[0]['enabled'] = False
        with patch.object(mod,'preserve_disabled',side_effect=preserve):
            self.assertFalse(self.run_install(register=True)['enabled'])
        self.assertFalse(pending.exists())

    def test_hidden_legacy_install_is_found_from_exact_config_and_cache(self):
        cache = self.home / 'plugins/cache/personal/tas-notes'
        cache.mkdir(parents=True)
        (self.home/'config.toml').write_text('[plugins."tas-notes@personal"]\nenabled = false\n',encoding='utf-8')
        self.assertEqual([{'pluginId':'tas-notes@personal','installed':True,'enabled':False}],mod.legacy_installations([],self.home))


if __name__ == '__main__':
    unittest.main()
