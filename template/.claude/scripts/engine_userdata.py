"""Resolve agent-owned engine data; shared by shell, PowerShell and MCP launchers."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys


def isolation(repo, env=None, platform=None):
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if platform not in ('win32', 'linux'):
        raise ValueError(f'Unsupported engine data isolation platform: {platform}')
    windows = platform == 'win32'
    key = 'APPDATA' if windows else 'XDG_DATA_HOME'
    if env.get('ENGINE_OWNER_USERDATA') == '1':
        base = env.get(key) or (str(Path.home() / '.local/share') if not windows else '')
        if not base:
            raise ValueError('APPDATA is required for owner user data')
    else:
        local = env.get('LOCALAPPDATA') if windows else str(Path.home() / '.local/share')
        if not local:
            raise ValueError('LOCALAPPDATA is required; refusing owner user data')
        root = Path(repo).resolve()
        leaf = root.name if (root / '.git').is_file() else 'main'
        base = str(Path(local) / '{{PROJECT_NAME}}/agent-appdata' / leaf)
    base = base.replace('\\', '/').rstrip('/')
    userdir = base + ('/Godot' if windows else '/godot') + '/app_userdata/{{PROJECT_NAME}}'
    return {key: base, 'USERDIR': userdir}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--shell', action='store_true')
    args = parser.parse_args()
    try:
        values = isolation(args.repo)
    except ValueError as error:
        parser.exit(1, f'engine_userdata: {error}\n')
    if args.shell:
        print('\n'.join(f'export {key}={shlex.quote(value)}' for key, value in values.items()))
    else:
        print(json.dumps(values))


if __name__ == '__main__':
    main()
