"""Memory admission for supervised sidecars; exit 12 means no child ran."""
import os
from pathlib import Path
import subprocess
import sys

MEMORY_REFUSED = 12
MEMORY_TOOL = Path(__file__).with_name('launch_memory.py')


def launch(label, child, env=None):
    """Wait before every attempt; retry only the launcher's pre-dispatch memory refusal.

    `child` returns the final exit, including a detached child's exit rather than its handoff.
    Its caller owns capture files and preserves refused detached attempts before retrying.
    """
    while True:
        subprocess.run([sys.executable, str(MEMORY_TOOL), 'wait', '--label', label],
                       env=os.environ if env is None else env, check=True)
        code = child()
        if code != MEMORY_REFUSED:
            return code
        print('WAIT memory headroom for %s: launcher refused admission; retrying' % label,
              file=sys.stderr, flush=True)
