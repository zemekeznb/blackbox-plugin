"""Confirmed local deletion, replay protection and recoverable space reclamation."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime

from tas_notes_core import NotesError, canonical


def exists(db, table):
    return bool(db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone())


def occupied(db):
    return (db.execute('PRAGMA page_count').fetchone()[0] - db.execute('PRAGMA freelist_count').fetchone()[0]) * db.execute('PRAGMA page_size').fetchone()[0]


def replay_key(project, session, key):
    return hashlib.sha256(canonical([project, session, key]).encode()).digest()


def deleted_replay(db, project, session, key):
    return exists(db, 'deleted_sources') and db.execute('SELECT 1 FROM deleted_sources WHERE key=?', (replay_key(project, session, key),)).fetchone() is not None


def with_boundaries(db, investigation, rows, lower, upper):
    if not exists(db, 'deleted_sources'):
        return rows
    gaps = [{'seq': r[0], 'kind': 'UserPromptSubmit', 'content': '{}'} for r in db.execute(
        'SELECT DISTINCT seq FROM deleted_sources WHERE investigation=? AND seq>=? AND seq<=?', (investigation, lower, upper))]
    return sorted(list(rows) + gaps, key=lambda r: r['seq'])


def schema(db):
    # Only mutation creates metadata. No deleted content or raw host event key retained.
    db.execute('CREATE TABLE IF NOT EXISTS deleted_sources (key BLOB PRIMARY KEY, investigation TEXT NOT NULL, seq INTEGER NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS deleted_sources_boundary ON deleted_sources(investigation,seq)')
    db.execute('CREATE TABLE IF NOT EXISTS cleanup_receipts (token TEXT PRIMARY KEY, result TEXT NOT NULL)')


def validate(value):
    keys = {'action', 'projects', 'investigation', 'ids', 'anomaly_type', 'start', 'end', 'through'}
    if not isinstance(value, dict) or set(value) not in (keys, keys | {'excluded_ids'}):
        raise NotesError('invalid cleanup scope')
    q = dict(value)
    if q['action'] not in ('anomalies', 'session', 'clear_project', 'delete_project', 'dates'):
        raise NotesError('invalid cleanup action')
    if (not isinstance(q['projects'], list) or len(q['projects']) > 100
            or any(not isinstance(p, str) or not p or len(p) > 4096 for p in q['projects'])
            or len(set(q['projects'])) != len(q['projects'])):
        raise NotesError('invalid projects')
    for key in ('investigation', 'anomaly_type', 'start', 'end'):
        if not isinstance(q[key], str) or len(q[key]) > 200:
            raise NotesError('invalid scope field')
    if (not isinstance(q['ids'], list) or len(q['ids']) > 5000
            or any(type(n) is not int or not 0 < n < 2**63 for n in q['ids'])):
        raise NotesError('invalid anomaly selection')
    excluded = q.get('excluded_ids', [])
    if (not isinstance(excluded, list) or len(excluded) > 5000
            or any(type(n) is not int or not 0 < n < 2**63 for n in excluded)
            or (excluded and (q['action'] != 'anomalies' or q['ids']))):
        raise NotesError('invalid anomaly exclusions')
    if 'excluded_ids' in q:
        q['excluded_ids'] = sorted(set(excluded))
    if q['through'] is not None and (type(q['through']) is not int or not 0 <= q['through'] < 2**63):
        raise NotesError('invalid cutoff')
    dates = []
    for key in ('start', 'end'):
        try:
            date = datetime.fromisoformat(q[key]) if q[key] else None
            if date and date.tzinfo is None:
                raise ValueError()
        except ValueError:
            raise NotesError('invalid date') from None
        dates.append(date)
    if all(dates) and dates[0] >= dates[1]:
        raise NotesError('invalid date range')
    if q['action'] in ('anomalies', 'session') and not q['investigation']:
        raise NotesError('session required')
    if q['action'] in ('clear_project', 'delete_project') and (not q['projects'] or q['investigation']):
        raise NotesError('project scope required')
    if q['action'] == 'dates' and not q['end']:
        raise NotesError('date end required')
    if q['action'] != 'anomalies' and (q['ids'] or q['anomaly_type']):
        raise NotesError('unexpected anomaly filter')
    if q['action'] not in ('dates', 'anomalies') and (q['start'] or q['end']):
        raise NotesError('unexpected date filter')
    q['projects'] = sorted(q['projects']); q['ids'] = sorted(set(q['ids']))
    return q, dates


def plan(db, value):
    from tas_notes_anomalies import detect, exception_type, linked_events
    q, dates = validate(value)
    maximum = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    if q['through'] is None:
        q['through'] = maximum
    projects = [dict(r) for r in db.execute('SELECT path,enabled FROM projects ORDER BY path')]
    known = {p['path'] for p in projects}
    if not set(q['projects']) <= known:
        raise NotesError('project changed')
    sessions = [dict(r) for r in db.execute('SELECT * FROM investigations ORDER BY id')
                if (not q['projects'] or r['project'] in q['projects']) and (not q['investigation'] or r['id'] == q['investigation'])]
    if q['investigation'] and not sessions:
        raise NotesError('session changed')
    if not q['projects']:
        # Freeze all-project scopes to the exact registered paths at preview time.
        q['projects'] = sorted({s['project'] for s in sessions}) if q['investigation'] else sorted(known)
    chosen, all_rows, hits, linked = {}, {}, {}, {}
    versions, archives = [], []
    for s in sessions:
        rows = db.execute('SELECT * FROM events WHERE investigation=? ORDER BY seq', (s['id'],)).fetchall()
        for r in rows:
            slim = {key: r[key] for key in ('seq', 'investigation', 'digest', 'event_key')}
            slim['_bytes'] = len(r['content'].encode())
            all_rows[r['seq']] = slim
            if r['seq'] <= q['through']:
                hit = detect(json.loads(r['content'])) if r['kind'] == 'PostToolUse' else None
                if hit:
                    hits[r['seq']] = hit
                stamp = datetime.fromisoformat(r['captured'])
                match = (not dates[0] or stamp >= dates[0]) and (not dates[1] or stamp < dates[1])
                if q['action'] == 'anomalies':
                    match = match and r['seq'] not in q.get('excluded_ids', []) and bool(hit) and (not q['ids'] or r['seq'] in q['ids']) and (not q['anomaly_type'] or exception_type(hit) == q['anomaly_type'])
                if match:
                    chosen[r['seq']] = slim
        if q['action'] == 'anomalies':
            # Inspect bounded repeat ownership, including survivors after the cutoff.
            expanded = with_boundaries(db, s['id'], rows, 0, maximum)
            for i, r in enumerate(expanded):
                if r['kind'] == 'PostToolUse' and detect(json.loads(r['content'])):
                    linked[r['seq']] = {v['seq'] for v in linked_events(json.loads(r['content']), expanded[i+1:])[0]}
        for table in ('revisions', 'summaries'):
            versions.extend((table, dict(r)) for r in db.execute(f'SELECT * FROM {table} WHERE investigation=? ORDER BY id', (s['id'],)))
        if exists(db, 'reader_session_state'):
            archives.extend(tuple(r) for r in db.execute('SELECT * FROM reader_session_state WHERE investigation=?', (s['id'],)))
    if q['action'] == 'anomalies' and q['ids'] and set(q['ids']) != set(chosen):
        raise NotesError('selected anomalies changed')
    anomaly_count = sum(n in hits for n in chosen)
    exclusive = set()
    if q['action'] == 'anomalies':
        owners = {}
        for source, children in linked.items():
            for child in children:
                owners.setdefault(child, set()).add(source)
        for source in list(chosen):
            exclusive.update(n for n in linked.get(source, ()) if n <= q['through'] and n not in hits and owners[n] <= chosen.keys())
        chosen.update((n, all_rows[n]) for n in exclusive)
    whole = q['action'] in ('session', 'clear_project', 'delete_project')
    # Session/project removal invalidates on any new source, version or archive change.
    complete = q['action'] in ('session', 'delete_project')
    if complete and any(n > q['through'] for n in all_rows):
        raise NotesError('new records arrived; preview again')
    identity = {'scope': q, 'rows': [(n, r['digest']) for n, r in sorted(chosen.items())],
                'projects': [p for p in projects if p['path'] in q['projects']],
                'versions': versions if whole else [], 'archives': archives if whole else [],
                'full': [(n, r['digest']) for n, r in sorted(all_rows.items())] if complete else []}
    token = hashlib.sha256(canonical(identity).encode()).hexdigest()
    touched = {r['investigation'] for r in chosen.values()}
    count = len(chosen)
    preview = {'scope': q, 'token': token, 'events': count, 'anomalies': anomaly_count,
               'other_events': count-anomaly_count, 'exclusive_related': len(exclusive),
               'sessions': len(sessions) if whole else len(touched), 'projects': q['projects'],
               'versions': len(versions) if whole else 0, 'retained_versions': 0 if whole else len(versions),
               'archived_sessions': sum(r[1] == 1 for r in archives),
               'content_bytes': sum(r['_bytes'] for r in chosen.values()),
               'can_apply': bool(count or (whole and versions) or q['action'] == 'delete_project')}
    return preview, chosen, sessions, versions


def preview(store, value):
    with store.connect() as db:
        db.execute('BEGIN')
        result = plan(db, value)[0]
    return result


def reclaim(store, token):
    # VACUUM uses SQLite's own atomic commit and locking. Failure never undoes an
    # already committed deletion; the durable receipt allows a safe retry.
    try:
        with store.connect() as db:
            if not exists(db, 'cleanup_receipts'):
                raise NotesError('unknown deletion')
            row = db.execute('SELECT result FROM cleanup_receipts WHERE token=?', (token,)).fetchone()
            if not row:
                raise NotesError('unknown deletion')
            result = json.loads(row[0])
            if not result['reclaimed']:
                db.execute('VACUUM')
                result['reclaimed'] = True
                db.execute('UPDATE cleanup_receipts SET result=? WHERE token=?', (canonical(result), token))
            result['database_bytes'] = occupied(db)
            result['disk_bytes'] = store.database.stat().st_size
            return result
    except (sqlite3.Error, OSError):
        with store.connect() as db:
            row = db.execute('SELECT result FROM cleanup_receipts WHERE token=?', (token,)).fetchone()
        if not row:
            raise
        result = json.loads(row[0]); result['reclaimed'] = False
        return result


def apply(store, value):
    if (not isinstance(value, dict) or set(value) != {'scope', 'token'}
            or not isinstance(value['token'], str) or not re.fullmatch('[a-f0-9]{64}', value['token'])):
        raise NotesError('invalid deletion confirmation')
    validate(value['scope'])
    token = value['token']
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if exists(db, 'cleanup_receipts') and db.execute('SELECT 1 FROM cleanup_receipts WHERE token=?', (token,)).fetchone():
            db.rollback()
        else:
            summary, chosen, sessions, versions = plan(db, value['scope'])
            if token != summary['token'] or not summary['can_apply']:
                raise NotesError('deletion scope changed; preview again')
            before = occupied(db)
            schema(db)
            context = {s['id']: s for s in sessions}
            for row in chosen.values():
                s = context[row['investigation']]
                db.execute('INSERT OR IGNORE INTO deleted_sources VALUES (?,?,?)',
                           (replay_key(s['project'], s['session'], row['event_key']), s['id'], row['seq']))
            db.executemany('DELETE FROM events WHERE seq=?', ((n,) for n in chosen))
            action = summary['scope']['action']
            if action in ('session', 'clear_project', 'delete_project'):
                for table, row in versions:
                    db.execute(f'DELETE FROM {table} WHERE id=?', (row['id'],))
            for s in sessions:
                identifier = s['id']
                remaining = any(db.execute(f'SELECT 1 FROM {table} WHERE investigation=? LIMIT 1', (identifier,)).fetchone()
                                for table in ('events', 'revisions', 'summaries'))
                if not remaining and action in ('session', 'clear_project', 'delete_project'):
                    if exists(db, 'reader_session_state'):
                        db.execute('DELETE FROM reader_session_state WHERE investigation=?', (identifier,))
                    db.execute('DELETE FROM capture_usage WHERE investigation=?', (identifier,))
                    db.execute('DELETE FROM investigations WHERE id=?', (identifier,))
            if action == 'delete_project':
                db.executemany('DELETE FROM projects WHERE path=?', ((p,) for p in summary['projects']))
            result = {'token': token, 'deleted': True, 'reclaimed': False, 'events': summary['events'],
                      'anomalies': summary['anomalies'], 'content_bytes': summary['content_bytes'],
                      'freed_capacity_bytes': max(0, before-occupied(db))}
            db.execute('INSERT INTO cleanup_receipts VALUES (?,?)', (token, canonical(result)))
            result['freed_capacity_bytes'] = max(0, before-occupied(db))
            db.execute('UPDATE cleanup_receipts SET result=? WHERE token=?', (canonical(result), token))
            db.commit()
    return reclaim(store, token)

def pending(db):
    if not exists(db, 'cleanup_receipts'):
        return []
    return [r for row in db.execute('SELECT result FROM cleanup_receipts ORDER BY rowid DESC')
            if not (r := json.loads(row[0]))['reclaimed']]


def dispatch(store, args):
    if not isinstance(args, dict) or set(args) != {'operation', 'value'}:
        raise NotesError('invalid cleanup request')
    if args['operation'] == 'preview':
        return preview(store, args['value'])
    if args['operation'] == 'apply':
        return apply(store, args['value'])
    if (args['operation'] == 'retry' and isinstance(args['value'], dict)
            and set(args['value']) == {'token'} and isinstance(args['value']['token'], str)
            and re.fullmatch('[a-f0-9]{64}', args['value']['token'])):
        return reclaim(store, args['value']['token'])
    raise NotesError('invalid cleanup operation')
