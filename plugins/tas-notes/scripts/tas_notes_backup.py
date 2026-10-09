"""Verified SQLite snapshots and restore into a NEW, disabled state directory."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile

from tas_notes_core import NotesError, diagnostic


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def verify(path):
    with closing(readonly(path)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise NotesError('backup integrity check failed')
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise NotesError('unsupported backup version')
        required = {'projects', 'investigations', 'events', 'revisions', 'summaries'}
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= names or db.execute('PRAGMA foreign_key_check').fetchone():
            raise NotesError('invalid backup schema or references')
        return {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                for table in sorted(required)}


def outside_projects(path, projects):
    path = Path(path).resolve()
    for project in projects:
        root = Path(project).resolve()
        if path == root or root in path.parents:
            raise NotesError('backup/restore must be outside recorded projects')
    # Inspect the destination, not this module's installation location. A copied
    # reviewer has no source checkout nearby; worktrees use a .git file rather
    # than a directory. Both must keep runtime snapshots outside repositories.
    for ancestor in (path, *path.parents):
        marker = ancestor / '.git'
        if marker.exists() or marker.is_symlink():
            raise NotesError('runtime snapshots must be outside Git repositories')
    return path


def backup(store, destination):
    with store.connect() as db:
        projects = [r[0] for r in db.execute('SELECT path FROM projects')]
    destination = outside_projects(destination, projects)
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise NotesError('choose a new backup file in an existing directory')
    fd, name = tempfile.mkstemp(prefix='.blackbox-backup-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with store.connect() as source, closing(sqlite3.connect(temporary)) as target:
            source.backup(target)
        counts = verify(temporary)
        with closing(readonly(temporary)) as snapshot:
            outside_projects(destination, [r[0] for r in snapshot.execute('SELECT path FROM projects')])
        with temporary.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        # Windows rename refuses an existing target and also works on FAT/exFAT.
        if os.name == 'nt':
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
        return {'backup': str(destination), 'sha256': digest, 'counts': counts,
                'verified': True, 'contains_sensitive_local_data': True}
    finally:
        temporary.unlink(missing_ok=True)


def restore(source, destination):
    source = Path(source).resolve(strict=True)
    expected = verify(source)
    with closing(readonly(source)) as db:
        projects = [r[0] for r in db.execute('SELECT path FROM projects')]
    destination = outside_projects(destination, projects)
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise NotesError('restore requires a new state directory in an existing parent')
    destination.mkdir()
    database = destination / 'notes.sqlite3'
    try:
        with closing(readonly(source)) as original, closing(sqlite3.connect(database)) as target:
            original.backup(target)
            # Restoring an archive never silently resumes recording in its projects.
            target.execute('UPDATE projects SET enabled=0')
            target.commit()
        if verify(database) != expected:
            raise NotesError('restored row counts differ')
        # SQLite backups do not carry pending filesystem rejection markers.
        # New capture must not bridge an archived session across this restore.
        # This is continuity metadata, not evidence that any event was lost.
        from tas_notes_diagnostics import unscoped_error
        diagnostic(destination, 'RestoreContinuityUnknown')
        boundary = unscoped_error(destination)
        if (not boundary or boundary['state'] != 'recorded'
                or boundary['code'] != 'RestoreContinuityUnknown'):
            raise NotesError('restored capture continuity marker could not be saved')
        return {'restored_state': str(destination), 'verified': True,
                'counts': expected, 'all_projects_disabled': True,
                'capture_continuity': 'unknown_after_restore',
                'runtime_configured': False}
    except BaseException:
        # Only this new directory and our own restoration files can exist here.
        for name in ('notes.sqlite3-journal', 'notes.sqlite3-wal', 'notes.sqlite3-shm', 'notes.sqlite3',
                     'capture-unknown.json', 'last-error.json'):
            (destination / name).unlink(missing_ok=True)
        destination.rmdir()
        raise
