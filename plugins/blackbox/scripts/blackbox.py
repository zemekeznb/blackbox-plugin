"""Blackbox Plugin local entry point; Python standard library only."""
import sys
from pathlib import Path

def main(argv=None):
    try:
        # The configured runtime starts in isolated mode, without importing from
        # cwd. Access can fail before core.main exists, so handle startup here.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from blackbox_core import main as run
    except (OSError, ImportError) as error:
        names = {'OSError', 'PermissionError', 'FileNotFoundError', 'NotADirectoryError',
                 'IsADirectoryError', 'ImportError', 'ModuleNotFoundError'}
        name = type(error).__name__
        if name not in names:
            name = 'OSError' if isinstance(error, OSError) else 'ImportError'
        fields = [name, 'stage=import_installed_modules']
        for key in ('errno', 'winerror'):
            value = getattr(error, key, None)
            if type(value) is int and -2147483648 <= value <= 4294967295:
                fields.append(f'{key}={value}')
        print('Blackbox Plugin: ' + '; '.join(fields) + '; operation not completed.', file=sys.stderr)
        # Preserve a real startup failure here. The Hook's outer launch.cmd
        # records a bounded failure marker and remains fail-open for the host.
        return 1
    return run(argv)

if __name__ == '__main__':
    raise SystemExit(main())
