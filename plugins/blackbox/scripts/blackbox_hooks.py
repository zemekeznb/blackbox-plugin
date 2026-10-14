"""Read-only host Hook trust inspection; never writes trust or invokes models."""
import json
from blackbox_codex import SessionNames, MAX_LINE_BYTES, MAX_OUTPUT_BYTES

REQUIRED = {'postToolUse', 'preCompact', 'postCompact', 'sessionStart',
            'sessionEnd', 'userPromptSubmit', 'stop', 'interrupt'}
GUIDANCE = '在终端启动 Codex，输入 /hooks，审阅并信任 blackbox@personal 的 Hook，然后刷新状态。'


def classify(hooks):
    hooks = [h for h in hooks if h.get('pluginId') == 'blackbox@personal']
    if not REQUIRED <= {h.get('eventName') for h in hooks}:
        return 'hooks_missing'
    if any(not h.get('enabled') for h in hooks):
        return 'hooks_disabled'
    if any(h.get('trustStatus') not in ('trusted', 'managed') for h in hooks):
        return 'hooks_untrusted'
    return 'trusted'


class HookInspector(SessionNames):
    def _exchange(self, process, paths, found):
        consumed = 0
        def send(value):
            process.stdin.write((json.dumps(value) + '\n').encode())
            process.stdin.flush()
        def receive(identifier):
            nonlocal consumed
            while True:
                line = process.stdout.readline(MAX_LINE_BYTES + 1)
                consumed += len(line)
                if not line or len(line) > MAX_LINE_BYTES or consumed > MAX_OUTPUT_BYTES:
                    raise ValueError('unavailable')
                value = json.loads(line)
                if value.get('id') == identifier:
                    return value
        try:
            send({'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'blackbox-hook-inspector', 'version': '1'},
                'capabilities': {'experimentalApi': True}}})
            if 'error' in receive(1):
                return
            send({'method': 'initialized'})
            send({'id': 2, 'method': 'hooks/list', 'params': {'cwds': list(paths)}})
            for item in receive(2).get('result', {}).get('data', []):
                if item.get('cwd') in paths and not item.get('errors'):
                    found[item['cwd']] = classify(item.get('hooks', []))
        except (OSError, ValueError, TypeError, AttributeError):
            return


def inspect(paths):
    paths = list(dict.fromkeys(paths))
    provider = HookInspector()
    try:
        result = provider._lookup(paths) if paths else {}
        return {path: result.get(path, 'hooks_unknown') for path in paths}
    finally:
        provider.close()


if __name__ == '__main__':
    from pathlib import Path
    result = inspect([str(Path.cwd().resolve())])
    print(json.dumps({'hook_trust': result, 'guidance': GUIDANCE}, ensure_ascii=True))
