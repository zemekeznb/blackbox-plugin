"""Prepare independent controls; audit real desktop Hook results without injection."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store
from tas_notes_launcher import build_id
import tas_notes_anomalies as anomalies
from tas_notes_mcp import Reader
from bash_error_cases import EXPECTED

FIRST = ('file_missing', 'json_invalid', 'subprocess_timeout', 'file_locked', 'powershell_start', 'success')


def psquote(value):
    return "'" + str(value).replace("'", "''") + "'"


def expected_output(case, output):
    if not isinstance(output, str):
        return False
    hit = anomalies.detect({'tool_name': 'Bash', 'tool_response': output})
    if case == 'powershell_start':
        return bool(hit and hit['rule'] == 'powershell-process-start')
    expected = EXPECTED[case]
    if expected:
        matched = bool(hit and hit['title'] == '工具返回 ' + expected)
        if case.startswith('http'):
            matched &= ('HTTP Error ' + case[4:] + ':') in output
        if case == 'connect_timeout':
            matched &= 'client.connect(server.getsockname())' in output
        return matched
    marker = {'success': 'NOTES049_SUCCESS', 'http200': 'NOTES049_HTTP_RESPONSE',
              'http200_error_text': 'error: synthetic application message'}[case]
    return hit is None and marker in output


def assess(rows, commands, selected):
    matches = {case: [] for case in selected}
    for row in rows:
        if row['kind'] != 'PostToolUse':
            continue
        body = json.loads(row['content'])
        args = body.get('tool_input')
        command = args.get('command', args.get('cmd')) if isinstance(args, dict) else None
        case = next((case for case in selected if commands[case] == command), None)
        if case is None or body.get('tool_name') != 'Bash':
            continue
        coverage = body.get('_coverage', {})
        matches[case].append({'seq': row['seq'], 'digest': row['digest'],
            'turn_id': body.get('turn_id'), 'expected_output': expected_output(case, body.get('tool_response')),
            'boundary': coverage.get('capture_boundary_version') == 1,
            'complete': not coverage.get('truncated_fields') and not coverage.get('capture_gap_before')})
    passed = all(len(items) == 1 and items[0]['expected_output'] and items[0]['boundary']
                 and items[0]['complete'] for items in matches.values())
    return {'passed': passed, 'cases': matches}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'check'))
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run-root', type=Path)
    parser.add_argument('--group', choices=('first', 'network', 'all'), default='all')
    args = parser.parse_args()
    store = Store(args.state_dir, read_only=True)
    project = str(args.project.resolve(strict=True))
    if not any(p['path'] == project and p['enabled'] for p in store.status()['projects']):
        raise RuntimeError('selected project must already be enabled')
    if args.phase == 'prepare':
        root = Path(tempfile.mkdtemp(prefix='blackbox-notes049-')).resolve()
        shutil.copyfile(Path(__file__).with_name('bash_error_cases.py'), root / 'cases.py')
        commands = {}
        for case in EXPECTED:
            script = root / (case + '.py')
            script.write_text("import sys\nsys.stderr.reconfigure(encoding='utf-8')\nfrom pathlib import Path\nfrom cases import run\n"
                              + f"run({case!r}, Path(__file__).parent)\n", encoding='utf-8')
            commands[case] = '& ' + psquote(sys.executable) + ' ' + psquote(script) + '; exit $LASTEXITCODE'
        invalid = root / 'intentionally-invalid.exe'
        invalid.write_bytes(b'NOTES049 deliberately invalid executable, not program code.\r\n')
        commands['powershell_start'] = "$PSStyle.OutputRendering = 'PlainText'\n& " + psquote(invalid) + '\nif ($?) { exit 0 } else { exit 1 }'
        controls = {}
        for case, command in commands.items():
            result = subprocess.run([shutil.which('pwsh') or 'pwsh', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command],
                                    capture_output=True, timeout=25, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
            (root / (case + '-control.txt')).write_text(output, encoding='utf-8')
            exit_code = 0 if case != 'powershell_start' and EXPECTED[case] is None else 1
            controls[case] = {'exit_code': result.returncode,
                              'valid': result.returncode == exit_code and expected_output(case, output)}
        with store.connect() as db:
            before = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        manifest = {'project': project, 'before_seq': before, 'build': build_id(),
                    'commands': commands, 'controls': controls}
        (root / 'control.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(json.dumps({'run_root': str(root), 'before_seq': before, 'controls': controls}, indent=2))
        return
    if args.run_root is None:
        parser.error('--run-root required')
    root = args.run_root.resolve(strict=True)
    manifest = json.loads((root / 'control.json').read_text(encoding='utf-8'))
    if manifest['project'] != project or manifest['build'] != build_id():
        raise RuntimeError('project or source build changed')
    commands = manifest['commands']
    selected = [case for case in commands if args.group == 'all' or (case in FIRST) == (args.group == 'first')]
    with store.connect() as db:
        through = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        rows = db.execute('''SELECT e.* FROM events e JOIN investigations i ON i.id=e.investigation
            WHERE i.project=? AND e.seq>? AND e.seq<=? ORDER BY e.seq LIMIT 1001''',
            (project, manifest['before_seq'], through)).fetchall()
    if len(rows) > 1000:
        raise RuntimeError('audit window exceeds limit')
    report = assess(rows, commands, selected)
    report.update(before_seq=manifest['before_seq'], through=through, rule=anomalies.RULE_VERSION,
                  controls_valid=all(manifest['controls'][case]['valid'] for case in selected))
    reader = Reader(args.state_dir)
    try:
        listed = set()
        before = None
        while True:
            page = anomalies.listing(store, project_path=project, before=before, through=through)
            listed.update(int(item['id']) for item in page['items'])
            before = page.get('next_before')
            if before is None or int(before) <= manifest['before_seq']:
                break
        for case, items in report['cases'].items():
            for item in items:
                error = case == 'powershell_start' or EXPECTED[case] is not None
                item['list_matches'] = (item['seq'] in listed) == error
                if not error or not item['expected_output']:
                    continue
                detail = anomalies.detail(store, item['seq'], through)
                path = f'/api/anomaly-export/{item["seq"]}?through={through}&rule={anomalies.RULE_VERSION}'
                response = reader.call('read_view', {'path': path})
                private = response.get('_meta', {}).get('blackbox', {})
                item['reader_export_matches'] = private.get('status') == 200 and private.get('body') == detail['markdown']
                raw = detail['markdown'].encode('utf-8')
                item['export_sha256'] = hashlib.sha256(raw).hexdigest()
                (root / (case + '-expected.md')).write_bytes(raw)
                report['passed'] &= item['reader_export_matches']
                report['passed'] &= commands[case] in detail['markdown']
                report['passed'] &= item['list_matches']
    finally:
        reader.names.close()
        reader.projects.close()
    report['passed'] &= report['controls_valid']
    report['passed'] &= all(item.get('list_matches', False) for items in report['cases'].values() for item in items)
    (root / ('report-' + args.group + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
