"""The one reader of sidecar launch argv: which `*_sidecar.sh` and `sidecar_fanout.py` calls a shell
command executes, and the launcher flags each call carries. Stdlib only."""
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

# lib/sidecar_common.sh SC_OPTSTRING letters that take a value (the `:`-suffixed ones).
SIDECAR_VALUE_FLAGS = set("metnodfTRxPSrpLlaGBDCZKYQ")


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
    """[Invocation(kind, script, path, args)] for each executed segment that runs a launcher.
    kind is 'sidecar' or 'fanout', script the lowercase basename, path the invoked token as
    written, args the launcher's own arguments. Segments inside subshells, brace groups and
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
        if script == 'sidecar_fanout.py':
            calls.append(Invocation('fanout', script, path, segment[script_index + 1:]))
        elif script.endswith('_sidecar.sh'):
            calls.append(Invocation('sidecar', script, path, segment[script_index + 1:]))
    return calls


def launcher_calls(command):
    """(kind, script basename, args) per executed launcher call."""
    return [(c.kind, c.script, c.args) for c in launcher_invocations(command)]


def sidecar_flag_pairs(args):
    """([(letter, value), ...], first operand index) read like getopts reads SC_OPTSTRING.
    Repeated flags remain visible. Parsing stops at `--`, a `--long` option, or the first operand;
    a value-taking flag without a value is represented with None for the caller to reject."""
    pairs, index = [], 0
    while index < len(args):
        token = args[index]
        if not token.startswith('-') or token == '-' or token.startswith('--'):
            return pairs, index
        for pos, letter in enumerate(token[1:], 1):
            if letter in SIDECAR_VALUE_FLAGS:
                rest = token[pos + 1:]
                if rest:
                    pairs.append((letter, rest))
                elif index + 1 < len(args):
                    index += 1
                    pairs.append((letter, args[index]))
                else:
                    pairs.append((letter, None))
                break
            pairs.append((letter, True))
        index += 1
    return pairs, len(args)


def sidecar_flags(args):
    """{flag letter: value, or True for a bare flag}, retaining the last repeated value."""
    pairs, _operand_index = sidecar_flag_pairs(args)
    return dict(pairs)


# Every `sidecar_fanout.build_parser()` option, classified; test_sidecar_argv fails on an unclassified one.
# Non-launch options stay unknown to parse_fanout_args, so an argv carrying one is no launch.
FANOUT_VALUE_OPTIONS = ('--max-parallel', '--out-dir', '--fold', '--fold-alias', '--fold-effort', '--fold-plan',
                        '--fold-subject', '--ledger-roster', '--ledger-scope-json', '--ledger-stage',
                        '--ledger-subject', '--ledger-task', '--plan', '--root', '--ledger-head',
                        '--ledger-blobs', '--ledger-premise-commits')
FANOUT_FLAG_OPTIONS = ('--authorize', '--compare', '--detach', '--dry-run', '--no-new-decision', '--refold', '--review')
FANOUT_NON_LAUNCH_OPTIONS = ('-h', '--help', '--status', '--review-artifact', '--select')


def fanout_out_dir(jobs_path, out_value, spill_value, repo):
    """The directory `sidecar_fanout.py` writes to: `--out-dir`, else the jobs file's `spillDir`
    (relative to `repo`), else `<jobs parent>/fanout`. `out_value` arrives resolved by the caller."""
    if out_value:
        return out_value
    if spill_value:
        return spill_value if os.path.isabs(spill_value) else os.path.join(repo, spill_value)
    return os.path.join(os.path.dirname(os.path.abspath(jobs_path)), "fanout")


def _full_fanout_option(token):
    """`token` with its option name spelled in full the way argparse resolves it (its default
    abbreviation rule: an exact name, else the one option the prefix starts); unchanged when it names
    none or several, which no launch is. A `=value` suffix stays."""
    name, equals, value = token.partition('=')
    known = FANOUT_VALUE_OPTIONS + FANOUT_FLAG_OPTIONS + FANOUT_NON_LAUNCH_OPTIONS
    if not name.startswith('--') or name in known:
        return token
    matches = [option for option in known if option.startswith(name)]
    return matches[0] + equals + value if len(matches) == 1 else token


def parse_fanout_args(args):
    """(jobs value, --out-dir value or None) as written in a `sidecar_fanout.py` argv, or None
    when the argv is not one jobs file plus known options. Option names may be abbreviated as
    argparse allows (`--out` for `--out-dir`). Paths are not resolved."""
    jobs_value = out_value = None
    index = 0
    positional_only = False
    while index < len(args):
        token = args[index] if positional_only else _full_fanout_option(args[index])
        if token == '':
            # An unquoted variable that expanded to nothing leaves no argument in the shell.
            index += 1
            continue
        if not positional_only and token == '--':
            positional_only = True
            index += 1
            continue
        if not positional_only and token in FANOUT_FLAG_OPTIONS:
            index += 1
            continue
        if not positional_only and token in FANOUT_VALUE_OPTIONS:
            if index + 1 >= len(args):
                return None
            if token == '--out-dir':
                out_value = args[index + 1]
            index += 2
            continue
        if not positional_only and token.startswith('--out-dir='):
            out_value = token.split('=', 1)[1]
            index += 1
            continue
        if not positional_only and token.split('=', 1)[0] in FANOUT_VALUE_OPTIONS and '=' in token:
            index += 1
            continue
        if not positional_only and token.startswith('-'):
            return None
        if jobs_value is not None:
            return None
        jobs_value = token
        index += 1
    if jobs_value is None:
        return None
    return jobs_value, out_value
