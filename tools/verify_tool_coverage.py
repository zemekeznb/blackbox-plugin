"""Prepare controlled local network failures and inspect actual Hook sources.

No Hook injection, transcript reads, project enabling or model calls. Runtime
fixtures and complete evidence live in a system temporary directory, not Git.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store
import tas_notes_anomalies as anomalies

FIXTURES = {
    'connection': '''import socket
with socket.socket() as reserved:
    reserved.bind(('127.0.0.1', 0))
    with socket.socket() as client:
        client.settimeout(3)
        client.connect(reserved.getsockname())
''',
    'http429': '''from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.request
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(429)
        self.end_headers()
        self.wfile.write(b'Synthetic NOTES-046 local rate-limit response')
    def log_message(self, *args):
        pass
server = HTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.open('http://127.0.0.1:%d/' % server.server_port, timeout=3)
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
''',
}
EXPECTED = {'connection': 'ConnectionRefusedError', 'http429': 'urllib.error.HTTPError'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'check'))
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run-root', type=Path)
    args = parser.parse_args()
    project = str(args.project.resolve(strict=True))
    store = Store(args.state_dir, read_only=True)
    if not any(p['path'] == project and p['enabled'] for p in store.status()['projects']):
        raise RuntimeError('selected project must already be enabled')
    if args.phase == 'prepare':
        root = Path(tempfile.mkdtemp(prefix='blackbox-notes046-'))
        with store.connect() as db:
            before = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        manifest = {'project': project, 'before_seq': before, 'commands': {}}
        for case, source in FIXTURES.items():
            script = root / (case + '.py')
            script.write_text("import sys\nsys.stderr.reconfigure(encoding='utf-8')\n" + source, encoding='utf-8')
            manifest['commands'][case] = '& "' + sys.executable + '" "' + str(script) + '"; exit $LASTEXITCODE'
        (root / 'control.json').write_text(json.dumps(manifest), encoding='utf-8')
        print(json.dumps({'run_root': str(root), **manifest}, ensure_ascii=True, indent=2))
        return
    if args.run_root is None:
        parser.error('--run-root required')
    root = args.run_root.resolve(strict=True)
    manifest = json.loads((root / 'control.json').read_text(encoding='utf-8'))
    if manifest['project'] != project:
        raise RuntimeError('project changed')
    with store.connect() as db:
        through = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        rows = db.execute('''SELECT e.* FROM events e JOIN investigations i ON i.id=e.investigation
            WHERE i.project=? AND e.seq>? AND e.seq<=? AND e.kind='PostToolUse'
            ORDER BY e.seq LIMIT 5001''', (project, manifest['before_seq'], through)).fetchall()
    if len(rows) > 5000:
        raise RuntimeError('inspection window too large')
    cases = {case: [] for case in manifest['commands']}
    for row in rows:
        body = json.loads(row['content'])
        inp = body.get('tool_input')
        command = inp.get('command', inp.get('cmd')) if isinstance(inp, dict) else None
        case = next((key for key, value in manifest['commands'].items() if value == command), None)
        if body.get('tool_name') != 'Bash' or case is None:
            continue
        hit = anomalies.detect(body)
        valid = hit is not None and hit['title'] == '工具返回 ' + EXPECTED[case]
        record = {'seq': row['seq'], 'digest': row['digest'], 'expected_format': valid,
                  'response_type': type(body.get('tool_response')).__name__}
        if valid:
            detail = anomalies.detail(store, row['seq'], through)
            raw = detail['markdown'].encode('utf-8')
            (root / (case + '-expected.md')).write_bytes(raw)
            record['export_sha256'] = hashlib.sha256(raw).hexdigest()
        cases[case].append(record)
    report = {'surface': 'real desktop Hook sources; no synthetic injection',
              'before_seq': manifest['before_seq'], 'through': through,
              'rule': anomalies.RULE_VERSION, 'cases': cases,
              'passed': all(len(v) == 1 and v[0]['expected_format'] for v in cases.values()),
              'limits': ['Local controlled failures, not external DNS or service outage evidence.',
                         'Expected Markdown generation is not a browser download test.']}
    (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
