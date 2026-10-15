"""Failures before core imports remain diagnostic without exposing local paths."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ENTRY = Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts/blackbox.py'

INJECT_ERROR = r'''
import builtins, pathlib, runpy, sys
entry, kind, target = sys.argv[1:4]
original_import = builtins.__import__
original_resolve = pathlib.Path.resolve
error_class = {'permission': PermissionError, 'import': ImportError,
               'missing': ModuleNotFoundError, 'bad_codes': PermissionError}[kind]
error = error_class('PRIVATE-PATH-AND-ERROR-SENTINEL')
if kind in ('permission', 'bad_codes'):
    error.errno = 13 if kind == 'permission' else 'PRIVATE-ERRNO'
    error.winerror = 5 if kind == 'permission' else True
def patched_import(name, *args, **kwargs):
    if target == 'import' and name == 'blackbox_core':
        raise error
    return original_import(name, *args, **kwargs)
def patched_resolve(path, *args, **kwargs):
    if target == 'resolve' and str(path) == entry:
        raise error
    return original_resolve(path, *args, **kwargs)
builtins.__import__ = patched_import
pathlib.Path.resolve = patched_resolve
sys.argv = [entry, 'status']
runpy.run_path(entry, run_name='__main__')
'''


class EntrypointErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='blackbox-entry-errors-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = dict(os.environ, CODEX_HOME=str(self.root / 'host'),
                                USERPROFILE=str(self.root / 'profile'),
                                LOCALAPPDATA=str(self.root / 'appdata'))
        for name in ('BLACKBOX_STATE_DIR', 'TAS_NOTES_STATE_DIR'):
            self.environment.pop(name, None)

    def inject(self, kind, target='import'):
        result = subprocess.run([sys.executable, '-I', '-B', '-c', INJECT_ERROR,
                                 str(ENTRY), kind, target], env=self.environment,
                                cwd=self.root, capture_output=True, text=True,
                                encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('PRIVATE', result.stderr)
        self.assertNotIn(str(ENTRY), result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('stage=import_installed_modules', result.stderr)
        self.assertFalse((self.root / 'host').exists())
        self.assertFalse((self.root / 'appdata').exists())
        return result.stderr

    def test_module_permission_failure_has_stage_and_numeric_system_codes(self):
        error = self.inject('permission')
        self.assertIn('PermissionError', error)
        self.assertIn('errno=13', error)
        self.assertIn('winerror=5', error)

    def test_entry_directory_resolution_failure_is_also_caught(self):
        error = self.inject('permission', 'resolve')
        self.assertIn('PermissionError', error)
        self.assertIn('winerror=5', error)

    def test_import_failures_do_not_print_module_or_dependency_names(self):
        for kind, expected in (('import', 'ImportError'), ('missing', 'ModuleNotFoundError')):
            with self.subTest(kind=kind):
                error = self.inject(kind)
                self.assertIn(expected, error)
                self.assertNotIn('errno=', error)

    def test_non_numeric_or_boolean_error_fields_are_omitted(self):
        error = self.inject('bad_codes')
        self.assertNotIn('errno=', error)
        self.assertNotIn('winerror=', error)

    def test_importable_main_forwards_arguments_and_return_code(self):
        code = (
            'import json,runpy,sys,types; '
            'sys.modules["blackbox_core"]=types.SimpleNamespace(main=lambda argv:{"args":argv,"code":7}); '
            'module=runpy.run_path(sys.argv[1],run_name="blackbox_entry_test"); '
            'print(json.dumps(module["main"](["status"])))'
        )
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ENTRY)],
                                env=self.environment, cwd=self.root, capture_output=True,
                                text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout), {'args': ['status'], 'code': 7})


if __name__ == '__main__':
    unittest.main()
