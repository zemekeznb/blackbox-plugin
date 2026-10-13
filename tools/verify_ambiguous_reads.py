"""NOTES-064 ordinary alias reads and unchanged mixed-output boundaries."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from verify_curl_errors import main, anomalies, psquote
from verify_explicit_results import serve, FORMAT
from verify_quality_errors import TRACE

ALIASES = ('cat', 'gc', 'type')
CASES = {**{a+'_'+kind: (None, 0) for a in ALIASES for kind in ('python_log', 'ps_log')},
         **{a+'_missing': ('powershell-diagnostic', 1) for a in ALIASES},
         'relative_missing': ('powershell-diagnostic', 1), 'redefined_alias': ('python-exception', 1),
         'partial': ('curl-transfer-result', 18), 'success': (None, 0), 'raw_partial': (None, 18)}
EXPECTED = {case: (rule, 'Traceback' if case.endswith('python_log') or case=='redefined_alias'
                  else 'Cannot find path' if 'missing' in case or case.endswith('ps_log') else 'HTTP/1.1')
            for case, (rule, _) in CASES.items()}


def prepare(project, store):
    root = Path(tempfile.mkdtemp(prefix='blackbox-notes064-'))
    commands, controls = {}, {}
    (root/'python.log').write_text(TRACE, encoding='utf-8')
    (root/'ps.log').write_text("Get-Content:\nLine |\n   1 | Get-Content old.log\n     | ~~~~~~~~~~~~~~~~~~~\n     | Cannot find path 'C:\\unrelated\\old.log' because it does not exist.\n", encoding='utf-8')
    (root/'fail.py').write_text("raise ValueError('NOTES064 actual failure')\n", encoding='utf-8')
    with (root/'server-output.txt').open('wb') as log:
        proc = subprocess.Popen([sys.executable, __file__, 'serve', '--run-root', str(root)],
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(100):
        if (root/'ready.json').exists(): break
        if proc.poll() is not None: raise RuntimeError('fixture exited')
        time.sleep(.05)
    port = json.loads((root/'ready.json').read_text())['port']
    for case, (rule, code) in CASES.items():
        if case.endswith('_log'):
            alias, kind, _ = case.split('_')
            cmd = alias+' -Raw -LiteralPath '+psquote(root/(kind+'.log'))
        elif case=='relative_missing':
            cmd = 'cat '+psquote(root.name+'-missing.log')
        elif case.endswith('_missing'):
            cmd = case.split('_')[0]+' -LiteralPath '+psquote(root/(case+'.log'))
        elif case=='redefined_alias':
            cmd = 'Microsoft.PowerShell.Utility\\Set-Alias -Name cat -Value '+psquote(sys.executable)+' -Scope Local -Force -ErrorAction Stop; cat '+psquote(root/'fail.py')+'; exit $LASTEXITCODE'
        else:
            cmd = '& '+psquote(shutil.which('curl.exe'))+" -q -sS --stderr - -i --noproxy '*' --max-time 3"
            if case!='raw_partial': cmd += ' --write-out '+psquote(FORMAT)
            cmd += ' '+psquote(f'http://127.0.0.1:{port}/{case}/{root.name}')+'; exit $LASTEXITCODE'
        commands[case] = cmd
        result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-Command', cmd],
                                capture_output=True, timeout=10, cwd=project,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        raw = (result.stdout+result.stderr).decode('utf-8', errors='replace')
        (root/(case+'-control.txt')).write_bytes(raw.encode())
        hit = anomalies.detect({'tool_name':'Bash','tool_input':{'command':cmd},'tool_response':raw})
        controls[case] = {'exit_code':result.returncode, 'expected_rule':rule,
                          'observed_rule':hit['rule'] if hit else None,
                          'valid':result.returncode==code and (hit['rule'] if hit else None)==rule and EXPECTED[case][1] in raw}
    with store.connect() as db: before = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    # Different shell, same actual curl executable; no concatenation or stream replay.
    # The desktop cmd.exe runner escapes quotes; this experiment requires the
    # discovered native executable path to contain no spaces or cmd operators.
    curl = shutil.which('curl.exe')
    if any(x in curl for x in ' &|<>^"'): raise RuntimeError('unsafe unquoted cmd executable')
    prefix = curl+' -q -sS -i --noproxy * --max-time 3 '+f'http://127.0.0.1:{port}/raw_partial/{root.name}-cmd'
    manifest = {'project':project,'before_seq':before,'commands':commands,'controls':controls,
                'server_pid':proc.pid,'stop_url':f'http://127.0.0.1:{port}/stop',
                'prefix_exploration':{'command':prefix,'shell':'C:/Windows/System32/cmd.exe'}}
    (root/'control.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'run_root':str(root),'controls':controls}))
    return all(c['valid'] for c in controls.values())


if __name__=='__main__': main(EXPECTED, prepare, serve)
