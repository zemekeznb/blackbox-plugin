import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit, parse_qs

ENTRY = Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts/tas_notes.py'

class LauncherTests(unittest.TestCase):
    def test_reviewer_survives_launch_command_exit(self):
        with tempfile.TemporaryDirectory(prefix='notes-launch-test-') as root:
            launch = subprocess.run([sys.executable, str(ENTRY), '--state-dir', root,
                                     'serve', '--background'], capture_output=True, timeout=15)
            self.assertEqual(launch.returncode, 0, launch.stderr)
            value = json.loads(launch.stdout)
            url = urlsplit(value['url'])
            try:
                self.assertEqual(url.hostname, '127.0.0.1')
                token = parse_qs(url.fragment)['token'][0]
                connection = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
                try:
                    connection.request('GET', '/api/records', headers={'Authorization':'Bearer '+token})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read()), [])
                finally:
                    connection.close()
                self.assertEqual(list(Path(root).glob('.review-ready-*')), [])
            finally:
                os.kill(value['pid'], signal.SIGTERM)

if __name__ == '__main__': unittest.main()
