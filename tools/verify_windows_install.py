"""Exercise a trusted Alpha ZIP's installer in isolated Windows child environments.

This is NOT a clean OS, new Windows account, Codex registration, or real Hook
acceptance test. No execution-policy override, service, or shortcut is requested.
Only synthetic fixtures under a newly created temporary directory are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from verify_alpha_candidate import extract_verified, verify_archive


class AcceptanceError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def decode_output(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    for encoding in ('utf-8', 'mbcs'):
        try:
            return value.decode(encoding)
        except (UnicodeError, LookupError):
            pass
    return value.decode('utf-8', errors='replace')


def database_snapshot(state):
    """Read every table without opening or migrating a writable Store."""
    database = state / 'notes.sqlite3'
    require(database.is_file() and not database.is_symlink(), 'Expected a regular fixture database.')
    before = digest(database)
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        tables = {}
        names = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        for (name,) in names:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = connection.execute('SELECT * FROM ' + quoted).fetchall()
            encoded = sorted(json.dumps(row, ensure_ascii=True, separators=(',', ':')) for row in rows)
            tables[name] = {'count': len(rows), 'rows_sha256': hashlib.sha256(
                ('\n'.join(encoded)).encode('ascii')).hexdigest()}
        projects = connection.execute('SELECT path, enabled FROM projects ORDER BY path').fetchall()
        schema = connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        result = {'sha256': before, 'tables': tables,
                  'projects': [{'path': path, 'enabled': bool(enabled)} for path, enabled in projects],
                  'schema_sha256': hashlib.sha256(json.dumps(schema, ensure_ascii=True).encode('ascii')).hexdigest(),
                  'user_version': connection.execute('PRAGMA user_version').fetchone()[0]}
    finally:
        connection.close()
    require(digest(database) == before, 'Read-only database inspection changed bytes.')
    return result


def state_files_snapshot(state):
    result = {}
    for path in sorted(state.rglob('*')):
        require(not path.is_symlink(), 'Unexpected link in an isolated state fixture.')
        relative = str(path.relative_to(state))
        result[relative] = ({'type': 'directory'} if path.is_dir() else
                            {'type': 'file', 'size': path.stat().st_size, 'sha256': digest(path)})
    return result


class Harness:
    def __init__(self, root, package, powershell, system_path):
        self.root, self.package = root, package
        self.powershell, self.system_path = powershell, system_path
        self.entry = package / 'plugins/blackbox/scripts/blackbox.py'

    def fixture(self, name, with_python=True):
        root = self.root / name
        profile = root / '隔离 用户'
        appdata, roaming, temporary = profile / 'AppData/Local', profile / 'AppData/Roaming', root / '临时 Temp'
        for path in (appdata, roaming, temporary, profile / 'Desktop'):
            path.mkdir(parents=True)
        environment = dict(os.environ)
        for name_to_remove in ('BLACKBOX_STATE_DIR', 'TAS_NOTES_STATE_DIR', 'PYTHONHOME', 'PYTHONPATH'):
            environment.pop(name_to_remove, None)
        environment.update(LOCALAPPDATA=str(appdata), APPDATA=str(roaming),
                           USERPROFILE=str(profile), CODEX_HOME=str(root / '隔离 Codex'),
                           TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE='1',
                           PYTHONNOUSERSITE='1', PATH=self.system_path,
                           PSModulePath=str(self.powershell.parent / 'Modules'))
        if with_python:
            environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + self.system_path
        return {'root': root, 'environment': environment, 'default_state': appdata / 'Blackbox',
                'custom_state': root / '指定 状态', 'commands': []}

    def run(self, fixture, arguments, *, input_text=None, expected=0, timeout=45):
        record = {'command': [str(argument) for argument in arguments], 'exit_code': None}
        fixture['commands'].append(record)
        try:
            completed = subprocess.run(record['command'], cwd=self.package, env=fixture['environment'],
                                       input=input_text.encode('utf-8') if input_text is not None else None,
                                       capture_output=True, timeout=timeout,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as error:
            record.update(stdout=decode_output(error.stdout), stderr=decode_output(error.stderr),
                          error='TimeoutExpired', timeout_seconds=timeout)
            raise AcceptanceError('Child command timed out; see recorded output.') from error
        except OSError as error:
            record.update(stdout='', stderr='', error=type(error).__name__, error_message=str(error))
            raise AcceptanceError('Child command could not start; see recorded error.') from error
        record.update(exit_code=completed.returncode, stdout=decode_output(completed.stdout),
                      stderr=decode_output(completed.stderr))
        require(completed.returncode == expected,
                f'Expected exit {expected}, received {completed.returncode}; see recorded child output.')
        return record

    def install(self, fixture, *, state=None, python=None, check_only=False, expected=0):
        command = [self.powershell, '-NoProfile', '-NonInteractive', '-File', self.package / 'install.ps1', '-RuntimeOnly']
        if python is not None:
            command += ['-Python', python]
        if state is not None:
            command += ['-StateDir', state]
        if check_only:
            command.append('-CheckOnly')
        return self.run(fixture, command, expected=expected)

    def cli(self, fixture, state, *arguments, payload=None, expected=0):
        return self.run(fixture, [sys.executable, '-B', str(self.entry), '--state-dir', state, *arguments],
                        input_text=json.dumps(payload) if payload is not None else None, expected=expected)

    @staticmethod
    def boundaries(fixture, *, absent_state=False, allow_sentinel=False):
        root = fixture['root']
        data = {'codex_directory_absent': not Path(fixture['environment']['CODEX_HOME']).exists(),
                'shortcut_files': [str(path.relative_to(root)) for path in root.rglob('*.lnk')],
                'reader_runtime_files': [str(path.relative_to(root)) for path in root.rglob('review-*')],
                'default_state_exists': fixture['default_state'].exists(),
                'custom_state_exists': fixture['custom_state'].exists()}
        require(data['codex_directory_absent'], 'Installer wrote the isolated Codex directory.')
        require(not data['shortcut_files'], 'Installer created a shortcut in the isolated fixture.')
        expected_registry = str((fixture['custom_state'] / 'review-instance.json').relative_to(root))
        require(data['reader_runtime_files'] == ([expected_registry] if allow_sentinel else []),
                'Unexpected reading-service runtime files.')
        if absent_state:
            require(not data['default_state_exists'] and not data['custom_state_exists'],
                    'State was created on a check or failed installation.')
        return data

    @staticmethod
    def empty_state(state):
        result = database_snapshot(state)
        require(result['user_version'] == 1, 'Unexpected fixture database schema version.')
        require(all(value['count'] == 0 for value in result['tables'].values()), 'Fresh database contains records.')
        require((state / 'runtime.cmd').is_file(), 'Runtime bootstrap was not created.')
        result['runtime_sha256'] = digest(state / 'runtime.cmd')
        return result


def exercise(harness, receipt):
    def scenario(name, operation, *, with_python=True):
        result = {'name': name, 'status': 'failed', 'host_capture': 'not_tested'}
        receipt['scenarios'].append(result)
        fixture = None
        try:
            fixture = harness.fixture(name, with_python)
            result['fixture_root'] = str(fixture['root'])
            result['child_environment'] = {key: fixture['environment'][key] for key in (
                'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'CODEX_HOME', 'TEMP', 'TMP',
                'PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE', 'PATH', 'PSModulePath')}
            result['commands'] = fixture['commands']
            operation(fixture, result)
            result['status'] = 'passed'
        except Exception as error:
            result['error'] = {'type': type(error).__name__, 'message': str(error)}
            if fixture is not None:
                result['state_paths_after_failure'] = {
                    'default_exists': fixture['default_state'].exists(),
                    'custom_exists': fixture['custom_state'].exists()}

    def check_only(fixture, result):
        command = harness.install(fixture, state=fixture['custom_state'], python=sys.executable, check_only=True)
        require('release_manifest_verified' in command['stdout'], 'Extracted release was not verified by preflight.')
        require('Check only:' in command['stdout'], 'Check-only confirmation is missing.')
        result['boundaries'] = harness.boundaries(fixture, absent_state=True)

    def first_default(fixture, result):
        command = harness.install(fixture)
        require('pending verification' in command['stdout'], 'Installer did not distinguish host verification.')
        result['database'] = harness.empty_state(fixture['default_state'])
        result['boundaries'] = harness.boundaries(fixture)
        require(not fixture['custom_state'].exists(), 'Default install touched custom state.')

    def first_custom(fixture, result):
        command = harness.install(fixture, state=fixture['custom_state'], python=sys.executable)
        require('BLACKBOX_STATE_DIR' in command['stdout'], 'Custom state inheritance warning is missing.')
        result['database'] = harness.empty_state(fixture['custom_state'])
        result['boundaries'] = harness.boundaries(fixture)
        require(not fixture['default_state'].exists(), 'Custom install touched default state.')

    def missing_default(fixture, result):
        command = harness.install(fixture, expected=1)
        require('Install Python 3.11+' in command['stdout'], 'Missing Python action is not explained.')
        require('Traceback' not in command['stdout'] + command['stderr'], 'Missing Python produced a traceback.')
        result['boundaries'] = harness.boundaries(fixture, absent_state=True)

    def missing_mcp(fixture, result):
        command = harness.install(fixture, python=sys.executable, expected=1)
        require('MCP requires python on PATH' in command['stdout'], 'Missing MCP PATH Python was not identified.')
        require('Traceback' not in command['stdout'] + command['stderr'], 'Missing MCP Python produced a traceback.')
        result['boundaries'] = harness.boundaries(fixture, absent_state=True)

    def repeat_empty(fixture, result):
        harness.install(fixture)
        before = harness.empty_state(fixture['default_state'])
        harness.install(fixture)
        after = harness.empty_state(fixture['default_state'])
        require(before == after, 'Repeating installation changed empty-state database or runtime bytes.')
        result.update(before=before, after=after, boundaries=harness.boundaries(fixture))

    def payload(project, event_id):
        return {'hook_event_name': 'PostToolUse', 'cwd': str(project),
                'session_id': 'synthetic-windows-install', 'event_id': event_id,
                'tool_name': 'shell', 'tool_input': {'command': 'synthetic fixture only'},
                'tool_response': 'RuntimeError: synthetic installer acceptance', 'exit_code': 1}

    def repeat_populated(fixture, result):
        state = fixture['custom_state']
        harness.install(fixture, state=state, python=sys.executable)
        enabled, disabled = fixture['root'] / 'synthetic-enabled-project', fixture['root'] / 'synthetic-disabled-project'
        enabled.mkdir()
        disabled.mkdir()
        harness.cli(fixture, state, 'enable', enabled, '--accept-capture-scope')
        harness.cli(fixture, state, 'enable', disabled, '--accept-capture-scope')
        harness.cli(fixture, state, 'disable', disabled)
        # Seed synthetic history directly: current production Hook requires a
        # real host-confirmed session and must reject this invented identity.
        seed = ('import sys,json; sys.path.insert(0,sys.argv[1]); '
                'from blackbox_core import Store; '
                'Store(sys.argv[2]).record(json.loads(sys.stdin.read()))')
        harness.run(fixture, [sys.executable, '-B', '-c', seed,
                             harness.package / 'plugins/blackbox/scripts', state],
                    input_text=json.dumps(payload(enabled, 'recorded-synthetic-fixture')))
        # Legacy editing is intentionally unavailable in the current CLI. These
        # two explicitly synthetic historical rows test reinstall preservation;
        # they are fixtures, not a restored or newly produced user archive.
        connection = sqlite3.connect(state / 'notes.sqlite3')
        try:
            investigation, source_seq = connection.execute(
                'SELECT investigation,seq FROM events ORDER BY seq').fetchone()
            connection.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?)',
                ('synthetic-legacy-revision', investigation, 1, source_seq,
                 'Synthetic historical title', 'Synthetic historical manual body.', '2026-10-11T00:00:00Z'))
            connection.execute('INSERT INTO summaries VALUES (?,?,?,?,?,?)',
                ('synthetic-legacy-summary', investigation, source_seq, 'synthetic-fingerprint',
                 '{"synthetic":"historical summary fixture"}', '2026-10-11T00:00:00Z'))
            connection.commit()
        finally:
            connection.close()
        result['synthetic_legacy_rows_created_via_sql'] = {'revisions': 1, 'summaries': 1}
        registry = state / 'review-instance.json'
        registry.write_text('{"synthetic_sentinel":"not a running reader; preserve exactly"}', encoding='utf-8')
        sentinel_before = digest(registry)
        before = database_snapshot(state)
        require(before['tables']['events']['count'] == 1, 'Synthetic fixture was not recorded exactly once.')
        require(sorted(project['enabled'] for project in before['projects']) == [False, True],
                'Fixture did not establish enabled and disabled project choices.')
        harness.install(fixture, state=state, python=sys.executable)
        after = database_snapshot(state)
        require(before == after, 'Repeating installation changed fixture data or project choices.')
        require(digest(registry) == sentinel_before, 'Repeating installation altered the synthetic service sentinel.')
        result.update(before=before, after=after, synthetic_service_sentinel_preserved=True,
                      real_running_service_preservation='not_tested', synthetic_hook_input=True,
                      boundaries=harness.boundaries(fixture, allow_sentinel=True))

    def default_no_capture(fixture, result):
        harness.install(fixture)
        state = fixture['default_state']
        before = harness.empty_state(state)
        files_before = state_files_snapshot(state)
        project = fixture['root'] / 'synthetic-unapproved-project'
        project.mkdir()
        hook = harness.cli(fixture, state, 'hook', payload=payload(project, 'must-not-be-recorded'))
        require(not hook['stdout'] and not hook['stderr'], 'Unapproved Hook produced unexpected output or an error.')
        after = harness.empty_state(state)
        files_after = state_files_snapshot(state)
        require(before == after, 'An unapproved synthetic project changed the database.')
        require(files_before == files_after, 'Unapproved Hook changed state files or created failure diagnostics.')
        require(not any(Path(name).name in ('last-error.json', 'capture-unknown.json') for name in files_after),
                'Unapproved Hook created failure diagnostics.')
        result.update(before=before, after=after, synthetic_hook_input=True,
                      state_files_before=files_before, state_files_after=files_after,
                      boundaries=harness.boundaries(fixture))

    scenario('check_only', check_only)
    scenario('first_install_default', first_default)
    scenario('first_install_custom', first_custom)
    scenario('missing_default_python', missing_default, with_python=False)
    scenario('missing_mcp_path_python', missing_mcp, with_python=False)
    scenario('repeat_empty_install', repeat_empty)
    scenario('repeat_populated_install', repeat_populated)
    scenario('default_no_capture', default_no_capture)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = {'schema_version': 1, 'started_utc': datetime.now(timezone.utc).isoformat(),
               'status': 'failed', 'test_environment': 'existing_windows_account_isolated_child_environments',
               'clean_windows_os': 'not_tested', 'new_windows_account': 'not_tested',
               'host_plugin_registration': 'not_tested', 'host_capture': 'not_tested',
               'execution_policy_overridden': False, 'services_requested': False,
               'shortcuts_requested': False, 'license_pending': None, 'publish_ready': False,
               'scenarios': [], 'cleanup': 'not_needed',
               'limitations': ['Uses the current Windows installation and existing Python interpreter.',
                               'Does not validate Windows Store aliases, a Python installation, or a newly downloaded file security zone.',
                               'Does not register a Codex plugin or deliver a real host Hook.',
                               'Reader absence is checked through isolated runtime files, not global process monitoring.',
                               'Shortcut absence is checked inside the isolated fixture; no shortcut option is requested.']}
    temporary_root = None
    temporary_base = None
    try:
        require(os.name == 'nt', 'This verifier supports Windows only.')
        archive = args.archive.resolve(strict=True)
        receipt['archive'] = str(archive)
        receipt['archive_sha256'] = digest(archive)
        manifest, files = verify_archive(archive)
        receipt.update(archive_integrity='passed', version=manifest['version'], source=manifest['source'],
                       license_pending=not bool(files.get('LICENSE', b'').strip()),
                       archive_file_count=len(files), publisher_authenticity='not_verified',
                       windows={'version': platform.version(), 'release': platform.release()},
                       python={'executable': sys.executable, 'version': platform.python_version()})
        windows = Path(os.environ['SystemRoot']).resolve(strict=True)
        system = windows / 'System32'
        powershell = system / 'WindowsPowerShell/v1.0/powershell.exe'
        require(powershell.is_file(), 'Windows PowerShell was not found in System32.')
        system_path = os.pathsep.join(str(path) for path in (system, windows, system / 'Wbem', powershell.parent))
        temporary_base = Path(tempfile.gettempdir()).resolve(strict=True)
        temporary_root = Path(tempfile.mkdtemp(prefix='blackbox-windows-install-', dir=temporary_base)).resolve(strict=True)
        require(temporary_root.parent == temporary_base, 'Temporary test root is outside the expected directory.')
        receipt['temporary_root'] = str(temporary_root)
        package = temporary_root / '候选 插件 with spaces'
        extract_verified(files, package)
        harness = Harness(temporary_root, package, powershell, system_path)
        policy_fixture = harness.fixture('policy_inspection')
        receipt['execution_policy_commands'] = policy_fixture['commands']
        policy = harness.run(policy_fixture, [powershell, '-NoProfile', '-NonInteractive', '-Command',
            "@{effective=(Get-ExecutionPolicy).ToString(); scopes=@(Get-ExecutionPolicy -List | ForEach-Object { "
            "@{scope=$_.Scope.ToString(); policy=$_.ExecutionPolicy.ToString()} })} | ConvertTo-Json -Depth 4"])
        receipt['execution_policy_check'] = policy
        receipt['execution_policy'] = json.loads(policy['stdout'].lstrip('\ufeff'))
        exercise(harness, receipt)
        receipt['passed'] = sum(item['status'] == 'passed' for item in receipt['scenarios'])
        receipt['failed'] = sum(item['status'] != 'passed' for item in receipt['scenarios'])
        receipt['status'] = 'passed' if receipt['passed'] == 8 and receipt['failed'] == 0 else 'failed'
    except Exception as error:
        receipt['error'] = {'type': type(error).__name__, 'message': str(error)}
    finally:
        if temporary_root is not None:
            try:
                resolved = temporary_root.resolve(strict=True)
                require(resolved == temporary_root and resolved.parent == temporary_base
                        and resolved.name.startswith('blackbox-windows-install-') and not temporary_root.is_symlink(),
                        'Refusing cleanup outside the exact verified temporary test root.')
                shutil.rmtree(resolved)
                receipt['cleanup'] = 'passed'
            except Exception as error:
                receipt['cleanup'] = 'failed'
                receipt['cleanup_error'] = {'type': type(error).__name__, 'message': str(error)}
                receipt['status'] = 'failed'
        receipt['finished_utc'] = datetime.now(timezone.utc).isoformat()
    output = args.output.resolve()
    # Never silently overwrite a previous acceptance receipt.
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    except OSError as error:
        print(json.dumps({'status': 'failed', 'receipt_write_error': str(error), 'receipt': receipt}, ensure_ascii=True))
        return 2
    print(json.dumps({'status': receipt['status'], 'passed': receipt.get('passed', 0),
                      'failed': receipt.get('failed', 0), 'cleanup': receipt['cleanup'],
                      'output': str(output), 'clean_windows_os': 'not_tested', 'publish_ready': False}, ensure_ascii=True))
    return 0 if receipt['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
