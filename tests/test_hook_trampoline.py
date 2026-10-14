"""The temporary diagnostic wrapper must preserve actual tool evidence."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HookTrampolineTests(unittest.TestCase):
    def test_forwards_bytes_and_failure_exit_without_logging_body(self):
        wrapper = Path(__file__).resolve().parents[1] / 'tools/trace_hook_entry.py'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'target.py'
            target.write_text('import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\nsys.exit(7)\n')
            env = dict(os.environ, BLACKBOX_STATE_DIR=temp, LOCALAPPDATA=temp)
            for raw in (b'{"secret":"PRIVATE_SENTINEL"}\r\n', b'\xffinvalid-json\x00'):
                result = subprocess.run([sys.executable, str(wrapper), str(target)],
                                        input=raw, capture_output=True, env=env, timeout=12)
                self.assertEqual(result.returncode, 7)
                self.assertEqual(result.stdout, raw)
                self.assertEqual(result.stderr, b'')
            self.assertFalse((root / 'capture-probe.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
