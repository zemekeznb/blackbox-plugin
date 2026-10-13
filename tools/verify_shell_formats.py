"""NOTES-050 real command fixtures and bounded, read-only desktop evidence audit."""
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
from verify_bash_coverage import psquote

EXPECTED = {
    'git_no_repository': ('git-diagnostic', 'not a git repository'),
    'git_bad_revision': ('git-diagnostic', 'unknown revision or path'),
    'git_pathspec': ('git-diagnostic', 'did not match any file(s) known to git'),
    'git_bad_option': ('git-diagnostic', 'unknown option'),
    'node_type': ('node-exception', 'TypeError'),
    'node_reference': ('node-exception', 'ReferenceError'),
    'node_syntax': ('node-exception', 'SyntaxError'),
    'node_module': ('node-exception', 'MODULE_NOT_FOUND'),
    'ps_command': ('powershell-diagnostic', 'not recognized'),
    'ps_path': ('powershell-diagnostic', 'Cannot find path'),
    'ps_syntax': ('powershell-diagnostic', 'Missing condition'),
    'ps_normal_view': ('powershell-diagnostic', 'CommandNotFoundException'),
    'git_success': (None, 'On branch'),
    'node_success': (None, 'NOTES050_SUCCESS'),
    'ps_success': (None, 'NOTES050_SUCCESS'),
    'plain_error_words': (None, 'error: this is ordinary output'),
    'read_git_log': (None, 'fatal: not a git repository'),
    'node_warning': (None, 'NOTES050_WARNING'),
}


def valid(case, body, expected=EXPECTED):
    if body.get('tool_name') != 'Bash':
        return False
    output = body.get('tool_response')
    if not isinstance(output, str) or expected[case][1] not in output:
        return False
    hit = anomalies.detect(body)
    return (hit['rule'] if hit else None) == expected[case][0]


def audit(rows, commands, expected=EXPECTED):
    matches = {case: [] for case in commands}
    for row in rows:
        if row['kind'] != 'PostToolUse':
            continue
        body = json.loads(row['content'])
        args = body.get('tool_input')
        command = args.get('command', args.get('cmd')) if isinstance(args, dict) else None
        case = next((case for case in commands if commands[case] == command), None)
        if case is None or body.get('tool_name') != 'Bash':
            continue
        coverage = body.get('_coverage', {})
        matches[case].append({'seq': row['seq'], 'digest': row['digest'], 'turn_id': body.get('turn_id'),
            'valid': valid(case, body, expected), 'complete': coverage.get('capture_boundary_version') == 1
            and not coverage.get('truncated_fields') and not coverage.get('capture_gap_before')})
    return {'passed': all(len(items) == 1 and items[0]['valid'] and items[0]['complete']
                         for items in matches.values()), 'cases': matches}


def prepare(root):
    git, node, pwsh = (shutil.which(name) for name in ('git', 'node', 'pwsh'))
    if not all((git, node, pwsh)):
        raise RuntimeError('existing Git, Node and PowerShell required')
    repo = root / 'repo'
    repo.mkdir()
    empty = root / 'not-repo'
    empty.mkdir()
    subprocess.run([git, 'init', '--quiet', str(repo)], check=True, timeout=10,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    commands = {}
    for case, folder, args in (
        ('git_no_repository', empty, 'status'),
        ('git_bad_revision', repo, 'rev-parse NOTES050_missing_ref'),
        ('git_pathspec', repo, 'ls-files --error-unmatch NOTES050_missing_path'),
        ('git_bad_option', repo, 'status --NOTES050-invalid-option'),
        ('git_success', repo, 'status')):
        commands[case] = '& ' + psquote(git) + ' -C ' + psquote(folder) + ' ' + args + '; exit $LASTEXITCODE'
    for case, content in {
        'node_type': 'null.NOTES050();', 'node_reference': 'NOTES050_missing_function();',
        'node_syntax': 'const = 1;', 'node_module': "require('./NOTES050_missing_module.cjs');",
        'node_success': "console.log('NOTES050_SUCCESS');",
        'node_warning': "process.emitWarning('NOTES050_WARNING');",
    }.items():
        path = root / (case + '.cjs')
        path.write_text(content + '\n', encoding='utf-8')
        commands[case] = '& ' + psquote(node) + ' ' + psquote(path) + '; exit $LASTEXITCODE'
    for case, content in {
        'ps_command': 'NOTES050_AbsentCommand',
        'ps_path': 'Get-Item -LiteralPath ' + psquote(root / 'absent.txt') + ' -ErrorAction Stop',
        'ps_syntax': 'if (',
        'ps_normal_view': "$ErrorView = 'NormalView'\nNOTES050_AbsentCommand",
        'ps_success': "Write-Output 'NOTES050_SUCCESS'",
        'plain_error_words': "Write-Output 'error: this is ordinary output'; Write-Output 'fatal: example'; Write-Output 'TypeError: example'",
        'read_git_log': 'Get-Content -LiteralPath ' + psquote(root / 'git-log.txt'),
    }.items():
        path = root / (case + '.ps1')
        path.write_text("$PSStyle.OutputRendering = 'PlainText'\n[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()\n"
                        + "$ErrorActionPreference = 'Stop'\n" + content + '\n', encoding='utf-8')
        commands[case] = '& ' + psquote(pwsh) + ' -NoLogo -NoProfile -NonInteractive -File ' + psquote(path) + '; exit $LASTEXITCODE'
    (root / 'git-log.txt').write_text('fatal: not a git repository (or any of the parent directories): .git\n', encoding='utf-8')
    controls = {}
    for case, command in commands.items():
        result = subprocess.run([pwsh, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        (root / (case + '-control.txt')).write_text(output, encoding='utf-8')
        controls[case] = {'exit_code': result.returncode,
                         'expected_status': (result.returncode != 0) == bool(EXPECTED[case][0]),
                         'marker': EXPECTED[case][1] in output}
    return {'commands': commands, 'controls': controls, 'executables': {'git': git, 'node': node, 'pwsh': pwsh}}


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
        root = Path(tempfile.mkdtemp(prefix='blackbox-notes050-')).resolve()
        manifest = prepare(root)
        with store.connect() as db:
            before = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        manifest.update(project=project, before_seq=before)
        (root / 'control.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(json.dumps({'run_root': str(root), 'controls': manifest['controls']}, indent=2))
        return
    if args.run_root is None:
        parser.error('--run-root required')
    root = args.run_root.resolve(strict=True)
    manifest = json.loads((root / 'control.json').read_text(encoding='utf-8'))
    if manifest['project'] != project or set(manifest['commands']) != set(EXPECTED):
        raise RuntimeError('unexpected project or case manifest')
    with store.connect() as db:
        through = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        rows = db.execute('''SELECT e.* FROM events e JOIN investigations i ON i.id=e.investigation
            WHERE i.project=? AND e.seq>? AND e.seq<=? ORDER BY e.seq LIMIT 1001''',
            (project, manifest['before_seq'], through)).fetchall()
    if len(rows) > 1000:
        raise RuntimeError('audit window exceeds 1000 events')
    report = audit(rows, manifest['commands'])
    report.update(through=through, before_seq=manifest['before_seq'], rule=anomalies.RULE_VERSION, build=build_id())
    report['controls_valid'] = all(
        info['expected_status'] and info['marker'] and valid(case, {
            'tool_name': 'Bash', 'tool_input': {'command': manifest['commands'][case]},
            'tool_response': (root / (case + '-control.txt')).read_text(encoding='utf-8')})
        for case, info in manifest['controls'].items())
    reader = Reader(args.state_dir)
    try:
        listed, before = set(), None
        while True:
            page = anomalies.listing(store, project_path=project, before=before, through=through)
            listed.update(int(item['id']) for item in page['items'])
            before = page.get('next_before')
            if before is None or int(before) <= manifest['before_seq']:
                break
        for case, items in report['cases'].items():
            for item in items:
                error = bool(EXPECTED[case][0])
                item['listed_correctly'] = (item['seq'] in listed) == error
                report['passed'] &= item['listed_correctly']
                if not error or not item['valid']:
                    continue
                detail = anomalies.detail(store, item['seq'], through)
                response = reader.call('read_view', {'path': f'/api/anomaly-export/{item["seq"]}?through={through}&rule={anomalies.RULE_VERSION}'})
                private = response.get('_meta', {}).get('blackbox', {})
                item['export_matches'] = (private.get('status') == 200
                    and private.get('body') == detail['markdown'] and manifest['commands'][case] in detail['markdown'])
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
