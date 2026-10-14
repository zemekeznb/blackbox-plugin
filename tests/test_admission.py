import sys,io,json,tempfile,unittest,hashlib,time,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/blackbox/scripts'))
from blackbox_core import Store
import blackbox_admission as admission

class AdmissionTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);self.project=self.root/'project';self.project.mkdir();self.store=Store(self.root/'state');self.store.set_project(str(self.project),True)
  self.payload=dict(cwd=str(self.project),session_id='synthetic',hook_event_name='PostToolUse',tool_name='Bash',tool_use_id='1',tool_response='private body',tool_input={'command':'synthetic'})
 def hook(self,**updates):
  return self.store.record_stream(io.BytesIO(json.dumps(dict(self.payload,**updates)).encode()),verify_session=True)
 def legacy_cache(self,session='synthetic',state='allowed',age=0,skipped=0):
  key=hashlib.sha256(admission.canonical([admission.project_path(str(self.project)),session]).encode()).hexdigest()
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS session_admission (key TEXT PRIMARY KEY,state TEXT NOT NULL,checked REAL NOT NULL,skipped INTEGER NOT NULL)')
   db.execute('INSERT INTO session_admission VALUES (?,?,?,?)',(key,state,time.time()-age,skipped))
  return key
 def test_sources_ephemeral_and_untitled_archived(self):
  for source in ('vscode','appServer'):
   for archived in (True,False):self.assertEqual(admission.classify(dict(source=source,ephemeral=False,name=None,archived=archived)),'allowed')
  for source in ('cli','exec',{'subAgent':'review'},{'subAgent':{'thread_spawn':{}}}):self.assertEqual(admission.classify(dict(source=source,ephemeral=False)),'excluded')
  self.assertEqual(admission.classify(dict(source='vscode',ephemeral=True)),'excluded')
  for source in ('unknown',None,{'custom':'app'},[]):self.assertEqual(admission.classify(dict(source=source,ephemeral=False)),'unconfirmed')
  self.assertEqual(admission.classify(dict(source='vscode')),'unconfirmed')
 def test_hook_excluded_and_unknown_never_save_body(self):
  for state in ('excluded','unconfirmed','unavailable'):
   with patch.object(admission,'lookup',return_value=state):result=self.hook(session_id=state)
   self.assertEqual(result['status'],'ignored')
  with self.store.connect() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM investigations').fetchone()[0],0)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
  self.assertNotIn(b'private body',self.store.database.read_bytes())
 def test_verified_caches_and_records_without_repeated_rpc(self):
  with patch.object(admission,'lookup',return_value='allowed') as lookup:
   self.assertEqual(self.hook()['status'],'recorded');self.assertEqual(self.hook(tool_use_id='2')['status'],'recorded');self.assertEqual(lookup.call_count,1)
 def test_cli_metadata_rejects_lifecycle_and_tool_bodies(self):
  import blackbox_codex as codex
  from test_codex_names import FakeProcess
  for name,archived in ((None,False),('Named CLI',True)):
   for kind in ('SessionEnd','PostToolUse'):
    with self.subTest(name=name,archived=archived,kind=kind):
     session=f'cli-{name}-{kind}'
     process=FakeProcess([{'id':1,'result':{}},{'id':2,'result':{'thread':dict(id=session,source='cli',ephemeral=False,name=name,archived=archived)}}])
     with patch.object(codex,'codex_executable',return_value='codex.exe'),patch.object(codex.subprocess,'Popen',return_value=process):result=self.hook(session_id=session,hook_event_name=kind)
     self.assertEqual(result['status'],'ignored');self.assertEqual(result['reason'],'session_excluded')
     requests=[json.loads(v) for v in process.stdin.content.splitlines()]
     self.assertEqual([v['method'] for v in requests],['initialize','initialized','thread/read'])
     self.assertEqual(requests[-1]['params'],dict(threadId=session,includeTurns=False))
  with self.store.connect() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM investigations').fetchone()[0],0)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
  self.assertNotIn(b'private body',self.store.database.read_bytes())
 def test_desktop_metadata_records_untitled_and_archived_sessions(self):
  import blackbox_codex as codex
  from test_codex_names import FakeProcess
  for source in ('vscode','appServer'):
   for archived in (True,False):
    with self.subTest(source=source,archived=archived):
     session=f'{source}-{archived}'
     process=FakeProcess([{'id':1,'result':{}},{'id':2,'result':{'thread':dict(id=session,source=source,ephemeral=False,name=None,archived=archived)}}])
     with patch.object(codex,'codex_executable',return_value='codex.exe'),patch.object(codex.subprocess,'Popen',return_value=process):result=self.hook(session_id=session)
     self.assertEqual(result['status'],'recorded')
     self.assertEqual(len(self.store.detail(result['investigation'])['events']),1)
 def test_legacy_positive_requires_lookup_even_when_fresh_or_expired(self):
  for age in (0,admission.VALID_SECONDS+1):
   with self.subTest(age=age):
    session=f'legacy-cli-{age}';key=self.legacy_cache(session,age=age)
    with patch.object(admission,'lookup',return_value=admission.classify(dict(source='cli',ephemeral=False))) as lookup:result=self.hook(session_id=session)
    lookup.assert_called_once_with(session);self.assertEqual(result['reason'],'session_excluded')
    with self.store.connect() as db:self.assertIsNone(db.execute('SELECT key FROM session_admission WHERE key=?',(key,)).fetchone())
  self.assertEqual(self.store.listing(),[]);self.assertNotIn(b'private body',self.store.database.read_bytes())
 def test_legacy_positive_cannot_authorize_during_transport_outage(self):
  for age in (0,admission.VALID_SECONDS+1):
   with self.subTest(age=age):
    session=f'legacy-unavailable-{age}';self.legacy_cache(session,age=age)
    with patch.object(admission,'lookup',return_value='unavailable') as lookup:result=self.hook(session_id=session)
    lookup.assert_called_once_with(session);self.assertEqual(result['reason'],'session_unavailable')
  self.assertEqual(self.store.listing(),[]);self.assertNotIn(b'private body',self.store.database.read_bytes())
 def test_reverified_legacy_desktop_caches_only_new_policy_decision(self):
  self.legacy_cache()
  with patch.object(admission,'lookup',return_value='allowed') as lookup:
   self.assertEqual(self.hook()['status'],'recorded');self.assertEqual(self.hook(tool_use_id='2')['status'],'recorded');lookup.assert_called_once_with('synthetic')
  with patch.object(admission,'lookup',return_value='unavailable'),patch.object(admission,'VALID_SECONDS',-1):self.assertEqual(self.hook(tool_use_id='3')['status'],'recorded')
 def test_legacy_skipped_events_still_mark_gap_after_reverification(self):
  self.legacy_cache(state='unconfirmed',skipped=1)
  with patch.object(admission,'lookup',return_value='allowed'):result=self.hook()
  events=self.store.detail(result['investigation'])['events']
  self.assertEqual(len(events),1);self.assertTrue(json.loads(events[0]['content'])['_coverage']['capture_gap_before'])
 def test_legacy_outage_marks_gap_when_desktop_verification_recovers(self):
  self.legacy_cache()
  with patch.object(admission,'lookup',return_value='unavailable'):self.assertEqual(self.hook()['status'],'ignored')
  with patch.object(admission,'lookup',return_value='allowed'),patch.object(admission,'RETRY_SECONDS',-1):result=self.hook(tool_use_id='2')
  events=self.store.detail(result['investigation'])['events']
  self.assertEqual(len(events),1);self.assertTrue(json.loads(events[0]['content'])['_coverage']['capture_gap_before'])
 def test_late_lookup_failure_cannot_replace_new_policy_desktop_verification(self):
  started=threading.Event();release=threading.Event();main=threading.current_thread()
  def lookup(session):
   if threading.current_thread() is main:return 'allowed'
   started.set()
   if not release.wait(5):raise RuntimeError('test lookup was not released')
   return 'unavailable'
  with patch.object(admission,'lookup',side_effect=lookup),ThreadPoolExecutor(max_workers=1) as executor:
   pending=executor.submit(admission.admit,self.store,self.payload)
   try:
    self.assertTrue(started.wait(5));self.assertEqual(admission.admit(self.store,self.payload),(True,'session_allowed'))
   finally:release.set()
   self.assertEqual(pending.result(timeout=5),(True,'session_verified_concurrently'))
  with patch.object(admission,'lookup') as lookup:self.assertEqual(self.hook()['status'],'recorded');lookup.assert_not_called()
 def test_project_optout_precedes_lookup(self):
  self.store.set_project(str(self.project),False)
  with patch.object(admission,'lookup') as lookup:self.assertEqual(self.hook(session_id=None)['reason'],'project_not_enabled');lookup.assert_not_called()
 def test_late_registration_records_new_only_with_gap(self):
  with patch.object(admission,'lookup',return_value='unconfirmed'):self.hook()
  with patch.object(admission,'lookup',return_value='allowed'),patch.object(admission,'VALID_SECONDS',-1),patch.object(admission,'RETRY_SECONDS',-1):r=self.hook(tool_use_id='2')
  events=self.store.detail(r['investigation'])['events'];self.assertEqual(len(events),1);self.assertTrue(json.loads(events[0]['content'])['_coverage']['capture_gap_before'])
 def test_verified_identity_survives_transport_outage(self):
  with patch.object(admission,'lookup',return_value='allowed'):self.hook()
  with patch.object(admission,'lookup',return_value='unavailable'),patch.object(admission,'VALID_SECONDS',-1):self.assertEqual(self.hook(tool_use_id='2')['status'],'recorded')
 def test_explicit_exclusion_overrides_expired_positive(self):
  with patch.object(admission,'lookup',return_value='allowed'):self.hook()
  with patch.object(admission,'lookup',return_value='excluded'),patch.object(admission,'VALID_SECONDS',-1):self.assertEqual(self.hook(tool_use_id='2')['status'],'ignored')
 def test_pending_metadata_bounded(self):
  with patch.object(admission,'lookup',return_value='unconfirmed'),patch.object(admission,'MAX_ENTRIES',3):
   for i in range(8):self.hook(session_id=str(i))
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM session_admission').fetchone()[0],3)
 def test_cache_bound_includes_legacy_rows(self):
  for i in range(5):self.legacy_cache(session=f'old-{i}')
  with patch.object(admission,'lookup',return_value='excluded'),patch.object(admission,'MAX_ENTRIES',3):self.hook(session_id='new-cli')
  with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM session_admission').fetchone()[0],3)
 def test_no_cache_created_when_disabled(self):
  self.store.set_project(str(self.project),False);self.hook()
  with self.store.connect() as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='session_admission'").fetchone())
 def test_host_missing_is_not_proof_internal(self):
  self.assertEqual(admission.SessionEligibility()._thread_error({'error':{'code':-32600,'message':'thread not loaded: synthetic'}}),'unconfirmed')
 def test_metadata_retry_does_not_read_turns(self):
  from test_codex_names import FakeProcess
  process=FakeProcess([{'id':1,'result':{}},{'id':2,'error':{'code':-32600,'message':'thread not loaded: synthetic'}},{'id':2,'result':{'thread':{'id':'synthetic','source':'vscode','ephemeral':False}}}])
  found={}
  with patch('blackbox_codex.time.sleep') as delay:admission.SessionEligibility()._exchange(process,['synthetic'],found)
  self.assertEqual(found,{'synthetic':'allowed'});delay.assert_called_once_with(.15)
  requests=[json.loads(v) for v in process.stdin.content.splitlines()];reads=[v for v in requests if v.get('method')=='thread/read']
  self.assertEqual(len(reads),2);self.assertTrue(all(v['params']['includeTurns'] is False for v in reads))
 def test_production_main_enforces_admission(self):
  import blackbox_core as core
  from contextlib import redirect_stdout,redirect_stderr
  with patch.object(admission,'lookup',return_value='excluded'),patch.object(core.sys,'stdin',io.TextIOWrapper(io.BytesIO(json.dumps(self.payload).encode()))),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
   core.main(['--state-dir',str(self.store.directory),'hook'])
  self.assertEqual(self.store.listing(),[])
if __name__=='__main__':unittest.main()
