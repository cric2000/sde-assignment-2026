#!/usr/bin/env python3
"""External evaluation harness; scenario files are never passed to controller code.

Starts unmodified simulator CLI processes, controller and official grader. Evidence
queries read only the controller's DB. Existing output paths are never overwritten.
"""

import argparse
import json
import os
import platform
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def wait_ready(client, process, timeout=240):
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if process.poll() is not None:
            raise RuntimeError("simulator exited before readiness; inspect server.log")
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.25)
    raise RuntimeError("simulator readiness timeout")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="scenarios/public_01_dev.yaml")
    p.add_argument("--name", required=True)
    p.add_argument("--port", type=int, default=18700)
    p.add_argument("--baseline", action="store_true")
    p.add_argument("--crash", action="store_true")
    p.add_argument("--probe", action="store_true")
    a = p.parse_args()
    output = ROOT / "runs" / a.name
    output.mkdir(parents=True, exist_ok=False)
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    python = sys.executable
    if a.baseline:
        with (output / "baseline.log").open("w") as log:
            subprocess.run([python, "-m", "sim", "baseline", "--scenario", a.scenario,
                            "--run-dir", str(output / "baseline")], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    url = f"http://127.0.0.1:{a.port}"
    env = os.environ | {"CONTROLLER_PYTHON": python, "CONTROLLER_STATE_DIR": str(output / "controller")}
    with (output / "server.log").open("w") as server_log, (output / "controller.log").open("w") as controller_log:
        server = subprocess.Popen([python, "-m", "sim", "serve", "--scenario", a.scenario,
                                    "--run-dir", str(output / "candidate"), "--port", str(a.port)],
                                   cwd=ROOT, stdout=server_log, stderr=subprocess.STDOUT)
        controller = None
        try:
            with httpx.Client(base_url=url, timeout=180, trust_env=False) as client:
                wait_ready(client, server)
                if a.probe:
                    probe = {path: client.get(path).json() for path in ("/sim/state", "/workflows")}
                    wid = probe["/workflows"][0]["workflow_id"]
                    probe["config_history"] = client.get(f"/workflows/{wid}/config/history").json()
                    (reports / (a.name + "_probe.json")).write_text(json.dumps(probe, indent=2))
                    print(json.dumps(probe, indent=2))
                    return
                started = time.monotonic()
                controller = subprocess.Popen([str(ROOT / "run.sh"), url], cwd=ROOT, env=env,
                                              stdout=controller_log, stderr=subprocess.STDOUT)
                crash_evidence = None
                if a.crash:
                    dbpath = output / "controller" / "state.db"
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline and controller.poll() is None:
                        try:
                            with sqlite3.connect(dbpath, timeout=1) as db:
                                pending = db.execute("SELECT id,approval,status FROM proposals WHERE status='pending' LIMIT 1").fetchone()
                                checkpoint = dict(db.execute("SELECT key,value FROM meta WHERE key IN ('event_watermark','sim_minute','events_ingested')"))
                            if pending:
                                controller.send_signal(signal.SIGKILL)
                                controller.wait(timeout=10)
                                crash_evidence = {"signal": "SIGKILL", "pending_proposal": pending, "checkpoint_before": checkpoint,
                                                  "killed_returncode": controller.returncode}
                                controller = subprocess.Popen([str(ROOT / "run.sh"), url], cwd=ROOT, env=env,
                                                              stdout=controller_log, stderr=subprocess.STDOUT)
                                break
                        except (sqlite3.Error, FileNotFoundError):
                            pass
                        time.sleep(0.02)
                    if not crash_evidence:
                        raise RuntimeError("did not observe pending approval for crash injection")
                while controller.poll() is None:
                    time.sleep(0.5)
                if controller.returncode:
                    raise RuntimeError(f"controller exited {controller.returncode}; inspect {output / 'controller.log'}")
                elapsed = time.monotonic() - started
                final = client.get("/sim/state").json()
                if not final["finished"]:
                    raise RuntimeError("controller exited before simulator finished")
                sys.path.insert(0, str(ROOT))
                from controller.inspect import Reader
                reader = Reader(output / "controller" / "state.db")
                overview = reader.overview()
                metrics = overview["metrics"]
                summary = {"name": a.name, "scenario": a.scenario, "python": sys.version, "platform": platform.platform(),
                           "cpu_count": os.cpu_count(), "wall_seconds": elapsed,
                           "wall_seconds_per_simulated_day": elapsed / (final["sim_minute"] / 1440),
                           "ingest_events_per_second": metrics["events_ingested"] / metrics["ingest_seconds"],
                           "database_bytes": (output / "controller" / "state.db").stat().st_size,
                           "final_state": final, "metrics": metrics, "crash": crash_evidence}
                with reader.db:
                    summary["integrity_check"] = reader.db.execute("PRAGMA integrity_check").fetchone()[0]
                    summary["duplicate_proposal_applications"] = reader.db.execute("SELECT COUNT(*) FROM "
                        "(SELECT json_extract(payload,'$.proposal_id'),COUNT(*) n FROM decisions WHERE kind='proposal_applied' GROUP BY 1 HAVING n>1)").fetchone()[0]
                    summary["pending_proposals_at_finish"] = reader.db.execute("SELECT COUNT(*) FROM proposals WHERE status IN ('pending','applying','sending','sending_auto')").fetchone()[0]
                    summary["unjoined_calls"] = reader.db.execute("SELECT COUNT(*) FROM calls c LEFT JOIN leads l ON c.lead=l.id WHERE l.id IS NULL").fetchone()[0]
                    summary["durable_event_count"] = reader.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                    summary["source_last_event_matches_watermark"] = metrics["event_watermark"] == final["last_event_id"]
                applied = [p for p in overview["proposals"] if p["status"] == "applied"]
                if applied:
                    (reports / (a.name + "_audit.json")).write_text(json.dumps(reader.why(applied[-1]["workflow"]), indent=2))
                reader.close()
                (reports / (a.name + "_metrics.json")).write_text(json.dumps(summary, indent=2))
                (reports / (a.name + "_overview.json")).write_text(json.dumps(overview, indent=2))
                print(json.dumps(summary, indent=2))
        finally:
            if controller is not None and controller.poll() is None:
                controller.terminate()
                controller.wait(timeout=10)
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    if a.baseline:
        subprocess.run([python, "-m", "sim", "grade", "--run", str(output / "candidate"),
                        "--baseline", str(output / "baseline"), "--out", str(reports / (a.name + ".md"))], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
