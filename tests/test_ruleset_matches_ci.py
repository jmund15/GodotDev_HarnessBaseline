"""The branch ruleset requires status contexts; each must be a job `name:` in baseline.yml, or a push waits forever on a
check nothing reports."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def required_contexts(ruleset):
    return [check["context"] for rule in ruleset["rules"] if rule["type"] == "required_status_checks"
            for check in rule["parameters"]["required_status_checks"]]


def job_names(workflow_text):
    """`name:` of each job: the lines at 4-space indent under `jobs:`."""
    jobs = workflow_text.split("\njobs:\n", 1)[1]
    return re.findall(r"^    name:\s*(.+?)\s*$", jobs, re.M)


def test_each_required_status_context_is_a_baseline_job_name():
    ruleset = json.loads((ROOT / ".github" / "ruleset-main.json").read_text(encoding="utf-8"))
    names = job_names((ROOT / ".github" / "workflows" / "baseline.yml").read_text(encoding="utf-8"))
    contexts = required_contexts(ruleset)
    assert contexts and names
    assert set(contexts) <= set(names), f"ruleset requires {contexts}; baseline.yml jobs are {names}"


def test_a_renamed_job_is_caught():
    assert not set(required_contexts({"rules": [{"type": "required_status_checks", "parameters": {
        "required_status_checks": [{"context": "baseline"}]}}]})) <= set(job_names("x\njobs:\n  b:\n    name: ci\n"))
