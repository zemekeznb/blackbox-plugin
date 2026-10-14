"""Isolated official CLI registration tests. Not real desktop or clean-OS acceptance."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib

from verify_alpha_candidate import verify_archive, extract_verified


def verify(archive, codex):
    manifest, files = verify_archive(archive)
    with tempfile.TemporaryDirectory(prefix='blackbox-full-install-') as temp:
        root = Path(temp).resolve()
        package = root / 'package with spaces'
        extract_verified(files, package)
        home = root / 'codex'
        home.mkdir()
        config = home / 'config.toml'
        # Fabricated provider metadata; no real credentials or configuration read.
        protected = {'model_provider': 'fixture', 'model': 'fixture-model',
                     'model_providers': {'fixture': {'name': 'Fixture', 'base_url': 'https://example.invalid/v1'}}}
        config.write_text('model_provider = "fixture"\nmodel = "fixture-model"\n[model_providers.fixture]\nname = "Fixture"\nbase_url = "https://example.invalid/v1"\n', encoding='utf-8')
        state = root / 'state'
        env = os.environ.copy()
        env.update(CODEX_HOME=str(home), LOCALAPPDATA=str(root / 'local'),
                   APPDATA=str(root / 'roaming'), USERPROFILE=str(root / 'user'),
                   HOME=str(root / 'user'), BLACKBOX_STATE_DIR=str(state), PYTHONDONTWRITEBYTECODE='1')
        # Every generated directory is owned by this TemporaryDirectory, never a real state path.
        for key in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE'):
            Path(env[key]).mkdir(parents=True, exist_ok=True)
        powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        env['PSModulePath'] = str(Path(powershell).parent / 'Modules')
        results = []

        def run(name, *extra, expected=0, source=package, cli=codex):
            process = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-File', str(source / 'install.ps1'),
                                      '-Codex', cli, *extra], env=env, capture_output=True, timeout=180)
            output = process.stdout.decode('utf-8', errors='replace')
            if process.returncode != expected:
                raise RuntimeError(name + ': ' + output)
            results.append({'case': name, 'exit_code': process.returncode, 'passed': True})
            current = tomllib.loads(config.read_text(encoding='utf-8-sig'))
            if any(current.get(key) != value for key, value in protected.items()):
                raise RuntimeError('Unrelated model configuration changed')
            return output

        run('missing_cli', '-CheckOnly', expected=1, cli=str(root / 'missing.exe'))
        if state.exists():
            raise RuntimeError('Missing CLI created runtime state')
        run('check_only', '-CheckOnly')
        if state.exists() or (home / 'plugins/cache/personal/blackbox').exists():
            raise RuntimeError('CheckOnly created runtime or installed plugin')
        run('first_install')
        database = state / 'notes.sqlite3'
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        run('repeat_install')
        if hashlib.sha256(database.read_bytes()).hexdigest() != before:
            raise RuntimeError('Repeated installation changed database')
        # Prove default project authorization is empty, without generating events.
        import sqlite3
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0]:
                raise RuntimeError('Installation authorized a project')
        moved = root / 'new package'
        shutil.copytree(package, moved)
        run('source_conflict', expected=1, source=moved)
        run('explicit_source_switch', '-ReplaceSource', source=moved)
        run('switch_repeat', source=moved)
        run('graphical_runtime_setup', '-RuntimeOnly', source=moved)
        if hashlib.sha256(database.read_bytes()).hexdigest() != before:
            raise RuntimeError('Source switch/runtime setup changed database')
        cache = home / 'plugins/cache/personal/blackbox' / manifest['version']
        if not (cache / 'skills/setup/SKILL.md').is_file():
            raise RuntimeError('Missing packaged onboarding skill')
        return {'version': manifest['version'], 'archive_sha256': hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
                'cases': results, 'database_preserved': True, 'provider_configuration_preserved': True,
                'projects_enabled': 0, 'real_host_capture': 'not_tested', 'clean_windows': 'not_tested'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--codex', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output path; previous evidence is not overwritten.')
    result = verify(args.archive, args.codex)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
