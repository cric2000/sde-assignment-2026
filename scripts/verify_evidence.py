#!/usr/bin/env python3
"""Check the submission offline; optionally exercise local audit/API state."""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controller.inspect import Reader
from scripts.package_evidence import validate_evaluations


def verify_live(provenance):
    db = ROOT / provenance["public_02"]["state_directory"] / "state.db"
    reader = Reader(db)
    try:
        proposal = reader.db.execute("SELECT * FROM proposals WHERE kind='hallucination_fix' AND status='applied' "
                                     "ORDER BY updated DESC LIMIT 1").fetchone()
        ts = datetime.fromtimestamp((reader.meta()["origin"] + proposal["updated"]) * 60, timezone.utc).isoformat()
        audit = reader.why(proposal["workflow"], ts)
        assert audit["id"] == proposal["id"] and audit["approval"] == proposal["approval"]
        saved = json.loads((ROOT / "reports/public_02_audit.json").read_text())
        assert saved["id"] == audit["id"] and saved["approval"] == audit["approval"]
        lead = reader.db.execute("SELECT lead FROM calls WHERE workflow=? AND prompt=? LIMIT 1",
                                 (proposal["workflow"], json.loads(proposal["body"])["proposed_config"]["prompt_version"])).fetchone()[0]
        assert reader.exposure(lead)["calls"]
    finally:
        reader.close()
    env = os.environ | {"CONTROLLER_DB": str(db)}
    with (ROOT / "runs" / "view_smoke.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "controller.view:app", "--host", "127.0.0.1", "--port", "18800"],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url="http://127.0.0.1:18800", timeout=10, trust_env=False) as client:
                for _ in range(40):
                    if server.poll() is not None:
                        raise RuntimeError("view exited; inspect runs/view_smoke.log")
                    try:
                        if client.get("/metrics").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                for endpoint in ("/overview", "/metrics", f"/workflows/{proposal['workflow']}/timeline"):
                    response = client.get(endpoint)
                    assert response.status_code == 200, response.text
                assert client.post("/overview").status_code == 405
                assert len(client.get("/overview").json()["workflows"]) == 3
        finally:
            server.terminate()
            server.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="also check the retained incident DB and localhost API")
    args = parser.parse_args()
    required = ("README.md", "ASSIGNMENT.md", "run.sh", "requirements.txt", "requirements-tested.txt", "SUBMISSION.md", "SUBMISSION_TEMPLATE.md",
                "docs/CONTROLLER.md", "docs/RUNBOOK.md", "reports/README.md", "reports/crash_recovery.md")
    assert all((ROOT / path).is_file() for path in required), "missing required submission file"
    assert os.access(ROOT / "run.sh", os.X_OK), "run.sh must be executable"
    provenance = json.loads((ROOT / "reports/provenance.json").read_text())
    assert {"public_01", "public_02", "public_03"} <= provenance.keys()
    validate_evaluations(ROOT / "reports", {name: name for name in provenance})
    for name, source in provenance.items():
        assert (ROOT / f"reports/{name}.md").is_file()
        metrics = json.loads((ROOT / f"reports/{name}_metrics.json").read_text())
        assert metrics["name"] == source["evaluation_name"], name
    for path in (ROOT / "reports").glob("*.json"):
        json.loads(path.read_text())
    submission = (ROOT / "SUBMISSION.md").read_text()
    for short in ("public_01", "public_02"):
        grade = (ROOT / f"reports/{short}.md").read_text()
        table = grade.split("## Outcomes vs baseline (no-op controller)\n\n")[1].split("\n\n")[0]
        assert table in submission, short
    assert re.findall(r"^## (\d+)\.", submission, re.M) == [str(i) for i in range(15)]
    assert "__LOAD_RESULTS__" not in submission
    for reply in re.findall(r"\*\*Reply:\*\* (.*)", submission):
        assert len(reply.split()) <= 120
    for path in (ROOT / "controller").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert not any(n.name == "sim" or n.name.startswith("sim.") for n in node.names)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("sim")
    documents = [ROOT / "README.md", ROOT / "ASSIGNMENT.md", ROOT / "SUBMISSION.md", *(ROOT / "docs").glob("*.md"),
                 *(ROOT / "reports").glob("*.md")]
    for path in documents:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            if target.startswith(("http://", "https://", "#")):
                continue
            assert (path.parent / target.split("#")[0]).exists(), f"broken link in {path}: {target}"
    print("Verified required artifacts, completed evidence, provenance, exact grade tables, submission headings, "
          "stakeholder word limits, documentation links and HTTP-only import boundary.")
    if args.live:
        verify_live(provenance)
        print("Verified retained audit/exposure state and real read-only supervisor HTTP routes.")


if __name__ == "__main__":
    main()
