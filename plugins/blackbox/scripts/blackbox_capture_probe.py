"""Opt-in, short-lived metadata-only Hook entry diagnostics for NOTES-047."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

TOOL = 'mcp__blackbox_error_probe__run_case'
CASES = ('success', 'error', 'log_success', 'json_error', 'protocol_error')


class CaptureProbe:
    def __init__(self, directory):
        self.path = None
        self.identifier = uuid4().hex
        try:
            config = Path(directory) / 'capture-probe.json'
            if config.is_symlink() or config.stat().st_size > 2048:
                return
            value = json.loads(config.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                return
            remaining = (datetime.fromisoformat(value['expires']) - datetime.now(timezone.utc)).total_seconds()
            if (value.get('enabled') is not True or not 0 < remaining <= 900
                    or value.get('project') != str(Path.cwd().resolve())):
                return
            self.path = Path(directory) / 'capture-probe.jsonl'
            self.emit('entry')
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            self.path = None

    def emit(self, phase, **metadata):
        if self.path is None:
            return
        try:
            if self.path.is_symlink() or (self.path.exists() and self.path.stat().st_size >= 65536):
                return
            line = json.dumps({'probe': 'NOTES-047', 'invocation': self.identifier,
                               'time': datetime.now(timezone.utc).isoformat(), 'phase': phase, **metadata})
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(line + '\n')
        except OSError:
            pass  # Diagnostic failure must never change capture behavior.

    def received(self, payload):
        if not isinstance(payload, dict) or payload.get('tool_name') != TOOL:
            self.emit('received', target=False)
            return
        args = payload.get('tool_input')
        case = args.get('case') if isinstance(args, dict) else None
        case = case if isinstance(case, str) and case in CASES else 'unknown'
        response = payload.get('tool_response')
        self.emit('received', target=True, case=case,
                  fields={k: type(payload[k]).__name__ for k in
                          ('hook_event_name', 'cwd', 'session_id', 'thread_id', 'tool_name',
                           'tool_input', 'tool_response', 'error', 'tool_result') if k in payload},
                  is_error=response.get('isError') if isinstance(response, dict) and type(response.get('isError')) is bool else None)

    def outcome(self, result):
        status = result.get('status') if isinstance(result, dict) else None
        self.emit('complete', status=status if status in ('recorded', 'ignored', 'duplicate') else 'unknown')
