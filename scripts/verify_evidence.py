#!/usr/bin/env python3
"""Verify packaged evidence, read-only supervisor HTTP routes and audit queries."""

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


def main():
    provenance = json.loads((ROOT / "reports/provenance.json").read_text())
    db = ROOT / provenance["public_02"]["state_directory"] / "state.db"
    reader = Reader(db)
    try:
        proposal = reader.db.execute("SELECT * FROM proposals WHERE kind='hallucination_fix' AND status='applied' "
                                     "ORDER BY updated DESC LIMIT 1").fetchone()
        ts = datetime.fromtimestamp((reader.meta()["origin"] + proposal["updated"]) * 60, timezone.utc).isoformat()
        audit = reader.why(proposal["workflow"], ts)
        assert audit["id"] == proposal["id"] and audit["approval"] == proposal["approval"]
        (ROOT / "reports/public_02_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
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
    print("Verified supervisor HTTP routes, real audit/exposure queries, JSON evidence, exact grade tables, submission headings, stakeholder word limits and HTTP-only import boundary.")


if __name__ == "__main__":
    main()
