"""Conservative command diagnostic formats; never infer exit status or cause."""
import re
import ntpath

SGR = re.compile(r'\x1b\[[0-9;]*m')
# A deliberately small shell grammar. No expansion, pipelines or command search.
TOKEN = re.compile(r"\s*(?:'((?:[^']|'')*)'|\"([^\"$`]*)\"|([^\s'\";&|<>$`(){}]+))")


def direct_tokens(content):
    args = content.get('tool_input')
    if (not isinstance(args, dict) or args.get('truncated') is True
            or 'tool_input' in content.get('_coverage', {}).get('truncated_fields', [])):
        return []
    command = args.get('command', args.get('cmd'))
    if not isinstance(command, str) or '\n' in command or '\r' in command:
        return []
    command = re.sub(r';\s*exit\s+\$LASTEXITCODE\s*$', '', command.strip(), flags=re.I).rstrip()
    if command.startswith('&'):
        command = command[1:].lstrip()
    tokens, position = [], 0
    while position < len(command):
        if position and not command[position].isspace():
            return []
        match = TOKEN.match(command, position)
        if not match:
            return []
        tokens.append(next(value for value in match.groups() if value is not None).replace("''", "'"))
        position = match.end()
    return tokens


def direct_program(content, names):
    tokens = direct_tokens(content)
    return bool(tokens and re.split(r'[/\\]', tokens[0])[-1].lower() in names)


def content_read_path(content, *, ambiguous_alias=False):
    """A bounded Get-Content invocation, or optional unresolved alias candidate.

    Default establishes read syntax without inspecting current files or aliases.
    ambiguous_alias=True returns syntax only, never resolved command identity.
    Exact target diagnostics remain text observations, not verified failures.
    """
    tokens = direct_tokens(content)
    if ambiguous_alias:
        # Candidate syntax only, NOT proof of the shell's mutable alias target.
        # Never unwrap or inspect a compound binding as an ordinary alias.
        if not tokens or tokens[0].lower() not in ('cat', 'gc', 'type'):
            return None
        tokens = ['Microsoft.PowerShell.Management\\Get-Content', *tokens[1:]]
    args = content.get('tool_input')
    if (not tokens and isinstance(args, dict) and not args.get('truncated')
            and 'tool_input' not in content.get('_coverage', {}).get('truncated_fields', [])):
        command = args.get('command', args.get('cmd'))
        # Exact same-call binding, not the current machine's mutable alias table.
        prefix = re.fullmatch(r'Microsoft\.PowerShell\.Utility\\Set-Alias -Name (cat|gc|type) '
                              r'-Value Microsoft\.PowerShell\.Management\\Get-Content '
                              r'-Scope Local -Force -ErrorAction Stop; (.+)', command or '', re.I)
        if prefix and '\n' not in command and '\r' not in command:
            inner = direct_tokens({'tool_input': {'command': prefix[2]}})
            if inner and inner[0].lower() == prefix[1].lower():
                tokens = ['Microsoft.PowerShell.Management\\Get-Content', *inner[1:]]
    # A fresh, profile-free PowerShell child can emit the native single-line
    # diagnostic. Only unwrap this exact invocation, never arbitrary scripts.
    if (len(tokens) == 5 and ntpath.basename(tokens[0]).lower() == 'pwsh.exe'
            and [x.lower() for x in tokens[1:4]] == ['-noprofile', '-noninteractive', '-command']):
        tokens = direct_tokens({'tool_input': {'command': tokens[4]}})
    if (not tokens or tokens[0].lower() not in
            ('get-content', 'microsoft.powershell.management\\get-content')
            or '[REDACTED]' in repr(tokens)):
        return None
    path, seen, index = None, set(), 1
    while index < len(tokens):
        option = tokens[index]
        index += 1
        key = option.lower()
        if key in ('-raw', '-literalpath', '-path', '-encoding', '-tail', '-totalcount'):
            if key in seen:
                return None
            seen.add(key)
            if key == '-raw':
                continue
            if index == len(tokens):
                return None
            value = tokens[index]
            index += 1
            if key in ('-literalpath', '-path'):
                if path is not None or not value:
                    return None
                path = value
            elif key == '-encoding':
                if value.lower() not in ('utf8', 'utf8bom', 'utf8nobom', 'unicode', 'bigendianunicode', 'ascii', 'default', 'oem'):
                    return None
            elif not re.fullmatch(r'[0-9]{1,9}', value):
                return None
        elif key.startswith('-') or path is not None or not option:
            return None
        else:
            path = option
    return path


def literal_content_read(content):
    return content_read_path(content) is not None


def absolute_content_read(content):
    path = content_read_path(content)
    return bool(path and re.match(r'^[A-Za-z]:[/\\]', path))


def content_read_diagnostic(content, body, *, ambiguous_alias=False):
    # This short native PowerShell format omits Line/source/pointer rows.
    # Require the exact requested absolute path; never search arbitrary logs.
    path = content_read_path(content, ambiguous_alias=ambiguous_alias)
    if not path:
        return None
    absolute = bool(re.match(r'^[A-Za-z]:[/\\]', path))
    relative = ntpath.normpath(path)
    if not absolute and (not ambiguous_alias or ntpath.splitdrive(path)[0]
                         or path.startswith(('/', '\\'))
                         or any(x in ('', '..') for x in path.replace('\\', '/').split('/'))
                         or any(x in path for x in '*?[]')):
        return None
    text = SGR.sub('', body).replace('\r\r\n', '\n').replace('\r\n', '\n').rstrip('\n')
    lines = text.split('\n')
    # A complete native ConciseView record must belong to this read and this
    # requested path. Foreign Get-Item/ParserError records are log content.
    if len(lines) >= 5 and lines[0].rstrip() == 'Get-Content:' and lines[1] == 'Line |':
        if (re.fullmatch(r'\s+\d+ \| .+', lines[2])
                and re.fullmatch(r'\s+\|\s+[~^]+\s*', lines[3])
                and all(re.fullmatch(r'\s+\| .+', line) for line in lines[4:])):
            text = 'Get-Content: ' + ' '.join(line.split('|', 1)[1].strip() for line in lines[4:])
    match = re.fullmatch(r"Get-Content: Cannot find path '([^\r\n]+)' because it does not exist\.", text)
    if not match:
        return None
    actual = ntpath.normcase(ntpath.normpath(match[1]))
    target = ntpath.normcase(relative)
    # Relative candidates have no captured cwd. Only accept a complete native
    # record with an absolute path ending in whole requested path components;
    # never resolve against today's filesystem. A self-referential log remains
    # indistinguishable, just as for exact absolute target diagnostics.
    if absolute and actual != target:
        return None
    if not absolute and (not re.match(r'^[A-Za-z]:[/\\]', match[1])
                         or not actual.endswith('\\' + target)):
        return None
    return {'rule': 'powershell-diagnostic', 'title': '工具返回 PowerShell 路径不存在', 'evidence': body}


def git_diagnostic(lines):
    for index, line in enumerate(lines):
        if re.fullmatch(r'fatal: not a git repository \(or any of the parent directories\): \.git', line):
            return '工具返回 Git 仓库错误'
        if re.fullmatch(r"fatal: ambiguous argument '.+': unknown revision or path not in the working tree\.", line):
            return '工具返回 Git 引用或路径错误'
        if re.fullmatch(r"error: pathspec '.+' did not match any file\(s\) known to git", line):
            return '工具返回 Git 路径匹配错误'
        if (re.fullmatch(r"error: unknown option [`'].+'", line) and index + 1 < len(lines)
                and re.match(r'^usage: git [a-z][a-z-]* ', lines[index + 1])):
            return '工具返回 Git 参数错误'
    return None


def node_diagnostic(lines):
    # The uncaught exception has an error header, one or more V8 frames and a
    # Node runtime footer in the same retained segment. A warning is not one.
    header = re.compile(r'^(Error|TypeError|ReferenceError|SyntaxError|RangeError|URIError|EvalError|AggregateError)(?: \[[A-Z][A-Z0-9_]+\])?: .+')
    frame = re.compile(r'^\s+at .+:\d+:\d+\)?(?: \{)?$')
    current, has_frame = None, False
    for line in lines:
        match = header.fullmatch(line)
        if match:
            current, has_frame = match[1], False
        elif current and frame.fullmatch(line):
            has_frame = True
        elif re.fullmatch(r'Node\.js v\d+\.\d+\.\d+(?:[-+][\w.-]+)?', line):
            if current and has_frame:
                return '工具返回 Node.js ' + current
            current, has_frame = None, False
        elif current and not line.startswith((' ', '\t')) and line not in ('Require stack:', '}') and not line.startswith('- '):
            # Do not bridge unrelated stdout and a later runtime banner.
            current, has_frame = None, False
    return None


def powershell_diagnostic(lines):
    for start, line in enumerate(lines):
        # ConciseView: heading, source row, pointer and message rows. Restrict
        # accepted diagnoses; an arbitrary heading or pipe table is not enough.
        heading = re.fullmatch(r'([\w.-]+):(?: .*)?', line)
        if heading and start + 1 < len(lines) and lines[start + 1] == 'Line |':
            index, source, pointer, messages = start + 2, False, False, []
            while index < len(lines):
                row = lines[index]
                if re.match(r'^\s+\d+ \|', row):
                    source = True
                elif re.fullmatch(r'\s+\|\s+[~^]+\s*', row):
                    pointer = True
                elif re.match(r'^\s+\| ', row):
                    messages.append(row.split('|', 1)[1].strip())
                else:
                    break
                index += 1
            message = ' '.join(messages)
            if not source or not pointer:
                continue
            if re.match(r"^The term '.+' is not recognized as (?:a name of|the name of) a cmdlet, function, script file, or (?:executable|operable) program\.", message):
                return '工具返回 PowerShell 命令不存在'
            if heading[1] in ('Get-Item', 'Get-Content', 'Set-Location') and re.fullmatch(r"Cannot find path '.+' because it does not exist\.", message):
                return '工具返回 PowerShell 路径不存在'
            if heading[1] == 'ParserError' and message:
                return '工具返回 PowerShell 语法错误'
        # NormalView uses a location, source/caret and explicit record fields.
        # Bound to a single contiguous record with known error identifiers.
        if re.match(r'^At .+:(?:line:)?\d+ char:\d+$', line):
            record = lines[start + 1:start + 7]
            if (len(record) >= 4 and re.match(r'^\+ .+', record[0])
                    and re.fullmatch(r'\+\s+[~^]+\s*', record[1])
                    and re.match(r'^\s*\+ CategoryInfo\s+: .+', record[2])):
                identifier = re.fullmatch(r'\s*\+ FullyQualifiedErrorId\s+: (CommandNotFoundException|PathNotFound(?:,[\w.]+)?|MissingConditionInIfStatement)\s*', record[3])
                if identifier:
                    return '工具返回 PowerShell ' + identifier[1].split(',')[0]
    return None


def shell_diagnostic(content, body):
    # Keep raw evidence untouched. Normalize only a copy for matching.
    lines = [line for line in SGR.sub('', body).splitlines() if line.strip()]
    if direct_program(content, ('git', 'git.exe')):
        title = git_diagnostic(lines)
        if title:
            return {'rule': 'git-diagnostic', 'title': title, 'evidence': body}
    if direct_program(content, ('node', 'node.exe', 'nodejs')):
        title = node_diagnostic(lines)
        if title:
            return {'rule': 'node-exception', 'title': title, 'evidence': body}
    title = powershell_diagnostic(lines)
    if title:
        return {'rule': 'powershell-diagnostic', 'title': title, 'evidence': body}
    return None
