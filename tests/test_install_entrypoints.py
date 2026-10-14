"""Exercise the copyable install command without networking or installing anything."""
import ast
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def command_from(path):
    commands = re.findall(r'```powershell\npython -I -c "([^\n]+)"\n```',
                          path.read_text(encoding='utf-8'))
    if len(commands) != 1:
        raise AssertionError('Expected one copyable isolated-Python install command.')
    return commands[0]


class InstallEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.command = command_from(ROOT / 'release/INSTALL.md')

    def test_fixed_version_and_digest_match_bootstrap(self):
        version = json.loads((ROOT / 'plugins/blackbox/.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']
        # Git text checkouts may use CRLF; the published source uses LF.
        source = (ROOT / 'tools/bootstrap_blackbox.py').read_bytes().replace(b'\r\n', b'\n')
        literals = [node.value for node in ast.walk(ast.parse(self.command))
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        self.assertIn('https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/'
                      'v' + version + '/tools/bootstrap_blackbox.py', literals)
        self.assertIn(hashlib.sha256(source).hexdigest(), literals)

    def test_published_script_can_reach_help_through_the_exact_command(self):
        source = (ROOT / 'tools/bootstrap_blackbox.py').read_bytes().replace(b'\r\n', b'\n')
        with patch('urllib.request.urlopen', return_value=io.BytesIO(source)), \
                patch('sys.argv', ['blackbox-install', '--help']), \
                patch('subprocess.run') as process, redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as stopped:
                exec(compile(self.command, 'documented-install-command', 'exec'), {'__name__': '__main__'})
        self.assertEqual(0, stopped.exception.code)
        self.assertIn('usage:', output.getvalue())
        process.assert_not_called()

    def test_changed_download_is_rejected_before_execution(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'raise RuntimeError("executed")')), \
                patch('subprocess.run') as process:
            with self.assertRaisesRegex(AssertionError, 'Installer checksum mismatch'):
                exec(compile(self.command, 'documented-install-command', 'exec'), {'__name__': '__main__'})
        process.assert_not_called()

    def test_readme_and_guide_offer_the_same_command_when_readme_is_present(self):
        # The release's regression bundle deliberately excludes the repository README.
        readme = ROOT / 'README.md'
        if readme.exists():
            self.assertEqual(self.command, command_from(readme))


if __name__ == '__main__':
    unittest.main()
