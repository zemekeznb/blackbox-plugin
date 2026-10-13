"""Temporary runtime trampoline. Forward bounded stdin unchanged; no body logs.

Invoke with original tas_notes.py and arguments. Runtime configuration must be
restored after the explicitly authorized experiment. Not a default entry point.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_capture_probe import CaptureProbe


def main():
    state = Path(os.environ.get('TAS_NOTES_STATE_DIR', str(Path(os.environ['LOCALAPPDATA'])/'TAS Notes')))
    probe = CaptureProbe(state)
    raw = sys.stdin.buffer.read(512*1024+1)
    try:
        probe.received(json.loads(raw.decode('utf-8-sig')))
    except (ValueError, UnicodeError, RecursionError):
        probe.emit('parse_failed')
    child = subprocess.run([sys.executable, *sys.argv[1:]], input=raw, timeout=8)
    probe.emit('forwarded', exit_code=child.returncode)
    return child.returncode


if __name__ == '__main__':
    raise SystemExit(main())
