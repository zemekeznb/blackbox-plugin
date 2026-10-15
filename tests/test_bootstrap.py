"""Customer setup guards; fabricated packages, no real downloads or installation."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('bb_bootstrap', Path(__file__).resolve().parents[1] / 'tools/bootstrap_blackbox.py')
bb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bb)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def executable(self, name):
        path = self.root / name / 'codex.exe'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'fixture, not executable')
        return str(path)

    def archive(self, payload=None, change=None):
        payload = payload or {'plugins/blackbox/scripts/example.py': b'# fixture\n'}
        files = dict(payload)
        manifest = {'version': bb.VERSION, 'files': {name: {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for name, body in files.items()}}
        files['release-manifest.json'] = json.dumps(manifest).encode()
        files['SHA256SUMS'] = b'fixture'
        if change:
            change(files)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as bundle:
            for name, body in files.items():
                bundle.writestr(name, body)
        data = stream.getvalue()
        return data, hashlib.sha256(data).hexdigest()

    def test_active_host_precedes_unrelated_cli_on_path(self):
        active = self.executable('desktop')
        with patch.object(bb, 'active_codex', return_value=[active]), patch.object(bb.shutil, 'which') as which:
            self.assertEqual(active, bb.find_codex())
        which.assert_not_called()

    def test_no_cli_path_uses_unique_desktop_installation(self):
        path = self.executable('AppData/Local/OpenAI/Codex/bin/current')
        with patch.object(bb, 'active_codex', return_value=[]), patch.object(bb.shutil, 'which', return_value=None), patch.object(bb.Path, 'home', return_value=self.root), patch.dict(bb.os.environ, {'LOCALAPPDATA': str(self.root/'AppData/Local'), 'CODEX_CLI_PATH': ''}):
            self.assertEqual(path, bb.find_codex())

    def test_ambiguous_host_aborts_before_download(self):
        hosts = [self.executable('one'), self.executable('two')]
        with patch.object(bb, 'active_codex', return_value=hosts), patch.object(bb, 'download') as download:
            with self.assertRaisesRegex(bb.SetupError, 'Multiple'):
                bb.install()
        download.assert_not_called()

    def test_unknown_host_is_actionable(self):
        with patch.object(bb, 'active_codex', return_value=[]), patch.object(bb.shutil, 'which', return_value=None), patch.object(bb.Path, 'home', return_value=self.root), patch.dict(bb.os.environ, {'LOCALAPPDATA': str(self.root), 'CODEX_CLI_PATH': ''}):
            with self.assertRaisesRegex(bb.SetupError, 'Open the desktop app'):
                bb.find_codex()

    def test_checksum_failure_precedes_extraction(self):
        data, _ = self.archive()
        with self.assertRaisesRegex(bb.SetupError, 'SHA256'):
            bb.checked_files(data, '0'*64)

    def test_network_failure_is_not_success_and_does_not_echo_child_details(self):
        output = io.StringIO()
        with patch.object(bb.sys, 'argv', ['bootstrap']), patch.object(bb, 'find_codex', return_value='codex.exe'), patch.object(bb, 'download', side_effect=bb.urllib.error.URLError('PRIVATE_FIXTURE_DETAIL')), patch.object(bb, 'helper') as helper, patch('sys.stdout', output):
            self.assertEqual(1, bb.main())
        helper.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['installed'])
        self.assertNotIn('PRIVATE_FIXTURE_DETAIL', output.getvalue())

    def test_download_size_limit_prevents_package_processing(self):
        with patch.object(bb.urllib.request, 'urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = b'12345'
            with self.assertRaisesRegex(bb.SetupError, 'size limit'):
                bb.download('https://example.invalid/fixture', 4)

    def test_rejects_traversal_reserved_and_case_conflicting_names(self):
        for path in ('../escape', 'C:/escape', 'scripts/CON.py', 'scripts/a./file', 'scripts\\file'):
            with self.subTest(path=path):
                data, digest = self.archive({path: b'data'})
                with self.assertRaises(bb.SetupError):
                    bb.checked_files(data, digest)
        data, digest = self.archive({'A': b'a', 'a': b'b'})
        with self.assertRaises(bb.SetupError):
            bb.checked_files(data, digest)

    def test_rejects_case_conflicting_directory_before_writes(self):
        data, digest = self.archive({'A': b'a', 'a/b': b'b'})
        with self.assertRaisesRegex(bb.SetupError, 'conflicting'):
            bb.checked_files(data, digest)

    def test_modified_manifest_payload_is_rejected(self):
        data, digest = self.archive(change=lambda files: files.update({'plugins/blackbox/scripts/example.py': b'changed'}))
        with self.assertRaisesRegex(bb.SetupError, 'manifest'):
            bb.checked_files(data, digest)

    def test_existing_source_is_preserved_on_conflict(self):
        data, digest = self.archive()
        files = bb.checked_files(data, digest)
        destination = self.root / 'stable'
        bb.prepare(files, destination)
        target = destination / 'plugins/blackbox/scripts/example.py'
        target.write_bytes(b'local edit')
        with self.assertRaisesRegex(bb.SetupError, 'differs'):
            bb.prepare(files, destination)
        self.assertEqual(b'local edit', target.read_bytes())

    def test_repeat_preparation_reuses_identical_files(self):
        data, digest = self.archive()
        files = bb.checked_files(data, digest)
        target = self.root / 'stable'
        self.assertEqual(target, bb.prepare(files, target))
        self.assertEqual(target, bb.prepare(files, target))

    def test_untracked_source_file_is_not_silently_installed(self):
        data, digest = self.archive()
        files = bb.checked_files(data, digest)
        target = self.root / 'stable'
        bb.prepare(files, target)
        (target/'unexpected.py').write_text('# local file')
        with self.assertRaisesRegex(bb.SetupError, 'unexpected'):
            bb.prepare(files, target)
        self.assertTrue((target/'unexpected.py').exists())

    def installation(self, failure=None):
        data, digest = self.archive()
        stem = 'blackbox-plugin-' + bb.VERSION + '-candidate.zip'
        calls = []

        def run(root, name, *args):
            calls.append((name, args))
            if name == failure:
                raise bb.SetupError('fixture failure')
            if name == 'blackbox_preflight.py':
                return {'ok': True, 'state_directory': str(self.root/'state')}
            if name == 'blackbox.py':
                return {'runtime_configured': True}
            return {'registration': 'verified', 'installed_path': str(self.root/'cache')}

        with patch.object(bb, 'find_codex', return_value='codex.exe'), patch.object(bb.Path, 'home', return_value=self.root), patch.object(bb, 'download', side_effect=[(digest+'  '+stem).encode(), data]), patch.object(bb, 'helper', side_effect=run):
            try:
                return bb.install(), calls
            except bb.SetupError:
                self.failure_calls = calls
                raise

    def test_preflight_failure_does_not_register_or_configure(self):
        with self.assertRaises(bb.SetupError):
            self.installation('blackbox_preflight.py')
        self.assertEqual(['blackbox_preflight.py'], [name for name, _ in self.failure_calls])

    def test_registration_precheck_failure_does_not_configure(self):
        with self.assertRaises(bb.SetupError):
            self.installation('blackbox_install.py')
        self.assertNotIn('blackbox.py', [name for name, _ in self.failure_calls])

    def test_success_returns_verified_cache_without_project_or_trust_commands(self):
        result, calls = self.installation()
        self.assertTrue(result['installed'])
        self.assertEqual('not_verified', result['capture'])
        self.assertEqual(['blackbox_preflight.py', 'blackbox_install.py', 'blackbox.py', 'blackbox_install.py'], [name for name, _ in calls])
        self.assertFalse(any(arg in ('enable', 'hook', 'trust') for _, args in calls for arg in args))


if __name__ == '__main__':
    unittest.main()
