import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

ENTRY = Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts/tas_notes.py'
sys.path.insert(0,str(ENTRY.parent))
from tas_notes_launcher import healthy

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

    def test_invalid_registry_never_contacts_remote(self):
        with patch('tas_notes_launcher.http.client.HTTPConnection') as connection:
            for value in [None,{}, {'pid':1,'url':'https://example.org/#token=secret'},
                          {'pid':1,'url':'http://localhost:80/#token='+'a'*43},
                          {'pid':1,'url':'http://127.0.0.1:99999/#token='+'a'*43}]:
                self.assertFalse(healthy(value,'build'))
            connection.assert_not_called()

    def test_concurrent_open_reuses_and_stale_registry_recovers(self):
        with tempfile.TemporaryDirectory(prefix='notes-open-test-') as root:
            registry=Path(root)/'review-instance.json'
            registry.write_text('{broken')
            def launch(_):
                result=subprocess.run([sys.executable,str(ENTRY),'--state-dir',root,
                                       'open','--no-browser'],capture_output=True,timeout=25)
                self.assertEqual(result.returncode,0,result.stderr)
                return json.loads(result.stdout)
            with ThreadPoolExecutor(max_workers=3) as pool:
                values=list(pool.map(launch,range(3)))
            first=values[0]
            try:
                self.assertEqual(len({v['url'] for v in values}),1)
                self.assertEqual(sum(not v['reused'] for v in values),1)
            finally:
                os.kill(first['pid'],signal.SIGTERM)
            # An invalid registry is not used as a kill instruction or browser URL.
            registry.write_text(json.dumps({'pid':os.getpid(),'url':'http://example.org/'}))
            second=launch(0)
            try:
                self.assertFalse(second['reused'])
                self.assertTrue(launch(0)['reused'])
            finally:
                os.kill(second['pid'],signal.SIGTERM)

if __name__ == '__main__': unittest.main()
