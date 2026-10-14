"""New format regressions are synthetic; desktop delivery is audited separately."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/blackbox/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import blackbox_anomalies as a
from blackbox_core import Store
from blackbox_mcp import Reader
from verify_shell_formats import audit

GIT = 'fatal: not a git repository (or any of the parent directories): .git\n'
NODE = "TypeError: cannot read null\n    at run (C:\\test\\main.cjs:1:3)\n    at node:internal/main/run_main_module:33:47\n\nNode.js v24.16.0\n"
PS = "Get-Item: C:\\test\\main.ps1:4\nLine |\n   4 |  Get-Item missing\n     |  ~~~~~~~~~~~~~~~~\n     | Cannot find path 'C:\\test\\missing' because it does not\n     | exist.\n"
NORMAL = "absent : The term 'absent' is not recognized\nAt C:\\test\\main.ps1:4 char:1\n+ absent\n+ ~~~~~~\n+ CategoryInfo          : ObjectNotFound: (absent:String) [], ParentContainsErrorRecordException\n+ FullyQualifiedErrorId : CommandNotFoundException\n"


def body(output, command='git status', **extra):
    return dict(tool_name='Bash', tool_input={'command': command}, tool_response=output, **extra)


class ShellFormatTests(unittest.TestCase):
    def test_git_specific_diagnostics_and_command_identity(self):
        for output in (GIT, "fatal: ambiguous argument 'missing': unknown revision or path not in the working tree.\n",
                       "error: pathspec 'missing' did not match any file(s) known to git\n",
                       "error: unknown option 'bad'\nusage: git status [<options>]\n"):
            hit = a.detect(body(output, "& 'C:\\Program Files\\Git\\cmd\\git.exe' -C 'C:\\a b' status; exit $LASTEXITCODE"))
            self.assertEqual(hit['rule'], 'git-diagnostic')
            self.assertEqual(hit['evidence'], output)
        for command in ('Get-Content git.log', 'echo git status', 'notgit status',
                        'git --version; echo test', 'git --version\nGet-Content git.log',
                        "'git'notgit status", 'cmd /c git status', 'git status | Get-Content git.log'):
            self.assertIsNone(a.detect(body(GIT, command)))
        self.assertIsNone(a.detect(body('fatal: arbitrary failure\n')))
        self.assertIsNone(a.detect(body("error: unknown option 'bad'\n")))
        self.assertIsNone(a.detect(body(GIT, _coverage={'truncated_fields': ['tool_input']})))

    def test_node_requires_error_frames_and_runtime_footer_together(self):
        self.assertEqual(a.detect(body(NODE, 'node main.cjs'))['title'], '工具返回 Node.js TypeError')
        for output in (NODE.replace('TypeError:', 'Warning:'), NODE.split('Node.js')[0],
                       'TypeError: failed\nNode.js v24.16.0\n',
                       NODE.replace('    at run', 'ordinary stdout\n    at run')):
            self.assertIsNone(a.detect(body(output, 'node main.cjs')))
        self.assertIsNone(a.detect(body(NODE, 'Get-Content node.log')))
        self.assertIsNone(a.detect(body(NODE, 'npm run start')))

    def test_node_module_metadata_and_syntax_diagnostics(self):
        module = "Error: Cannot find module 'absent'\nRequire stack:\n- C:\\test\\main.cjs\n    at require (node:internal/helpers:1:2) {\n  code: 'MODULE_NOT_FOUND',\n  requireStack: [\n    'C:\\test\\main.cjs'\n  ]\n}\nNode.js v24.16.0\n"
        self.assertIsNotNone(a.detect(body(module, 'node main.cjs')))
        syntax = 'C:\\test\\main.cjs:1\nconst = 1;\n      ^\n\n' + NODE.replace('TypeError:', 'SyntaxError:')
        self.assertEqual(a.detect(body(syntax, 'node main.cjs'))['title'], '工具返回 Node.js SyntaxError')

    def test_powershell_requires_structure_and_known_message(self):
        self.assertEqual(a.detect(body(PS, 'Get-Item missing'))['rule'], 'powershell-diagnostic')
        for output in (PS.replace('Line |', 'Output'), PS.replace('     |  ~~~~~~~~~~~~~~~~\n', ''),
                       PS.replace('   4 |  Get-Item missing\n', ''),
                       PS.replace("Cannot find path", 'Found path'), "Get-Item: unknown error\n"):
            self.assertIsNone(a.detect(body(output)))
        command = PS.replace('Get-Item:', 'absent:').replace("Cannot find path 'C:\\test\\missing' because it does not\n     | exist.",
                    "The term 'absent' is not recognized as a name of a cmdlet, function, script file, or executable\n     | program. Check spelling.")
        self.assertEqual(a.detect(body(command))['title'], '工具返回 PowerShell 命令不存在')
        parser = PS.replace('Get-Item:', 'ParserError:').replace("Cannot find path 'C:\\test\\missing' because it does not\n     | exist.", 'Missing condition.')
        self.assertEqual(a.detect(body(parser))['title'], '工具返回 PowerShell 语法错误')

    def test_normal_view_requires_contiguous_record_fields(self):
        self.assertIsNotNone(a.detect(body(NORMAL)))
        for output in (NORMAL.replace('+ CategoryInfo', '+ OrdinaryOutput'),
                       NORMAL.replace('+ ~~~~~~\n', ''), NORMAL.replace('At C:', 'From C:'),
                       NORMAL.replace('CommandNotFoundException\n', 'anything\n'),
                       NORMAL.replace('+ FullyQualifiedErrorId', 'unrelated line\n+ FullyQualifiedErrorId')):
            self.assertIsNone(a.detect(body(output)))

    def test_color_and_windows_newlines_preserve_raw_evidence(self):
        output = '\x1b[31;1m' + PS.replace('\n', '\r\r\n') + '\x1b[0m'
        self.assertEqual(a.detect(body(output))['evidence'], output)

    def test_non_bash_and_generic_words_are_not_new_signals(self):
        for output, command in ((GIT, 'git status'), (NODE, 'node main.cjs'), (PS, 'Get-Item missing')):
            candidate = body(output, command)
            candidate['tool_name'] = 'mcp__example__read_log'
            self.assertIsNone(a.detect(candidate))
            hit = a.detect(body(output, command))
            self.assertFalse(hit['signal']['independently_verified'])
            self.assertEqual(hit['signal']['kind'], 'diagnostic_format')
        for output in ('error', 'fatal: example', 'TypeError: example', 'exit code 1',
                       '{"success":false,"error":"failed"}', 'NOTES050_WARNING'):
            self.assertIsNone(a.detect(body(output, 'node main.cjs')))
        self.assertIsNone(a.detect(body('OK', "echo 'fatal: not a git repository (or any of the parent directories): .git'")))

    def test_truncated_segments_never_join_missing_evidence(self):
        def truncated(head, tail):
            return body({'truncated': True, 'original_length': 100000,
                         'preview': head, 'tail': tail}, 'node main.cjs',
                        _coverage={'truncated_fields': ['tool_response']})
        self.assertIsNone(a.detect(truncated(NODE.split('Node.js')[0], 'cut\nNode.js v24.16.0\n')))
        hit = a.detect(truncated('ordinary output\ncut', 'cut\n' + NODE))
        self.assertEqual(hit['signal']['segment'], 'tail')
        self.assertTrue(hit['signal']['truncated'])
        cut_git = body({'truncated': True, 'original_length': 10000, 'preview': GIT.rstrip(),
                        'tail': 'cut\nnormal output\n'}, _coverage={'truncated_fields': ['tool_response']})
        self.assertIsNone(a.detect(cut_git))

    def test_reader_snapshot_and_old_rule_rejection_without_rewriting_events(self):
        with tempfile.TemporaryDirectory(prefix='blackbox-shell-test-') as tmp:
            root = Path(tmp)
            project = root / 'project'
            project.mkdir()
            store = Store(root / 'state')
            store.set_project(str(project), True)
            for index, (output, command) in enumerate(((GIT, 'git status'), (NODE, 'node a.cjs'), (PS, 'Get-Item missing'))):
                store.record(dict(cwd=str(project), session_id='synthetic', turn_id='test',
                    hook_event_name='PostToolUse', tool_use_id=str(index), **body(output, command)))
            with store.connect() as db:
                original = [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY seq')]
            page = a.listing(store)
            self.assertEqual(len(page['items']), 3)
            reader = Reader(root / 'state')
            try:
                for item in page['items']:
                    detail = a.detail(store, item['id'], page['snapshot_seq'])
                    path = f'/api/anomaly-export/{item["id"]}?through={page["snapshot_seq"]}&rule={a.RULE_VERSION}'
                    response = reader.call('read_view', {'path': path})['_meta']['blackbox']
                    self.assertEqual(response['status'], 200)
                    self.assertEqual(response['body'], detail['markdown'])
                    self.assertEqual(reader.call('read_view', {'path': path.replace(a.RULE_VERSION, 'observed-errors-4')})['_meta']['blackbox']['status'], 400)
            finally:
                reader.names.close()
                reader.projects.close()
            with store.connect() as db:
                self.assertEqual(original, [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY seq')])

    def test_desktop_audit_rejects_missing_duplicate_wrong_source_and_gaps(self):
        candidate = body(GIT, 'git status', _coverage={'capture_boundary_version': 1})
        row = {'kind': 'PostToolUse', 'seq': 1, 'digest': 'synthetic', 'content': json.dumps(candidate)}
        commands = {'git_no_repository': 'git status'}
        self.assertTrue(audit([row], commands)['passed'])
        self.assertFalse(audit([], commands)['passed'])
        self.assertFalse(audit([row, row], commands)['passed'])
        for changes in ({'tool_name': 'Read'}, {'tool_response': 'OK'},
                        {'tool_input': {'command': 'git diff'}},
                        {'_coverage': {}}, {'_coverage': {'capture_boundary_version': 1, 'capture_gap_before': True}},
                        {'_coverage': {'capture_boundary_version': 1, 'truncated_fields': ['tool_response']}}):
            self.assertFalse(audit([dict(row, content=json.dumps(dict(candidate, **changes)))], commands)['passed'])


if __name__ == '__main__':
    unittest.main()
