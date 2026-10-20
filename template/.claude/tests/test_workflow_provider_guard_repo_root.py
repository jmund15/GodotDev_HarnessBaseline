"""The guard injects args.__repoRoot: the checkout the payload cwd belongs to (a submodule resolves to its superproject)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_workflow_provider_guard import SYN, agents, wpg

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "protocol.file.allow=always"]


def git(cwd, *argv):
    subprocess.run(GIT + list(argv), cwd=cwd, check=True, capture_output=True, text=True)


def injected(cwd):
    st = wpg._session_transport
    saved = (st.HOST_TRANSPORT, wpg.model_registry.role_tiers)
    try:
        st.HOST_TRANSPORT = "host"
        call = agents([("off-fan-1", "fanout")], extra={"fold": {"subjects": []}})
        call["cwd"] = cwd
        upd = wpg.transport_injection(call, "off", SYN) or {}
    finally:
        st.HOST_TRANSPORT, wpg.model_registry.role_tiers = saved
    return upd.get("args") or {}


class RepoRootInjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name).resolve()
        sub, repo = base / "sub", base / "repo"
        for path in (sub, repo):
            path.mkdir()
            git(path, "init", "-q")
            (path / "f.txt").write_text("x\n", encoding="utf-8")
            git(path, "add", "f.txt")
            git(path, "commit", "-q", "-m", "init")
        git(repo, "submodule", "add", "-q", str(sub), "Jmodot")
        (repo / "deep" / "er").mkdir(parents=True)
        cls.repo = repo

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def root_for(self, cwd):
        return injected(str(cwd).replace("\\", "/")).get("__repoRoot")

    def assertSameDir(self, got, want):
        self.assertIsInstance(got, str)
        self.assertNotIn("\\", got)
        self.assertEqual(Path(got).resolve(), Path(want).resolve())

    def test_repo_root_cwd(self):
        self.assertSameDir(self.root_for(self.repo), self.repo)

    def test_subdirectory_cwd(self):
        self.assertSameDir(self.root_for(self.repo / "deep" / "er"), self.repo)

    def test_submodule_cwd_resolves_to_superproject(self):
        self.assertSameDir(self.root_for(self.repo / "Jmodot"), self.repo)

    def test_fold_repo_root_uses_the_same_resolution(self):
        args = injected(str(self.repo / "deep" / "er").replace("\\", "/"))
        self.assertSameDir(args["fold"]["repoRoot"], self.repo)

    def test_unanswerable_cwd_injects_nothing_and_keeps_the_cwd_fold_root(self):
        args = injected("C:/no/such/checkout")
        self.assertNotIn("__repoRoot", args)
        self.assertEqual(args["fold"]["repoRoot"], "C:/no/such/checkout")

    def test_string_args_carry_it(self):
        call = agents([("off-fan-1", "fanout")])
        call["cwd"] = str(self.repo).replace("\\", "/")
        call["tool_input"]["args"] = json.dumps(call["tool_input"]["args"])
        st = wpg._session_transport
        saved = (st.HOST_TRANSPORT, wpg.model_registry.role_tiers)
        try:
            st.HOST_TRANSPORT = "host"
            upd = wpg.transport_injection(call, "off", SYN)
        finally:
            st.HOST_TRANSPORT, wpg.model_registry.role_tiers = saved
        self.assertSameDir(json.loads(upd["args"])["__repoRoot"], self.repo)


if __name__ == "__main__":
    unittest.main()
