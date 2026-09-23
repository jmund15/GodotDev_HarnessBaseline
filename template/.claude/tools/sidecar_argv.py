"""The one reader of sidecar launch argv: which `*_sidecar.sh` and `sidecar_fanout.py` calls a shell
command executes, and the launcher flags each call carries. Stdlib only."""
import os
import re
import shlex
from collections import namedtuple

Invocation = namedtuple("Invocation", "kind script path args")

_FD_REDIRECTION = re.compile(r'(?<!\S)(?:\d*>\s*&\s*(?:\d+|-)|&>>?(?:[^\s;&|]+|\s+[^\s;&|]+))')
_REDIRECTION = re.compile(r'^\d*(?:>>?|<)(.*)$')
_ENV_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_PYTHON = re.compile(r'python(?:3(?:\.\d+)?)?|py')

# lib/sidecar_common.sh SC_OPTSTRING letters that take a value (the `:`-suffixed ones).
SIDECAR_VALUE_FLAGS = set("metnodfTRxPSrpLlaGDCZ")


def shell_segments(command):
    """Token lists of the top-level segments a shell command runs, split on `;`, `&`, `|` and
    newlines. Quoted text stays one token. [] when the command does not tokenize."""
    command = _FD_REDIRECTION.sub(' ', command)
    try:
        lexer = shlex.shlex(command.replace('\n', ' ; '), posix=True, punctuation_chars=';&|')
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError:
        return []
    segments, current = [], []
    for token in tokens:
        if token and all(char in ';&|' for char in token):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


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


def launcher_invocations(command):
    """[Invocation(kind, script, path, args)] for each executed segment that runs a launcher.
    kind is 'sidecar' or 'fanout', script the lowercase basename, path the invoked token as
    written, args the launcher's own arguments."""
    calls = []
    for raw_segment in shell_segments(command):
        segment = strip_redirections(raw_segment)
        index = 0
        while index < len(segment) and _ENV_ASSIGNMENT.match(segment[index]):
            index += 1
        if index >= len(segment):
            continue
        executable = os.path.basename(segment[index].replace('\\', '/')).lower()
        script_index = index
        if _PYTHON.fullmatch(executable):
            script_index += 1
            while script_index < len(segment) and segment[script_index].startswith('-'):
                if segment[script_index] in ('-c', '-m'):
                    script_index = len(segment)
                    break
                script_index += 1
        elif executable in ('bash', 'sh'):
            script_index += 1
            while script_index < len(segment) and segment[script_index].startswith('-'):
                script_index += 1
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


def parse_fanout_args(args):
    """(jobs value, --out-dir value or None) as written in a `sidecar_fanout.py` argv, or None
    when the argv is not one jobs file plus known options. Paths are not resolved."""
    jobs_value = out_value = None
    index = 0
    positional_only = False
    while index < len(args):
        token = args[index]
        if not positional_only and token == '--':
            positional_only = True
            index += 1
            continue
        if not positional_only and token in ('--authorize', '--compare', '--dry-run'):
            index += 1
            continue
        if not positional_only and token in ('--max-parallel', '--out-dir'):
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
        if not positional_only and token.startswith('--max-parallel='):
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
