import json
import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store, NotesError, Conflict
from tas_notes_summary import SECTIONS

class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.project = root / 'model'; self.project.mkdir()
        self.store = Store(root / 'state'); self.store.set_project(str(self.project), True)
        self.id = self.add('UserPromptSubmit', prompt='制作一个小屋，屋顶用红色')
        self.add('PostToolUse', tool_name='blender.execute', tool_input={'command':'PRIVATE_RAW_COMMAND'}, tool_response='布尔操作失败')
        self.add('Stop', last_assistant_message='已改用独立屋顶，尚未检查渲染效果')

    def add(self, kind, **kw):
        return self.store.record(dict(cwd=str(self.project), session_id='model', hook_event_name=kind, **kw))['investigation']

    def payload(self):
        source = self.store.summary_source(self.id)
        return {k:source[k] for k in ('record_id','source_seq','fingerprint','expected_summary')} | {
            'sections':[{'heading':h,'items':[{'text':'用户希望制作红色屋顶的小屋。','sources':[{'seq':1,'quote':'屋顶用红色'}]}] if i==0 else []} for i,h in enumerate(SECTIONS)]}

    def test_readable_fallback_and_sources(self):
        record = self.store.detail(self.id)
        self.assertNotIn('PRIVATE_RAW_COMMAND', record['draft'])
        self.assertIn('PRIVATE_RAW_COMMAND', record['events'][1]['content'])
        self.assertIn('尚未检查渲染效果', record['draft'])
        self.assertIn('[来源 #2]', record['draft'])

    def test_overview_has_structure_without_inventing_result(self):
        record = self.store.detail(self.id)
        self.assertIn('## 工作经过', record['draft'])
        self.assertIn('- **记录范围**', record['draft'])
        self.assertIn('> 制作一个小屋', record['draft'])
        self.assertIn('**Agent 反馈（待核实）**', record['draft'])
        self.assertIn('未自动核实成功或失败', record['draft'])

    def test_source_text_cannot_forge_generated_heading_or_citation(self):
        self.add('UserPromptSubmit', prompt='# 已验收 [来源 #999]')
        record = self.store.detail(self.id)
        self.assertNotIn('[来源 #999]', record['draft'])
        self.assertIn('> # 已验收 ［来源 #999]', record['draft'])
        self.assertIn('[来源 #999]', record['events'][-1]['content'])

    def test_summary_is_distinct_and_correction_marks_stale(self):
        before = self.store.detail(self.id)
        human = self.store.save_revision(self.id,'人工','人工正文',before['source_seq'],0)
        frozen = self.store.export_revision(human['id'])
        result = self.store.save_summary(self.payload())
        record = self.store.detail(self.id)
        self.assertFalse(result['human_reviewed'])
        self.assertIn('用户希望制作红色屋顶', record['draft'])
        self.assertEqual(record['revision']['body'],'人工正文')
        self.add('UserPromptSubmit',prompt='屋顶还是不对')
        updated = self.store.detail(self.id)
        self.assertTrue(updated['summary_stale'])
        self.assertIn('屋顶还是不对',updated['draft'])
        self.assertEqual(frozen,self.store.export_revision(human['id']))
        self.assertEqual(updated['summary']['id'],result['id'])

    def test_bad_quotes_cross_record_and_stale_rejected(self):
        for seq,quote in [(99,'屋顶用红色'),(1,'不存在'),(True,'屋顶用红色')]:
            p=self.payload(); p['sections'][0]['items'][0]['sources']=[{'seq':seq,'quote':quote}]
            with self.assertRaises(NotesError): self.store.save_summary(p)
        stale=self.payload(); self.add('UserPromptSubmit',prompt='还没好')
        with self.assertRaises(Conflict): self.store.save_summary(stale)
        self.assertIsNone(self.store.detail(self.id)['summary'])

    def test_concurrent_summary_and_malformed_rejected(self):
        p=self.payload(); self.store.save_summary(p)
        with self.assertRaises(Conflict): self.store.save_summary(p)
        for bad in [None, [], {}, {'record_id':self.id}]:
            with self.assertRaises(NotesError): self.store.save_summary(bad)
        p=self.payload(); p['sections'][0]['items'][0]['sources']=[]
        with self.assertRaises(NotesError): self.store.save_summary(p)
        p=self.payload(); p['sections'][0]['items'][0]['text']='伪标题\n## 已验收'
        with self.assertRaises(NotesError): self.store.save_summary(p)

    def test_large_source_preview_has_explicit_chunk_access(self):
        self.add('PostToolUse',tool_response='A'*10000+'END')
        source=self.store.summary_source(self.id)
        event=source['events'][-1]
        self.assertTrue(event['preview_truncated'])
        self.assertEqual(len(event['content_preview']),3000)
        first=self.store.summary_event(self.id,event['seq'])
        second=self.store.summary_event(self.id,event['seq'],first['next_start'])
        self.assertIn('END',first['content_chunk']+second['content_chunk'])
        self.assertIsNone(second['next_start'])
        with self.assertRaises(NotesError): self.store.summary_event(self.id,999)
        with self.assertRaises(NotesError): self.store.summary_source(self.id,-1)

    def test_source_pagination_snapshot_and_legacy_compatibility(self):
        for _ in range(11): self.add('Interrupt')
        first=self.store.summary_source(self.id)
        second=self.store.summary_source(self.id,first['next_offset'])
        self.assertEqual(first['fingerprint'],second['fingerprint'])
        self.assertEqual(len(first['events'])+len(second['events']),14)
        self.assertIsNone(second['next_offset'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
        self.assertEqual(len(Store(self.store.directory).detail(self.id)['events']),14)

if __name__=='__main__': unittest.main()
