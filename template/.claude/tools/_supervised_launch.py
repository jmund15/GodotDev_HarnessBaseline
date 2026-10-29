"""Memory admission for supervised sidecars; exit 12 means no child ran."""
import os
from pathlib import Path
import subprocess
import sys

from launch_memory import launch_class_args

MEMORY_REFUSED = 12
MEMORY_TOOL = Path(__file__).with_name('launch_memory.py')


def memory_args(argv):
    """The launcher argv's brief and shape, as the memory tool's class flags."""
    return launch_class_args(argv or [])


def launch(label, child, env=None, argv=None):
    """Wait before every attempt; retry only the launcher's pre-dispatch memory refusal.

    `argv` is the launcher command `child` runs: its `-f` brief and `-G` shape pick the job's floors.

    `child` returns the final exit, including a detached child's exit rather than its handoff.
    Its caller owns capture files and preserves refused detached attempts before retrying.
    """
    while True:
        subprocess.run([sys.executable, str(MEMORY_TOOL), 'wait', '--label', label, *memory_args(argv)],
                       env=os.environ if env is None else env, check=True)
        code = child()
        if code != MEMORY_REFUSED:
            return code
        print('WAIT memory headroom for %s: launcher refused admission; retrying' % label,
              file=sys.stderr, flush=True)
