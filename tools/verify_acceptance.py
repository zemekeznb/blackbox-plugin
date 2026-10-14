"""Explicit installed-plugin acceptance: real host tools, isolated local data.

Requires --run. Uses existing host defaults/trust, never injects Hook payloads.
Raw events and reports stay in a new temporary directory outside the repository.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile


SIGNATURES = {
    'type': ('BLACKBOX_ACCEPTANCE_TYPE', 'Traceback (most recent call last):',
             'TypeError: controlled acceptance failure'),
    'module': ('BLACKBOX_ACCEPTANCE_MODULE', 'Traceback (most recent call last):',
               "ModuleNotFoundError: No module named 'blackbox_acceptance_nonexistent_module'"),
}


def output_matches(case, output):
    if not isinstance(output, str):
        return False
    return not output.strip() if case == 'silent' else all(s in output for s in SIGNATURES[case])


def case_evidence(case, script, command, host, output, metadata, content):
    """Do not equate any nonzero exit with execution of the intended fixture."""
    args = content.get('tool_input', {})
    operation = args.get('command', args.get('cmd')) if isinstance(args, dict) else None
    host_command = metadata.get('command')
    if isinstance(host_command, list):
        host_command = ' '.join(str(part) for part in host_command)
    path_seen = (isinstance(host_command, str)
                 and str(script).replace('\\', '/').lower() in host_command.replace('\\', '/').lower())
    return {
        'host_fixture_path': bool(path_seen),
        'host_expected_exit': host.get('status') == 'failed' and host.get('exit_code') == (7 if case == 'silent' else 1),
        'host_expected_output': output_matches(case, output),
        'hook_expected_command': content.get('tool_name') == 'Bash' and operation == command,
        'hook_expected_output': output_matches(case, content.get('tool_response')),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--plugin-root', type=Path, required=True)
    args = parser.parse_args()
    plugin = args.plugin_root.resolve(strict=True)
    sys.path.insert(0, str(plugin / 'scripts'))
    from blackbox_core import Store
    from blackbox_launcher import build_id, release_version
    import blackbox_anomalies as anomalies
    from verify_live_codex import Rpc

    root = Path(tempfile.mkdtemp(prefix='blackbox-acceptance-')).resolve()
    project, state = root / 'test project', root / 'state'
    project.mkdir()
    fixtures = {'type': "import sys\nprint('BLACKBOX_ACCEPTANCE_TYPE', file=sys.stderr, flush=True)\nraise TypeError('controlled acceptance failure')\n",
                'module': "import sys\nprint('BLACKBOX_ACCEPTANCE_MODULE', file=sys.stderr, flush=True)\nimport blackbox_acceptance_nonexistent_module\n",
                'silent': 'raise SystemExit(7)\n'}
    controls = {}
    for name, source in fixtures.items():
        (project / (name + '.py')).write_text(source, encoding='utf-8')
        result = subprocess.run([sys.executable, name + '.py'], cwd=project,
                                capture_output=True, timeout=10)
        controls[name] = {'exit_code': result.returncode,
                          'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr),
                          'expected_output': output_matches(name, (result.stdout + result.stderr).decode('utf-8', errors='replace'))}
    cli = plugin / 'scripts/blackbox.py'
    subprocess.run([sys.executable, str(cli), '--state-dir', str(state), 'configure-runtime'],
                   check=True, capture_output=True)
    store = Store(state)
    store.set_project(str(project), True)
    report = {'version': release_version(plugin), 'build': build_id(plugin),
              'host': subprocess.check_output(['codex', '--version'], text=True).strip(),
              'hooks_feature_override': False, 'independent': controls,
              'rounds': [], 'checks': {
                  'independent_fixtures_valid': all(c['exit_code'] == (7 if name == 'silent' else 1)
                                                    and c['expected_output'] for name, c in controls.items())}}
    print(json.dumps({'test_root': str(root)}, ensure_ascii=True), flush=True)
    rpc = None
    try:
        rpc = Rpc(dict(os.environ, BLACKBOX_STATE_DIR=str(state)), enable_hooks=False,
                  capture_synthetic_output=True)
        rpc.request('initialize', {'clientInfo': {'name': 'blackbox-acceptance', 'version': '0.1'},
                                  'capabilities': {'experimentalApi': True}})
        rpc.send({'method': 'initialized'})
        hooks = rpc.request('hooks/list', {'cwds': [str(project)]})
        selected = [h for entry in hooks['data'] for h in entry.get('hooks', [])
                    if h.get('pluginId') == 'blackbox@personal']
        report['hooks_config'] = [{k: h.get(k) for k in ('eventName','trustStatus','enabled','timeoutSec')}
                                  for h in selected]
        if len(selected) != 8 or any(h.get('trustStatus') != 'trusted' or not h.get('enabled') for h in selected):
            raise RuntimeError('normal installed hook trust/enablement is required')
        if any(str(plugin).replace('\\','/').lower() not in h.get('command','').replace('\\','/').lower()
               for h in selected):
            raise RuntimeError('host is not using the selected installed plugin')
        thread = rpc.request('thread/start', {'cwd': str(project), 'ephemeral': True,
                             'sandbox': 'read-only', 'approvalPolicy': 'never'})['thread']['id']
        def command(name):
            return '& "' + sys.executable + '" "' + str(project / (name + '.py')) + '"; exit $LASTEXITCODE'
        fixed_parameters = ('使用执行命令工具；完整命令逐字照抄，不加包装、不改引号。'
                            '每次固定工作目录为 ' + json.dumps(str(project)) + '。'
                            '若工具有 yield_time_ms/max_output_tokens/login 参数，分别固定为 10000/2000/false；'
                            '若使用 timeout_ms 参数，固定为 10000。其他可选参数一律不传。'
                            '本回合和之后回合相同命令的全部工具参数必须逐字相同。')
        rounds = [(['type','type'], '请执行下面同一命令两次，必须是两次独立工具调用，完整工具参数保持相同。'),
                  (['type'], '这是新的用户回合。请只执行下面的命令一次。'),
                  (['module','silent'], '请按顺序执行下面两条命令，每条使用一次独立工具调用。')]
        for cases, instruction in rounds:
            before = max((r['source_seq'] for r in store.listing()), default=0)
            begin_commands = len(rpc.commands)
            begin_hooks = len(rpc.hooks)
            prompt = ('隔离安装验收。' + instruction + '\n' + '\n'.join(command(case) for case in cases)
                      + '\n' + fixed_parameters
                      + '\n错误是预期测试结果。不要修复，不要额外重试，不要读取其他文件，'
                      + '不要联网、调用其他 Agent、或申请提权。保留工具输出后简短结束。')
            status = rpc.turn(thread, prompt)
            # The host can report the turn before the final hook notification.
            rpc.until(lambda: any(h['eventName'] == 'stop' for h in rpc.hooks[begin_hooks:]), timeout=12)
            events = [e for r in store.listing() for e in store.detail(r['id'])['events']
                      if e['seq'] > before and e['kind'] == 'PostToolUse']
            evidence = []
            for index, case in enumerate(cases):
                offset = begin_commands + index
                if index >= len(events) or offset >= len(rpc.commands):
                    evidence.append({'case': case, 'matched': False, 'reason': 'missing_host_command_or_hook'})
                    continue
                facts = case_evidence(case, project / (case + '.py'), command(case),
                                      rpc.commands[offset], rpc.command_diagnostics[offset],
                                      rpc.command_metadata[offset], json.loads(events[index]['content']))
                evidence.append({'case': case, 'matched': all(facts.values()), 'checks': facts})
            report['rounds'].append({'cases': cases, 'turn_status': status,
                 'host_commands': rpc.commands[begin_commands:],
                 'case_evidence': evidence,
                 'events': [{'seq': e['seq'], 'tool': json.loads(e['content']).get('tool_name'),
                             'fields': sorted(json.loads(e['content'])),
                             'signal': (anomalies.detect(json.loads(e['content'])) or {}).get('title')}
                            for e in events]})
            if any('setup refresh had errors' in output
                   for output in rpc.command_diagnostics[begin_commands:]):
                report['blocker'] = 'host_sandbox_setup_refresh_failed_before_command_start'
                break
        records = store.listing()
        events = [e for row in records for e in store.detail(row['id'])['events']]
        tools = [e for e in events if e['kind'] == 'PostToolUse']
        typed = [e for e in tools if (anomalies.detect(json.loads(e['content'])) or {}).get('title') == '工具返回 TypeError']
        checks = report['checks']
        checks['single_session'] = len(records) == 1
        checks['five_calls_five_events'] = len(tools) == 5 and len(rpc.commands) == 5
        checks['turns_completed'] = len(report['rounds']) == 3 and all(
            r['turn_status'] == 'completed' for r in report['rounds'])
        checks['fixture_commands_started'] = len(rpc.commands) == 5 and all(
            e['matched'] for r in report['rounds'] for e in r['case_evidence'])
        checks['per_round_delivery_matches'] = len(report['rounds']) == 3 and all(
            len(r['cases']) == len(r['host_commands']) == len(r['events']) for r in report['rounds'])
        checks['three_type_errors_retained'] = len(typed) == 3
        checks['module_error_detected'] = any((anomalies.detect(json.loads(e['content'])) or {}).get('title')
                                            == '工具返回 ModuleNotFoundError' for e in tools)
        checks['identical_complete_type_inputs'] = False
        checks['same_round_repeat_linked'] = False
        checks['cross_round_boundary_captured'] = False
        checks['cross_round_not_linked'] = False
        if len(typed) == 3:
            keys = [anomalies.target_key(json.loads(e['content'])) for e in typed]
            checks['identical_complete_type_inputs'] = bool(keys[0]) and len(set(keys)) == 1
            first, second, third = [anomalies.detail(store, e['seq']) for e in typed]
            report['type_error_links'] = [[e['seq'] for e in item['events']] for item in (first,second,third)]
            checks['same_round_repeat_linked'] = [e['seq'] for e in first['events']] == [typed[0]['seq'],typed[1]['seq']]
            checks['cross_round_not_linked'] = len(second['events']) == 1
            checks['cross_round_boundary_captured'] = any(
                typed[1]['seq'] < e['seq'] < typed[2]['seq'] and e['kind'] in ('Stop', 'UserPromptSubmit')
                for e in events)
        listing = anomalies.listing(store)
        checks['four_anomalies'] = len(listing['items']) == 4
        report['list_snapshot'] = listing['snapshot_seq']
        if listing['items']:
            detail = anomalies.detail(store, listing['items'][0]['id'], listing['snapshot_seq'])
            frozen = detail['markdown'].encode('utf-8')
            (root / 'expected-export.md').write_bytes(frozen)
            report['export'] = {'id': detail['id'], 'through': detail['source_seq'],
                                'rule': detail['rule_version'],
                                'sha256': hashlib.sha256(frozen).hexdigest()}
        silent_round = report['rounds'][-1]
        silent = tools[-1] if tools else None
        report['silent_failure_gap_observed'] = bool(
            silent_round['cases'] == ['module', 'silent'] and len(silent_round['case_evidence']) == 2
            and silent_round['case_evidence'][1]['matched'] and silent
            and not anomalies.detect(json.loads(silent['content'])))
        checks['silent_failure_contract_observed'] = report['silent_failure_gap_observed']
        before = [(r['id'],r['source_seq']) for r in store.listing()]
        store.set_project(str(project), False)
        begin_commands, begin_hooks = len(rpc.commands), len(rpc.hooks)
        report['disabled_turn'] = rpc.turn(thread,
            '停用采集验证。只执行一次命令 Write-Output BLACKBOX_DISABLED_TOOL_OK。'
            + fixed_parameters + '不要调用其他工具、读取文件、联网或申请提权。然后简短结束。')
        rpc.unsubscribe(thread)
        disabled_commands = rpc.commands[begin_commands:]
        checks['disabled_tool_executed'] = (report['disabled_turn'] == 'completed'
            and len(disabled_commands) == 1 and disabled_commands[0]['exit_code'] == 0
            and disabled_commands[0]['status'] == 'completed'
            and rpc.command_diagnostics[begin_commands].strip() == 'BLACKBOX_DISABLED_TOOL_OK')
        checks['disabled_posttool_hook_completed'] = any(
            h['eventName'] == 'postToolUse' and h['status'] == 'completed' for h in rpc.hooks[begin_hooks:])
        checks['disabled_no_append'] = before == [(r['id'],r['source_seq']) for r in store.listing()]
    finally:
        if rpc:
            rpc.close()
        store.set_project(str(project), False)
        report['hooks'] = rpc.hooks if rpc else []
        durations = [h['durationMs'] for h in report['hooks'] if isinstance(h.get('durationMs'),(int,float))]
        report['hook_duration_ms'] = {'count':len(durations),'median':statistics.median(durations),
                                      'max':max(durations)} if durations else None
        report['checks']['hook_completions_successful'] = bool(report['hooks']) and all(h['status']=='completed' for h in report['hooks'])
        report['checks']['project_disabled'] = not store.status()['projects'][0]['enabled']
        (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=True,indent=2),flush=True)
        print('Report: '+str(root/'report.json'),flush=True)
    return 0 if report['checks'] and all(report['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
