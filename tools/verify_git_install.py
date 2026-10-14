"""Exercise Codex Git install and packaged setup in a fresh, isolated CODEX_HOME.

Uses Git to fetch the selected repository/ref; public sources need no login. No model is
called, no project enabled, no Hook trusted, and the user's Codex is not installed.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib


def verify(source, ref, version, codex):
    codex = shutil.which(codex)
    if not codex:
        raise RuntimeError('Codex executable not found')
    with tempfile.TemporaryDirectory(prefix='blackbox-git-install-') as temp:
        root = Path(temp).resolve()
        home, state = root / 'codex', root / 'state'
        home.mkdir()
        config = home / 'config.toml'
        config.write_text('model = "fixture-no-model-call"\n', encoding='utf-8')
        env = dict(os.environ, CODEX_HOME=str(home), BLACKBOX_STATE_DIR=str(state),
                   LOCALAPPDATA=str(root / 'local'), PYTHONDONTWRITEBYTECODE='1',
                   GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
        cases = []

        def run(name, command, cwd=root):
            p = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                               encoding='utf-8', errors='replace', timeout=180)
            cases.append({'name': name, 'exit_code': p.returncode})
            if p.returncode:
                raise RuntimeError(name + ' failed; inspect the isolated CLI separately (output omitted)')
            value = json.loads(p.stdout)
            if not isinstance(value, dict):
                raise RuntimeError(name + ' returned invalid JSON')
            return value

        run('git_marketplace_add', [codex, 'plugin', 'marketplace', 'add', source, '--ref', ref, '--json'])
        added = run('plugin_add', [codex, 'plugin', 'add', 'blackbox@personal', '--json'])
        cache = Path(added.get('installedPath', '')).resolve()
        expected = home / 'plugins/cache/personal/blackbox' / version
        if cache != expected or added.get('pluginId') != 'blackbox@personal' or added.get('version') != version:
            raise RuntimeError('Wrong installed cache, identity or version')
        if state.exists():
            raise RuntimeError('Plugin installation unexpectedly initialized recording state')
        scripts = cache / 'scripts'

        def python(name, script, *args):
            return run(name, [sys.executable, '-B', '-E', '-s', str(scripts / script), *map(str, args)])

        checked = python('installed_preflight', 'blackbox_preflight.py', '--plugin-root', cache, '--json')
        if not checked.get('ok') or Path(checked['state_directory']).resolve() != state:
            raise RuntimeError('Installed preflight failed or selected another state directory')
        python('installed_identity_check', 'blackbox_install.py', '--retire-legacy', '--codex', codex)
        initialized = python('configure_runtime', 'blackbox.py', '--state-dir', state, 'configure-runtime')
        if initialized.get('runtime_configured') is not True:
            raise RuntimeError('Runtime was not configured')
        database = state / 'notes.sqlite3'
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        python('repeat_configure', 'blackbox.py', '--state-dir', state, 'configure-runtime')
        run('repeat_plugin_add', [codex, 'plugin', 'add', 'blackbox@personal', '--json'])
        if hashlib.sha256(database.read_bytes()).hexdigest() != before:
            raise RuntimeError('Repeated setup/install changed database bytes')
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Invalid database')
            if any(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('projects', 'events')):
                raise RuntimeError('Installation enabled recording or saved events')
        hooks = python('hook_trust_check', 'blackbox_hooks.py')
        trust = list(hooks.get('hook_trust', {}).values())
        if not trust or any(value != 'hooks_untrusted' for value in trust):
            raise RuntimeError('Fresh isolated Hooks were not reported untrusted')
        saved = tomllib.loads(config.read_text(encoding='utf-8-sig'))
        if saved.get('model') != 'fixture-no-model-call':
            raise RuntimeError('Setup changed protected model setting')
        if saved.get('hooks', {}).get('state'):
            raise RuntimeError('Setup wrote Hook trust')
        if not (cache / 'skills/setup/SKILL.md').is_file():
            raise RuntimeError('Missing onboarding skill')
        return {'version': version, 'ref': ref, 'source': source, 'cases': cases,
                'isolated_home': True, 'installed_cache_preflight': 'passed',
                'runtime_configuration': 'passed', 'repeat_preserved_database': True,
                'projects_enabled': 0, 'events': 0, 'hook_trust': 'hooks_untrusted',
                'model_configuration_preserved': True, 'real_host_capture': 'not_tested',
                'desktop_setup_ui': 'not_tested', 'synthetic_hooks_used': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='zemekeznb/blackbox-plugin')
    parser.add_argument('--ref', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new evidence file; existing evidence is not overwritten')
    result = verify(args.source, args.ref, args.version, args.codex)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
