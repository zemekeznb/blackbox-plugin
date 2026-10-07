from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'
sys.path.insert(0, str(SCRIPTS))
from tas_notes_core import NotesError, Store
from tas_notes_backup import backup, restore


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


if __name__ == '__main__':
    unittest.main()
