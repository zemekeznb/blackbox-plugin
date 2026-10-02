"""Windowless shortcut entry. Does not require Codex or call a model."""
import ctypes
import sys
from tas_notes_core import main

if __name__ == '__main__':
    # pythonw has no standard streams. CLI messages are not secret-bearing logs.
    import os
    with open(os.devnull, 'w') as sink:
        sys.stdout = sink
        sys.stderr = sink
        try:
            code = main(['open'])
        except Exception:
            code = 1
        if code:
            ctypes.windll.user32.MessageBoxW(None,
                '无法打开 TAS Notes。请让 Codex 检查本地 Python、记录目录权限及审阅服务。已有记录不会因此删除。',
                'TAS Notes', 0x10)
    raise SystemExit(code)
