"""The floor's shell lexer (T1): which segments a shell command executes, the nested bodies it runs
(`$(...)`, backticks, `eval`, `bash -c`, wrapper prefixes), and the seat-launcher
(`seat.sh`) calls among them. `shell_code` blanks heredoc and inline-program bodies whose reader is not a
shell. Moved from `tools/sidecar_argv.py` and `hooks/foreground_wait_guard.py`; stdlib only."""
import os
import re
import shlex
from collections import namedtuple

_SHELL_VAR = re.compile(r"\$(?:\{[^}]*\}|\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)")

Invocation = namedtuple("Invocation", "kind script path args")

_FD_REDIRECTION = re.compile(r'(?<!\S)(?:\d*>\s*&\s*(?:\d+|-)|&>>?(?:[^\s;&|]+|\s+[^\s;&|]+))')
_REDIRECTION = re.compile(r'^\d*(?:>>?|<)(.*)$')
_ENV_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_PYTHON = re.compile(r'python(?:3(?:\.\d+)?)?|py')
# Reserved words that open a command position: the next word is the program the segment runs.
_RESERVED = frozenset({'!', '{', 'if', 'then', 'do', 'else', 'elif', 'while', 'until'})
_SEPARATORS = ';&|()'
# Prefix commands that execute their operand: name -> (value-taking options, positionals before it).
_WRAPPERS = {
    'command': (frozenset(), 0),
    'env': (frozenset({'-u', '-C', '--unset', '--chdir'}), 0),
    'exec': (frozenset({'-a'}), 0),
    'nice': (frozenset({'-n', '--adjustment'}), 0),
    'nohup': (frozenset(), 0),
    'stdbuf': (frozenset({'-i', '-o', '-e'}), 0),
    'time': (frozenset({'-f', '-o'}), 0),
    'timeout': (frozenset({'-k', '-s', '--kill-after', '--signal'}), 1),
}

_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def strip_heredocs(command, quoted_only=False):
    """The command without heredoc bodies: each body is data fed to stdin, never a command the
    shell runs, and its free text (an apostrophe, a launch line) must not reach the tokenizer.
    The operator line stays; `<<-` also matches a delimiter indented with tabs. `quoted_only`
    keeps unquoted-delimiter bodies, whose `$(...)` and backticks the shell still expands."""
    out, pending = [], []
    for line in command.split('\n'):
        if pending:
            strip_tabs, delimiter, keep = pending[0]
            ends = (line.lstrip('\t') if strip_tabs else line) == delimiter
            if ends:
                pending.pop(0)
            if keep or (ends and quoted_only):
                out.append(line)
            continue
        out.append(line)
        pending.extend((bool(m.group(1)), m.group(3), quoted_only and not m.group(2))
                       for m in _HEREDOC.finditer(line))
    return '\n'.join(out)


def shell_segments(command):
    """Token lists of the segments a shell command runs, split on `;`, `&`, `|`, `(`, `)` and
    newlines, with heredoc bodies removed. Quoted text stays one token. [] when the command does
    not tokenize."""
    command = _FD_REDIRECTION.sub(' ', strip_heredocs(command))
    try:
        lexer = shlex.shlex(command.replace('\n', ' ; '), posix=True, punctuation_chars=_SEPARATORS)
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError:
        return []
    segments, current = [], []
    for token in tokens:
        if token and all(char in _SEPARATORS for char in token):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def shell_segments_with_comments(command):
    """(command tokens, trailing shell comment) per segment, including grouped commands.

    A `#` inside quotes or a word is data. A comment consumes the rest of its line,
    including separators that would otherwise create phantom executable segments.
    """
    result = []
    for line in strip_heredocs(command).split('\n'):
        quote = None
        escaped = False
        comment_at = None
        for index, char in enumerate(line):
            if escaped:
                escaped = False
            elif char == '\\' and quote != "'":
                escaped = True
            elif char == quote:
                quote = None
            elif char in "'\"" and quote is None:
                quote = char
            elif char == '#' and quote is None and (index == 0 or line[index - 1].isspace()
                                                  or line[index - 1] in _SEPARATORS):
                comment_at = index
                break
        code = line if comment_at is None else line[:comment_at]
        segments = shell_segments(code)
        if segments:
            result.extend((tokens, '') for tokens in segments[:-1])
            result.append((segments[-1], '' if comment_at is None else line[comment_at:]))
    return result


def executable_index(segment):
    """Index of the program run by a segment after reserved words, assignments and wrappers."""
    segment = strip_redirections(segment)
    index = 0
    while index < len(segment) and (segment[index] in _RESERVED
                                    or _ENV_ASSIGNMENT.match(segment[index])):
        index += 1
    return segment, _skip_wrappers(segment, index)


def strip_redirections(tokens):
    """Tokens without shell redirections such as `> log` and `2>/dev/null`. A quoted argument
    beginning with `>` is indistinguishable from a redirect after shlex removes its quotes."""
    out, skip_target = [], False
    for token in tokens:
        if skip_target:
            skip_target = False
            continue
        match = _REDIRECTION.match(token)
        if match:
            skip_target = not match.group(1)
            continue
        out.append(token)
    return out


def _closing_paren(command, index):
    """Index of the `)` that closes a `(` opened just before `index`, or len(command)."""
    depth, quote = 1, None
    while index < len(command):
        char = command[index]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == '\\':
            index += 1
        elif quote == '"':
            quote = None if char == '"' else quote
        elif char in '\'"':
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(command)


def command_substitutions(command, placeholder=''):
    """(command with its `$(...)`, backtick and unquoted `<(...)` / `>(...)` process substitutions replaced by
    `placeholder`, [each substitution's body]). Single-quoted text is literal, so a substitution inside it stays in
    the command; a process substitution inside double quotes is literal too."""
    out, bodies, quote, index = [], [], None, 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == '\\':
            out.append(command[index:index + 2])
            index += 2
            continue
        elif command.startswith('$(', index) or (quote is None and command.startswith(('<(', '>('), index)):
            end = _closing_paren(command, index + 2)
            bodies.append(command[index + 2:end])
            out.append(placeholder)
            index = end + 1
            continue
        elif char == '`':
            end = command.find('`', index + 1)
            end = len(command) if end < 0 else end
            bodies.append(command[index + 1:end])
            out.append(placeholder)
            index = end + 1
            continue
        elif char == '"':
            quote = None if quote == '"' else '"'
        elif char == "'" and quote is None:
            quote = "'"
        out.append(char)
        index += 1
    return ''.join(out), bodies


def _skip_wrappers(segment, index):
    """Index of the program a segment runs once prefix commands that execute their operand
    (`nohup`, `timeout 600`, `env -u X FOO=1`, ...) are stepped over."""
    while index < len(segment):
        name = os.path.basename(segment[index].replace('\\', '/')).lower()
        if name not in _WRAPPERS:
            return index
        value_options, positionals = _WRAPPERS[name]
        index += 1
        while index < len(segment) and segment[index].startswith('-') and segment[index] != '-':
            index += 2 if segment[index] in value_options else 1
        while name == 'env' and index < len(segment) and _ENV_ASSIGNMENT.match(segment[index]):
            index += 1
        index += positionals
    return index


def _shell_operand(tokens, index):
    index += 1
    command_string = False
    while index < len(tokens) and tokens[index][:1] in ('-', '+'):
        option = tokens[index]
        index += 1
        if option == '--':
            break
        if option.startswith('--'):
            if option in ('--rcfile', '--init-file'):
                index += 1
            continue
        letters = option[1:]
        if option[0] == '-' and 'n' in letters:
            return len(tokens), False
        command_string |= option[0] == '-' and 'c' in letters
        index += letters.count('o') + letters.count('O')
    return index, command_string


LauncherBody = namedtuple('LauncherBody', 'body start end scoped')


def literal_launcher_body(tokens, index):
    """Literal eval/bash/sh command body and its argv slice. A shell scopes cwd/env; eval does not.
    `None` means no executable body, including `bash -n`. Callers own body recursion and state."""
    if index >= len(tokens):
        return None
    head = os.path.basename(tokens[index].replace('\\', '/')).lower()
    if head == 'eval':
        return LauncherBody(' '.join(tokens[index + 1:]), index + 1, len(tokens), False)
    if head not in ('bash', 'sh'):
        return None
    operand, command_string = _shell_operand(tokens, index)
    if command_string and operand < len(tokens):
        return LauncherBody(tokens[operand], operand, operand + 1, True)
    return None


def launcher_invocations(command):
    """[Invocation(kind, script, path, args)] for each executed segment that runs the seat launcher
    (`seat.sh`, paid external work): kind is 'seat', script the lowercase basename, path the invoked
    token as written, args the launcher's own arguments. Segments inside subshells, brace groups and
    if/while/until/for bodies count; reserved words, `NAME=value` and wrapper prefixes are stepped
    over; `$(...)`/backtick/`<(...)` bodies (each leaves a `${SUBST}` word in its place, so the surrounding argument keeps its slot), `eval` arguments and `bash -c` / `sh -c` strings (after any
    `-o <option>`) are read as the commands they run. `bash -n` runs nothing."""
    command, substituted = command_substitutions(strip_heredocs(command, quoted_only=True), '${SUBST}')
    calls = []
    for body in substituted:
        calls.extend(launcher_invocations(body))
    for raw_segment in shell_segments(command):
        segment = strip_redirections(raw_segment)
        index = 0
        while index < len(segment) and (segment[index] in _RESERVED
                                        or _ENV_ASSIGNMENT.match(segment[index])):
            index += 1
        index = _skip_wrappers(segment, index)
        if index >= len(segment):
            continue
        executable = os.path.basename(segment[index].replace('\\', '/')).lower()
        body = literal_launcher_body(segment, index)
        if body is not None:
            calls.extend(launcher_invocations(body.body))
            continue
        script_index = index
        if _PYTHON.fullmatch(executable):
            script_index += 1
            while script_index < len(segment) and segment[script_index].startswith('-'):
                if segment[script_index] in ('-c', '-m'):
                    script_index = len(segment)
                    break
                script_index += 1
        elif executable in ('bash', 'sh'):
            script_index, command_string = _shell_operand(segment, index)
            if command_string:
                continue
        if script_index >= len(segment):
            continue
        path = segment[script_index]
        script = os.path.basename(path.replace('\\', '/')).lower()
        if script == 'seat.sh':
            calls.append(Invocation('seat', script, path, segment[script_index + 1:]))
    return calls




# A heredoc body is stdin for the program before `<<`; only a shell runs it as code.
_HEREDOC_BODY = re.compile(r"(?P<head>[^\n]*?)<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)[^\n]*\n(?P<body>.*?)^[ \t]*(?P=tag)[ \t]*$",
                           re.DOTALL | re.MULTILINE)
_SHELL_WORDS = frozenset(("bash", "sh", "zsh", "dash", "pwsh", "powershell", "su", "flock", "script"))
# A quoted `-c`/`-e`/`-p` program is code for the program before it; only a shell runs it as shell code.
_INLINE_PROGRAM = re.compile(
    r"""(?P<head>(?:^|(?<=[\s;&|(]))(?P<prog>[\w./\\:-]+)(?:\s+-[\w-]+)*?\s+-[cep]\s+)(?P<body>"(?:\\.|[^"\\])*"|'[^']*')""",
    re.DOTALL)


def shell_code(command):
    """`command` with every heredoc body blanked whose reader is not a shell: that body is data."""
    def blank(m):
        words = re.findall(r"[\w.-]+", m.group("head"))
        reader = next((w.lower() for w in reversed(words) if not w.startswith("-")), "")
        if reader.rsplit("/", 1)[-1].removesuffix(".exe") in _SHELL_WORDS:
            return m.group(0)
        return m.group(0)[:m.start("body") - m.start()] + "\n" * m.group("body").count("\n") + \
            m.group(0)[m.end("body") - m.start():]

    def blank_inline(m):
        if m.group("prog").replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe") in _SHELL_WORDS:
            return m.group(0)
        return m.group("head") + '""'
    return _INLINE_PROGRAM.sub(blank_inline, _HEREDOC_BODY.sub(blank, str(command or "")))
