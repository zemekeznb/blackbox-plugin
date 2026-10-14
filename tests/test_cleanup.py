import sys, tempfile, unittest, sqlite3, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/blackbox/scripts'))
from blackbox_core import Store, NotesError, CaptureLimit
from blackbox_cleanup import preview, apply, reclaim, occupied, pending
from blackbox_sessions import manage_session, inspect_session
from blackbox_anomalies import detail, listing
from blackbox_mcp import Reader

class CleanupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.store=Store(self.root/'state');self.project=self.root/'project';self.project.mkdir();self.store.set_project(str(self.project),True);self.n=0
    def emit(self, session='a', error=True, size=0, project=None, key=None):
        self.n+=1
        payload={'cwd':str(project or self.project),'session_id':session,'hook_event_name':'PostToolUse','tool_name':'Bash','tool_use_id':key or str(self.n),'tool_input':{'command':'sample'},'tool_response':('Traceback (most recent call last):\n  File "x.py", line 1\nRuntimeError: sample\n' if error else 'success')+'x'*size}
        self.last_payload=payload
        return self.store.record(payload)
    def scope(self,action='anomalies',**kw):
        return dict(dict(action=action,projects=[],investigation='',ids=[],anomaly_type='',start='',end='',through=None),**kw)
    def delete(self,q):
        p=preview(self.store,q);return apply(self.store,dict(scope=p['scope'],token=p['token']))
    def rows(self):
        with self.store.connect() as db:return [tuple(r) for r in db.execute('SELECT * FROM events ORDER BY seq')]
    def test_filtered_unloaded_and_new_events_survive_replay_does_not(self):
        for _ in range(130): inv=self.emit()['investigation']
        deleted=self.last_payload.copy();other=self.emit('b');before=[r for r in self.rows() if r[1]==other['investigation']]
        p=preview(self.store,self.scope(investigation=inv,anomaly_type='RuntimeError'));self.assertEqual(p['anomalies'],130)
        self.emit();result=apply(self.store,dict(scope=p['scope'],token=p['token']));self.assertTrue(result['reclaimed'])
        self.assertEqual(len(self.rows()),2);self.assertEqual(before,[r for r in self.rows() if r[1]==other['investigation']])
        self.assertEqual(self.store.record(deleted)['reason'],'deleted_replay');self.assertEqual(apply(self.store,dict(scope=p['scope'],token=p['token']))['events'],130)
        self.assertEqual(len(self.rows()),2);self.assertEqual(self.emit()['status'],'recorded')
    def test_all_matching_exclusions_preserve_unloaded_and_new_sources(self):
        for _ in range(130): inv=self.emit()['investigation']
        self.emit('other')
        p=preview(self.store,self.scope(investigation=inv,through=130,excluded_ids=[1,1]))
        self.assertEqual(p['anomalies'],129)
        self.assertEqual(p['scope']['excluded_ids'],[1])
        self.emit()
        with self.assertRaises(NotesError):apply(self.store,dict(scope=dict(p['scope'],excluded_ids=[]),token=p['token']))
        apply(self.store,dict(scope=p['scope'],token=p['token']))
        self.assertEqual([r[0] for r in self.rows()],[1,131,132])
    def test_invalid_exclusions_and_exclude_everything_do_not_delete(self):
        inv=self.emit()['investigation']
        for extra in ([True],[-1],'1'):
            with self.assertRaises(NotesError):preview(self.store,self.scope(investigation=inv,excluded_ids=extra))
        with self.assertRaises(NotesError):preview(self.store,self.scope(investigation=inv,ids=[1],excluded_ids=[1]))
        with self.assertRaises(NotesError):preview(self.store,self.scope('session',investigation=inv,excluded_ids=[1]))
        p=preview(self.store,self.scope(investigation=inv,excluded_ids=[1]))
        self.assertEqual(p['anomalies'],0);self.assertFalse(p['can_apply'])
        self.assertEqual(len(self.rows()),1)
    def test_archive_new_event_stays_archived(self):
        inv=self.emit()['investigation'];manage_session(self.store,dict(investigation=inv,action='archive',snapshot=inspect_session(self.store,inv)['snapshot']))
        self.emit();self.delete(self.scope(investigation=inv,ids=[1]));self.assertTrue(inspect_session(self.store,inv)['archived'])
    def test_delete_session_new_event_invalidates(self):
        inv=self.emit()['investigation'];p=preview(self.store,self.scope('session',investigation=inv));self.emit();before=self.rows()
        with self.assertRaises(NotesError):apply(self.store,dict(scope=p['scope'],token=p['token']))
        self.assertEqual(before,self.rows())
    def test_clear_keeps_new_and_optin_delete_revokes_exact_paths(self):
        inv=self.emit()['investigation'];p=preview(self.store,self.scope('clear_project',projects=[str(self.project.resolve())]));self.emit()
        apply(self.store,dict(scope=p['scope'],token=p['token']));self.assertEqual(len(self.rows()),1);self.assertEqual(self.emit()['status'],'recorded')
        other=self.root/'project-other';other.mkdir();self.store.set_project(str(other),True);self.emit(project=other);kept=self.rows()[-1]
        self.delete(self.scope('delete_project',projects=[str(self.project.resolve())]));self.assertEqual(self.rows(),[kept]);self.assertEqual(self.emit()['reason'],'project_not_enabled')
    def test_dates_all_events_and_history_preserved(self):
        inv=self.emit(error=False)['investigation'];self.emit();self.emit(error=False)
        with self.store.connect() as db:
            for seq,stamp in [(1,'2026-10-10T15:59:59+00:00'),(2,'2026-10-10T16:00:00+00:00'),(3,'2026-10-11T16:00:00+00:00')]:db.execute('UPDATE events SET captured=? WHERE seq=?',(stamp,seq))
            db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?)',('v',inv,1,2,'title','saved text','now'))
        p=preview(self.store,self.scope('dates',investigation=inv,start='2026-10-11T00:00:00+08:00',end='2026-10-12T00:00:00+08:00'));self.assertEqual(p['events'],1);self.assertEqual(p['retained_versions'],1)
        apply(self.store,dict(scope=p['scope'],token=p['token']));self.assertEqual([r[0] for r in self.rows()],[1,3]);self.assertTrue(self.store.detail(inv)['source_removed']);self.assertIn('来源已清理',self.store.export_revision('v'))
        self.delete(self.scope('dates',investigation=inv,end='2026-10-11T00:00:00+08:00'));self.assertEqual([r[0] for r in self.rows()],[3])
    def test_real_space_reclaimed_and_normal_capture_restored(self):
        for _ in range(80):inv=self.emit(size=12000)['investigation']
        before=self.store.database.stat().st_size
        with patch('blackbox_core.MAX_DATABASE_BYTES',before):
            with self.assertRaises(CaptureLimit):self.emit(error=False)
            result=self.delete(self.scope('session',investigation=inv));self.assertTrue(result['reclaimed']);self.assertLess(self.store.database.stat().st_size,before//2)
            self.assertEqual(self.emit(error=False)['status'],'recorded')
        with self.store.connect() as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_session_bytes_recover_without_error_reserve(self):
        with patch('blackbox_core.MAX_SESSION_BYTES',5000):
            inv=self.emit(error=False,size=3000)['investigation']
            with self.assertRaises(CaptureLimit):self.emit(error=False,size=3000)
            self.delete(self.scope('dates',investigation=inv,end='2099-01-01T00:00:00+00:00'))
            self.assertEqual(self.emit(error=False,size=3000)['status'],'recorded')
    def test_reclaim_failure_receipt_survives_restart_and_retry(self):
        inv=self.emit()['investigation'];original=self.store.connect
        from contextlib import contextmanager
        @contextmanager
        def fail():
            with original() as db:
                class Proxy:
                    def execute(_,sql,*args):
                        if sql=='VACUUM':raise sqlite3.OperationalError('database or disk is full')
                        return db.execute(sql,*args)
                    def __getattr__(_,key):return getattr(db,key)
                yield Proxy()
        with patch.object(self.store,'connect',fail):result=self.delete(self.scope('session',investigation=inv))
        self.assertFalse(result['reclaimed']);self.assertEqual(self.rows(),[])
        with self.store.connect() as db:self.assertEqual(len(pending(db)),1)
        self.assertTrue(reclaim(Store(self.root/'state'),result['token'])['reclaimed'])
    def test_mcp_preview_is_private_no_delete_before_apply(self):
        inv=self.emit()['investigation'];reader=Reader(self.root/'state');args={'operation':'preview','value':self.scope('session',investigation=inv)}
        result=reader.call('cleanup',args);self.assertEqual(result['content'],[]);self.assertEqual(result['_meta']['blackbox']['status'],200);self.assertEqual(len(self.rows()),1)
        p=result['_meta']['blackbox']['body'];result=reader.call('cleanup',{'operation':'apply','value':dict(scope=p['scope'],token=p['token'])});self.assertEqual(result['_meta']['blackbox']['status'],200);self.assertEqual(self.rows(),[])
    def test_shared_anomaly_source_retained_gap_stops_reassociation(self):
        inv=self.emit()['investigation'];self.emit();self.emit(error=False)
        p=preview(self.store,self.scope(investigation=inv,ids=[1]));self.assertEqual(p['events'],1)
        self.delete(self.scope(investigation=inv,ids=[2]));self.assertEqual(len(detail(self.store,1)['events']),1)
    def test_invalid_scope_does_not_mutate(self):
        inv=self.emit()['investigation'];before=self.rows()
        for value in [self.scope('dates'),self.scope('session',investigation=inv,through=0),self.scope('delete_project',projects=['unknown'])]:
            with self.assertRaises(NotesError):preview(self.store,value)
        self.assertEqual(before,self.rows())
    def test_5000_event_quota_recovers_for_normal_events(self):
        inv=self.emit(error=False)['investigation']
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM events LIMIT 1').fetchone()
            db.execute('BEGIN')
            db.executemany('INSERT INTO events(investigation,event_key,kind,content,digest,captured) VALUES (?,?,?,?,?,?)',[(inv,'bulk'+str(i),row['kind'],row['content'],row['digest'],row['captured']) for i in range(4999)])
            db.commit()
        with self.assertRaises(CaptureLimit):self.emit(error=False)
        self.delete(self.scope('dates',investigation=inv,end='2099-01-01T00:00:00+00:00',through=10))
        self.assertEqual(self.emit(error=False)['status'],'recorded')
    def test_failed_commit_is_atomic(self):
        inv=self.emit()['investigation'];before=self.rows();original=self.store.connect
        from contextlib import contextmanager
        @contextmanager
        def fail():
            with original() as db:
                class Proxy:
                    def commit(_):raise sqlite3.OperationalError('database or disk is full')
                    def __getattr__(_,key):return getattr(db,key)
                yield Proxy()
        with patch.object(self.store,'connect',fail):
            with self.assertRaises(sqlite3.OperationalError):self.delete(self.scope('session',investigation=inv))
        self.assertEqual(self.rows(),before)
        self.assertEqual(self.store.status()['storage']['cleanup_pending'],[])
    def test_http_cleanup_auth_and_confirmation(self):
        import http.client,threading
        from blackbox_web import make_server
        inv=self.emit()['investigation'];server,token=make_server(self.store,'test-token');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            def request(value,auth=True,origin=None):
                c=http.client.HTTPConnection(*server.server_address,timeout=10);headers={'Content-Type':'application/json'}
                if auth:headers['Authorization']='Bearer '+token
                if origin:headers['Origin']=origin
                c.request('POST','/api/cleanup',json.dumps(value),headers);r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
            q=dict(operation='preview',value=self.scope('session',investigation=inv))
            self.assertEqual(request(q,False)[0],401);self.assertEqual(request(q,origin='https://example.com')[0],403)
            status,p=request(q);self.assertEqual(status,200);self.assertEqual(len(self.rows()),1)
            self.assertEqual(request(dict(operation='apply',value=dict(scope=p['scope'],token='0'*64)))[0],400)
            self.assertEqual(request(dict(operation='apply',value=dict(scope=p['scope'],token=p['token'])))[0],200);self.assertEqual(self.rows(),[])
        finally:server.shutdown();thread.join();server.server_close()
    def test_partial_delete_last_archived_event_does_not_unarchive_future(self):
        inv=self.emit()['investigation'];manage_session(self.store,dict(investigation=inv,action='archive',snapshot=inspect_session(self.store,inv)['snapshot']))
        self.delete(self.scope(investigation=inv,ids=[1]));self.assertEqual(self.emit()['investigation'],inv);self.assertTrue(inspect_session(self.store,inv)['archived'])
    def test_process_exit_after_commit_recovers_receipt(self):
        import subprocess
        inv=self.emit()['investigation']
        script="import sys,os;sys.path.insert(0,sys.argv[1]);from blackbox_core import Store;import blackbox_cleanup as c;import json;s=Store(sys.argv[2]);q=json.loads(sys.argv[3]);p=c.preview(s,q);c.reclaim=lambda *a:os._exit(23);c.apply(s,dict(scope=p['scope'],token=p['token']))"
        result=subprocess.run([sys.executable,'-c',script,str(Path(__file__).resolve().parents[1]/'plugins/blackbox/scripts'),str(self.store.directory),json.dumps(self.scope('session',investigation=inv))],capture_output=True)
        self.assertEqual(result.returncode,23,result.stderr);self.assertEqual(self.rows(),[])
        with self.store.connect() as db:receipt=pending(db)[0]
        self.assertTrue(reclaim(self.store,receipt['token'])['reclaimed'])
    def test_actual_writer_lock_prevents_partial_delete(self):
        inv=self.emit()['investigation'];p=preview(self.store,self.scope('session',investigation=inv));before=self.rows()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError):apply(self.store,dict(scope=p['scope'],token=p['token']))
        self.assertEqual(before,self.rows())
    def test_multiple_fill_cleanup_cycles_reclaim_disk(self):
        sizes=[]
        for cycle in range(3):
            for _ in range(30):inv=self.emit(size=12000)['investigation']
            before=self.store.database.stat().st_size;self.delete(self.scope('session',investigation=inv));after=self.store.database.stat().st_size
            self.assertLess(after,before//2);sizes.append(after)
        self.assertLess(sizes[-1],sizes[0]+65536)
    def test_dst_repeated_hour_uses_fixed_offset_boundaries(self):
        inv=self.emit(error=False)['investigation'];self.emit(error=False);self.emit(error=False)
        with self.store.connect() as db:
            for seq,stamp in [(1,'2026-11-01T01:30:00-04:00'),(2,'2026-11-01T01:30:00-05:00'),(3,'2026-11-02T00:00:00-05:00')]:db.execute('UPDATE events SET captured=? WHERE seq=?',(stamp,seq))
        p=preview(self.store,self.scope('dates',investigation=inv,start='2026-11-01T00:00:00-04:00',end='2026-11-02T00:00:00-05:00'));self.assertEqual(p['events'],2)
        apply(self.store,dict(scope=p['scope'],token=p['token']));self.assertEqual([r[0] for r in self.rows()],[3])
if __name__=='__main__':unittest.main()
