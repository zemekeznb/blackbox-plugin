"""Host-verified interactive session admission. Never persists unknown event bodies."""
import hashlib
import time

from blackbox_codex import SessionNames
from blackbox_core import canonical, project_path, session_identity, diagnostic

MAX_ENTRIES = 1024
VALID_SECONDS = 86400
RETRY_SECONDS = 5


def classify(thread):
    source = thread.get('source')
    if thread.get('ephemeral') is True or source == 'exec' or (isinstance(source, dict) and 'subAgent' in source):
        return 'excluded'
    if thread.get('ephemeral') is False and isinstance(source, str) and source in ('cli', 'vscode', 'appServer'):
        return 'allowed'
    return 'unconfirmed'


class SessionEligibility(SessionNames):
    READ_ATTEMPTS = 2

    def _thread_value(self, thread):
        return classify(thread)

    def _thread_error(self, reply):
        error = reply.get('error', {})
        if isinstance(error, dict) and error.get('code') == -32600 and str(error.get('message', '')).startswith('thread not loaded:'):
            return 'unconfirmed'
        return 'unavailable'


def lookup(session):
    provider = SessionEligibility()
    try:
        return provider.names({'current': session}).get('current') or 'unavailable'
    finally:
        provider.close()


def admit(store, payload):
    project = project_path(payload.get('cwd'))
    stamp = time.time()
    with store.connect() as db:
        authorized = db.execute('SELECT enabled FROM projects WHERE path=?', (project,)).fetchone()
        if not authorized or not authorized[0]:
            return False, 'project_not_enabled'
        session = session_identity(payload)
        key = hashlib.sha256(canonical([project, session]).encode()).hexdigest()
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='session_admission'").fetchone()
        row = db.execute('SELECT state,checked,skipped FROM session_admission WHERE key=?', (key,)).fetchone() if exists else None
    if row and 0 <= stamp-row['checked'] < (VALID_SECONDS if row['state'] in ('allowed', 'excluded') else RETRY_SECONDS):
        return row['state'] == 'allowed', 'session_' + row['state']
    state = lookup(session)
    # An unavailable transport must not erase a previously verified identity.
    if state == 'unavailable' and row and row['state'] == 'allowed':
        return True, 'session_previously_verified'
    if state not in ('allowed', 'excluded', 'unconfirmed', 'unavailable'):
        state = 'unavailable'
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS session_admission (key TEXT PRIMARY KEY,state TEXT NOT NULL,checked REAL NOT NULL,skipped INTEGER NOT NULL)')
        prior = db.execute('SELECT state,checked,skipped FROM session_admission WHERE key=?', (key,)).fetchone()
        # Do not let a late failed lookup overwrite concurrent positive evidence.
        if prior and prior['state'] == 'allowed' and prior['checked'] >= stamp:
            db.rollback()
            return True, 'session_verified_concurrently'
        if state == 'allowed' and prior and prior['skipped']:
            diagnostic(store.directory, 'SessionIdentityUnconfirmed', project, session)
        db.execute('INSERT INTO session_admission VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET state=excluded.state,checked=excluded.checked,skipped=excluded.skipped',
                   (key, state, stamp, int(state != 'allowed')))
        db.execute('DELETE FROM session_admission WHERE key IN (SELECT key FROM session_admission ORDER BY checked DESC,key LIMIT -1 OFFSET ?)', (MAX_ENTRIES,))
        db.commit()
    return state == 'allowed', 'session_' + state
