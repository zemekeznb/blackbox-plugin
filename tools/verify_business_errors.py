"""NOTES-051 local HTTP fixtures and bounded read-only real Hook acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store
from tas_notes_launcher import build_id
import tas_notes_anomalies as anomalies
from tas_notes_mcp import Reader
from verify_bash_coverage import psquote
from verify_shell_formats import audit, valid
import business_error_cases
import business_protocol_cases


def fixtures(suite):
    return business_protocol_cases if suite == 'notes052' else business_error_cases


def prepare(root, suite='notes051'):
    cases = fixtures(suite)
    curl, pwsh = shutil.which('curl.exe'), shutil.which('pwsh')
    if not curl or not pwsh:
        raise RuntimeError('existing curl.exe and PowerShell required')
    run_id = uuid4().hex
    (root / 'server-config.json').write_text(json.dumps({'run_id': run_id, 'suite': suite}), encoding='utf-8')
    with (root / 'server-output.txt').open('wb') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('business_error_cases.py')), '--run-root', str(root)],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(100):
        if (root / 'server-ready.json').exists():
            break
        if process.poll() is not None:
            raise RuntimeError('test service exited')
        time.sleep(.05)
    port = json.loads((root / 'server-ready.json').read_text(encoding='utf-8'))['port']
    commands, controls = {}, {}
    for case in cases.CASES:
        if case == 'read_saved_response':
            command = 'Get-Content -Raw -LiteralPath ' + psquote(root / 'rpc_inventory-control.txt')
        else:
            command = ('& ' + psquote(curl) + ' -q -sS' + ('' if case == 'rpc_body_only' else ' -i')
                + " --max-time 5 --noproxy '*' -H 'Content-Type: application/json'"
                + (cases.headers(case) if suite == 'notes052' else '') + ' --data-raw '
                + psquote(json.dumps(cases.request(case), separators=(',', ':')))
                + ' ' + psquote('http://127.0.0.1:%d/%s/%s' % (port, run_id, case)) + '; exit $LASTEXITCODE')
        commands[case] = command
        result = subprocess.run([pwsh, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        (root / (case + '-control.txt')).write_bytes(output.encode('utf-8'))
        controls[case] = {'exit_code': result.returncode, 'expected_rule': cases.CASES[case][0],
            'valid': result.returncode == 0 and valid(case, {'tool_name': 'Bash', 'tool_input': {'command': command}, 'tool_response': output}, cases.CASES)}
    return {'suite': suite, 'commands': commands, 'controls': controls, 'server_pid': process.pid,
            'stop_url': 'http://127.0.0.1:%d/%s/stop' % (port, run_id),
            'executables': {'curl': curl, 'pwsh': pwsh}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'check', 'stop'))
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run-root', type=Path)
    parser.add_argument('--suite', choices=('notes051', 'notes052'), default='notes051')
    args = parser.parse_args()
    project = str(args.project.resolve(strict=True))
    store = Store(args.state_dir, read_only=True)
    if not any(p['path'] == project and p['enabled'] for p in store.status()['projects']):
        raise RuntimeError('selected project must already be enabled')
    if args.phase == 'prepare':
        root = Path(tempfile.mkdtemp(prefix='blackbox-' + args.suite + '-')).resolve()
        manifest = prepare(root, args.suite)
        with store.connect() as db:
            before = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        manifest.update(project=project, before_seq=before)
        (root / 'control.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(json.dumps({'run_root': str(root), 'controls': manifest['controls']}, indent=2))
        if not all(item['valid'] for item in manifest['controls'].values()):
            raise SystemExit(1)
        return
    if args.run_root is None:
        parser.error('--run-root required')
    root = args.run_root.resolve(strict=True)
    manifest = json.loads((root / 'control.json').read_text(encoding='utf-8'))
    suite = manifest.get('suite', 'notes051')
    if suite not in ('notes051', 'notes052'):
        raise RuntimeError('unexpected fixture suite')
    cases = fixtures(suite)
    if manifest['project'] != project or set(manifest['commands']) != set(cases.CASES):
        raise RuntimeError('unexpected project or case manifest')
    if args.phase == 'stop':
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(manifest['stop_url'], data=b'', method='POST'), timeout=5) as response:
            print(json.dumps({'stopped': response.status == 204}))
        return
    with store.connect() as db:
        through = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        rows = db.execute('''SELECT e.* FROM events e JOIN investigations i ON i.id=e.investigation
            WHERE i.project=? AND e.seq>? AND e.seq<=? ORDER BY e.seq LIMIT 1001''',
            (project, manifest['before_seq'], through)).fetchall()
    if len(rows) > 1000:
        raise RuntimeError('audit window exceeds 1000 events')
    report = audit(rows, manifest['commands'], cases.CASES)
    report.update(through=through, before_seq=manifest['before_seq'], rule=anomalies.RULE_VERSION, build=build_id())
    report['controls_valid'] = all(info['valid'] and info['exit_code'] == 0 for info in manifest['controls'].values())
    listed, before = set(), None
    while True:
        page = anomalies.listing(store, project_path=project, before=before, through=through)
        listed.update(int(item['id']) for item in page['items'])
        before = page.get('next_before')
        if before is None or int(before) <= manifest['before_seq']:
            break
    reader = Reader(args.state_dir)
    try:
        for case, items in report['cases'].items():
            for item in items:
                error = bool(cases.CASES[case][0])
                item['listed_correctly'] = (item['seq'] in listed) == error
                report['passed'] &= item['listed_correctly']
                if not error or not item['valid']:
                    continue
                detail = anomalies.detail(store, item['seq'], through)
                item['title'] = detail['title']
                response = reader.call('read_view', {'path': f'/api/anomaly-export/{item["seq"]}?through={through}&rule={anomalies.RULE_VERSION}'})['_meta']['blackbox']
                item['export_matches'] = response.get('status') == 200 and response.get('body') == detail['markdown'] and manifest['commands'][case] in detail['markdown']
                raw = detail['markdown'].encode('utf-8')
                item['export_sha256'] = hashlib.sha256(raw).hexdigest()
                (root / (case + '-expected.md')).write_bytes(raw)
                report['passed'] &= item['export_matches']
    finally:
        reader.names.close()
        reader.projects.close()
    report['passed'] &= report['controls_valid']
    (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
