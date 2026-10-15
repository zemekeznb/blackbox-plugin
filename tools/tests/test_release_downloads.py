"""Synthetic release and transport tests; no GitHub account or runtime needed."""
import contextlib
import copy
import hashlib
import http.client
import io
import json
from pathlib import Path
import ssl
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_release_downloads as verifier


TAG = 'v1.2.3-alpha.4'
VERSION = TAG[1:]
COMMIT = 'a' * 40
OTHER_COMMIT = 'b' * 40
STEM = 'blackbox-plugin-' + VERSION + '-candidate'
ZIP = STEM + '.zip'
CHECKSUM = ZIP + '.sha256'
MANIFEST = STEM + '.release-manifest.json'
BOOTSTRAP = 'tools/bootstrap_blackbox.py'
INSTRUCTIONS = 'release/INSTALL-CODEX.md'
PLUGIN = 'plugins/blackbox/.codex-plugin/plugin.json'


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


class ReleaseFixture:
    def __init__(self, *, source=COMMIT, tag_commit=COMMIT, dirty=False, payload=None, version=None, tag=TAG):
        self.tag = tag
        payload_version = tag[1:]
        stem = 'blackbox-plugin-' + payload_version + '-candidate'
        zip_name, checksum_name, manifest_name = stem + '.zip', stem + '.zip.sha256', stem + '.release-manifest.json'
        self.payload = {
            BOOTSTRAP: ("VERSION = '" + payload_version + "'\nREPOSITORY = '" + verifier.REPOSITORY_URL + "'\n"
                        "raise RuntimeError('downloaded code must never run')\n").encode(),
            INSTRUCTIONS: ('Install using ' + verifier.RAW + '/' + tag + '/' + BOOTSTRAP + '\n').encode(),
            PLUGIN: encoded({'name': 'blackbox', 'version': payload_version}),
        }
        if payload:
            self.payload.update(payload)
        manifest = {'schema_version': 1, 'version': version or payload_version,
                    'source': {'commit': source, 'dirty': dirty},
                    'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                              for name, data in self.payload.items()}}
        self.payload['release-manifest.json'] = encoded(manifest)
        self.payload['SHA256SUMS'] = ''.join(
            hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
            for name, data in self.payload.items()).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, data in self.payload.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, data)
        archive = stream.getvalue()
        assets = {zip_name: archive, checksum_name: (hashlib.sha256(archive).hexdigest() + '  ' + zip_name + '\n').encode(),
                  manifest_name: self.payload['release-manifest.json']}
        self.release = {'tag_name': tag, 'draft': False,
                        'html_url': verifier.REPOSITORY_URL + '/releases/tag/' + tag,
                        'assets': [{'name': name, 'state': 'uploaded', 'size': len(data),
                                    'browser_download_url': self.asset_url(name)}
                                   for name, data in assets.items()]}
        self.responses = {self.asset_url(name): data for name, data in assets.items()}
        self.responses.update({verifier.RAW + '/' + tag + '/' + name: self.payload[name]
                               for name in (BOOTSTRAP, INSTRUCTIONS)})
        self.reference = {'ref': 'refs/tags/' + tag, 'object': {'type': 'commit', 'sha': tag_commit}}
        self.calls = []

    def asset_url(self, name):
        return verifier.REPOSITORY_URL + '/releases/download/' + self.tag + '/' + name

    def change_asset(self, name, data):
        self.responses[self.asset_url(name)] = data
        next(asset for asset in self.release['assets'] if asset['name'] == name)['size'] = len(data)

    def fetch(self, url, limit):
        self.calls.append((url, limit))
        if url == verifier.API + '/releases/tags/' + self.tag:
            return encoded(self.release)
        if url == verifier.API + '/git/ref/tags/' + self.tag:
            return encoded(self.reference)
        return self.responses[url]


class ReleaseTests(unittest.TestCase):
    def verify(self, fixture, expected=COMMIT):
        return verifier.verify_release(TAG, expected, fixture.fetch)

    def test_valid_release_is_verified_without_executing_payload(self):
        fixture = ReleaseFixture()
        result = self.verify(fixture)
        self.assertEqual(result['download_verification'], 'passed')
        self.assertTrue(result['source_matches_tag'])
        self.assertEqual(result['source_check'], 'expected_commit_verified')
        self.assertEqual(result['verified_file_count'], 5)
        self.assertFalse(result['package_code_executed'])
        self.assertFalse(result['plugin_installed'])
        self.assertEqual(len(fixture.calls), 7)

    def test_historical_source_mismatch_is_explicit_without_expected_commit(self):
        fixture = ReleaseFixture(tag='v0.4.0-alpha.6.45',
                                 tag_commit='9301faf80e1d59c07a8456ff9b72fac5b7719669',
                                 source='3c88cc3fc40bba1cbcd94bbe983108a078a20eaf')
        result = verifier.verify_release(fixture.tag, fetch=fixture.fetch)
        self.assertFalse(result['source_matches_tag'])
        self.assertEqual(result['manifest_source_commit'], '3c88cc3fc40bba1cbcd94bbe983108a078a20eaf')
        self.assertEqual(result['source_check'], 'historical_provenance_differs_from_current_tag')

    def test_future_or_unknown_historical_mismatches_rejected_without_expected_commit(self):
        fixtures = (ReleaseFixture(source=OTHER_COMMIT),
                    ReleaseFixture(tag='v0.4.0-alpha.6.45', tag_commit=OTHER_COMMIT,
                                   source='3c88cc3fc40bba1cbcd94bbe983108a078a20eaf'),
                    ReleaseFixture(tag='v0.4.0-alpha.6.45',
                                   tag_commit='9301faf80e1d59c07a8456ff9b72fac5b7719669', source=OTHER_COMMIT))
        for fixture in fixtures:
            with self.subTest(tag=fixture.tag), self.assertRaisesRegex(verifier.DownloadVerificationError, 'differs from'):
                verifier.verify_release(fixture.tag, fetch=fixture.fetch)

    def test_expected_commit_rejects_moved_tag_and_wrong_manifest_provenance(self):
        for fixture in (ReleaseFixture(tag_commit=OTHER_COMMIT), ReleaseFixture(source=OTHER_COMMIT)):
            with self.subTest(fixture=fixture), self.assertRaisesRegex(verifier.DownloadVerificationError, 'expected source commit'):
                self.verify(fixture)

    def test_invalid_tags_and_commit_fail_before_network(self):
        fetch = Mock()
        for tag in ('main', 'v01.2.3', 'v1.2.3-01', 'v1.2.3..4', 'v1.2.3/',
                    'v1.2.3?token=private', 'v1.2.3\n', '../v1.2.3', 'v' + 'a' * 129):
            with self.subTest(tag=tag), self.assertRaises(verifier.DownloadVerificationError):
                verifier.verify_release(tag, fetch=fetch)
        for commit in ('a' * 39, 'A' * 40, 'main', 'a' * 40 + '\n'):
            with self.subTest(commit=commit), self.assertRaises(verifier.DownloadVerificationError):
                verifier.verify_release(TAG, commit, fetch)
        fetch.assert_not_called()

    def test_semantic_tag_accepts_prerelease_and_build_metadata(self):
        self.assertEqual(verifier.validate_tag('v0.1.0-alpha.1+build.2'), '0.1.0-alpha.1+build.2')

    def test_release_must_be_published_and_canonical(self):
        for changes in ({'draft': True}, {'draft': None}, {'tag_name': 'v1.2.4'},
                        {'html_url': 'https://example.invalid/release'}):
            fixture = ReleaseFixture()
            fixture.release.update(changes)
            with self.subTest(changes=changes), self.assertRaises(verifier.DownloadVerificationError):
                self.verify(fixture)

    def test_exact_asset_set_required(self):
        for mode in ('missing', 'extra', 'duplicate', 'renamed'):
            fixture = ReleaseFixture()
            if mode == 'missing':
                fixture.release['assets'].pop()
            elif mode == 'extra':
                fixture.release['assets'].append(copy.deepcopy(fixture.release['assets'][0]))
            elif mode == 'duplicate':
                fixture.release['assets'][1] = copy.deepcopy(fixture.release['assets'][0])
            else:
                fixture.release['assets'][0]['name'] = 'other.zip'
            with self.subTest(mode=mode), self.assertRaises(verifier.DownloadVerificationError):
                self.verify(fixture)

    def test_asset_url_state_and_size_checked_before_download(self):
        for changes in ({'browser_download_url': 'https://example.invalid/private.zip'},
                        {'browser_download_url': verifier.REPOSITORY_URL + '/releases/download/main/other.zip'},
                        {'state': 'new'}, {'size': 0}, {'size': True}, {'size': verifier.MAX_PACKAGE_BYTES + 1}):
            fixture = ReleaseFixture()
            fixture.release['assets'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(verifier.DownloadVerificationError):
                self.verify(fixture)
            self.assertEqual(len(fixture.calls), 1)

    def test_actual_asset_size_must_match_metadata(self):
        fixture = ReleaseFixture()
        fixture.release['assets'][0]['size'] += 1
        with self.assertRaisesRegex(verifier.DownloadVerificationError, 'size differs'):
            self.verify(fixture)

    def test_outer_checksum_digest_and_exact_filename_are_required(self):
        for checksum in (b'0' * 64 + b'  ' + ZIP.encode() + b'\n',
                         b'a' * 64 + b'  ../' + ZIP.encode() + b'\n',
                         b'a' * 64 + b'  other.zip\n', b'bad checksum\n'):
            fixture = ReleaseFixture()
            fixture.change_asset(CHECKSUM, checksum)
            with self.subTest(checksum=checksum[:8]), self.assertRaisesRegex(verifier.DownloadVerificationError, 'checksum'):
                self.verify(fixture)

    def test_external_manifest_must_be_byte_identical(self):
        fixture = ReleaseFixture()
        fixture.change_asset(MANIFEST, fixture.responses[fixture.asset_url(MANIFEST)] + b'\n')
        with self.assertRaisesRegex(verifier.DownloadVerificationError, 'External manifest bytes'):
            self.verify(fixture)

    def test_invalid_zip_rejected_even_with_matching_outer_checksum(self):
        fixture = ReleaseFixture()
        data = b'not a zip file'
        fixture.change_asset(ZIP, data)
        fixture.change_asset(CHECKSUM, (hashlib.sha256(data).hexdigest() + '  ' + ZIP + '\n').encode())
        with self.assertRaisesRegex(verifier.DownloadVerificationError, 'archive integrity'):
            self.verify(fixture)

    def test_archive_and_plugin_versions_must_match_release(self):
        fixtures = (ReleaseFixture(version='1.2.4'),
                    ReleaseFixture(payload={PLUGIN: encoded({'name': 'blackbox', 'version': '1.2.4'})}),
                    ReleaseFixture(payload={PLUGIN: encoded({'name': 'other', 'version': VERSION})}))
        for fixture in fixtures:
            with self.subTest(fixture=fixture), self.assertRaisesRegex(verifier.DownloadVerificationError, 'version'):
                self.verify(fixture)

    def test_dirty_or_missing_source_rejected(self):
        for fixture in (ReleaseFixture(dirty=True), ReleaseFixture(source=None)):
            with self.subTest(fixture=fixture), self.assertRaisesRegex(verifier.DownloadVerificationError, 'clean source'):
                self.verify(fixture, expected=None)

    def test_public_entrypoint_bytes_must_equal_packaged_copy(self):
        for path in (BOOTSTRAP, INSTRUCTIONS):
            fixture = ReleaseFixture()
            fixture.responses[verifier.RAW + '/' + TAG + '/' + path] += b'\n'
            with self.subTest(path=path), self.assertRaisesRegex(verifier.DownloadVerificationError, 'entrypoint bytes'):
                self.verify(fixture)

    def test_bootstrap_literal_version_and_repository_checked_without_execution(self):
        original = ReleaseFixture().payload[BOOTSTRAP]
        cases = (original.replace(VERSION.encode(), b'1.2.4'),
                 original.replace(verifier.REPOSITORY_URL.encode(), b'https://example.invalid'),
                 original + b"VERSION = '1.2.3-alpha.4'\n",
                 original.replace(b"VERSION = '", b"VERSION = str('"))
        for data in cases:
            with self.subTest(data=data[:20]), self.assertRaises(verifier.DownloadVerificationError):
                self.verify(ReleaseFixture(payload={BOOTSTRAP: data}))

    def test_instruction_bootstrap_url_must_be_fixed_to_same_repository_tag(self):
        original = ReleaseFixture().payload[INSTRUCTIONS]
        cases = (original.replace(TAG.encode(), b'main'),
                 original.replace(verifier.REPOSITORY.encode(), b'other/project'),
                 b'No bootstrap link',
                 original + b'https://example.invalid/tools/bootstrap_blackbox.py')
        for data in cases:
            with self.subTest(data=data[:20]), self.assertRaisesRegex(verifier.DownloadVerificationError, 'fixed tag'):
                self.verify(ReleaseFixture(payload={INSTRUCTIONS: data}))

    def test_annotated_tag_is_resolved_to_source_commit(self):
        fixture = ReleaseFixture()
        fixture.reference['object'] = {'type': 'tag', 'sha': OTHER_COMMIT}
        fixture.responses[verifier.API + '/git/tags/' + OTHER_COMMIT] = encoded({
            'sha': OTHER_COMMIT, 'object': {'type': 'commit', 'sha': COMMIT}})
        self.assertEqual(self.verify(fixture)['tag_commit'], COMMIT)

    def test_tag_cycles_and_invalid_reference_identity_rejected(self):
        for mode in ('cycle', 'wrong-ref', 'blob', 'bad-sha'):
            fixture = ReleaseFixture()
            if mode == 'cycle':
                fixture.reference['object'] = {'type': 'tag', 'sha': OTHER_COMMIT}
                fixture.responses[verifier.API + '/git/tags/' + OTHER_COMMIT] = encoded({
                    'sha': OTHER_COMMIT, 'object': fixture.reference['object']})
            elif mode == 'wrong-ref':
                fixture.reference['ref'] = 'refs/tags/other'
            elif mode == 'blob':
                fixture.reference['object']['type'] = 'blob'
            else:
                fixture.reference['object']['sha'] = 'private value'
            with self.subTest(mode=mode), self.assertRaises(verifier.DownloadVerificationError):
                self.verify(fixture)

    def test_metadata_duplicate_keys_rejected(self):
        with self.assertRaisesRegex(verifier.DownloadVerificationError, 'invalid JSON'):
            verifier.verify_release(TAG, fetch=lambda url, limit: b'{"draft":false,"draft":true}')

    def test_injected_fetch_cannot_return_oversized_data(self):
        with self.assertRaisesRegex(verifier.DownloadVerificationError, 'limit'):
            verifier.verify_release(TAG, fetch=lambda url, limit: b'x' * (limit + 1))


class TransportTests(unittest.TestCase):
    def response(self, data=b'public', url=verifier.API + '/releases/tags/' + TAG, length=None):
        response = Mock()
        response.geturl.return_value = url
        response.headers = {} if length is None else {'Content-Length': length}
        response.read.return_value = data
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_requests_are_anonymous_gets_and_reads_are_bounded(self):
        response = self.response()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(verifier.urllib.request, 'build_opener', return_value=opener), \
                patch.dict('os.environ', {'GH_TOKEN': 'never-send-this', 'GITHUB_TOKEN': 'never-send-this'}):
            self.assertEqual(verifier.download(verifier.API, 100), b'public')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertNotIn('authorization', {key.lower() for key in request.headers})
        self.assertNotIn('never-send-this', repr(request.headers))
        response.read.assert_called_once_with(101)

    def test_transient_http_retries_then_succeeds(self):
        opener = Mock()
        opener.open.side_effect = [urllib.error.HTTPError(verifier.API, 503, 'private server error', {}, None), self.response()]
        with patch.object(verifier.urllib.request, 'build_opener', return_value=opener), patch.object(verifier.time, 'sleep') as sleep:
            self.assertEqual(verifier.download(verifier.API, 100), b'public')
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_missing_release_does_not_retry_or_leak_server_response(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(verifier.API, 404, 'private server error', {}, None)
        with patch.object(verifier.urllib.request, 'build_opener', return_value=opener), patch.object(verifier.time, 'sleep') as sleep:
            with self.assertRaisesRegex(verifier.DownloadVerificationError, 'HTTP status 404') as caught:
                verifier.download(verifier.API, 100)
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIn('private', str(caught.exception))

    def test_timeout_retry_is_bounded_and_tls_failures_are_not_retried(self):
        for reason, count in ((TimeoutError('private path'), 3), (ssl.SSLError('private certificate'), 1)):
            opener = Mock()
            opener.open.side_effect = urllib.error.URLError(reason)
            with self.subTest(reason=type(reason)), patch.object(verifier.urllib.request, 'build_opener', return_value=opener), \
                    patch.object(verifier.time, 'sleep'), self.assertRaises(verifier.DownloadVerificationError):
                verifier.download(verifier.API, 100)
            self.assertEqual(opener.open.call_count, count)

    def test_truncated_transport_retries_without_exposing_partial_content(self):
        response = self.response()
        response.read.side_effect = http.client.IncompleteRead(b'private partial content')
        opener = Mock()
        opener.open.return_value = response
        with patch.object(verifier.urllib.request, 'build_opener', return_value=opener), \
                patch.object(verifier.time, 'sleep'), self.assertRaises(verifier.DownloadVerificationError) as caught:
            verifier.download(verifier.API, 100)
        self.assertEqual(opener.open.call_count, 3)
        self.assertNotIn('private', str(caught.exception))

    def test_size_limits_reject_header_and_body_overflow(self):
        for response in (self.response(length='101'), self.response(b'x' * 101), self.response(length='invalid')):
            opener = Mock()
            opener.open.return_value = response
            with patch.object(verifier.urllib.request, 'build_opener', return_value=opener), \
                    self.assertRaises(verifier.DownloadVerificationError):
                verifier.download(verifier.API, 100)
            self.assertEqual(opener.open.call_count, 1)

    def test_external_insecure_or_credentialed_redirects_rejected(self):
        handler = verifier.PublicRedirects()
        request = urllib.request.Request(verifier.API)
        for url in ('http://github.com/file', 'https://example.invalid/file',
                    'https://user:secret@github.com/file', 'https://github.com:444/file',
                    'https://github.com/file#private', 'https://github.com/file\nprivate'):
            with self.subTest(url=url), self.assertRaises(verifier.DownloadVerificationError):
                handler.redirect_request(request, None, 302, 'Found', {}, url)


class CommandTests(unittest.TestCase):
    def test_output_report_written_and_existing_file_preserved(self):
        result = {'download_verification': 'passed', 'tag': TAG}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'report.json'
            with patch.object(verifier, 'verify_release', return_value=result), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verifier.main(['--tag', TAG, '--output', str(output)]), 0)
            self.assertEqual(json.loads(output.read_text()), result)
            original = output.read_bytes()
            errors = io.StringIO()
            with patch.object(verifier, 'verify_release', return_value=result), contextlib.redirect_stderr(errors):
                self.assertEqual(verifier.main(['--tag', TAG, '--output', str(output)]), 2)
            self.assertEqual(output.read_bytes(), original)
            self.assertNotIn(str(output), errors.getvalue())

    def test_failure_contains_no_original_exception_paths_or_downloaded_text(self):
        errors = io.StringIO()
        with patch.object(verifier, 'verify_release', side_effect=OSError('private user path or token')), \
                contextlib.redirect_stderr(errors):
            self.assertEqual(verifier.main(['--tag', TAG]), 2)
        self.assertEqual(json.loads(errors.getvalue())['download_verification'], 'failed')
        self.assertNotIn('private user', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
