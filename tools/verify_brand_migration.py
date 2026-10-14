"""Upgrade a trusted legacy ZIP to Blackbox inside isolated Windows directories."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

from verify_alpha_candidate import verify_archive, extract_verified


def verify(legacy_zip, current_zip, codex, *, disabled=False, graphical=False):
    old_manifest, old_files = verify_archive(legacy_zip)
    manifest, files = verify_archive(current_zip)
    with tempfile.TemporaryDirectory(prefix='blackbox-brand-upgrade-') as temporary:
        root = Path(temporary)
        old_package, package = root / 'old package', root / 'new package'
        extract_verified(old_files, old_package)
        extract_verified(files, package)
        home, local = root / 'codex', root / 'local'
        home.mkdir(); local.mkdir()
        config = home / 'config.toml'
        config.write_text('model="fixture-model"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\nbase_url="https://example.invalid"\n', encoding='utf-8')
        protected = tomllib.loads(config.read_text())
        env = os.environ.copy()
        env.pop('TAS_NOTES_STATE_DIR', None); env.pop('BLACKBOX_STATE_DIR', None)
        env.update(CODEX_HOME=str(home), LOCALAPPDATA=str(local), APPDATA=str(root/'roaming'),
                   USERPROFILE=str(root/'user'), HOME=str(root/'user'), PYTHONDONTWRITEBYTECODE='1')
        powershell = str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
        env['PSModulePath'] = str(Path(powershell).parent/'Modules')
        def run(args):
            result = subprocess.run([str(a) for a in args], env=env, capture_output=True, timeout=180)
            if result.returncode:
                raise RuntimeError('Isolated upgrade command failed: ' + result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
            return result.stdout
        run([powershell,'-NoProfile','-NonInteractive','-File',old_package/'install.ps1','-RuntimeOnly'])
        run([codex,'plugin','marketplace','add',old_package,'--json'])
        run([codex,'plugin','add','tas-notes@personal','--json'])
        if disabled:
            raw = config.read_text(encoding='utf-8')
            raw = raw.replace('enabled = true', 'enabled = false')
            config.write_text(raw, encoding='utf-8')
        state = local/'TAS Notes'
        project = root/'fixture-project'; project.mkdir()
        seed = ('import sys;sys.path.insert(0,sys.argv[1]);from tas_notes_core import Store;'
                's=Store(sys.argv[2]);s.set_project(sys.argv[3],True);'
                's.record({"hook_event_name":"PostToolUse","cwd":sys.argv[3],"session_id":"fixture","tool_name":"shell","tool_response":"RuntimeError: fixture"})')
        run([sys.executable,'-B','-c',seed,old_package/'plugins/tas-notes/scripts',state,project])
        (state/'reader-selection.json').write_bytes(b'{"fixture":"preserve"}')
        before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in state.iterdir() if p.is_file()}
        if graphical:
            run([codex,'plugin','marketplace','remove','personal','--json'])
            run([codex,'plugin','marketplace','add',package,'--json'])
            run([codex,'plugin','add','blackbox@personal','--json'])
            installed = home/'plugins/cache/personal/blackbox'/manifest['version']
            run([sys.executable,installed/'scripts/blackbox_install.py','--codex',codex,'--retire-legacy'])
            run([sys.executable,installed/'scripts/blackbox.py','configure-runtime'])
        else:
            run([powershell,'-NoProfile','-NonInteractive','-File',package/'install.ps1','-Codex',codex,'-ReplaceSource'])
        migrated = local/'Blackbox'
        after = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in migrated.iterdir() if p.is_file()}
        if before != after or state.resolve() != migrated.resolve():
            raise RuntimeError('Migration did not preserve all state bytes or legacy alias')
        inventory = json.loads(run([codex,'plugin','list','--json']))['installed']
        if any(p.get('pluginId') == 'tas-notes@personal' and p.get('installed') for p in inventory):
            raise RuntimeError('Legacy plugin still installed')
        if not any(p.get('pluginId') == 'blackbox@personal' and p.get('installed') and p.get('enabled') is (not disabled) for p in inventory):
            raise RuntimeError('New plugin enabled state incorrect: ' + json.dumps({'disabled':disabled,'graphical':graphical,
                'plugins':[{k:p.get(k) for k in ('pluginId','installed','enabled')} for p in inventory],
                'settings':tomllib.loads(config.read_text(encoding='utf-8')).get('plugins')}))
        after_config = tomllib.loads(config.read_text(encoding='utf-8'))
        if any(after_config.get(k) != v for k,v in protected.items()):
            raise RuntimeError('Model settings changed')
        run([powershell,'-NoProfile','-NonInteractive','-File',package/'install.ps1','-Codex',codex])
        if before != {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in migrated.iterdir() if p.is_file()}:
            raise RuntimeError('Repeat installation changed state')
        return {'version':manifest['version'],'legacy_version':old_manifest['version'],'disabled_predecessor':disabled,'graphical_setup_commands':graphical,
                'all_state_bytes_preserved':True,'legacy_path_alias':True,'legacy_plugin_retired':True,
                'provider_config_preserved':True,'repeat_install':'passed','real_host_capture':'not_tested'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('legacy_zip',type=Path);parser.add_argument('current_zip',type=Path)
    parser.add_argument('--codex',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): parser.error('Output already exists')
    result={'sha256':hashlib.sha256(args.current_zip.read_bytes()).hexdigest(),
            'upgrades':[verify(args.legacy_zip,args.current_zip,args.codex,disabled=value,graphical=graphical)
                        for graphical in (False,True) for value in (False,True)]}
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
