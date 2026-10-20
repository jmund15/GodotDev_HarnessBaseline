"""The probe must reach the native Ollama server, never whatever answers on ::1:11434."""
import importlib.util
import pathlib
import urllib.parse

_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "ollama_contention_probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("ollama_contention_probe", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_host_is_ipv4_loopback():
    host = urllib.parse.urlparse(_load().DEFAULT_HOST).hostname
    assert host == "127.0.0.1", f"DEFAULT_HOST resolves via {host!r}; localhost can reach Docker's [::]:11434"


if __name__ == "__main__":
    test_default_host_is_ipv4_loopback()
    print("ok")
