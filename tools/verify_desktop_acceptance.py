"""Prepare/check real desktop tool evidence in an explicitly selected live project.

Does not inject hooks, call a model, change capture authorization, or read host
transcripts. Only exact synthetic fixture commands are included in the report.
Runtime fixtures and full evidence remain outside the repository.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'check', 'freeze'))
    parser.add_argument('--plugin-root', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run-root', type=Path)
    parser.add_argument('--through', type=int)
    args = parser.parse_args()
    sys.path.insert(0, str(args.plugin_root.resolve(strict=True) / 'scripts'))
    from tas_notes_core import Store
    from tas_notes_launcher import build_id, release_version
    import tas_notes_anomalies as anomalies
    from verify_acceptance import output_matches

    store = Store(args.state_dir)
    project = str(args.project.resolve(strict=True))
    if not any(p['path'] == project and p['enabled'] for p in store.status()['projects']):
        raise RuntimeError('the explicitly selected project must already be enabled')
    if args.phase == 'prepare':
        root = Path(tempfile.mkdtemp(prefix='blackbox-desktop-acceptance-')).resolve()
        fixtures = {
            'type': "import sys\nprint('BLACKBOX_ACCEPTANCE_TYPE', file=sys.stderr, flush=True)\nraise TypeError('controlled acceptance failure')\n",
            'module': "import sys\nprint('BLACKBOX_ACCEPTANCE_MODULE', file=sys.stderr, flush=True)\nimport blackbox_acceptance_nonexistent_module\n",
            'silent': 'raise SystemExit(7)\n',
        }
        report = {'project': project, 'version': release_version(args.plugin_root),
                  'build': build_id(args.plugin_root), 'commands': {}, 'independent': {}}
        with store.connect() as db:
            report['before_seq'] = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        for case, source in fixtures.items():
            script = root / (case + '.py')
            script.write_text(source, encoding='utf-8')
            result = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=10)
            report['independent'][case] = {
                'exit_code': result.returncode,
                'expected_output': output_matches(case, (result.stdout + result.stderr).decode('utf-8'))}
            report['commands'][case] = '& "' + sys.executable + '" "' + str(script) + '"; exit $LASTEXITCODE'
        (root / 'control.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'run_root': str(root), **report}, indent=2))
        return
    if args.run_root is None:
        parser.error('--run-root is required for check/freeze')
    root = args.run_root.resolve(strict=True)
    control = json.loads((root / 'control.json').read_text(encoding='utf-8'))
    if control['project'] != project or control['build'] != build_id(args.plugin_root):
        raise RuntimeError('project/build changed from prepared controls')
    with store.connect() as db:
        rows = db.execute('''SELECT e.*,i.project,i.session FROM events e
            JOIN investigations i ON i.id=e.investigation
            WHERE i.project=? AND e.seq>? ORDER BY e.seq''', (project, control['before_seq'])).fetchall()
    matches = []
    for row in rows:
        if row['kind'] != 'PostToolUse':
            continue
        body = json.loads(row['content'])
        inp = body.get('tool_input')
        cmd = inp.get('command', inp.get('cmd')) if isinstance(inp, dict) else None
        case = next((name for name, expected in control['commands'].items() if cmd == expected), None)
        if case and body.get('tool_name') == 'Bash':
            matches.append((case, dict(row), body))
    types = [(row, body) for case, row, body in matches if case == 'type']
    checks = {
        'independent_fixtures_valid': all(r['expected_output'] and r['exit_code'] == (7 if case == 'silent' else 1)
                                          for case, r in control['independent'].items()),
        'exact_five_synthetic_calls': [c for c, _, _ in matches] == ['type','type','type','module','silent'],
        'hook_outputs_match_controls': len(matches) == 5 and all(output_matches(case, body.get('tool_response')) for case, _, body in matches),
        'capture_boundary_contract': len(matches) == 5 and all(body.get('_coverage', {}).get('capture_boundary_version') == 1 for _, _, body in matches),
        'four_expected_detections': len(matches) == 5 and [bool(anomalies.detect(body)) for _, _, body in matches] == [True,True,True,True,False],
    }
    if len(types) == 3 and all(anomalies.detect(body) for _, body in types):
        keys = [anomalies.target_key(body) for _, body in types]
        turns = [body.get('turn_id') for _, body in types]
        checks['identical_complete_type_inputs'] = bool(keys[0]) and len(set(keys)) == 1
        checks['same_turn_and_changed_turn_observed'] = bool(turns[0]) and turns[0] == turns[1] and bool(turns[2]) and turns[2] != turns[1]
        details = [anomalies.detail(store, row['seq']) for row, _ in types]
        checks['same_turn_repeat_linked'] = [r['seq'] for r in details[0]['events']] == [types[0][0]['seq'],types[1][0]['seq']]
        checks['cross_turn_not_linked'] = [r['seq'] for r in details[1]['events']] == [types[1][0]['seq']]
    else:
        checks['three_type_errors_detected'] = False
    report = {'surface':'current Codex desktop real tool hooks', 'version':control['version'],
              'build':control['build'], 'checks':checks,
              'events':[{'case':case,'seq':row['seq'],'turn_id':body.get('turn_id'),
                         'digest':row['digest'],'captured':row['captured'],
                         'signal':(anomalies.detect(body) or {}).get('title')}
                        for case,row,body in matches],
              'limitations':['Desktop session-tree evidence, not recovery of the independent CLI sandbox.',
                             'Raw events remain in the explicitly enabled current project.',
                             'Hook output alone is not independent evidence of a silent exit code.']}
    if args.phase == 'freeze':
        if not all(checks.values()) or args.through is None:
            raise RuntimeError('successful checks and explicit browser snapshot cutoff required')
        detail = anomalies.detail(store, types[0][0]['seq'], args.through)
        raw = detail['markdown'].encode('utf-8')
        (root / 'expected-export.md').write_bytes(raw)
        report['export'] = {'id':detail['id'], 'through':detail['source_seq'], 'rule':detail['rule_version'],
                            'sha256':hashlib.sha256(raw).hexdigest(), 'bytes':len(raw)}
    (root / ('export-report.json' if args.phase == 'freeze' else 'report.json')).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
