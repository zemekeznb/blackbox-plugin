"""Harness contract checks; these are not evidence of actual host delivery."""
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    'acceptance_harness', Path(__file__).resolve().parents[1] / 'tools/verify_acceptance.py')
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class AcceptanceHarnessTests(unittest.TestCase):
    def evidence(self, case='type', **overrides):
        script = Path('C:/synthetic project') / (case + '.py')
        command = '& "C:/python.exe" "' + str(script) + '"; exit $LASTEXITCODE'
        output = '' if case == 'silent' else '\n'.join(harness.SIGNATURES[case])
        arguments = dict(case=case, script=script, command=command,
                         host={'status': 'failed', 'exit_code': 7 if case == 'silent' else 1},
                         output=output, metadata={'command': 'powershell -Command ' + command},
                         content={'tool_name': 'Bash', 'tool_input': {'cmd': command}, 'tool_response': output})
        arguments.update(overrides)
        return harness.case_evidence(**arguments)

    def test_controlled_case_requires_independent_host_and_hook_evidence(self):
        for case in ('type', 'module', 'silent'):
            self.assertTrue(all(self.evidence(case).values()))

    def test_startup_failure_cannot_pass_as_fixture_execution(self):
        facts = self.evidence(host={'status': 'failed', 'exit_code': -1},
                              output='Failed to create unified exec process: setup refresh had errors')
        self.assertFalse(facts['host_expected_exit'])
        self.assertFalse(facts['host_expected_output'])

    def test_unrelated_failed_command_cannot_prove_expected_fixture(self):
        self.assertFalse(self.evidence(metadata={'command': 'powershell -Command invalid'})['host_fixture_path'])

    def test_silent_exit_requires_exact_status_and_empty_output(self):
        self.assertFalse(self.evidence('silent', host={'status': 'failed', 'exit_code': 1})['host_expected_exit'])
        self.assertFalse(self.evidence('silent', output='Python failed to launch')['host_expected_output'])

    def test_expected_host_output_does_not_substitute_for_hook_delivery(self):
        self.assertFalse(all(self.evidence(content={}).values()))

    def test_altered_hook_command_does_not_pass_exact_fixture_contract(self):
        content = {'tool_name': 'Bash', 'tool_input': {'cmd': 'another command'},
                   'tool_response': '\n'.join(harness.SIGNATURES['type'])}
        self.assertFalse(self.evidence(content=content)['hook_expected_command'])


if __name__ == '__main__':
    unittest.main()
