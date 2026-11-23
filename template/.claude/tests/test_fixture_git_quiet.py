"""A fixture repo is copied while its template's git may still be writing: `git commit` spawns `git maintenance run
--auto` detached, which writes `.git/objects/maintenance.lock` and packs, and a `copytree` racing it fails with
`shutil.Error` (seen on the first Ubuntu CI run). `conftest.py` turns that work off for every git a test spawns."""
import subprocess


def _config(key, cwd):
    return subprocess.run(["git", "config", "--get", key], cwd=cwd, capture_output=True, text=True).stdout.strip()


def test_background_git_work_is_off_for_every_spawned_git(tmp_path):
    assert (_config("gc.auto", tmp_path), _config("maintenance.auto", tmp_path)) == ("0", "false")
