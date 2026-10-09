import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'
sys.path.insert(0, str(SCRIPTS))
from tas_notes_core import CaptureLimit, MAX_INPUT, NotesError, Store
from tas_notes_backup import backup, restore
import tas_notes_anomalies as anomalies
from tas_notes_diagnostics import unscoped_error


class BackupPathTests(unittest.TestCase):
    def check_repository_rejected(self, git_file):
        with tempfile.TemporaryDirectory(prefix='notes-backup-paths-') as temporary:
            base = Path(temporary)
            store = Store(base / 'state')
            repository = base / 'repository'
            nested = repository / 'nested' / 'destinations'
            nested.mkdir(parents=True)
            marker = repository / '.git'
            if git_file:
                metadata = base / 'worktree-metadata'
                metadata.mkdir()
                marker.write_text('gitdir: ' + str(metadata) + '\n', encoding='utf-8')
            else:
                marker.mkdir()
            # Reproduce a reviewer copied under AppData, away from its source
            # checkout. Destination safety must not depend on __file__.
            installed = base / 'AppData/reviewer/build/scripts/tas_notes_backup.py'
            with patch('tas_notes_backup.__file__', str(installed)):
                for parent in (repository, nested):
                    with self.subTest(parent=parent):
                        destination = parent / 'backup.sqlite3'
                        restored = parent / 'restored-state'
                        with self.assertRaises(NotesError):
                            backup(store, destination)
                        with self.assertRaises(NotesError):
                            restore(store.database, restored)
                        self.assertFalse(destination.exists())
                        self.assertFalse(restored.exists())
                        self.assertEqual(list(parent.glob('.blackbox-backup-*')), [])

    def test_backup_and_restore_reject_git_directory_ancestors(self):
        self.check_repository_rejected(git_file=False)

    def test_backup_and_restore_reject_worktree_git_file_ancestors(self):
        self.check_repository_rejected(git_file=True)

    def test_backup_and_restore_allow_directory_outside_repositories(self):
        with tempfile.TemporaryDirectory(prefix='notes-backup-allowed-') as temporary:
            base = Path(temporary)
            store = Store(base / 'state')
            project = base / 'recorded-project'
            project.mkdir()
            store.set_project(str(project), True)
            destination = base / 'snapshots'
            destination.mkdir()
            saved = destination / 'copy.sqlite3'
            result = backup(store, saved)
            self.assertTrue(result['verified'])
            self.assertTrue(saved.is_file())
            restored = destination / 'restored-state'
            result = restore(saved, restored)
            self.assertTrue(result['verified'])
            self.assertTrue(result['all_projects_disabled'])
            with Store(restored, read_only=True).connect() as db:
                self.assertEqual(db.execute('SELECT enabled FROM projects').fetchone()[0], 0)
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT enabled FROM projects').fetchone()[0], 1)

    def test_restore_preserves_sources_and_stops_unconsumed_rejection_boundary(self):
        with tempfile.TemporaryDirectory(prefix='notes-restore-boundary-') as temporary:
            base = Path(temporary)
            project = base / 'project'
            project.mkdir()
            original = Store(base / 'state')
            original.set_project(str(project), True)
            payload = dict(cwd=str(project), session_id='same-session', hook_event_name='PostToolUse',
                           event_id='before', tool_name='Bash', tool_input={'command': 'python synthetic.py'},
                           tool_response='Traceback (most recent call last):\n  File "synthetic.py", line 1, in <module>\nTypeError: controlled\n')
            identifier = original.record(payload)['investigation']
            prior = original.detail(identifier)['events']
            prior_hash = hashlib.sha256(prior[0]['content'].encode('utf-8')).hexdigest()
            with self.assertRaises(CaptureLimit):
                original.record_stream(io.BytesIO(b'x' * (MAX_INPUT + 1)))
            saved = base / 'snapshot.sqlite3'
            backup(original, saved)
            target = base / 'restored'
            result = restore(saved, target)
            self.assertEqual(result['capture_continuity'], 'unknown_after_restore')
            self.assertEqual(unscoped_error(target)['code'], 'RestoreContinuityUnknown')
            restored = Store(target)
            self.assertFalse(restored.status()['projects'][0]['enabled'])
            self.assertEqual(restored.detail(identifier)['events'], prior)
            restored.set_project(str(project), True)
            restored.record(dict(payload, event_id='after', tool_response='ordinary output'))
            events = restored.detail(identifier)['events']
            self.assertEqual(events[0], prior[0])
            self.assertEqual(hashlib.sha256(events[0]['content'].encode('utf-8')).hexdigest(), prior_hash)
            coverage = json.loads(events[-1]['content'])['_coverage']
            self.assertTrue(coverage['capture_gap_before'])
            self.assertEqual(coverage['capture_gap_reason'], 'restored_store')
            snapshot = anomalies.detail(restored, events[0]['seq'])
            self.assertEqual([e['seq'] for e in snapshot['events']], [events[0]['seq']])
            self.assertIn('恢复', snapshot['markdown'])
            self.assertEqual(unscoped_error(original.directory)['code'], 'InputTooLarge')

    def test_restore_fails_if_continuity_marker_cannot_be_persisted(self):
        with tempfile.TemporaryDirectory(prefix='notes-restore-marker-') as temporary:
            base = Path(temporary)
            original = Store(base / 'state')
            target = base / 'restored'
            with patch('tas_notes_backup.diagnostic'):
                with self.assertRaisesRegex(NotesError, 'continuity marker'):
                    restore(original.database, target)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
