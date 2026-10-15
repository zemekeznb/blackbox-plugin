"""Release automation tests use synthetic files and mocked GitHub operations."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_release as release


TAG = 'v0.4.0-alpha.6.99'
VERSION = TAG[1:]
COMMIT = 'a' * 40
SOURCE = {'commit': COMMIT, 'dirty': False, 'status': 'available'}


def payload():
    return {
        release.PLUGIN + '.codex-plugin/plugin.json': json.dumps({'version': VERSION}).encode(),
        'tools/bootstrap_blackbox.py': (
            f"VERSION = '{VERSION}'\nREPOSITORY = 'https://github.com/{release.REPOSITORY}'\n").encode(),
        'release/INSTALL-CODEX.md': (
            f'https://raw.githubusercontent.com/{release.REPOSITORY}/{TAG}/tools/bootstrap_blackbox.py\n').encode(),
        'LICENSE': b'MIT synthetic license fixture\n',
        'tests/test_example.py': b'# Synthetic fixture, no local records.\n',
    }


def candidate(directory, *, changes=None, file_changes=None, compression=zipfile.ZIP_STORED):
    files = payload()
    files.update(file_changes or {})
    manifest = {
        'schema_version': 1, 'product': 'Blackbox Plugin', 'version': VERSION,
        'artifact_kind': 'alpha_candidate', 'license_pending': False,
        'publish_ready': False, 'source': dict(SOURCE),
        'release_blockers': ['real_host_acceptance_not_attested', 'publication_not_authorized_by_builder'],
        'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                  for name, data in files.items()},
    }
    manifest.update(changes or {})
    encoded = json.dumps(manifest).encode()
    files['release-manifest.json'] = encoded
    files['SHA256SUMS'] = ''.join(
        f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in sorted(files.items())).encode()
    stem = f'blackbox-plugin-{VERSION}-candidate'
    archive = directory / (stem + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=compression) as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    (directory / (stem + '.zip.sha256')).write_bytes(
        f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n'.encode('ascii'))
    (directory / (stem + '.release-manifest.json')).write_bytes(encoded)
    return archive


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name, data in payload().items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.identity = patch.object(release, 'source_identity', return_value=dict(SOURCE)).start()
        self.addCleanup(patch.stopall)

    def test_local_only_never_invokes_gh(self):
        with patch.object(release, 'run_gh', side_effect=AssertionError('network forbidden')):
            result = release.check(TAG, COMMIT, root=self.root, local_only=True)
        self.assertEqual(result['remote_availability'], 'not_checked')
        self.assertFalse(result['published'])

    def test_dirty_unknown_or_different_commit_fail(self):
        for source in ({**SOURCE, 'dirty': True}, {**SOURCE, 'dirty': None},
                       {**SOURCE, 'status': 'unavailable'}, {**SOURCE, 'commit': 'b' * 40}):
            with self.subTest(source=source):
                self.identity.return_value = source
                with self.assertRaises(release.ReleaseError):
                    release.validate_source(TAG, COMMIT, self.root)

    def test_wrong_tag_or_unsafe_identifiers_fail(self):
        for tag, commit in (('v0.4.0-alpha.6.98', COMMIT), ('--draft', COMMIT),
                            (TAG + '/other', COMMIT), (TAG, COMMIT[:7]),
                            (TAG, 'a' * 64), ('v01.2.3', COMMIT), ('v1.2.3-', COMMIT),
                            ('v1.2.3-alpha..1', COMMIT), ('v1.2.3-alpha.01', COMMIT)):
            with self.subTest(tag=tag, commit=commit), self.assertRaises(release.ReleaseError):
                release.validate_source(tag, commit, self.root)

    def test_mixed_install_version_fails(self):
        (self.root / 'tools/bootstrap_blackbox.py').write_text("VERSION = '0.1.0'\n", encoding='utf-8')
        with self.assertRaisesRegex(release.ReleaseError, 'VERSION'):
            release.validate_source(TAG, COMMIT, self.root)

    def test_wrong_bootstrap_repository_fails_before_remote_checks(self):
        bootstrap = self.root / 'tools/bootstrap_blackbox.py'
        bootstrap.write_bytes(bootstrap.read_bytes().replace(
            release.REPOSITORY.encode(), b'example/other-repository'))
        with patch.object(release, 'run_gh') as gh, self.assertRaisesRegex(release.ReleaseError, 'repository'):
            release.check(TAG, COMMIT, root=self.root)
        gh.assert_not_called()


class RemoteTests(unittest.TestCase):
    def test_only_actual_404_is_absent(self):
        for code in (401, 403, 429, 500):
            result = subprocess.CompletedProcess([], 1, f'HTTP/2.0 {code} Error\nX-Test: yes\n\n{{}}', '')
            with self.subTest(code=code), patch.object(release, 'run_gh', return_value=result):
                with self.assertRaisesRegex(release.ReleaseError, str(code)):
                    release.api('repos/test/ref', allow_missing=True)
        result = subprocess.CompletedProcess([], 1, 'HTTP/2.0 404 Not Found\r\n\r\n{}', '')
        with patch.object(release, 'run_gh', return_value=result):
            self.assertIsNone(release.api('repos/test/ref', allow_missing=True))

    def test_transport_failure_is_not_absent(self):
        result = subprocess.CompletedProcess([], 1, '', 'network failure')
        with patch.object(release, 'run_gh', return_value=result):
            with self.assertRaisesRegex(release.ReleaseError, 'HTTP status'):
                release.api('repos/test/ref', allow_missing=True)

    def test_read_token_cannot_claim_drafts_absent(self):
        # A user's push role or self-reported Actions environment does not prove
        # that this particular token has Contents(write).
        for permissions in (None, {'push': False}, {'push': True}):
            for status in (401, 403, 404):
                repository = {'full_name': release.REPOSITORY}
                if permissions is not None:
                    repository['permissions'] = permissions
                with self.subTest(permissions=permissions, status=status), patch.dict(
                        os.environ, GITHUB_ACTIONS='true', GITHUB_REPOSITORY=release.REPOSITORY), patch.object(
                        release, 'api', side_effect=[repository, None, release.ReleaseError(f'HTTP {status}')]) as api:
                    with self.assertRaisesRegex(release.ReleaseError, 'local-only'):
                        release.ensure_available(TAG, COMMIT)
                self.assertEqual(api.call_count, 3)
                self.assertTrue(api.call_args.args[0].endswith('/releases/generate-notes'))

    def test_installation_token_without_user_permissions_can_verify_write_access(self):
        responses = [{'full_name': release.REPOSITORY}, None,
                     {'name': 'Synthetic release', 'body': 'PRIVATE-NOTES-MUST-NOT-BE-PRINTED'}, []]
        with patch.object(release, 'api', side_effect=responses) as api:
            self.assertIsNone(release.ensure_available(TAG, COMMIT))
        probe = api.call_args_list[2]
        self.assertEqual(probe.args, (f'repos/{release.REPOSITORY}/releases/generate-notes',))
        self.assertEqual(probe.kwargs, {'method': 'POST', 'expected_status': 200,
                                       'fields': (('tag_name', TAG), ('target_commitish', COMMIT))})
        self.assertEqual(api.call_count, 4)

    def test_permission_check_rejects_wrong_repository_and_invalid_response(self):
        for repository in ({}, {'full_name': 'other/repository'}, []):
            with patch.object(release, 'api', return_value=repository) as api:
                with self.assertRaisesRegex(release.ReleaseError, 'identity'):
                    release.ensure_available(TAG, COMMIT)
            self.assertEqual(api.call_count, 1)
        for notes in (None, [], {}, {'name': 'release'}, {'name': 'release', 'body': False}):
            with patch.object(release, 'api', side_effect=[{'full_name': release.REPOSITORY}, None, notes]) as api:
                with self.assertRaisesRegex(release.ReleaseError, 'permission check'):
                    release.ensure_available(TAG, COMMIT)
            self.assertEqual(api.call_count, 3)

    def test_generate_notes_requires_exact_200_without_relaxing_create_status(self):
        result = subprocess.CompletedProcess([], 0, 'HTTP/2.0 200 OK\n\n{"name":"release","body":""}', '')
        with patch.object(release, 'run_gh', return_value=result):
            self.assertEqual(release.api('repos/test/releases/generate-notes', method='POST',
                                         expected_status=200), {'name': 'release', 'body': ''})
            with self.assertRaisesRegex(release.ReleaseError, 'HTTP 200'):
                release.api('repos/test/git/refs', method='POST')
        for code in (201, 204, 401, 403, 404):
            result = subprocess.CompletedProcess([], 0, f'HTTP/2.0 {code} Result\n\n{{}}', '')
            with patch.object(release, 'run_gh', return_value=result):
                with self.assertRaisesRegex(release.ReleaseError, f'HTTP {code}'):
                    release.api('repos/test/releases/generate-notes', method='POST', expected_status=200)

    def test_existing_tag_stops_before_release_list(self):
        with patch.object(release, 'api', side_effect=[
                {'full_name': release.REPOSITORY, 'permissions': {'push': True}}, {'ref': TAG}]) as api:
            with self.assertRaisesRegex(release.ReleaseError, 'tag already exists'):
                release.ensure_available(TAG, COMMIT)
        self.assertEqual(api.call_count, 2)

    def test_paginated_existing_draft_is_rejected(self):
        pages = [
            {'full_name': release.REPOSITORY, 'permissions': {'push': True}}, None,
            {'name': 'Synthetic', 'body': ''},
            [{'tag_name': f'v0.0.{index}', 'draft': False} for index in range(100)],
            [{'tag_name': TAG, 'draft': True}],
        ]
        with patch.object(release, 'api', side_effect=pages) as api:
            with self.assertRaisesRegex(release.ReleaseError, 'draft or published'):
                release.ensure_available(TAG, COMMIT)
        self.assertIn('page=2', api.call_args_list[-1].args[0])

    def test_full_check_uses_only_reads_and_non_persisting_permission_probe(self):
        responses = [
            {'full_name': release.REPOSITORY, 'permissions': {'push': True}}, None,
            {'name': 'Synthetic', 'body': ''}, [],
        ]
        with patch.object(release, 'validate_source', return_value={}), patch.object(
                release, 'api', side_effect=responses) as api:
            result = release.check(TAG, COMMIT)
        self.assertEqual(result['remote_availability'], 'available')
        posts = [call for call in api.call_args_list if call.kwargs.get('method', 'GET') != 'GET']
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].args, (f'repos/{release.REPOSITORY}/releases/generate-notes',))
        self.assertEqual(posts[0].kwargs['method'], 'POST')

    def test_subprocess_uses_argv_and_fixed_host(self):
        result = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(release.subprocess, 'run', return_value=result) as run:
            release.run_gh(['release', 'view', TAG])
        arguments, options = run.call_args
        self.assertEqual(arguments[0], ['gh', 'release', 'view', TAG])
        self.assertNotIn('shell', options)
        self.assertEqual(options['env']['GH_HOST'], 'github.com')


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        patch.object(release, 'collect_payload', return_value=payload()).start()
        self.addCleanup(patch.stopall)

    def validate(self):
        return release.validate_artifacts(self.directory, TAG, COMMIT)

    def test_complete_candidate_is_accepted(self):
        candidate(self.directory)
        self.assertEqual(len(self.validate()), 3)

    def test_extra_file_is_rejected(self):
        candidate(self.directory)
        (self.directory / 'private.txt').write_bytes(b'synthetic')
        with self.assertRaisesRegex(release.ReleaseError, 'exactly'):
            self.validate()

    def test_each_artifact_obeys_the_public_download_size_limits(self):
        archive = candidate(self.directory)
        for name, limit in (('MAX_PACKAGE_BYTES', archive.stat().st_size - 1),
                            ('MAX_CHECKSUM_BYTES', 1), ('MAX_METADATA_BYTES', 1)):
            with self.subTest(limit=name), patch.object(release, name, limit):
                with self.assertRaisesRegex(release.ReleaseError, 'artifact exceeds'):
                    self.validate()

    def test_compressed_zip_cannot_expand_beyond_the_installer_limit(self):
        archive = candidate(self.directory, compression=zipfile.ZIP_DEFLATED,
                            file_changes={'tests/test_example.py': b'# synthetic\n' * 1000})
        with patch.object(release, 'MAX_PACKAGE_BYTES', archive.stat().st_size + 1):
            with self.assertRaisesRegex(release.ReleaseError, 'payload exceeds'):
                self.validate()

    def test_mismatched_outer_checksum_and_manifest_are_rejected(self):
        for pattern in ('*.sha256', '*.release-manifest.json'):
            with self.subTest(pattern=pattern):
                candidate(self.directory)
                next(self.directory.glob(pattern)).write_bytes(b'wrong')
                with self.assertRaises(release.ReleaseError):
                    self.validate()

    def test_manifest_source_license_and_acceptance_claims_are_enforced(self):
        for changes in ({'source': {**SOURCE, 'commit': 'b' * 40}},
                        {'source': {**SOURCE, 'dirty': True}},
                        {'source': {**SOURCE, 'status': 'unavailable'}},
                        {'version': '0.1.0'}, {'license_pending': True},
                        {'publish_ready': True}, {'release_blockers': []}):
            with self.subTest(changes=changes):
                candidate(self.directory, changes=changes)
                with self.assertRaises(release.ReleaseError):
                    self.validate()

    def test_self_consistent_but_different_payload_is_rejected(self):
        candidate(self.directory, file_changes={'tests/test_example.py': b'changed\n'})
        with self.assertRaisesRegex(release.ReleaseError, 'differs'):
            self.validate()

    def test_corrupt_zip_with_updated_outer_checksum_is_rejected(self):
        archive = candidate(self.directory)
        archive.write_bytes(b'not a zip')
        archive.with_name(archive.name + '.sha256').write_bytes(
            f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n'.encode('ascii'))
        with self.assertRaisesRegex(release.ReleaseError, 'ZIP'):
            self.validate()


class DraftTests(unittest.TestCase):
    def setUp(self):
        stem = f'blackbox-plugin-{VERSION}-candidate'
        self.contents = {stem + '.zip': b'zip', stem + '.zip.sha256': b'sum',
                         stem + '.release-manifest.json': b'{}'}
        self.release = {
            'tag_name': TAG, 'target_commitish': COMMIT, 'draft': True,
            'prerelease': True, 'published_at': None,
            'html_url': f'https://github.com/{release.REPOSITORY}/releases/tag/{TAG}',
            'assets': [{'name': name, 'size': len(data), 'state': 'uploaded',
                        'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
                       for name, data in self.contents.items()],
        }
        patch.object(release, 'validate_source', return_value={}).start()
        patch.object(release, 'validate_artifacts', return_value=self.contents).start()
        self.available = patch.object(release, 'ensure_available').start()
        self.api = patch.object(release, 'api', return_value={
            'ref': 'refs/tags/' + TAG, 'object': {'type': 'commit', 'sha': COMMIT}}).start()
        patch.object(release, 'releases', return_value=iter([self.release])).start()
        self.addCleanup(patch.stopall)

    def test_creates_new_tag_and_draft_only_with_exact_three_files(self):
        def run(arguments):
            self.assertEqual(arguments[:3], ['release', 'create', TAG])
            self.assertIn('--draft', arguments)
            self.assertIn('--prerelease', arguments)
            self.assertIn('--verify-tag', arguments)
            self.assertIn('--latest=false', arguments)
            self.assertNotIn('--clobber', arguments)
            self.assertEqual(arguments[arguments.index('--target') + 1], COMMIT)
            self.assertEqual(arguments[arguments.index('--repo') + 1], 'github.com/' + release.REPOSITORY)
            files = arguments[3:arguments.index('--repo')]
            self.assertEqual({Path(path).name for path in files}, set(self.contents))
            for path in files:
                self.assertEqual(Path(path).read_bytes(), self.contents[Path(path).name])
            notes = Path(arguments[arguments.index('--notes-file') + 1]).read_text(encoding='utf-8')
            self.assertIn('require maintainer acceptance', notes)
            self.assertNotIn('@', notes)
            return subprocess.CompletedProcess(arguments, 0, '', '')
        with patch.object(release, 'run_gh', side_effect=run) as gh:
            result = release.create_draft(TAG, COMMIT, Path('unused'))
        self.assertEqual(gh.call_count, 1)
        self.assertEqual(self.api.call_args.kwargs,
                         {'method': 'POST', 'fields': (('ref', 'refs/tags/' + TAG), ('sha', COMMIT))})
        self.assertEqual(result['state'], 'draft')
        self.assertFalse(result['published'])

    def test_existing_remote_state_causes_no_mutation(self):
        self.available.side_effect = release.ReleaseError('already exists')
        with patch.object(release, 'run_gh') as gh, self.assertRaises(release.ReleaseError):
            release.create_draft(TAG, COMMIT, Path('unused'))
        self.api.assert_not_called()
        gh.assert_not_called()

    def test_atomic_tag_collision_does_not_create_release_or_delete(self):
        self.api.side_effect = release.ReleaseError('HTTP 422')
        with patch.object(release, 'run_gh') as gh, self.assertRaisesRegex(release.ReleaseError, 'uncertain'):
            release.create_draft(TAG, COMMIT, Path('unused'))
        gh.assert_not_called()
        self.assertEqual(self.api.call_count, 1)

    def test_upload_failure_reports_partial_state_without_cleanup(self):
        with patch.object(release, 'run_gh', return_value=subprocess.CompletedProcess([], 1, '', '')) as gh:
            with self.assertRaisesRegex(release.ReleaseError, 'partial draft manually'):
                release.create_draft(TAG, COMMIT, Path('unused'))
        self.assertEqual(gh.call_count, 1)
        self.assertEqual(self.api.call_count, 1)

    def test_published_response_is_never_accepted_as_draft_success(self):
        self.release['draft'] = False
        with patch.object(release, 'run_gh', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaisesRegex(release.ReleaseError, 'verification did not complete'):
                release.create_draft(TAG, COMMIT, Path('unused'))

    def test_hyphen_in_build_metadata_does_not_mark_stable_version_prerelease(self):
        tag = 'v1.2.3+build-a'
        self.api.return_value['ref'] = 'refs/tags/' + tag
        self.release.update(tag_name=tag, prerelease=False)
        with patch.object(release, 'run_gh', return_value=subprocess.CompletedProcess([], 0, '', '')) as gh:
            release.create_draft(tag, COMMIT, Path('unused'))
        self.assertNotIn('--prerelease', gh.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
