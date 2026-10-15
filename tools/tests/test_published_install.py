"""Published-link promotion uses synthetic documents and mocked public releases."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import published_install as published


OLD = '0.4.0-alpha.6.45'
NEW = '0.4.0-alpha.6.46'
COMMIT = 'a' * 40
NEXT_COMMIT = 'b' * 40
DIGEST = 'c' * 64
NEXT_DIGEST = 'd' * 64


def metadata(version=OLD, commit=COMMIT, digest=DIGEST):
    return {'schema_version': 1, 'version': version, 'tag': 'v' + version,
            'tag_commit': commit, 'archive_sha256': digest}


def verified(version=NEW, commit=NEXT_COMMIT, digest=NEXT_DIGEST):
    return {'download_verification': 'passed', 'anonymous': True,
            'repository': published.REPOSITORY, 'version': version, 'tag': 'v' + version,
            'tag_commit': commit, 'archive_sha256': digest}


def document(version=OLD):
    return ('# Synthetic public guide\nOutside text is unchanged.\n'
            + published.START + '\nCurrent version **' + version + '**\n'
            + '<a href="' + published.REPOSITORY_URL + '/releases/tag/v' + version + '">'
            + '<img src="https://img.shields.io/badge/alpha-' + version.replace('-', '--')
            + '-b6532f?style=flat-square" alt="Alpha ' + version + '"></a>\n'
            + published.END + '\n\n' + published.START + '\n```text\n'
            + 'Install from ' + published.REPOSITORY_URL + '/blob/v' + version
            + '/release/INSTALL-CODEX.md\n```\n' + published.END + '\n')


class PublishedInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in published.FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(metadata()) + '\n' if name == published.POINTER else document(), encoding='utf-8', newline='\n')
        self.release = patch.object(published, 'verify_release', return_value=verified()).start()
        self.ancestor = patch.object(published, '_assert_ancestor').start()
        self.addCleanup(patch.stopall)

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def append(self, name, text):
        path = self.root / name
        path.write_bytes(path.read_bytes() + text.encode())

    def reject_without_writes(self, tag='v' + NEW, error=published.PublishedInstallError):
        before = self.snapshot()
        with self.assertRaises(error):
            published.promote(tag, root=self.root)
        self.assertEqual(self.snapshot(), before)

    def test_public_version_may_trail_candidate_version(self):
        plugin = self.root / 'plugins/blackbox/.codex-plugin/plugin.json'
        plugin.parent.mkdir(parents=True)
        plugin.write_text(json.dumps({'version': NEW}), encoding='utf-8')
        candidate = self.root / 'release/INSTALL.md'
        candidate.write_text('Candidate ' + NEW, encoding='utf-8')
        result = published.check(self.root)
        self.assertEqual(result['version'], OLD)
        self.assertFalse(result['changed'])
        self.release.assert_not_called()
        self.ancestor.assert_not_called()

    def test_promotion_changes_only_pointer_and_marked_regions(self):
        candidate = self.root / 'release/INSTALL.md'
        candidate.write_text('Candidate ' + NEW, encoding='utf-8')
        before = self.snapshot()
        result = published.promote('v' + NEW, NEXT_COMMIT, self.root)
        self.assertTrue(result['changed'])
        self.release.assert_called_once_with('v' + NEW, expected_commit=NEXT_COMMIT)
        self.ancestor.assert_called_once_with(self.root, NEXT_COMMIT)
        self.assertEqual(candidate.read_bytes(), before['release/INSTALL.md'])
        for name in published.DOCUMENTS:
            self.assertEqual((self.root / name).read_text(encoding='utf-8'), document(NEW))
        self.assertEqual(json.loads((self.root / published.POINTER).read_text()), metadata(NEW, NEXT_COMMIT, NEXT_DIGEST))
        self.assertEqual(published.check(self.root)['tag'], 'v' + NEW)

    def test_same_version_is_idempotent_but_reverified(self):
        self.release.return_value = verified(OLD, COMMIT, DIGEST)
        before = self.snapshot()
        result = published.promote('v' + OLD, root=self.root)
        self.assertFalse(result['changed'])
        self.assertEqual(self.snapshot(), before)
        self.release.assert_called_once()
        self.ancestor.assert_called_once()

    def test_rerun_after_success_is_idempotent(self):
        published.promote('v' + NEW, root=self.root)
        before = self.snapshot()
        self.assertFalse(published.promote('v' + NEW, root=self.root)['changed'])
        self.assertEqual(self.snapshot(), before)

    def test_same_version_changed_checksum_is_rejected(self):
        self.release.return_value = verified(OLD, COMMIT, NEXT_DIGEST)
        self.reject_without_writes('v' + OLD)

    def test_same_version_moved_tag_is_rejected(self):
        self.release.return_value = verified(OLD, NEXT_COMMIT, DIGEST)
        self.reject_without_writes('v' + OLD)

    def test_downgrade_does_not_contact_network(self):
        self.reject_without_writes('v0.4.0-alpha.6.9')
        self.release.assert_not_called()

    def test_equal_precedence_build_metadata_is_not_new_version(self):
        self.reject_without_writes('v' + OLD + '+different')
        self.release.assert_not_called()

    def test_numeric_prerelease_order_and_semver_precedence(self):
        ascending = ['1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.9', '1.0.0-alpha.10',
                     '1.0.0-alpha.beta', '1.0.0-beta', '1.0.0-beta.2',
                     '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0', '1.0.1', '2.0.0']
        for left, right in zip(ascending, ascending[1:]):
            with self.subTest(left=left, right=right):
                self.assertLess(published._compare_versions(left, right), 0)
                self.assertGreater(published._compare_versions(right, left), 0)
        self.assertEqual(published._compare_versions('1.0.0+one', '1.0.0+two'), 0)

    def test_invalid_tag_is_rejected_before_network(self):
        for tag in ['main', 'v01.2.3', 'v1.2.3-alpha.01', 'v1.2.3/../../main', 'v1.2.3\n']:
            with self.subTest(tag=tag):
                self.reject_without_writes(tag)
        self.release.assert_not_called()

    def test_draft_or_failed_download_never_changes_links(self):
        self.release.side_effect = published.DownloadVerificationError('Release must be published and match the requested tag.')
        self.reject_without_writes(error=published.DownloadVerificationError)

    def test_real_download_verifier_rejects_synthetic_draft(self):
        import verify_release_downloads
        self.release.side_effect = lambda tag, expected_commit=None: verify_release_downloads.verify_release(
            tag, expected_commit, fetch=lambda url, limit: json.dumps({'draft': True, 'tag_name': tag}).encode())
        self.reject_without_writes(error=published.DownloadVerificationError)
        self.ancestor.assert_not_called()

    def test_unverified_or_wrong_release_report_is_rejected(self):
        for changes in [{'anonymous': False}, {'tag': 'v1.2.3'}, {'repository': 'other/repo'},
                        {'download_verification': 'failed'}, {'tag_commit': '../main'},
                        {'archive_sha256': 'not-a-digest'}]:
            with self.subTest(changes=changes):
                self.release.return_value = dict(verified(), **changes)
                self.reject_without_writes()

    def test_nonancestor_release_is_rejected(self):
        self.ancestor.side_effect = published.PublishedInstallError('Tag does not belong to HEAD.')
        self.reject_without_writes()

    def test_missing_or_unbalanced_markers_stop_before_network(self):
        path = self.root / 'README.md'
        for content in ['no markers', published.START + '\nunfinished',
                        published.END + document(), published.START + document() + published.END,
                        document().replace('published:start', 'published:begin')]:
            with self.subTest(content=content[:30]):
                path.write_text(content, encoding='utf-8')
                self.reject_without_writes()
        self.release.assert_not_called()

    def test_document_version_drift_is_rejected(self):
        path = self.root / 'README.md'
        path.write_text(document(NEW), encoding='utf-8')
        self.reject_without_writes()
        self.release.assert_not_called()

    def test_version_outside_markers_is_rejected(self):
        self.append('README.md', '\nCurrent version ' + OLD)
        self.reject_without_writes()

    def test_historical_release_description_is_preserved(self):
        history = '\nThe published `v' + OLD + '` installation package and checksum are preserved.\n'
        self.append('docs/en/README.md', history)
        published.promote('v' + NEW, root=self.root)
        self.assertTrue((self.root / 'docs/en/README.md').read_text().endswith(history))

    def test_stable_version_numeric_prefix_is_replaced_once(self):
        for name in published.FILES:
            content = json.dumps(metadata('1.2.3')) if name == published.POINTER else document('1.2.3')
            (self.root / name).write_text(content, encoding='utf-8')
        self.release.return_value = verified('1.2.30', NEXT_COMMIT, NEXT_DIGEST)
        published.promote('v1.2.30', root=self.root)
        self.assertEqual((self.root / 'README.md').read_text(), document('1.2.30'))

    def test_badge_escaping_across_stable_and_prerelease_transitions(self):
        for old, new in [('1.2.3', '1.2.4-beta.1'), ('1.2.4-beta.1', '1.2.4'),
                         ('1.2.3-alpha--one+build--two', '1.2.4--foo+build--two')]:
            with self.subTest(old=old, new=new):
                for name in published.FILES:
                    content = json.dumps(metadata(old)) if name == published.POINTER else document(old)
                    (self.root / name).write_text(content, encoding='utf-8')
                self.release.return_value = verified(new)
                published.promote('v' + new, root=self.root)
                self.assertEqual((self.root / 'README.md').read_text(), document(new))

    def test_unescaped_prerelease_badge_is_rejected(self):
        path = self.root / 'README.md'
        path.write_text(document().replace(OLD.replace('-', '--'), OLD), encoding='utf-8')
        self.reject_without_writes()

    def test_reference_style_candidate_install_link_is_rejected(self):
        self.append('README.md', '\n[Install][candidate]\n\n[candidate]: release/INSTALL.md\n')
        self.reject_without_writes()

    def test_relative_stable_guide_links_are_allowed(self):
        links = {'README.md': 'docs/INSTALL.md', 'README.en.md': 'docs/INSTALL.md',
                 'docs/INSTALL.md': 'INSTALL.md', 'docs/en/README.md': '../INSTALL.md'}
        for name, target in links.items():
            self.append(name, '\n[Installation guide](' + target + ')\n')
        self.assertEqual(published.check(self.root)['version'], OLD)

    def test_candidate_main_and_foreign_install_links_are_rejected(self):
        invalid = [
            'release/INSTALL.md', '../release/INSTALL.md', '/release/INSTALL.md',
            published.REPOSITORY_URL + '/blob/main/release/INSTALL-CODEX.md',
            published.REPOSITORY_URL + '/blob/main/release/INSTALL.md',
            published.RAW + '/main/tools/bootstrap_blackbox.py',
            'https://example.invalid/release/INSTALL-CODEX.md',
            published.REPOSITORY_URL + '/blob/v' + OLD + '/release/INSTALL-CODEX.md?x=y',
            published.REPOSITORY_URL + '/blob/main/release/%49NSTALL-CODEX.md',
        ]
        path = self.root / 'README.md'
        for link in invalid:
            with self.subTest(link=link):
                path.write_text(document() + '\n' + published.START + '\n[Install](' + link + ')\n' + published.END, encoding='utf-8')
                self.reject_without_writes()

    def test_fixed_link_outside_marker_is_rejected(self):
        self.append('README.md', '\n[Install](' + published.RAW + '/v' + OLD + '/release/INSTALL-CODEX.md)')
        self.reject_without_writes()

    def test_fixed_install_guide_and_raw_bootstrap_inside_marker_advance(self):
        path = self.root / 'README.md'
        text = document() + '\n' + published.START + '\n'
        text += '[Payload guide](' + published.REPOSITORY_URL + '/blob/v' + OLD + '/release/INSTALL.md)\n'
        text += '[Bootstrap](' + published.RAW + '/v' + OLD + '/tools/bootstrap_blackbox.py)\n'
        text += published.END + '\n'
        path.write_text(text, encoding='utf-8')
        published.promote('v' + NEW, root=self.root)
        self.assertIn('/v' + NEW + '/release/INSTALL.md', path.read_text())
        self.assertIn('/v' + NEW + '/tools/bootstrap_blackbox.py', path.read_text())

    def test_each_document_must_contain_install_entrypoint(self):
        (self.root / 'README.md').write_text(published.START + '\n' + OLD + '\n' + published.END, encoding='utf-8')
        self.reject_without_writes()

    def test_invalid_pointer_schema_and_duplicate_keys(self):
        path = self.root / published.POINTER
        for value in [dict(metadata(), schema_version=True), dict(metadata(), extra='field'),
                      dict(metadata(), tag='v1.2.3'), dict(metadata(), tag_commit='A' * 40),
                      dict(metadata(), archive_sha256='D' * 64), []]:
            with self.subTest(value=value):
                path.write_text(json.dumps(value), encoding='utf-8')
                self.reject_without_writes()
        path.write_text(json.dumps(metadata())[:-1] + ',"version":"' + OLD + '"}', encoding='utf-8')
        self.reject_without_writes()

    def test_missing_file_is_rejected_without_partial_update(self):
        (self.root / 'README.en.md').unlink()
        self.reject_without_writes()

    def test_oversized_file_rejected_before_network(self):
        with patch.object(published, 'MAX_FILE_BYTES', 1):
            self.reject_without_writes()
        self.release.assert_not_called()

    def test_paths_outside_allowlist_rejected(self):
        for name in ['release/INSTALL.md', '../outside', 'plugins/blackbox/.codex-plugin/plugin.json']:
            with self.subTest(name=name), self.assertRaises(published.PublishedInstallError):
                published._path(self.root, name)

    def test_symlink_file_is_rejected(self):
        path = self.root / 'README.md'
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaises(published.PublishedInstallError):
                published._path(self.root, 'README.md')
        self.assertTrue(path.is_file())

    def test_replace_failure_restores_already_changed_files(self):
        before = self.snapshot()
        replace = os.replace
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Synthetic write failure')
            return replace(source, destination)

        with patch.object(published.os, 'replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                published.promote('v' + NEW, root=self.root)
        self.assertEqual(self.snapshot(), before)

    def test_staging_failure_never_changes_destinations(self):
        before = self.snapshot()
        stage = published._stage
        calls = 0

        def fail_second(path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Synthetic stage failure')
            return stage(path, content)

        with patch.object(published, '_stage', side_effect=fail_second):
            with self.assertRaises(OSError):
                published.promote('v' + NEW, root=self.root)
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_document_edit_is_not_overwritten(self):
        stage = published._stage
        once = False

        def edit_during_staging(path, content):
            nonlocal once
            if not once:
                once = True
                self.append('README.md', '\nA concurrent edit.\n')
            return stage(path, content)

        with patch.object(published, '_stage', side_effect=edit_during_staging):
            with self.assertRaises(published.PublishedInstallError):
                published.promote('v' + NEW, root=self.root)
        self.assertTrue((self.root / 'README.md').read_text().endswith('A concurrent edit.\n'))
        self.assertEqual(json.loads((self.root / published.POINTER).read_text())['version'], OLD)
        self.assertFalse(list(self.root.rglob('*.tmp')))

    def test_cli_json_success_and_failure(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = published.main(['check', '--root', str(self.root)])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())['changed'])
        self.release.side_effect = published.DownloadVerificationError('Synthetic unpublished release.')
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = published.main(['promote', '--root', str(self.root), '--tag', 'v' + NEW])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(error.getvalue())['published_install'], 'failed')


class AncestryTests(unittest.TestCase):
    def test_git_uses_full_commit_argv_without_shell(self):
        with patch.object(published.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            published._assert_ancestor(Path('repository'), COMMIT)
        self.assertEqual(run.call_args.args[0], ['git', '-C', 'repository', 'merge-base', '--is-ancestor', COMMIT, 'HEAD'])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs['timeout'], 15)

    def test_missing_commit_or_nonancestor_fails_closed(self):
        for code in (1, 128):
            with self.subTest(code=code), patch.object(published.subprocess, 'run', return_value=subprocess.CompletedProcess([], code)):
                with self.assertRaises(published.PublishedInstallError):
                    published._assert_ancestor(Path('repository'), COMMIT)

    def test_ancestry_timeout_fails_closed(self):
        with patch.object(published.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 15)):
            with self.assertRaisesRegex(published.PublishedInstallError, 'timed out'):
                published._assert_ancestor(Path('repository'), COMMIT)


if __name__ == '__main__':
    unittest.main()
