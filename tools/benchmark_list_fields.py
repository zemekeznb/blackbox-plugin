"""Isolated synthetic list benchmark; never points at the user's state directory."""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import platform
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store
import tas_notes_anomalies as a

CASES = [
    ('dense-1k', 1000, 1, 256, True, False),
    ('dense-5k', 5000, 1, 256, True, False),
    ('sparse-5k', 5000, 50, 256, True, False),
    ('large-2k', 2000, 20, 4096, True, False),
    ('old-page-5k', 5000, 1, 256, True, True),
    ('unbounded-turn-5k', 5000, 50, 256, False, False),
    ('old-unbounded-5k', 5000, 1, 256, False, True),
    ('old-large-2k', 2000, 1, 4096, True, True),
    ('dense-10k', 10000, 1, 256, True, False),
    ('dense-50k', 50000, 1, 256, True, False),
    ('sparse-10k', 10000, 100, 256, True, False),
    ('sparse-50k', 50000, 500, 256, True, False),
    ('large-10k', 10000, 100, 4096, True, False),
    ('old-page-50k', 50000, 1, 256, True, True),
    ('unbounded-turn-10k', 10000, 100, 256, False, False),
]

def memory_mib():
    if sys.platform != 'win32':
        return None
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    info = Counters(); info.cb = ctypes.sizeof(info)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = ctypes.c_void_p
    read = ctypes.windll.psapi.GetProcessMemoryInfo
    read.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not read(get_process(), ctypes.byref(info), info.cb):
        raise ctypes.WinError()
    return round(info.PeakWorkingSetSize / 1024**2, 2)

def seed(root, case):
    _, count, every, size, turns, _ = case
    project = root / 'project'; project.mkdir()
    store = Store(root / 'state'); store.set_project(str(project), True)
    first = store.record(dict(cwd=str(project), session_id='synthetic-performance',
        hook_event_name='PostToolUse', tool_name='Bash', tool_input={'command':'echo seed'},
        tool_response='ok', tool_use_id='seed'))
    inv = first['investigation']
    with store.connect() as db:
        db.execute('BEGIN')
        db.execute('DELETE FROM events')
        for n in range(1, count+1):
            error = (n-1) % every == 0
            content = dict(tool_name='Bash', tool_input={'command':f'python item_{n}.py'},
                tool_response=('Traceback (most recent call last):\n  File "example.py", line 1\nRuntimeError: synthetic\n' if error else 'ok\n') + 'x'*size,
                _coverage={'capture_boundary_version':1})
            if turns:
                content['turn_id'] = 'turn-' + str((n-1)//20)
            raw = json.dumps(content)
            db.execute('INSERT INTO events(seq,investigation,event_key,kind,content,digest,captured) VALUES(?,?,?,?,?,?,?)',
                (n, inv, str(n), 'PostToolUse', raw, hashlib.sha256(raw.encode()).hexdigest(), '2026-10-12T00:00:00+00:00'))
        db.commit()
    return inv

def worker(args):
    store = Store(Path(args.state)); subject = a; enrichment=[]
    if args.mode == 'legacy':
        spec=importlib.util.spec_from_file_location('benchmark_legacy',args.reference)
        legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
        subject=legacy
    enrich=subject.enrich_list_items
    def measured(*values):
        start=time.perf_counter(); enrich(*values); enrichment.append(time.perf_counter()-start)
    subject.enrich_list_items = measured if args.mode != 'baseline' else lambda *values: None
    timings=[]; page=None
    for _ in range(3):
        start=time.perf_counter()
        page=subject.listing(store, investigation=args.inv, through=args.count, before=args.before)
        timings.append(time.perf_counter()-start)
    peak=memory_mib()  # before detail correctness checks
    base=[{k:v for k,v in row.items() if k not in ('source_summary','repeat_count')} for row in page['items']]
    checks=[]
    if args.mode=='full':
        for item in page['items'][::max(1,len(page['items'])//3)]:
            checks.append(item['repeat_count']==len(a.detail(store,item['id'],page['snapshot_seq'])['events'])-1)
        assert all(checks)
    print(json.dumps(dict(mode=args.mode,seconds=timings,median_seconds=statistics.median(timings),
        enrichment_seconds=enrichment,peak_working_set_mib=peak,items=len(base),total=page['total_count'],
        next_before=page['next_before'],snapshot=page['snapshot_seq'],
        base_digest=hashlib.sha256(json.dumps(base,sort_keys=True).encode()).hexdigest(),
        enriched_digest=hashlib.sha256(json.dumps(page['items'],sort_keys=True).encode()).hexdigest(),detail_checks=len(checks))))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--worker',action='store_true')
    parser.add_argument('--state');parser.add_argument('--inv');parser.add_argument('--count',type=int)
    parser.add_argument('--before',type=int);parser.add_argument('--mode');parser.add_argument('--output')
    parser.add_argument('--case',action='append',dest='cases')
    parser.add_argument('--reference',help='Optional pre-change module for same-database legacy/full comparison')
    args=parser.parse_args()
    if args.worker:
        worker(args);return
    report={'python':sys.version,'platform':platform.platform(),'cases':[],
            'method':'3 sequential requests per fresh worker; baseline skips only enrich_list_items; Windows process peak working set includes interpreter/Store startup; DB seeding excluded; no OS cache flush'}
    for case in CASES:
        if args.cases and case[0] not in args.cases:continue
        with tempfile.TemporaryDirectory(prefix='blackbox-list-perf-') as folder:
            root=Path(folder);inv=seed(root,case)
            result={'name':case[0],'events':case[1],'error_every':case[2],'padding_bytes':case[3],
                    'turn_every':20 if case[4] else None,'before':201 if case[5] else None,
                    'database_mib':round(sum(p.stat().st_size for p in (root/'state').glob('*.sqlite3*'))/1024**2,2)}
            for mode in (('baseline','legacy','full') if args.reference else ('baseline','full')):
                command=[sys.executable,'-B',__file__,'--worker','--state',str(root/'state'),'--inv',inv,'--count',str(case[1]),'--mode',mode]
                if case[5]:command+=['--before','201']
                if args.reference:command+=['--reference',args.reference]
                try:
                    proc=subprocess.run(command,capture_output=True,text=True,timeout=120,check=True)
                    result[mode]=json.loads(proc.stdout.strip())
                except subprocess.TimeoutExpired:
                    result[mode]={'timeout_seconds':120}
            if 'base_digest' in result['full']:
                assert all(result['baseline'][key]==result['full'][key] for key in ('base_digest','items','total','next_before','snapshot'))
                result['unchanged_pagination_and_base_fields']=True
                if args.reference:
                    assert result['legacy']['enriched_digest']==result['full']['enriched_digest']
                    assert all(result['legacy'][key]==result['full'][key] for key in ('base_digest','items','total','next_before','snapshot'))
                    result['legacy_fields_identical']=True
            report['cases'].append(result)
            Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result),flush=True)

if __name__=='__main__':main()
