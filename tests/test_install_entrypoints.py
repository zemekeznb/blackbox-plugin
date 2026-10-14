"""Check the documented Codex install handoff without running an installer."""
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def install_prompt_from(path):
    blocks = re.findall(r'```text\n(.*?)\n```',
                        path.read_text(encoding='utf-8'), re.DOTALL)
    prompts = [block for block in blocks if 'INSTALL-CODEX.md' in block]
    if len(prompts) != 1:
        raise AssertionError('Expected one Codex install prompt linking to INSTALL-CODEX.md.')
    return prompts[0]


class InstallEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.prompt = install_prompt_from(ROOT / 'release/INSTALL.md')
        self.version = json.loads(
            (ROOT / 'plugins/blackbox/.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']

    def test_install_prompt_targets_the_fixed_plugin_version(self):
        links = re.findall(r'https://[^\s`<>]*?/release/INSTALL-CODEX\.md', self.prompt)
        self.assertEqual([
            'https://github.com/zemekeznb/blackbox-plugin/blob/v'
            + self.version + '/release/INSTALL-CODEX.md'
        ], links)

    def test_codex_instructions_target_the_same_bootstrap_version(self):
        instructions = (ROOT / 'release/INSTALL-CODEX.md').read_text(encoding='utf-8')
        links = re.findall(
            r'https://raw\.githubusercontent\.com/[^\s`<>]+/tools/bootstrap_blackbox\.py',
            instructions)
        self.assertEqual({
            'https://raw.githubusercontent.com/zemekeznb/blackbox-plugin/v'
            + self.version + '/tools/bootstrap_blackbox.py'
        }, set(links))

    def test_readme_and_guide_offer_the_same_prompt_when_readme_is_present(self):
        # The release's regression bundle deliberately excludes the repository README.
        readme = ROOT / 'README.md'
        if readme.exists():
            self.assertEqual(self.prompt, install_prompt_from(readme))


if __name__ == '__main__':
    unittest.main()
