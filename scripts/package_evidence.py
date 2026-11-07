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


def validate_evaluations(reports, selected):
    """Reject incomplete evidence before copying or archiving any report."""
    for stable, source in selected.items():
        for suffix in ("_metrics.json", "_overview.json", "_audit.json"):
            if not (reports / (source + suffix)).is_file():
                raise FileNotFoundError(f"missing {source + suffix}; no evidence was packaged")
        if stable != "public_03" and not (reports / (source + ".md")).is_file():
            raise FileNotFoundError(f"missing official grade for {source}; no evidence was packaged")
        metrics = json.loads((reports / (source + "_metrics.json")).read_text())
        state = metrics["final_state"]
        if metrics.get("profiled", False):
            raise ValueError(f"profiled evaluation is diagnostic, not benchmark evidence: {source}")
        complete = state["finished"] and state["sim_minute"] == state["end_minute"]
        clean = (metrics["integrity_check"] == "ok"
                 and metrics["source_last_event_matches_watermark"]
                 and metrics["metrics"]["event_watermark"] == state["last_event_id"]
                 and metrics["durable_event_count"] == metrics["metrics"]["events_ingested"]
                 and all(metrics[key] == 0 for key in (
                     "duplicate_proposal_applications", "unjoined_calls", "pending_proposals_at_finish")))
        if not complete or not clean:
            raise ValueError(f"incomplete or inconsistent evaluation: {source}; no evidence was packaged")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dev", required=True)
    p.add_argument("--incidents", required=True)
    p.add_argument("--load", required=True)
    p.add_argument("--welcome", help="optional completed welcome-message evaluation")
    a = p.parse_args()
    reports = ROOT / "reports"
    selected = {"public_01": a.dev, "public_02": a.incidents, "public_03": a.load}
    if a.welcome:
        selected["welcome_message"] = a.welcome
    validate_evaluations(reports, selected)
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
    keep |= {"README.md", "crash_recovery.md", "provenance.json", "statistical_calibration.json",
             "performance.md", "performance_before_metrics.json", "performance_after_metrics.json",
             "performance_regressions.json"}
    for path in list(reports.iterdir()):
        if path.is_file() and path.name not in keep:
            path.rename(archive / path.name)
    (reports / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
