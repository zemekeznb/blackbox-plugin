"""Optional, bounded reads of names for already-saved Codex thread identities.

This module never lists, resumes, creates or changes threads. Names are volatile
reading metadata, not captured evidence, and are never written into the store.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time


MAX_BATCH = 20
MAX_LINE_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_NAME = 512
CACHE_ENTRIES = 512
CACHE_SECONDS = 60
RPC_SECONDS = 6


def codex_executable():
    """Prefer PATH; desktop shortcuts may need the native desktop bin fallback."""
    executable = shutil.which('codex.exe' if os.name == 'nt' else 'codex')
    if executable:
        return executable
    if os.name == 'nt' and os.environ.get('LOCALAPPDATA'):
        root = Path(os.environ['LOCALAPPDATA']) / 'OpenAI' / 'Codex' / 'bin'
        try:
            # Inspect executable install directories only, never conversation data.
            candidates = []
            for index, directory in enumerate(root.iterdir()):
                if index >= 64:
                    break
                path = directory / 'codex.exe'
                if path.is_file():
                    candidates.append(path)
            if candidates:
                return str(max(candidates, key=lambda path: path.stat().st_mtime))
        except OSError:
            pass
    return None


def display_name(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value


class SessionNames:
    """One service-scoped cache and at most one owned app-server process."""
    def __init__(self):
        self._cache = OrderedDict()
        self._cache_lock = threading.Lock()
        self._lookup_lock = threading.Lock()
        self._process = None
        self._closed = threading.Event()

    def names(self, sessions):
        # The HTTP layer resolves investigations to stored session IDs first.
        if len(sessions) > MAX_BATCH:
            raise ValueError('too many sessions')
        result = {identifier: None for identifier in sessions}
        if self._closed.is_set():
            return result
        requested = list(dict.fromkeys(sessions.values()))
        requested = [value for value in requested
                     if isinstance(value, str) and 0 < len(value) <= 200]
        now = time.monotonic()
        with self._cache_lock:
            missing = [value for value in requested if value not in self._cache
                       or self._cache[value][0] <= now]
        # Simultaneous tab requests must neither multiply subprocesses nor wait
        # behind another lookup. Returning null never prevents opening a record.
        if missing and self._lookup_lock.acquire(blocking=False):
            try:
                with self._cache_lock:
                    missing = [value for value in missing if value not in self._cache
                               or self._cache[value][0] <= time.monotonic()]
                if missing:
                    found = self._lookup(missing)
                    expires = time.monotonic() + CACHE_SECONDS
                    with self._cache_lock:
                        for value in missing:
                            self._cache[value] = (expires, found.get(value))
                            self._cache.move_to_end(value)
                        while len(self._cache) > CACHE_ENTRIES:
                            self._cache.popitem(last=False)
            finally:
                self._lookup_lock.release()
        with self._cache_lock:
            for identifier, value in sessions.items():
                entry = self._cache.get(value)
                if entry and entry[0] > time.monotonic():
                    result[identifier] = entry[1]
        return result

    @staticmethod
    def _stop_process(process):
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def close(self):
        # Request handlers are daemon threads. Service shutdown must also reap
        # an in-flight metadata child before those handlers can be abandoned.
        self._closed.set()
        process = self._process
        if process is not None:
            self._stop_process(process)
        if self._lookup_lock.acquire(timeout=8):
            try:
                if self._process is not None:
                    self._stop_process(self._process)
            finally:
                self._lookup_lock.release()

    def _lookup(self, sessions):
        # Keep a process that resisted termination registered; never start a
        # second one alongside it. Normally terminate/wait reaps it immediately.
        if self._closed.is_set() or (self._process is not None and self._process.poll() is None):
            return {}
        executable = codex_executable()
        if not executable:
            return {}
        found = {}
        try:
            process = subprocess.Popen(
                [executable, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            return found
        self._process = process
        worker = threading.Thread(target=self._exchange, args=(process, sessions, found), daemon=True)
        worker.start()
        try:
            # This watchdog also covers a blocked stdin write or incomplete line.
            worker.join(RPC_SECONDS)
        finally:
            self._stop_process(process)
            worker.join(0.1)
            if process.poll() is not None:
                self._process = None
                # Closing a buffered reader while its worker is still blocked
                # can itself block. A terminated pipe normally ends the worker.
                if not worker.is_alive():
                    process.stdin.close()
                    process.stdout.close()
        return dict(found)

    def _thread_value(self, thread):
        return display_name(thread.get('name'))

    def _thread_error(self, reply):
        return None

    def _exchange(self, process, sessions, found):
        consumed = 0

        def send(value):
            process.stdin.write((json.dumps(value, ensure_ascii=True) + '\n').encode('utf-8'))
            process.stdin.flush()

        def receive(identifier):
            nonlocal consumed
            while True:
                line = process.stdout.readline(MAX_LINE_BYTES + 1)
                consumed += len(line)
                if not line or len(line) > MAX_LINE_BYTES or consumed > MAX_OUTPUT_BYTES:
                    raise ValueError('bounded metadata response unavailable')
                reply = json.loads(line)
                if not isinstance(reply, dict):
                    raise ValueError('invalid metadata response')
                if 'id' in reply and 'method' in reply:
                    raise ValueError('interactive metadata response')
                if reply.get('id') == identifier:
                    return reply

        try:
            send({'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'blackbox-reader', 'version': '1'}}})
            if 'error' in receive(1):
                return
            send({'method': 'initialized'})
            for identifier, session in enumerate(sessions, 2):
                send({'id': identifier, 'method': 'thread/read',
                      'params': {'threadId': session, 'includeTurns': False}})
                reply = receive(identifier)
                for _ in range(getattr(self, 'READ_ATTEMPTS', 1)-1):
                    if 'error' not in reply or self._thread_error(reply) != 'unconfirmed':
                        break
                    time.sleep(0.15)
                    send({'id': identifier, 'method': 'thread/read', 'params': {'threadId': session, 'includeTurns': False}})
                    reply = receive(identifier)
                if 'error' in reply:
                    found[session] = self._thread_error(reply)
                    continue
                result = reply.get('result')
                thread = result.get('thread') if isinstance(result, dict) else None
                # Ignore previews, paths and every other field returned by Codex.
                if isinstance(thread, dict) and thread.get('id') == session:
                    found[session] = self._thread_value(thread)
        except (OSError, ValueError, TypeError, RecursionError):
            return  # Unavailable titles are cached briefly; no content logging.


class SessionMetadata(SessionNames):
    def _thread_value(self, thread):
        name = display_name(thread.get('name'))
        return {'name': name, 'status': 'named' if name else 'untitled'}

    def _thread_error(self, reply):
        error = reply.get('error', {})
        if not isinstance(error, dict):
            return {'name': None, 'status': 'unavailable'}
        # This specific host response does not prove deletion or archival.
        missing = error.get('code') == -32600 and str(error.get('message', '')).startswith('thread not loaded:')
        return {'name': None, 'status': 'unlinked' if missing else 'unavailable'}


def session_labels(provider, sessions):
    values = provider.names(sessions)
    return {'names': {key: value.get('name') if isinstance(value, dict) else value for key, value in values.items()},
            'states': {key: value.get('status', 'unavailable') if isinstance(value, dict)
                       else 'named' if value else 'unavailable' for key, value in values.items()}}


class ProjectCatalog(SessionNames):
    """Read bounded project labels/roots, never threads or project files."""
    @staticmethod
    def _exchange(process, sessions, found):
        consumed = 0
        def call(identifier, method, params):
            nonlocal consumed
            process.stdin.write((json.dumps({'id':identifier,'method':method,'params':params})+'\n').encode())
            process.stdin.flush()
            while True:
                line = process.stdout.readline(MAX_LINE_BYTES+1); consumed += len(line)
                if not line or len(line)>MAX_LINE_BYTES or consumed>MAX_OUTPUT_BYTES:
                    raise ValueError('bounded project metadata unavailable')
                reply=json.loads(line)
                if not isinstance(reply,dict) or ('id' in reply and 'method' in reply):
                    raise ValueError('invalid project response')
                if reply.get('id')==identifier:
                    if 'error' in reply: raise ValueError('project metadata unavailable')
                    return reply.get('result',{})
        try:
            call(1,'initialize',{'clientInfo':{'name':'blackbox-projects','version':'1'},'capabilities':{'experimentalApi':True}})
            process.stdin.write(b'{"method":"initialized"}\n');process.stdin.flush()
            result=[];cursor=None
            for identifier in range(2,7):
                page=call(identifier,'project/list',{'limit':100,**({'cursor':cursor} if cursor else {})})
                for project in page.get('data',[]):
                    name=display_name(project.get('name'))
                    roots=[root['path'] for root in project.get('roots',[]) if isinstance(root,dict)
                           and isinstance(root.get('path'),str) and len(root['path'])<=4096]
                    if name and isinstance(project.get('id'),str) and roots:
                        result.append({'id':project['id'],'name':name,'roots':roots})
                cursor=page.get('nextCursor')
                if not cursor:
                    found['catalog']=result;return
            # Partial catalog could incorrectly resolve overlapping roots.
        except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):
            return

    def projects(self):
        return self.names({'catalog':'catalog'}).get('catalog') or []


def navigation_contexts(store, project_path, catalog):
    import ntpath
    from tas_notes_anomalies import contexts
    data=contexts(store)
    hosted=catalog.projects()
    groups={}
    for saved in data['projects']:
        matches=[p for p in hosted if any(ntpath.normcase(ntpath.normpath(saved['path'])) ==
                 ntpath.normcase(ntpath.normpath(root)) for root in p['roots'])]
        host=matches[0] if len(matches)==1 else None
        key=host['id'] if host else saved['path']
        if key not in groups:
            groups[key]={**saved,'name':host['name'] if host else None,
                         'host_id':host['id'] if host else None,'paths':[]}
        groups[key]['paths'].append(saved['path'])
    sessions=[]
    if project_path:
        # Validate the input with the existing scalar contract even if unmatched.
        contexts(store,project_path)
        group=next((g for g in groups.values() if project_path in g['paths']),None)
        for path in group['paths'] if group else [project_path]:
            sessions.extend({**s,'project':path} for s in contexts(store,path)['sessions'])
        sessions.sort(key=lambda s:s['last_captured'],reverse=True)
    result = {'projects':list(groups.values()),'sessions':sessions}
    if not project_path:
        from tas_notes_anomalies import anomaly_presence
        result['workspace_has_anomalies'] = anomaly_presence(store)['has_anomalies']
    return result
