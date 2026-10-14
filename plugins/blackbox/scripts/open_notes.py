"""Windowless shortcut entry. Does not require Codex or call a model."""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone


def parse_options(argv=None):
    import argparse
    from blackbox_launcher import resolve_state_directory
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path)
    args = parser.parse_args(argv)
    args.state_dir = resolve_state_directory(args.state_dir)
    return args


def mark(stage, directory):
    """Bounded diagnostic: no URLs, tokens, records or exception messages."""
    try:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'open-status.txt').write_text(
            datetime.now(timezone.utc).isoformat() + ' ' + stage, encoding='utf-8')
    except OSError:
        pass

if __name__ == '__main__':
    # pythonw has no standard streams. CLI messages are not secret-bearing logs.
    args = parse_options()
    mark('entry-started', args.state_dir)
    with open(os.devnull, 'w') as sink:
        sys.stdout = sink
        sys.stderr = sink
        try:
            from blackbox_core import main
            mark('core-loaded', args.state_dir)
            code = main(['--state-dir', str(args.state_dir), 'open'])
            mark('browser-requested' if not code else 'open-failed', args.state_dir)
        except Exception as error:
            mark('error-' + type(error).__name__, args.state_dir)
            code = 1
        if code:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                '无法打开 Blackbox Plugin。请让 Codex 检查本地 Python、记录目录权限及审阅服务。已有记录不会因此删除。',
                'Blackbox Plugin', 0x10)
    raise SystemExit(code)
