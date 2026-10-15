"""Packaging tests use fabricated source trees, never a runtime database."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SPEC = importlib.util.spec_from_file_location(
    'build_alpha', Path(__file__).resolve().parents[1] / 'tools/build_alpha.py')
alpha = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(alpha)


class AlphaPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blackbox-package-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'source'
        self.root.mkdir()
        for name in alpha.REQUIRED_FILES:
            self.put(name, b'# synthetic fixture\n')
        self.put(alpha.PLUGIN + '.codex-plugin/plugin.json',
                 json.dumps({'name': 'blackbox', 'version': '0.4.0-alpha.1'}).encode())
        self.put('tools/bootstrap_blackbox.py', b"VERSION = '0.4.0-alpha.1'\n")
        self.put('release/INSTALL-CODEX.md',
                 b'https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/'
                 b'v0.4.0-alpha.1/tools/bootstrap_blackbox.py\n')
        self.put('tests/test_example.py', b'import unittest\n')
        self.original_identity = alpha.source_identity
        self.identity = patch.object(alpha, 'source_identity', return_value={
            'commit': 'a' * 40, 'dirty': True, 'status': 'available'})
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def build(self, **kwargs):
        return alpha.build(self.root, Path(self.temp.name) / 'output', **kwargs)

    def test_deterministic_zip_hashes_and_version(self):
        first = self.build()
        first_bytes = Path(first['archive']).read_bytes()
        second = self.build()
        self.assertEqual(first_bytes, Path(second['archive']).read_bytes())
        self.assertEqual(hashlib.sha256(first_bytes).hexdigest(), first['sha256'])
        self.assertEqual('0.4.0-alpha.1', first['version'])
        checksum = Path(first['archive'] + '.sha256').read_text(encoding='ascii')
        self.assertEqual(first['sha256'] + '  ' + Path(first['archive']).name + '\n', checksum)
        with zipfile.ZipFile(first['archive']) as bundle:
            self.assertEqual(sorted(bundle.namelist()), bundle.namelist())
            self.assertTrue(all(info.date_time == alpha.FIXED_ZIP_TIME for info in bundle.infolist()))
            manifest = json.loads(bundle.read('release-manifest.json'))
            sidecar = Path(first['archive']).with_suffix('.release-manifest.json')
            self.assertEqual(bundle.read('release-manifest.json'), sidecar.read_bytes())
            self.assertTrue(manifest['license_pending'])
            self.assertFalse(manifest['publish_ready'])
            self.assertTrue(manifest['source']['dirty'])
            self.assertEqual('0.4.0-alpha.1', manifest['version'])
            for name, metadata in manifest['files'].items():
                data = bundle.read(name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), metadata['sha256'])
                self.assertEqual(len(data), metadata['size'])
            sums = bundle.read('SHA256SUMS').decode().splitlines()
            self.assertEqual(len(bundle.namelist()) - 1, len(sums))
            for line in sums:
                digest, name = line.split('  ', 1)
                self.assertEqual(hashlib.sha256(bundle.read(name)).hexdigest(), digest)

    def test_no_runtime_data_or_arbitrary_tools(self):
        excluded = ('.local/notes.db', '.codex/config.toml', 'plugins/blackbox/data.sqlite',
                    'plugins/blackbox/scripts/__pycache__/core.pyc', 'docs/private.md',
                    'tools/cleanup_notes025.py', '.env', 'run.log', 'dist/old.zip',
                    'plugins/blackbox/assets/notes.db')
        for name in excluded:
            self.put(name, b'PRIVATE RUNTIME FIXTURE')
        with zipfile.ZipFile(self.build()['archive']) as bundle:
            self.assertTrue(set(excluded).isdisjoint(bundle.namelist()))
            self.assertIn('tests/test_example.py', bundle.namelist())
            self.assertIn('tools/build_alpha.py', bundle.namelist())

    def test_bootstrap_version_drift_fails_before_writing(self):
        self.put('tools/bootstrap_blackbox.py', b"VERSION = '0.4.0-alpha.2'\n")
        with self.assertRaisesRegex(alpha.BuildError, 'VERSION.*plugin.json'):
            self.build()
        self.assertFalse((Path(self.temp.name) / 'output').exists())

    def test_bootstrap_requires_one_literal_version_without_executing_code(self):
        for source in (b'# missing version\n', b"VERSION = str('0.4.0-alpha.1')\n",
                       b"VERSION = '0.4.0-alpha.1'\nVERSION = '0.4.0-alpha.1'\n"):
            with self.subTest(source=source):
                self.put('tools/bootstrap_blackbox.py', source)
                with self.assertRaisesRegex(alpha.BuildError, 'one literal'):
                    self.build()
        self.put('tools/bootstrap_blackbox.py',
                 b"raise AssertionError('must not execute')\nVERSION: str = '0.4.0-alpha.1'\n")
        self.assertEqual('0.4.0-alpha.1', self.build()['version'])

    def test_install_instruction_missing_unpinned_or_mixed_targets_fail(self):
        target = 'https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/{}/tools/bootstrap_blackbox.py'
        for refs in ((), ('main',), ('v0.4.0-alpha.2',), ('v0.4.0-alpha.1', 'v0.4.0-alpha.2')):
            with self.subTest(refs=refs):
                self.put('release/INSTALL-CODEX.md', '\n'.join(target.format(ref) for ref in refs).encode())
                with self.assertRaisesRegex(alpha.BuildError, 'bootstrap links.*fixed tag'):
                    self.build()
        self.assertFalse((Path(self.temp.name) / 'output').exists())

    def test_install_instruction_prose_and_markdown_can_change(self):
        text = ('# Any translated heading\n\n'
                '[Installer](https://github.com/zemekeznb/blackbox-plugin/blob/'
                'v0.4.0-alpha.1/tools/bootstrap_blackbox.py)\n\n'
                '`https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/'
                'v0.4.0-alpha.1/tools/bootstrap_blackbox.py`\n')
        self.put('release/INSTALL-CODEX.md', text.encode())
        self.assertEqual('0.4.0-alpha.1', self.build()['version'])

    def test_required_file_missing_fails_before_writing(self):
        (self.root / 'install.ps1').unlink()
        with self.assertRaisesRegex(alpha.BuildError, 'Missing required file: install.ps1'):
            self.build()
        self.assertFalse((Path(self.temp.name) / 'output').exists())

    def test_changed_source_changes_manifest_and_archive(self):
        first = self.build()
        self.put(alpha.PLUGIN + 'scripts/blackbox.py', b'# different synthetic source\n')
        second = self.build()
        self.assertNotEqual(first['sha256'], second['sha256'])
        with zipfile.ZipFile(second['archive']) as bundle:
            manifest = json.loads(bundle.read('release-manifest.json'))
            entry = manifest['files'][alpha.PLUGIN + 'scripts/blackbox.py']
            self.assertEqual(hashlib.sha256(b'# different synthetic source\n').hexdigest(), entry['sha256'])

    def test_runtime_secrets_not_read_but_payload_secrets_rejected(self):
        secret = ('sk-' + 'a' * 32).encode()
        self.put('.local/runtime.log', secret)
        self.build()
        self.put(alpha.PLUGIN + 'scripts/leak.py', secret)
        with self.assertRaises(alpha.BuildError) as caught:
            self.build()
        self.assertIn('Possible secret (openai_key)', str(caught.exception))
        self.assertNotIn(secret.decode(), str(caught.exception))

    def test_common_secret_shapes_rejected_without_value(self):
        for content in (('ghp_' + 'a' * 36), ('AKIA' + 'A' * 16),
                        ('-----BEGIN ' + 'PRIVATE KEY-----'), ('Bearer ' + 'a' * 30)):
            with self.subTest(content=content[:6]):
                self.put('install.ps1', content.encode())
                with self.assertRaisesRegex(alpha.BuildError, 'Possible secret'):
                    self.build()

    def test_symlink_escape_and_internal_symlink_rejected(self):
        outside = Path(self.temp.name) / 'outside.py'
        outside.write_bytes(b'private')
        link = self.root / (alpha.PLUGIN + 'scripts/linked.py')
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('This environment cannot create symlinks; reparse-point path tests still run.')
        with self.assertRaisesRegex(alpha.BuildError, 'Symlink or reparse point'):
            self.build()
        link.unlink()
        link.symlink_to(self.root / (alpha.PLUGIN + 'scripts/blackbox.py'))
        with self.assertRaisesRegex(alpha.BuildError, 'Symlink or reparse point'):
            self.build()

    def test_reparse_parent_refused_before_globbing(self):
        original = alpha._linked
        assets = self.root / (alpha.PLUGIN + 'assets')
        with patch.object(alpha, '_linked', side_effect=lambda path: path == assets or original(path)):
            with self.assertRaisesRegex(alpha.BuildError, 'Symlink or reparse point'):
                self.build()

    def test_license_pending_blocks_release_but_not_candidate(self):
        self.assertTrue(self.build()['license_pending'])
        with self.assertRaisesRegex(alpha.BuildError, 'LICENSE is pending'):
            self.build(for_release=True)

    def test_empty_license_does_not_count_as_approved(self):
        self.put('LICENSE', b'\n ')
        with self.assertRaisesRegex(alpha.BuildError, 'LICENSE is empty'):
            self.build()

    def test_license_inclusion_does_not_claim_host_acceptance(self):
        self.put('LICENSE', b'SYNTHETIC TEST LICENSE; NOT A GRANT FOR THIS PRODUCT\n')
        with patch.object(alpha, 'source_identity', return_value={
                'commit': 'b' * 40, 'dirty': False, 'status': 'available'}):
            built = self.build(for_release=True)
        self.assertFalse(built['license_pending'])
        self.assertFalse(built['publish_ready'])
        with zipfile.ZipFile(built['archive']) as bundle:
            self.assertIn('LICENSE', bundle.namelist())
            manifest = json.loads(bundle.read('release-manifest.json'))
            self.assertIn('real_host_acceptance_not_attested', manifest['release_blockers'])

    def test_dirty_or_unknown_source_blocks_release(self):
        self.put('LICENSE', b'SYNTHETIC TEST LICENSE\n')
        for dirty in (True, None):
            with self.subTest(dirty=dirty), patch.object(alpha, 'source_identity', return_value={
                    'commit': None, 'dirty': dirty, 'status': 'unavailable'}):
                with self.assertRaisesRegex(alpha.BuildError, 'known clean Git commit'):
                    self.build(for_release=True)

    def test_unsafe_version_rejected(self):
        self.put(alpha.PLUGIN + '.codex-plugin/plugin.json', b'{"version":"../../escape"}')
        with self.assertRaisesRegex(alpha.BuildError, 'safe semantic version'):
            self.build()

    def test_source_git_failure_is_unknown_not_clean(self):
        with patch.object(alpha.subprocess, 'run', side_effect=FileNotFoundError):
            result = self.original_identity(self.root)
        self.assertIsNone(result['dirty'])
        self.assertIsNone(result['commit'])
        self.assertEqual('unavailable', result['status'])


if __name__ == '__main__':
    unittest.main()
