"""NOTES-061 real file reads; temp files only, no fixture service required."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from verify_curl_errors import main, anomalies, psquote
from verify_quality_errors import TRACE, PS, GIT, NODE

CASES = {'raw': (None, 0), 'lines': (None, 0), 'module': (None, 0), 'tail': (None, 0),
         'utf8': (None, 0), 'positional': (None, 0), 'git_log': (None, 0), 'node_log': (None, 0),
         'plain': (None, 0), 'ps_log': (None, 0),
         'alias': ('python-exception', 0), 'missing': ('powershell-diagnostic', 1),
         'missing_module': ('powershell-diagnostic', 1), 'python_error': ('python-exception', 1)}
EXPECTED = {case:(rule,'Cannot find path' if case in ('ps_log','missing','missing_module')
                 else 'fatal:' if case=='git_log' else 'NOTES060' if case in ('node_log','alias','raw','lines','module','tail','utf8','positional') else 'NOTES061')
            for case,(rule,_) in CASES.items()}


def prepare(project, store):
    root=Path(tempfile.mkdtemp(prefix='blackbox-notes061-'));commands={};controls={}
    for name,raw in {'trace.log':TRACE,'ps.log':PS,'git.log':GIT,'node.log':NODE,'plain.log':'NOTES061 warning\nOK\n'}.items():
        (root/name).write_text(raw,encoding='utf-8')
    (root/'fail.py').write_text("raise ValueError('NOTES061 real error')\n",encoding='utf-8')
    trace=psquote(root/'trace.log');module='Microsoft.PowerShell.Management\\Get-Content'
    commands={'raw':'Get-Content -Raw -LiteralPath '+trace,
              'lines':'Get-Content -LiteralPath '+trace,
              'module':module+' -Raw -LiteralPath '+trace,
              'tail':'Get-Content -Tail 20 -LiteralPath '+trace,
              'utf8':'Get-Content -Encoding utf8 -Raw -LiteralPath '+trace,
              'positional':'Get-Content '+trace,'alias':'cat '+trace,
              'missing':'Get-Content -Raw -LiteralPath '+psquote(root/'missing.log'),
              'missing_module':module+' -LiteralPath '+psquote(root/'missing-module.log'),
              'python_error':'& '+psquote(sys.executable)+' '+psquote(root/'fail.py')+'; exit $LASTEXITCODE'}
    for case,name in [('git_log','git.log'),('node_log','node.log'),('ps_log','ps.log'),('plain','plain.log')]:
        commands[case]='Get-Content -Raw -LiteralPath '+psquote(root/name)
    for case,(rule,code) in CASES.items():
        cmd=commands[case]
        result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-NonInteractive','-Command',cmd],
            capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raw=(result.stdout+result.stderr).decode('utf-8',errors='replace')
        (root/(case+'-control.txt')).write_bytes(raw.encode())
        hit=anomalies.detect({'tool_name':'Bash','tool_input':{'command':cmd},'tool_response':raw})
        controls[case]={'exit_code':result.returncode,'expected_rule':rule,'observed_rule':hit['rule'] if hit else None,
                        'valid':result.returncode==code and (hit['rule'] if hit else None)==rule and EXPECTED[case][1] in raw}
    with store.connect() as db:before=db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    m={'project':project,'before_seq':before,'commands':commands,'controls':controls}
    (root/'control.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps({'run_root':str(root),'controls':controls}));return all(c['valid'] for c in controls.values())


if __name__=='__main__': main(EXPECTED,prepare)
