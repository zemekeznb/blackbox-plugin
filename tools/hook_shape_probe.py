"""Test-only stdin trampoline: retain bounded field names/types, never field values.

Used solely by verify_live_codex.py in its isolated synthetic state directory.
The installed plugin, trust configuration and ordinary runtime are not modified.
"""
import json
import os
import re
from pathlib import Path
import subprocess
import sys


def main():
    raw = sys.stdin.buffer.read(512 * 1024 + 1)
    try:
        payload = json.loads(raw)
        known = {'session_id', 'thread_id', 'cwd', 'transcript_path', 'hook_event_name',
                 'permission_mode', 'tool_name', 'tool_input', 'tool_response', 'tool_result',
                 'tool_use_id', 'call_id', 'event_id', 'hook_event_id', 'exit_code', 'exitCode',
                 'status', 'error', 'returncode', 'success', 'output', 'stdout', 'stderr'}
        if isinstance(payload, dict) and payload.get('hook_event_name') == 'PostToolUse':
            shape = {'fields': {key: type(value).__name__ for key, value in payload.items() if key in known},
                     'unknown_field_count': len(set(payload) - known),
                     'other_field_names': [key for key in payload if key not in known
                         and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', key)]}
            for field in ('tool_response', 'tool_result'):
                if isinstance(payload.get(field), dict):
                    shape[field + '_fields'] = {key: type(value).__name__ for key, value in payload[field].items() if key in known}
            path = Path(os.environ['BLACKBOX_STATE_DIR']) / 'hook-shapes.jsonl'
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(shape) + '\n')
    except (ValueError, OSError, KeyError, RecursionError):
        pass
    return subprocess.run([sys.executable, *sys.argv[1:]], input=raw, timeout=8).returncode


if __name__ == '__main__':
    raise SystemExit(main())
