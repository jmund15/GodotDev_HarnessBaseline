"""Record what a proof process reads, for the harness pass cache's observed key (`harness_tests._PassCache`).

Active only when `HARNESS_READ_LOG` names a directory: the runner sets it, with this directory first on
`PYTHONPATH` and named by `HARNESS_READ_RECORDER`, for a Python proof and every Python child that inherits the
environment. A proof that builds a child's own `PYTHONPATH` keeps `HARNESS_READ_RECORDER` first on it; the
sitecustomize this one shadows still runs. Each process appends
one JSON line per new event to `<dir>/<pid>.jsonl`, so a child killed mid-run still leaves what it read:
`["r"|"w", path]` for a file open, `["l", path]` for a directory listing other than the importer's,
`["p", path]` for an existence or stat probe or a path an import could resolve to, and `["s", {argv, cwd, recorded}]` for a spawned process, where `recorded` says whether the
child loads this recorder with the same log: Python imports only the first sitecustomize on its path."""
import json
import os
import sys

_LOG = os.environ.get("HARNESS_READ_LOG")
_HERE = os.path.dirname(os.path.abspath(__file__))

if _LOG:
    _seen = set()
    _fh = None

    def _emit(kind, value):
        global _fh
        key = (kind, value if isinstance(value, str) else json.dumps(value, sort_keys=True))
        if key in _seen:
            return
        _seen.add(key)
        try:
            if _fh is None:
                os.makedirs(_LOG, exist_ok=True)
                _fh = open(os.path.join(_LOG, "%d.jsonl" % os.getpid()), "a", encoding="utf-8", buffering=1)
            _fh.write(json.dumps([kind, value]) + "\n")
        except Exception:
            pass

    def _abs(path):
        try:
            return os.path.abspath(os.fsdecode(path))
        except Exception:
            return None

    def _importer_listing():
        """The importer lists each sys.path directory it searches; that is not the proof reading it."""
        frame = sys._getframe(2)
        return frame is not None and "importlib._bootstrap" in frame.f_code.co_filename

    def _import_candidates(name, search):
        """Probe where an import could resolve, so a module appearing there (or shadowing) changes the key."""
        if not isinstance(name, str) or not name:
            return
        rel = name.replace(".", os.sep)
        for folder in search or ():
            if isinstance(folder, str) and folder:
                for candidate in (rel + ".py", rel):
                    p = _abs(os.path.join(folder, candidate))
                    if p:
                        _emit("p", p)

    def _hook(event, args):
        try:
            if event == "open":
                path, mode = args[0], args[1]
                if isinstance(path, int):
                    return
                p = _abs(path)
                if p and not p.startswith(_LOG):
                    _emit("w" if mode and any(c in str(mode) for c in "wax+") else "r", p)
            elif event == "_winapi.CopyFile2":
                # shutil.copy2 and copytree on Windows: a copy that raises no `open` event.
                for kind, path in (("r", args[0]), ("w", args[1])):
                    p = _abs(path)
                    if p and not p.startswith(_LOG):
                        _emit(kind, p)
            elif event in ("os.listdir", "os.scandir"):
                if _importer_listing():
                    return
                p = _abs(args[0] if args and args[0] is not None else ".")
                if p:
                    _emit("l", p)
            elif event == "import":
                _import_candidates(args[0], args[2])
            elif event == "subprocess.Popen":
                argv = args[1] if isinstance(args[1], (list, tuple)) else [args[1]]
                env = args[3] if len(args) > 3 else None
                inherits = env is None or (env.get("HARNESS_READ_LOG") == _LOG
                                           and _customizer(env.get("PYTHONPATH")) == os.path.normcase(_HERE))
                _emit("s", {"argv": [os.fsdecode(a) if isinstance(a, (str, bytes, os.PathLike)) else str(a) for a in argv],
                            "cwd": _abs(args[2]) if len(args) > 2 and args[2] is not None else os.getcwd(),
                            "recorded": inherits})
        except Exception:
            pass

    def _customizer(pythonpath, isfile=os.path.isfile):
        """The `PYTHONPATH` entry whose sitecustomize Python imports: the first that holds one."""
        for entry in str(pythonpath or "").split(os.pathsep):
            if entry and isfile(os.path.join(entry, "sitecustomize.py")):
                return os.path.normcase(os.path.abspath(entry))
        return None

    def _probe(original):
        def probe(path, *rest, **kw):
            if isinstance(path, (str, bytes, os.PathLike)):
                p = _abs(path)
                if p:
                    _emit("p", p)
            return original(path, *rest, **kw)
        return probe

    # `pathlib`'s exists/is_file/is_dir route through these. `os.stat` stays unwrapped: stdlib code tests it
    # by identity (`os.stat in os.supports_dir_fd`), and a wrapper would change what that code does.
    for _name in ("exists", "isfile", "isdir", "lexists"):
        setattr(os.path, _name, _probe(getattr(os.path, _name)))

    sys.addaudithook(_hook)


def _run_shadowed():
    """Run the sitecustomize this one shadows, the next on `sys.path`, so a proof's own still loads."""
    here, past = os.path.normcase(_HERE), False
    for entry in sys.path:
        folder = os.path.normcase(os.path.abspath(entry or "."))
        if not past:
            past = folder == here
        elif os.path.isfile(os.path.join(folder, "sitecustomize.py")):
            import runpy
            runpy.run_path(os.path.join(folder, "sitecustomize.py"), run_name="sitecustomize")
            return


_run_shadowed()
