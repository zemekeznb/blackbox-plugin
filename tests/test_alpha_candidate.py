import contextlib
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_alpha_candidate import VerificationError, extract_verified, main, verify_archive
from verify_alpha_candidate import smoke_directory


class SmokeCleanupTests(unittest.TestCase):
    def test_windows_directory_lock_retries_after_shutdown(self):
        directory = Mock(name='owned-temp')
        directory.name = 'owned-temp'
        directory.cleanup.side_effect = [PermissionError('still exiting'), None]
        with patch('verify_alpha_candidate.tempfile.TemporaryDirectory', return_value=directory), patch('verify_alpha_candidate.time.sleep'):
            with smoke_directory() as path:
                self.assertEqual(path, 'owned-temp')
        self.assertEqual(directory.cleanup.call_count, 2)

    def test_persistent_lock_is_not_reported_as_success(self):
        directory = Mock()
        directory.cleanup.side_effect = PermissionError('locked')
        with patch('verify_alpha_candidate.tempfile.TemporaryDirectory', return_value=directory), patch('verify_alpha_candidate.time.sleep'):
            with self.assertRaises(PermissionError):
                with smoke_directory():
                    pass
        self.assertEqual(directory.cleanup.call_count, 30)


class CandidateVerificationTests(unittest.TestCase):
    def package(self, payload=None, *, change=None, mode=None):
        payload = dict(payload or {'plugins/blackbox/scripts/example.py': b'print("example")\n'})
        manifest = {'schema_version': 1, 'version': '0.4.0-alpha.1',
                    'source': {'commit': 'a' * 40, 'dirty': True},
                    'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                              for name, data in payload.items()}}
        payload['release-manifest.json'] = json.dumps(manifest).encode()
        payload['SHA256SUMS'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                                      for name, data in payload.items()).encode()
        if change:
            change(payload)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as bundle:
            for name, data in payload.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (mode if mode is not None else stat.S_IFREG | 0o644) << 16
                bundle.writestr(info, data)
        stream.seek(0)
        return stream

    def test_valid_manifest_checksums_and_unicode_extraction(self):
        archive = self.package({'scripts/中文 file.py': b'pass\n'})
        manifest, files = verify_archive(archive)
        self.assertTrue(manifest['source']['dirty'])
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / '中文 path'
            extract_verified(files, destination)
            self.assertEqual((destination / 'scripts/中文 file.py').read_bytes(), b'pass\n')

    def test_unsafe_windows_and_traversal_paths_rejected(self):
        for name in ('../escape.py', '/absolute.py', 'C:/absolute.py', 'a\\..\\escape',
                     'a//b', 'a/./b', 'NUL', 'scripts/COM1.txt', 'a./b', 'a /b', 'a:b', 'a\nname'):
            with self.subTest(name=name), self.assertRaisesRegex(VerificationError, 'Unsafe'):
                verify_archive(self.package({name: b'data'}))

    def test_links_and_special_files_rejected(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600):
            with self.subTest(mode=mode), self.assertRaisesRegex(VerificationError, 'link or special'):
                verify_archive(self.package(mode=mode))

    def test_case_collisions_and_parent_file_conflicts_rejected(self):
        for payload in ({'A.py': b'a', 'a.py': b'b'}, {'scripts': b'a', 'scripts/a.py': b'b'}):
            with self.subTest(payload=payload), self.assertRaises(VerificationError):
                verify_archive(self.package(payload))

    def test_exact_duplicate_zip_entry_rejected(self):
        archive = self.package()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(archive, 'a') as bundle:
                bundle.writestr('SHA256SUMS', b'')
        archive.seek(0)
        with self.assertRaisesRegex(VerificationError, 'Duplicate'):
            verify_archive(archive)

    def test_corrupt_payload_and_extra_unlisted_file_rejected(self):
        cases = (
            lambda files: files.update({'plugins/blackbox/scripts/example.py': b'corrupt'}),
            lambda files: files.update({'unlisted.py': b'extra'}),
            lambda files: files.update({'SHA256SUMS': files['SHA256SUMS'].replace(b'a', b'b', 1)}),
        )
        for change in cases:
            with self.subTest(change=change), self.assertRaises(VerificationError):
                verify_archive(self.package(change=change))

    def test_manifest_must_be_covered_by_checksums(self):
        def omit_manifest(files):
            files['SHA256SUMS'] = b'\n'.join(line for line in files['SHA256SUMS'].splitlines()
                                            if not line.endswith(b'release-manifest.json')) + b'\n'
        with self.assertRaisesRegex(VerificationError, 'cover every'):
            verify_archive(self.package(change=omit_manifest))

    def test_manifest_size_and_hash_are_checked_independently(self):
        def wrong_size(files):
            manifest = json.loads(files['release-manifest.json'])
            next(iter(manifest['files'].values()))['size'] += 1
            files['release-manifest.json'] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(VerificationError, 'size or SHA-256'):
            verify_archive(self.package(change=wrong_size))

    def test_duplicate_manifest_json_key_is_rejected(self):
        def duplicate(files):
            files['release-manifest.json'] = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(VerificationError, 'Duplicate JSON'):
            verify_archive(self.package(change=duplicate))

    def test_size_and_count_limits_apply_before_extracting(self):
        with patch('verify_alpha_candidate.MAX_FILE_BYTES', 2), self.assertRaisesRegex(VerificationError, 'size limits'):
            verify_archive(self.package())
        with patch('verify_alpha_candidate.MAX_FILES', 2), self.assertRaisesRegex(VerificationError, 'too many'):
            verify_archive(self.package())

    def test_verify_only_never_extracts_or_runs_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'candidate.zip'
            archive.write_bytes(self.package().getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch('verify_alpha_candidate.smoke_test') as smoke:
                self.assertEqual(main([str(archive), '--verify-only']), 0)
            smoke.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual(result['isolated_smoke'], 'not_run')
            self.assertEqual(result['real_host_acceptance'], 'not_tested')
            self.assertFalse(result['publish_ready'])

    def test_missing_and_invalid_archives_fail_concisely(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'bad.zip'
            archive.write_bytes(b'not a zip PRIVATE_SENTINEL')
            for path in (archive, archive.with_name('missing.zip')):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    self.assertEqual(main([str(path), '--verify-only']), 2)
                self.assertNotIn('PRIVATE_SENTINEL', error.getvalue())
                self.assertNotIn('Traceback', error.getvalue())


if __name__ == '__main__':
    unittest.main()
