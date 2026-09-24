"""Read-only runtime probe. Never starts a turn or changes hook trust."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--cwd', type=Path)
    args = parser.parse_args()
    if args.cwd and not args.installed:
        parser.error('--cwd requires --installed; never write hook fixtures into a selected project')
    root = (Path(__file__).resolve().parents[1] / '.cache' / 'probe-workspace').resolve()
    if args.cwd:
        root = args.cwd.resolve()
    project_root = Path(__file__).resolve().parents[1]
    root.mkdir(parents=True, exist_ok=True)
    if not args.installed:
        config = root / '.codex' / 'hooks.json'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_bytes((project_root / 'plugins' / 'tas-notes' / 'hooks' / 'hooks.json').read_bytes())
    process = subprocess.Popen(
        [shutil.which('codex') or 'codex', 'app-server', '--stdio', '-c', 'features.hooks=true'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    incoming: queue.Queue = queue.Queue()

    def read():
        for line in process.stdout:
            try:
                incoming.put(json.loads(line))
            except ValueError:
                pass
        incoming.put({'eof': True})

    threading.Thread(target=read, daemon=True).start()

    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()

    def wait(identifier):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = incoming.get(timeout=max(0.1, deadline - time.monotonic()))
            if value.get('eof'):
                raise RuntimeError('app-server exited before the response')
            if value.get('id') == identifier:
                return value
        raise TimeoutError('app-server did not respond')

    try:
        send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'tas-notes-probe', 'version': '0.1.0'},
            'capabilities': {'experimentalApi': True}}})
        first = wait(1)
        if 'error' in first:
            raise RuntimeError('initialize failed')
        send({'method': 'initialized'})
        send({'id': 2, 'method': 'hooks/list', 'params': {'cwds': [str(root)]}})
        reply = wait(2)
        if 'error' in reply:
            print(json.dumps(reply, ensure_ascii=True))
            return
        result = reply.get('result', {})
        output = {'resultKeys': list(result), 'entries': []}
        for entry in result.get('data', []):
            output['entries'].append({
                'errors': entry.get('errors', []),
                'hooks': [{key: hook.get(key) for key in ('eventName', 'handlerType', 'timeoutSec', 'source', 'trustStatus', 'command', 'pluginId', 'enabled')}
                          for hook in entry.get('hooks', [])
                          if (args.installed and (hook.get('pluginId') or '').startswith('tas-notes@'))
                          or (not args.installed and str(root).lower() in hook.get('sourcePath', '').lower())],
            })
        send({'id': 3, 'method': 'plugin/read', 'params': {
            'marketplacePath': str(project_root / '.agents' / 'plugins' / 'marketplace.json'),
            'pluginName': 'tas-notes'}})
        plugin_reply = wait(3)
        # Only this explicitly addressed local plugin response is printed.
        output['plugin_read'] = plugin_reply.get('result', plugin_reply.get('error'))
        print(json.dumps(output, indent=2, ensure_ascii=True))
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == '__main__':
    main()
