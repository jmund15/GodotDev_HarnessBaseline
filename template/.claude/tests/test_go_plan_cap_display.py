"""The roster distinguishes a Go monthly plan cap from marginal dollars."""
import contextlib
import io
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import model_registry


def main():
    row = {
        "id": "synthetic-go", "alias": "synthetic-go", "transport": "go",
        "roles": [], "effort": {"evidence": "unmeasured"},
        "planCapUSD": {"monthly": 15},
    }
    data = {"models": [row], "transports": {"go": {"costModel": "plan-quota"}}}
    text = io.StringIO()
    with patch.object(model_registry, "load", return_value=data), contextlib.redirect_stdout(text):
        assert model_registry._cmd_available([]) == 0
    rendered = text.getvalue()
    assert "monthly plan cap $15" in rendered, rendered
    assert "$15/1M" not in rendered, rendered
    print("Go monthly plan cap is visible without implying a marginal price")


if __name__ == "__main__":
    main()
