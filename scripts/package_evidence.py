#!/usr/bin/env python3
"""Give selected evaluation outputs stable hand-in names, preserving older reports.

This only packages reports and the controller's own evidence; it never supplies
grading data to controller code or reads simulator internals.
"""

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dev", required=True)
    p.add_argument("--incidents", required=True)
    p.add_argument("--load", required=True)
    a = p.parse_args()
    reports = ROOT / "reports"
    selected = {"public_01": a.dev, "public_02": a.incidents, "public_03": a.load}
    for stable, source in selected.items():
        if not (reports / (source + "_metrics.json")).exists():
            raise FileNotFoundError(f"missing measurements for {source}; no evidence was packaged")
        if stable != "public_03" and not (reports / (source + ".md")).exists():
            raise FileNotFoundError(f"missing official grade for {source}; no evidence was packaged")
    archive = ROOT / "runs" / "report-archive" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    archive.mkdir(parents=True, exist_ok=False)
    provenance = {}
    for stable, source in selected.items():
        for suffix in (".md", "_metrics.json", "_overview.json", "_audit.json"):
            original, destination = reports / (source + suffix), reports / (stable + suffix)
            if original.exists() and original != destination:
                if destination.exists():
                    shutil.copy2(destination, archive / destination.name)
                shutil.copy2(original, destination)
        # Earlier evaluation harness versions kept our own DB beside their logs.
        # Move only that controller directory; never inspect simulator files.
        old_state, new_state = ROOT / "runs" / source / "controller", ROOT / ".controller-state" / source
        if old_state.exists() and not new_state.exists():
            new_state.parent.mkdir(exist_ok=True)
            old_state.rename(new_state)
        provenance[stable] = {"evaluation_name": source, "state_directory": str(new_state.relative_to(ROOT)),
                              "controller_log": f"runs/{source}/controller.log",
                              "simulator_baseline_and_candidate": f"runs/{source}/",
                              "note": "controller state and simulator output are separate; only the external harness reads grading outputs"}
    keep = {name + suffix for name in selected for suffix in (".md", "_metrics.json", "_overview.json", "_audit.json")}
    keep |= {"README.md", "crash_recovery.md", "provenance.json", "work_session.json", "statistical_calibration.json"}
    for path in list(reports.iterdir()):
        if path.is_file() and path.name not in keep:
            path.rename(archive / path.name)
    (reports / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
