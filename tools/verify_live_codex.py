"""Explicit live-host verification using synthetic inputs and an empty temporary project.

Starts real model turns only with --run. Does not install plugins or grant hook trust.
Does not read transcript files, inspect other sessions, or publish test recordings.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/tas-notes/scripts'
sys.path.insert(0, str(SCRIPTS))
from tas_notes_core import Store


class Rpc:
    def __init__(self, environment):
        self.process = subprocess.Popen(
            [shutil.which('codex') or 'codex', 'app-server', '--stdio', '-c', 'features.hooks=true'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', env=environment,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.queue = queue.Queue()
        self.responses = {}
        self.completed = {}
        self.hooks = []
        self.commands = []
        self.notifications = set()
        self.counter = 0
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            try:
                self.queue.put(json.loads(line))
            except ValueError:
                pass
        self.queue.put(None)

    def send(self, payload):
        self.process.stdin.write(json.dumps(payload) + '\n')
        self.process.stdin.flush()

    def receive(self, timeout=20):
        message = self.queue.get(timeout=timeout)
        if message is None:
            raise RuntimeError('app-server exited')
        if 'id' in message:
            if 'method' in message:
                # An interactive approval is not authorization to auto-accept it.
                raise RuntimeError('host requested interactive input: ' + message['method'])
            self.responses[message['id']] = message
        method, params = message.get('method'), message.get('params', {})
        self.notifications.add((method, params.get('threadId')))
        if method == 'item/completed' and params['item'].get('type') == 'commandExecution':
            item = params['item']
            self.commands.append({'exit_code': item.get('exitCode'),
                                  'status': item.get('status')})
        if method == 'hook/completed':
            run = params['run']
            if 'tas-notes' in run.get('sourcePath', '').replace('\\', '/').lower():
                safe = {key: run.get(key) for key in ('eventName', 'status', 'durationMs')}
                # Do not retain stderr/stdout bodies even in the synthetic test report.
                safe['entry_count'] = len(run.get('entries', []))
                self.hooks.append(safe)
                print(json.dumps({'hook': safe}, ensure_ascii=True), flush=True)
        if method == 'turn/completed':
            turn = params['turn']
            self.completed[turn['id']] = turn['status']
        return message

    def request(self, method, params, timeout=40):
        self.counter += 1
        identifier = self.counter
        self.send({'id': identifier, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while identifier not in self.responses:
            self.receive(max(0.1, deadline - time.monotonic()))
            if time.monotonic() > deadline:
                raise TimeoutError(method)
        reply = self.responses.pop(identifier)
        if 'error' in reply:
            raise RuntimeError(method + ': ' + json.dumps(reply['error'], ensure_ascii=True))
        return reply['result']

    def turn(self, thread_id, prompt, timeout=180):
        reply = self.request('turn/start', {'threadId': thread_id,
                             'input': [{'type': 'text', 'text': prompt}]})
        turn_id = reply['turn']['id']
        deadline = time.monotonic() + timeout
        while turn_id not in self.completed:
            try:
                self.receive(min(20, max(0.1, deadline - time.monotonic())))
            except queue.Empty:
                print('Waiting for synthetic test turn...', flush=True)
            if time.monotonic() > deadline:
                self.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
                raise TimeoutError('synthetic test turn')
        return self.completed[turn_id]

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def until(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            try:
                self.receive(min(5, max(0.1, deadline - time.monotonic())))
            except queue.Empty:
                pass
        return predicate()

    def unsubscribe(self, thread_id):
        self.request('thread/unsubscribe', {'threadId': thread_id})
        self.until(lambda: ('thread/closed', thread_id) in self.notifications, timeout=10)
        # Lifecycle hook notifications may arrive after the close notification.
        self.until(lambda: False, timeout=4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--phase', choices=('smoke', 'flow', 'lifecycle', 'exit'), default='smoke')
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='tas-notes-live-')).resolve()
    project, state = root / '空白 test project', root / 'state'
    project.mkdir()
    (project / 'fixture.txt').write_text('Synthetic fixture: 中文 content.\n', encoding='utf-8')
    environment = dict(os.environ, TAS_NOTES_STATE_DIR=str(state))
    subprocess.run([sys.executable, str(SCRIPTS / 'tas_notes.py'), '--state-dir', str(state),
                    'configure-runtime'], check=True, capture_output=True)
    store = Store(state)
    store.set_project(str(project), True)
    print(json.dumps({'test_root': str(root), 'phase': args.phase}, ensure_ascii=True), flush=True)
    rpc = None
    report = {'phase': args.phase, 'turns': [], 'assertions': {}}
    thread_id = None
    try:
        rpc = Rpc(environment)
        rpc.request('initialize', {'clientInfo': {'name': 'tas-notes-live-test', 'version': '0.1.0'},
                                  'capabilities': {'experimentalApi': True}})
        rpc.send({'method': 'initialized'})
        hooks = rpc.request('hooks/list', {'cwds': [str(project)]})
        selected = [hook for entry in hooks['data'] for hook in entry.get('hooks', [])
                    if (hook.get('pluginId') or '').startswith('tas-notes@')]
        if len(selected) != 8 or any(h.get('trustStatus') != 'trusted' or not h.get('enabled') for h in selected):
            raise RuntimeError('TAS Notes requires normal installation and explicit hook trust first')
        reply = rpc.request('thread/start', {'cwd': str(project), 'ephemeral': True,
                            'sandbox': 'read-only', 'approvalPolicy': 'never'})
        thread_id = reply['thread']['id']
        report['turns'].append(rpc.turn(thread_id, '这是安装测试。不要使用工具。只回复：TAS_NOTES_中文_OK。'))
        rows = store.listing()
        report['assertions']['record_created'] = bool(rows)
        if args.phase == 'flow' and rows:
            detail = store.detail(rows[0]['id'])
            review = store.save_revision(detail['id'], 'Synthetic review', 'Owner review before correction',
                                         detail['source_seq'], 0)
            report['turns'].append(rpc.turn(thread_id,
                '这是隔离测试，请只读当前目录的 fixture.txt，然后分别执行两条无副作用命令：'
                'cmd.exe /d /c exit 7 和 cmd.exe /d /c echo TAS_NOTES_TOOL_OK。'
                '前一条预期失败，不要修复或重试。不要访问其他文件、网络或额外 Agent。最后报告结果。'))
            report['turns'].append(rpc.turn(thread_id,
                '【测试反馈】还是有问题，请保留之前的调查。不要使用工具。只回复：已记录后续反馈，尚未验收。'))
            updated = store.detail(detail['id'])
            contents = [json.loads(e['content']) for e in updated['events'] if e['kind'] == 'PostToolUse']
            report['assertions']['tool_success_output_captured'] = any(
                'TAS_NOTES_TOOL_OK' in str(c.get('tool_response', '')) for c in contents)
            report['assertions']['failed_command_captured'] = any(
                'exit 7' in str(c.get('tool_input', '')) for c in contents)
            # PowerShell can map a native exit 7 to its own nonzero exit 1.
            report['assertions']['host_observed_failed_command'] = any(
                isinstance(c['exit_code'], int) and c['exit_code'] != 0 and c['status'] == 'failed'
                for c in rpc.commands)
            report['limitations'] = {'tool_replacement_character_observed': any(
                '\ufffd' in str(c.get('tool_response', '')) for c in contents),
                'exit_status_not_in_current_stored_fields': True}
            report['assertions']['human_revision_preserved'] = updated['revision']['body'] == 'Owner review before correction'
            report['assertions']['export_frozen'] = '还是有问题' not in store.export_revision(review['id'])
            report['assertions']['correction_captured'] = '还是有问题' in updated['draft']
            before = len(updated['events'])
            store.set_project(str(project), False)
            report['turns'].append(rpc.turn(thread_id, '不要使用工具，只回复：TAS_NOTES_DISABLED_OK。'))
            report['assertions']['disabled_no_append'] = len(store.detail(detail['id'])['events']) == before
        if args.phase == 'lifecycle' and rows:
            first_id = rows[0]['id']
            rpc.request('thread/compact/start', {'threadId': thread_id})
            report['assertions']['compact_hooks_observed'] = rpc.until(
                lambda: any(h['eventName'] == 'postCompact' for h in rpc.hooks), timeout=150)
            before_count = len(store.detail(first_id)['events'])
            report['turns'].append(rpc.turn(thread_id, '压缩后的测试。不要使用工具，只回复：TAS_NOTES_AFTER_COMPACT。'))
            report['assertions']['after_compact_same_record'] = (
                len(store.listing()) == 1 and len(store.detail(first_id)['events']) > before_count)
            # Interrupt a synthetic no-tools turn once its prompt hook has actually run.
            prompt_count = sum(h['eventName'] == 'userPromptSubmit' for h in rpc.hooks)
            reply = rpc.request('turn/start', {'threadId': thread_id, 'input': [{'type': 'text',
                'text': '中断测试。不要使用工具。请输出从一到一百的中文数字。'}]})
            turn_id = reply['turn']['id']
            rpc.until(lambda: sum(h['eventName'] == 'userPromptSubmit' for h in rpc.hooks) > prompt_count)
            rpc.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
            report['assertions']['interrupt_hook_observed'] = rpc.until(
                lambda: any(h['eventName'] == 'interrupt' for h in rpc.hooks))
            rpc.until(lambda: turn_id in rpc.completed)
            report['assertions']['turn_interrupted'] = rpc.completed.get(turn_id) == 'interrupted'
            rpc.unsubscribe(thread_id)
            report['assertions']['session_end_stored'] = any(
                e['kind'] == 'SessionEnd' for e in store.detail(first_id)['events'])
            reply = rpc.request('thread/start', {'cwd': str(project), 'ephemeral': True,
                                'sandbox': 'read-only', 'approvalPolicy': 'never'})
            thread_id = reply['thread']['id']
            report['turns'].append(rpc.turn(thread_id, '新会话测试。不要使用工具，只回复：TAS_NOTES_NEW_SESSION。'))
            report['assertions']['new_session_separate_record'] = len(store.listing()) == 2
            rpc.unsubscribe(thread_id)
            other = root / 'not enabled'
            other.mkdir()
            before = [(r['id'], r['source_seq']) for r in store.listing()]
            reply = rpc.request('thread/start', {'cwd': str(other), 'ephemeral': True,
                                'sandbox': 'read-only', 'approvalPolicy': 'never'})
            thread_id = reply['thread']['id']
            report['turns'].append(rpc.turn(thread_id, '未启用项目测试。不要使用工具，只回复：TAS_NOTES_OPT_OUT。'))
            report['assertions']['other_project_not_recorded'] = before == [
                (r['id'], r['source_seq']) for r in store.listing()]
        rpc.unsubscribe(thread_id)
        thread_id = None
        if args.phase == 'exit':
            completed = subprocess.run([
                shutil.which('codex') or 'codex', '--no-daemon', '-a', 'never',
                'exec', '--ephemeral', '--skip-git-repo-check', '--json',
                '-s', 'read-only', '-c', 'features.hooks=true', '-C', str(project),
                '正常退出测试。不要使用工具，只回复：TAS_NOTES_EXIT。'],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=180,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            report['assertions']['cli_exited_successfully'] = completed.returncode == 0
            report['assertions']['cli_created_separate_record'] = len(store.listing()) == 2
            report['assertions']['normal_exit_session_end_stored'] = any(
                e['kind'] == 'SessionEnd' for row in store.listing() for e in store.detail(row['id'])['events'])
    finally:
        try:
            if rpc is not None:
                rpc.close()
        finally:
            store.set_project(str(project), False)
        report['hooks'] = rpc.hooks if rpc else []
        report['host_commands'] = rpc.commands if rpc else []
        report['records'] = []
        for row in store.listing():
            detail = store.detail(row['id'])
            report['records'].append({'event_kinds': [e['kind'] for e in detail['events']],
                'field_keys': [sorted(json.loads(e['content'])) for e in detail['events']],
                'unicode_present': '中文' in detail['draft']})
        marker = state / 'last-error.json'
        report['last_error'] = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else None
        (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
        print('Report: ' + str(root / 'report.json'), flush=True)
    if (not report['assertions'].get('record_created') or not all(report['assertions'].values())
            or any(status != 'completed' for status in report['turns']) or report['last_error']
            or any(h['status'] != 'completed' for h in report['hooks'])):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
