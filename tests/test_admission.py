import sys,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/tas-notes/scripts'))
from tas_notes_core import Store
import tas_notes_admission as admission

class AdmissionTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);self.project=self.root/'project';self.project.mkdir();self.store=Store(self.root/'state');self.store.set_project(str(self.project),True)
  self.payload=dict(cwd=str(self.project),session_id='synthetic',hook_event_name='PostToolUse',tool_name='Bash',tool_use_id='1',tool_response='private body',tool_input={'command':'synthetic'})
 def hook(self,**updates):
  return self.store.record_stream(io.BytesIO(json.dumps(dict(self.payload,**updates)).encode()),verify_session=True)
 def test_sources_ephemeral_and_untitled_archived(self):
  for source in ('cli','vscode','appServer'):
   for archived in (True,False):self.assertEqual(admission.classify(dict(source=source,ephemeral=False,name=None,archived=archived)),'allowed')
  for source in ('exec',{'subAgent':'review'},{'subAgent':{'thread_spawn':{}}}):self.assertEqual(admission.classify(dict(source=source,ephemeral=False)),'excluded')
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
 def test_no_cache_created_when_disabled(self):
  self.store.set_project(str(self.project),False);self.hook()
  with self.store.connect() as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='session_admission'").fetchone())
 def test_host_missing_is_not_proof_internal(self):
  self.assertEqual(admission.SessionEligibility()._thread_error({'error':{'code':-32600,'message':'thread not loaded: synthetic'}}),'unconfirmed')
 def test_metadata_retry_does_not_read_turns(self):
  from test_codex_names import FakeProcess
  process=FakeProcess([{'id':1,'result':{}},{'id':2,'error':{'code':-32600,'message':'thread not loaded: synthetic'}},{'id':2,'result':{'thread':{'id':'synthetic','source':'vscode','ephemeral':False}}}])
  found={}
  with patch('tas_notes_codex.time.sleep') as delay:admission.SessionEligibility()._exchange(process,['synthetic'],found)
  self.assertEqual(found,{'synthetic':'allowed'});delay.assert_called_once_with(.15)
  requests=[json.loads(v) for v in process.stdin.content.splitlines()];reads=[v for v in requests if v.get('method')=='thread/read']
  self.assertEqual(len(reads),2);self.assertTrue(all(v['params']['includeTurns'] is False for v in reads))
 def test_production_main_enforces_admission(self):
  import tas_notes_core as core
  from contextlib import redirect_stdout,redirect_stderr
  with patch.object(admission,'lookup',return_value='excluded'),patch.object(core.sys,'stdin',io.TextIOWrapper(io.BytesIO(json.dumps(self.payload).encode()))),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
   core.main(['--state-dir',str(self.store.directory),'hook'])
  self.assertEqual(self.store.listing(),[])
if __name__=='__main__':unittest.main()
