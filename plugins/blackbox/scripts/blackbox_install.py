"""Register a complete local marketplace using the official CLI, never edit config."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import re
import tomllib
from copy import deepcopy

from blackbox_compat import LEGACY_PLUGIN


class InstallError(Exception):
    pass


def run_cli(executable, *args):
    try:
        result = subprocess.run([executable, 'plugin', *args], capture_output=True,
                                encoding='utf-8', errors='replace', timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError('Codex command could not finish. Check the selected executable and retry; no automatic rollback.') from None
    if result.returncode:
        # Host output can contain credentials or remote URLs. Do not echo it.
        raise InstallError('Codex plugin command failed: ' + ' '.join(args[:2]) +
                           '. Check host permissions/plugin support and retry the same package.')
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except ValueError:
        raise InstallError('Codex returned an unsupported JSON response; update the host or use its graphical installer.') from None


def same_path(a, b):
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def check_commands(executable):
    for command in (('add',), ('remove',), ('marketplace', 'add'), ('marketplace', 'remove')):
        try:
            response = subprocess.run([executable, 'plugin', *command, '--help'],
                                      capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            raise InstallError('Codex plugin capability check failed; runtime was not configured.') from None
        if response.returncode or b'--json' not in response.stdout:
            raise InstallError('This Codex does not support the required plugin registration commands. Update the desktop host or use a verified graphical installation.')


def rows(data, key):
    value = data.get(key)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise InstallError('Unsupported Codex inventory; no registration attempted.')
    return value


def plan(root, executable, replace_source=False):
    root = Path(root).resolve()
    executable = shutil.which(executable)
    if not executable or Path(executable).suffix.lower() not in ('.exe', ''):
        raise InstallError('Codex executable not found. Pass -Codex with the desktop host codex.exe path; do not choose a different account/host.')
    check_commands(executable)
    catalog = json.loads((root / '.agents/plugins/marketplace.json').read_text(encoding='utf-8-sig'))
    if catalog.get('name') != 'personal' or [p.get('name') for p in catalog.get('plugins', [])] != ['blackbox']:
        raise InstallError('Expected the complete Blackbox marketplace package.')
    markets = rows(run_cli(executable, 'marketplace', 'list', '--json'), 'marketplaces')
    inventory = run_cli(executable, 'list', '--json')
    installed = rows(inventory, 'installed')
    other = [p for p in installed if p.get('name') in ('blackbox', LEGACY_PLUGIN) and p.get('installed')
             and p.get('pluginId') not in ('blackbox@personal', LEGACY_PLUGIN + '@personal')]
    if other:
        raise InstallError('Blackbox is installed under another marketplace identity. Resolve that installation in Codex before retrying to avoid duplicate Hooks.')
    matches = [m for m in markets if m.get('name') == 'personal']
    if len(matches) > 1:
        raise InstallError('Multiple personal marketplaces found; resolve the conflict in Codex first.')
    conflict = bool(matches and not same_path(matches[0].get('root', ''), root))
    if conflict:
        if not replace_source:
            raise InstallError('personal already points to another directory. Keep using that stable directory, or explicitly pass -ReplaceSource to switch a Blackbox-only marketplace.')
        old_root = Path(matches[0]['root'])
        try:
            old = json.loads((old_root / '.agents/plugins/marketplace.json').read_text(encoding='utf-8-sig'))
            if old.get('name') != 'personal' or [p.get('name') for p in old['plugins']] not in (['blackbox'], [LEGACY_PLUGIN]):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise InstallError('Cannot safely replace this marketplace: it is missing, invalid or contains other plugins. Resolve the source in Codex manually.') from None
    return executable, matches, conflict


def preserve_disabled(home):
    """Official CLI installs enabled; preserve an explicitly disabled predecessor.

    Change only the new plugin's bool, retaining every other config byte.
    """
    path = Path(home) / 'config.toml'
    original = path.read_bytes()
    text = original.decode('utf-8')
    before = tomllib.loads(text)
    pattern = r'(?ms)(^\[plugins\."blackbox@personal"\][^\n]*\n)(.*?)(?=^\[|\Z)'
    match = re.search(pattern, text)
    if not match:
        raise InstallError('Cannot preserve disabled plugin setting; disable Blackbox in Codex before continuing.')
    body, count = re.subn(r'(?m)^(enabled\s*=\s*)true\b', r'\1false', match[2])
    if count != 1:
        raise InstallError('Cannot preserve disabled plugin setting; check Blackbox settings in Codex.')
    updated = text[:match.start(2)] + body + text[match.end(2):]
    expected = deepcopy(before)
    expected['plugins']['blackbox@personal']['enabled'] = False
    if tomllib.loads(updated) != expected or path.read_bytes() != original:
        raise InstallError('Codex configuration changed concurrently. Check plugin enabled state before retrying.')
    # Only the intended field changes; no serialization of unrelated tables.
    import tempfile
    handle, temp = tempfile.mkstemp(prefix='.blackbox-setting-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(updated.encode('utf-8'))
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def legacy_installations(installed, home):
    identity = LEGACY_PLUGIN + '@personal'
    found = [p for p in installed if p.get('pluginId') == identity and p.get('installed')]
    if found:
        return found
    # Once the new marketplace catalog is installed, some host versions omit
    # the old identity from plugin list despite retaining its config/cache.
    config = Path(home) / 'config.toml'
    cache = Path(home) / 'plugins/cache/personal' / LEGACY_PLUGIN
    if not config.is_file() or not cache.is_dir():
        return []
    settings = tomllib.loads(config.read_text(encoding='utf-8-sig')).get('plugins', {}).get(identity)
    if not isinstance(settings, dict):
        return []
    return [{'pluginId': identity, 'installed': True, 'enabled': settings.get('enabled', True)}]


def verify_payload(source, installed):
    source, installed = Path(source), Path(installed)
    for path in source.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        target = installed / path.relative_to(source)
        if not target.is_file() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
            raise InstallError('Installed payload does not match this package. Registration may be partial; retry from the same directory.')


def install(root, executable='codex', *, register=False, replace_source=False):
    root = Path(root).resolve()
    executable, matches, conflict = plan(root, executable, replace_source)
    result = {'codex': executable, 'codex_home': str(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve()),
              'registration': 'not_run', 'mcp_host': 'not_tested', 'real_hook': 'not_tested'}
    if not register:
        return result
    inventory = rows(run_cli(executable, 'list', '--json'), 'installed')
    legacy = legacy_installations(inventory, result['codex_home'])
    new = [p for p in inventory if p.get('pluginId') == 'blackbox@personal' and p.get('installed')]
    pending = Path(result['codex_home']) / 'blackbox-migration-preference.json'
    prior_off = False
    if pending.exists():
        receipt = json.loads(pending.read_text(encoding='utf-8'))
        if not isinstance(receipt, dict) or type(receipt.get('preserve_disabled')) is not bool:
            raise InstallError('Invalid pending migration preference; inspect it before retrying.')
        prior_off = receipt['preserve_disabled']
    preserve_off = bool((new and new[0].get('enabled') is False) or
                        (not new and legacy and legacy[0].get('enabled') is False) or prior_off)
    # Remove only the predecessor plugin through the host before enabling the
    # successor. Records are outside the plugin cache and are never deleted.
    if legacy:
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(json.dumps({'preserve_disabled': preserve_off}), encoding='utf-8')
        run_cli(executable, 'remove', LEGACY_PLUGIN + '@personal', '--json')
        after_remove = rows(run_cli(executable, 'list', '--json'), 'installed')
        if legacy_installations(after_remove, result['codex_home']):
            raise InstallError('Legacy plugin removal was not confirmed. New registration stopped to prevent duplicate Hooks.')
    if conflict:
        run_cli(executable, 'marketplace', 'remove', 'personal', '--json')
    if not matches or conflict:
        run_cli(executable, 'marketplace', 'add', str(root), '--json')
    added = run_cli(executable, 'add', 'blackbox@personal', '--json')
    version = json.loads((root / 'plugins/blackbox/.codex-plugin/plugin.json').read_text(encoding='utf-8-sig'))['version']
    if added.get('pluginId') != 'blackbox@personal' or added.get('version') != version or not added.get('installedPath'):
        raise InstallError('Codex did not confirm the requested plugin/version. Retry after checking its plugin directory.')
    cache = Path(added['installedPath']).resolve()
    expected = Path(result['codex_home']) / 'plugins/cache/personal/blackbox' / version
    if not same_path(cache, expected):
        raise InstallError('Codex installed into an unexpected home/cache. Check that the CLI belongs to the intended desktop account.')
    verify_payload(root / 'plugins/blackbox', cache)
    if preserve_off:
        preserve_disabled(result['codex_home'])
    installed = rows(run_cli(executable, 'list', '--json'), 'installed')
    if not any(p.get('pluginId') == 'blackbox@personal' and p.get('installed') is True
               and p.get('enabled') is (not preserve_off) and p.get('version') == version for p in installed):
        raise InstallError('Plugin files copied but not confirmed enabled. Check Codex plugin policy and retry.')
    result.update(registration='verified', version=version, installed_path=str(cache),
                  legacy_plugin_retired=bool(legacy), enabled=not preserve_off)
    pending.unlink(missing_ok=True)
    return result


def retire_legacy(executable='codex'):
    """Graphical setup runs from the installed successor, without a market root."""
    executable = shutil.which(executable)
    if not executable:
        from blackbox_codex import codex_executable
        executable = codex_executable()
    if not executable:
        raise InstallError('Cannot verify legacy plugin retirement: specify the desktop codex.exe.')
    check_commands(executable)
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve()
    plugin_root = Path(__file__).resolve().parents[1]
    version = json.loads((plugin_root / '.codex-plugin/plugin.json').read_text(encoding='utf-8-sig'))['version']
    if not same_path(plugin_root, home / 'plugins/cache/personal/blackbox' / version):
        raise InstallError('Graphical migration must run from the installed Blackbox cache. Use install.ps1 for a source/ZIP package.')
    installed = rows(run_cli(executable, 'list', '--json'), 'installed')
    if not any(p.get('pluginId') == 'blackbox@personal' and p.get('installed') for p in installed):
        raise InstallError('Install Blackbox in Codex before running graphical migration.')
    legacy = legacy_installations(installed, home)
    if legacy:
        if legacy[0].get('enabled') is False:
            preserve_disabled(home)
        run_cli(executable, 'remove', LEGACY_PLUGIN + '@personal', '--json')
        remaining = rows(run_cli(executable, 'list', '--json'), 'installed')
        if legacy_installations(remaining, home):
            raise InstallError('Legacy plugin remains installed. Resolve it in Codex before enabling recording.')
    return {'legacy_plugin_retired': bool(legacy), 'real_hook': 'not_tested'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--register', action='store_true')
    parser.add_argument('--replace-source', action='store_true')
    parser.add_argument('--retire-legacy', action='store_true')
    args = parser.parse_args()
    if args.retire_legacy and (args.root or args.register or args.replace_source):
        parser.error('--retire-legacy is only for graphical setup, without package flags')
    if not args.retire_legacy and not args.root:
        parser.error('--root is required for package installation')
    try:
        result = retire_legacy(args.codex) if args.retire_legacy else install(args.root, args.codex, register=args.register, replace_source=args.replace_source)
    except (InstallError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, InstallError) else 'Invalid package or inaccessible installation files.'}))
        return 1
    print(json.dumps({'ok': True, **result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
