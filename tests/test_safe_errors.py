"""Failures identify a bounded stage and OS code without exposing local data."""
from contextlib import redirect_stderr, redirect_stdout
import errno
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts'
sys.path.insert(0, str(SCRIPTS))
import blackbox_core as core
import blackbox_launcher as launcher


class SafeErrorTests(unittest.TestCase):
    def setUp(self):
        # Error-format unit tests must not consult the developer's host binding.
        binding = patch('blackbox_runtime.read_binding', return_value=None)
        binding.start()
        self.addCleanup(binding.stop)

    def cli(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = core.main(list(arguments))
        return result, output.getvalue(), errors.getvalue()

    def denied(self):
        error = PermissionError(errno.EACCES, 'SENSITIVE_PRIVATE_TEXT', 'SENSITIVE_PRIVATE_PATH')
        error.winerror = 5
        return error

    def test_state_access_failure_reports_codes_without_path_or_message(self):
        with patch.object(launcher, 'resolve_state_directory', side_effect=self.denied()), \
                patch.object(core, 'Store') as store:
            code, output, errors = self.cli('status')
        self.assertEqual(code, 1)
        self.assertEqual(output, '')
        self.assertEqual(errors, 'Blackbox Plugin: PermissionError; stage=resolve_state_directory; '
                                'errno=13; winerror=5; operation not completed.\n')
        store.assert_not_called()

    def test_database_failure_is_distinct_and_does_not_expose_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(core, 'Store', side_effect=sqlite3.OperationalError('SENSITIVE_SQL')):
                code, _, errors = self.cli('--state-dir', directory, 'status')
        self.assertEqual(code, 1)
        self.assertIn('stage=open_store', errors)
        self.assertNotIn('SENSITIVE', errors)
        self.assertNotIn('errno=', errors)

    def test_status_and_open_command_failures_are_distinguished(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Mock()
            store.status.side_effect = self.denied()
            with patch.object(core, 'Store', return_value=store), \
                    patch.object(launcher, 'ensure_reviewer', side_effect=self.denied()):
                for arguments, expected in ((['status'], 'status'),
                                            (['open', '--no-browser'], 'open')):
                    code, _, errors = self.cli('--state-dir', directory, *arguments)
                    self.assertEqual(code, 1)
                    self.assertIn('stage=' + expected + ';', errors)
                    self.assertNotIn('SENSITIVE', errors)

    def test_specific_stage_is_allowlisted_and_attributes_cannot_inject_output(self):
        error = self.denied()
        error._blackbox_stage = 'physical_directory_open'
        self.assertEqual(core._safe_failure_details(error, 'open'),
                         'stage=physical_directory_open; errno=13; winerror=5')
        for unexpected in ('SENSITIVE_PRIVATE_PATH\n', ['physical_directory_open'], None):
            error._blackbox_stage = unexpected
            error.errno = 'SENSITIVE_SQL'
            error.winerror = True
            self.assertEqual(core._safe_failure_details(error, 'open'), 'stage=open')
        error.winerror = 2 ** 100
        self.assertEqual(core._safe_failure_details(error, ['bad']), 'stage=unknown')

    def test_unhashable_public_code_does_not_cause_a_secondary_error(self):
        error = ValueError('SENSITIVE_PRIVATE_TEXT')
        for value in ([], {}, ['runtime_binding_invalid']):
            error.public_code = value
            self.assertEqual(core._safe_failure_details(error, 'status'), 'stage=status')
            with patch.object(launcher, 'resolve_state_directory', side_effect=error):
                code, _, errors = self.cli('status')
            self.assertEqual(code, 1)
            self.assertIn('ValueError; stage=resolve_state_directory', errors)
            self.assertNotIn('SENSITIVE', errors)

    def test_hook_keeps_zero_exit_and_existing_failure_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'notes.sqlite3').write_bytes(b'synthetic-placeholder')
            error, store = self.denied(), Mock()
            store.record_stream.side_effect = error
            with patch.object(core, 'Store', return_value=store), \
                    patch.object(sys, 'stdin', Mock(buffer=io.BytesIO(b'{}'))):
                code, output, errors = self.cli('--state-dir', directory, 'hook')
            self.assertEqual(code, 0)
            self.assertEqual(output, '')
            self.assertIn('stage=hook; errno=13; winerror=5', errors)
            self.assertNotIn('SENSITIVE', errors)
            store.capture_failure.assert_called_once_with(error)
            error._capture_diagnostic_recorded = True
            store.capture_failure.reset_mock()
            with patch.object(core, 'Store', return_value=store), \
                    patch.object(sys, 'stdin', Mock(buffer=io.BytesIO(b'{}'))):
                self.assertEqual(self.cli('--state-dir', directory, 'hook')[0], 0)
            store.capture_failure.assert_not_called()

    def test_hook_store_failure_keeps_fallback_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'notes.sqlite3').write_bytes(b'synthetic-placeholder')
            with patch.object(core, 'Store', side_effect=self.denied()), \
                    patch.object(core, 'diagnostic') as diagnostic:
                code, _, errors = self.cli('--state-dir', directory, 'hook')
            self.assertEqual(code, 0)
            self.assertIn('stage=open_store', errors)
            diagnostic.assert_called_once_with(Path(directory).resolve(), 'PermissionError')

    def test_missing_hook_state_still_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing'
            self.assertEqual(self.cli('--state-dir', str(missing), 'hook'), (0, '', ''))
            self.assertFalse(missing.exists())

    def test_reviewer_build_changed_keeps_actionable_existing_message(self):
        error = core.NotesError('SENSITIVE_PRIVATE_TEXT')
        error.public_code = 'reviewer_build_changed'
        with patch.object(launcher, 'resolve_state_directory', side_effect=error):
            code, _, errors = self.cli('open', '--no-browser')
        self.assertEqual(code, 1)
        self.assertIn('reviewer_build_changed; run stop, then open.', errors)
        self.assertNotIn('SENSITIVE', errors)

    def test_existing_database_resolution_failure_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'notes.sqlite3'
            database.write_bytes(b'synthetic-placeholder')
            with patch.object(launcher, 'physical_file_path', side_effect=self.denied()), \
                    patch.object(launcher, 'physical_directory_path') as alternate:
                with self.assertRaises(PermissionError):
                    launcher.resolve_state_directory(directory)
            alternate.assert_not_called()
            self.assertEqual(database.read_bytes(), b'synthetic-placeholder')

    @unittest.skipUnless(os.name == 'nt', 'Windows API failure behavior')
    def test_file_handle_resolution_preserves_windows_error(self):
        import ctypes
        kernel = Mock()
        def denied(*_):
            ctypes.set_last_error(5)
            return 0
        kernel.GetFinalPathNameByHandleW.side_effect = denied
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fixture'
            target.write_bytes(b'synthetic-placeholder')
            with patch('ctypes.WinDLL', return_value=kernel):
                with self.assertRaises(PermissionError) as caught:
                    launcher.physical_file_path(target)
        self.assertEqual(caught.exception.errno, errno.EACCES)
        self.assertEqual(caught.exception.winerror, 5)
        self.assertEqual(caught.exception._blackbox_stage, 'physical_file_resolve')

    @unittest.skipUnless(os.name == 'nt', 'Windows API failure behavior')
    def test_directory_open_preserves_windows_error(self):
        import ctypes
        kernel = Mock()
        def denied(*_):
            ctypes.set_last_error(5)
            return ctypes.c_void_p(-1).value
        kernel.CreateFileW.side_effect = denied
        with tempfile.TemporaryDirectory() as directory:
            with patch('ctypes.WinDLL', return_value=kernel):
                with self.assertRaises(PermissionError) as caught:
                    launcher.physical_directory_path(directory)
        self.assertEqual(caught.exception.errno, errno.EACCES)
        self.assertEqual(caught.exception.winerror, 5)
        self.assertEqual(caught.exception._blackbox_stage, 'physical_directory_open')
        kernel.CloseHandle.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows API failure behavior')
    def test_directory_resolution_captures_error_before_handle_close(self):
        import ctypes
        kernel = Mock()
        kernel.CreateFileW.return_value = 123
        def denied(*_):
            ctypes.set_last_error(5)
            return 0
        kernel.GetFinalPathNameByHandleW.side_effect = denied
        kernel.CloseHandle.side_effect = lambda _: ctypes.set_last_error(6)
        with tempfile.TemporaryDirectory() as directory:
            with patch('ctypes.WinDLL', return_value=kernel):
                with self.assertRaises(PermissionError) as caught:
                    launcher.physical_directory_path(directory)
        self.assertEqual(caught.exception.winerror, 5)
        self.assertEqual(caught.exception._blackbox_stage, 'physical_directory_resolve')
        kernel.CloseHandle.assert_called_once_with(123)

    @unittest.skipUnless(os.name == 'nt', 'Windows API failure behavior')
    def test_insufficient_buffer_does_not_report_stale_last_error(self):
        import ctypes
        kernel = Mock()
        kernel.CreateFileW.return_value = 123
        kernel.GetFinalPathNameByHandleW.return_value = 32768
        ctypes.set_last_error(5)
        with tempfile.TemporaryDirectory() as directory:
            with patch('ctypes.WinDLL', return_value=kernel):
                with self.assertRaises(OSError) as caught:
                    launcher.physical_directory_path(directory)
        self.assertEqual(caught.exception.winerror, 122)
        self.assertEqual(caught.exception._blackbox_stage, 'physical_directory_resolve')
        kernel.CloseHandle.assert_called_once_with(123)


if __name__ == '__main__':
    unittest.main()
