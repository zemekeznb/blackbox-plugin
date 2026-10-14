"""Explicit local session lifecycle. Never touches the host or exported copies."""
import hashlib
import re

from blackbox_core import NotesError, canonical


def archived_ids(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='reader_session_state'").fetchone():
        return set()
    return {row[0] for row in db.execute('SELECT investigation FROM reader_session_state WHERE archived=1')}


def summary(db, identifier):
    if not isinstance(identifier, str) or not 0 < len(identifier) <= 200:
        raise NotesError('invalid session')
    row = db.execute('SELECT id,project,session FROM investigations WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise NotesError('unknown session')
    value = dict(row)
    for table in ('events', 'revisions', 'summaries'):
        value[table] = db.execute(f'SELECT COUNT(*) FROM {table} WHERE investigation=?', (identifier,)).fetchone()[0]
    value['last_seq'] = db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE investigation=?', (identifier,)).fetchone()[0]
    value['archived'] = identifier in archived_ids(db)
    value['snapshot'] = hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
    return value


def inspect_session(store, identifier):
    with store.connect() as db:
        db.execute('BEGIN')
        return summary(db, identifier)


def manage_session(store, value):
    if (not isinstance(value, dict) or set(value) != {'investigation', 'action', 'snapshot'}
            or value['action'] not in ('archive', 'restore', 'delete')
            or not isinstance(value['snapshot'], str) or not re.fullmatch('[a-f0-9]{64}', value['snapshot'])):
        raise NotesError('invalid session action')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = summary(db, value['investigation'])
        if current['snapshot'] != value['snapshot']:
            raise NotesError('session changed; review again')
        identifier = current['id']
        if value['action'] == 'delete':
            from blackbox_cleanup import plan, apply
            scope = dict(action='session', projects=[], investigation=identifier, ids=[], anomaly_type='', start='', end='', through=None)
            prepared = plan(db, scope)[0]
            db.rollback()
            result = apply(store, {'scope': prepared['scope'], 'token': prepared['token']})
            return dict(result, investigation=identifier, action='delete', completed=result['reclaimed'])
        else:
            db.execute('''CREATE TABLE IF NOT EXISTS reader_session_state (
                investigation TEXT PRIMARY KEY REFERENCES investigations(id),
                archived INTEGER NOT NULL CHECK(archived IN (0,1)))''')
            db.execute('''INSERT INTO reader_session_state VALUES (?,?)
                ON CONFLICT(investigation) DO UPDATE SET archived=excluded.archived''',
                (identifier, int(value['action'] == 'archive')))
        db.commit()
    return {'investigation': identifier, 'action': value['action'], 'completed': True}
