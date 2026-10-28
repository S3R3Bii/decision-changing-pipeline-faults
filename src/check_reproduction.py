"""Rebuild every scenario, monitor setting and decision in a separate directory."""

from pathlib import Path
import csv
import json
import math
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def compare(a, b, key="root"):
    if isinstance(a, dict):
        assert a.keys() == b.keys(), key
        for k in a:
            if k not in ["frozen_utc", "evaluated_utc", "elapsed_seconds"]:
                compare(a[k], b[k], key + "." + k)
    elif isinstance(a, list):
        assert len(a) == len(b), key
        for i, (x, y) in enumerate(zip(a, b)):
            compare(x, y, key + f"[{i}]")
    elif isinstance(a, float):
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7), (key, a, b)
    else:
        assert a == b, (key, a, b)


with tempfile.TemporaryDirectory(prefix="pipeline-fault-replay-") as folder:
    replay = Path(folder)
    for directory in ["src", "sql", "data"]:
        shutil.copytree(
            ROOT / directory,
            replay / directory,
            ignore=shutil.ignore_patterns("__pycache__", "cache"),
        )
    for script, args in [
        ("experiment.py", ["validation"]),
        ("verify.py", ["validation"]),
        ("experiment.py", ["test"]),
        ("verify.py", ["test"]),
        ("figures.py", []),
    ]:
        subprocess.run(
            [sys.executable, str(replay / "src" / script), *args],
            cwd=replay,
            check=True,
            timeout=180,
        )
    for name in [
        "selection.json",
        "source_quality.json",
        "validation_cases.json",
        "test_cases.json",
        "test_summary.json",
    ]:
        compare(
            json.loads((ROOT / "results" / name).read_text()),
            json.loads((replay / "results" / name).read_text()),
            name,
        )

    def trials(path):
        with open(path, newline="") as handle:
            return {
                r["name"]: {
                    k: float(r[k])
                    for k in [
                        "decision_weighted_recall",
                        "background_burden",
                        "fault_recall",
                    ]
                }
                for r in csv.DictReader(handle)
            }

    compare(
        trials(ROOT / "results/search.csv"),
        trials(replay / "results/search.csv"),
        "all14_monitor_objectives",
    )
    print(
        "PASS: exact source, all390 scenarios,14 settings, exhaustive decisions/flags and quarter-block uncertainty reproduce offline."
    )
