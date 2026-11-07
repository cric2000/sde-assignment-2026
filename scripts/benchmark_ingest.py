#!/usr/bin/env python3
"""Isolated ingestion experiment using a fixed prefix captured through HTTP.

Never reads simulator internals or changes controller defaults. Component timers
add overhead; results are a replay microbenchmark, not a full scenario grade.
"""

import argparse
import hashlib
import json
import os
import resource
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controller import ingest as ingestion
from controller.model import timestamp_minute
from controller.store import Store
from scripts.evaluate import wait_ready


class ReplayAPI:
    """External test fixture containing only records previously observed over HTTP."""

    def __init__(self, path):
        self.path = path

    def get(self, path, kind, after):
        return [{"chunk": 0}] if kind == "history" and after < 0 else []

    def lines(self, chunk):
        with self.path.open() as stream:
            yield from stream


class TimedConnection:
    def __init__(self, connection):
        self.connection = connection
        self.seconds = 0
        self.commits = 0
        self.enabled = False

    def __getattr__(self, name):
        method = getattr(self.connection, name)
        if name not in {"execute", "executemany", "commit", "rollback"}:
            return method

        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return method(*args, **kwargs)
            finally:
                if self.enabled:
                    self.seconds += time.perf_counter() - start
                    self.commits += int(name == "commit")
        return measured


def fingerprint(db):
    digest = hashlib.sha256()
    for table in ("events", "leads", "campaigns", "calls", "rollups"):
        digest.update(table.encode())
        ordering = "1,2,3,4,5,6,7,8" if table == "rollups" else "1"
        for row in db.execute(f"SELECT * FROM {table} ORDER BY {ordering}"):
            digest.update(repr(tuple(row)).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def replay(path, state_dir, origin, batch_size):
    state_dir.mkdir(parents=True, exist_ok=True)
    store = Store(state_dir / "state.db")
    timers = {"json_seconds": 0.0, "store_seconds": 0.0, "store_sql_seconds": 0.0}
    connection = TimedConnection(store.db)
    store.db = connection
    original_json, original_ingest = ingestion.json, store.ingest

    def decode(line):
        start = time.perf_counter()
        try:
            return json.loads(line)
        finally:
            timers["json_seconds"] += time.perf_counter() - start

    def materialize(*args):
        start, sql_before = time.perf_counter(), connection.seconds
        try:
            return original_ingest(*args)
        finally:
            timers["store_seconds"] += time.perf_counter() - start
            timers["store_sql_seconds"] += connection.seconds - sql_before

    try:
        if store.get("origin", origin) != origin:
            raise ValueError("replay origin does not match the persisted state")
        with store.transaction():
            store.set("origin", origin)
        ingestion.json = SimpleNamespace(loads=decode, dumps=json.dumps)
        store.ingest = materialize
        connection.enabled = True
        started = time.perf_counter()
        added, _ = ingestion.Ingester(ReplayAPI(path), store, batch_size).drain()
        elapsed = time.perf_counter() - started
        connection.enabled = False
        result = {
            "batch_size": batch_size, "elapsed_seconds": elapsed, "new_events": added,
            "events_per_second": added / elapsed, "json_decode_seconds": timers["json_seconds"],
            "sqlite_seconds": connection.seconds,
            "record_preparation_seconds": timers["store_seconds"] - timers["store_sql_seconds"],
            "file_and_bookkeeping_seconds": elapsed - timers["json_seconds"] - timers["store_seconds"]
                - (connection.seconds - timers["store_sql_seconds"]),
            "commits": connection.commits,
            "durable_events": store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "watermark": store.get("event_watermark"),
            "integrity_check": store.db.execute("PRAGMA integrity_check").fetchone()[0],
            "journal_mode": store.db.execute("PRAGMA journal_mode").fetchone()[0],
            "synchronous": store.db.execute("PRAGMA synchronous").fetchone()[0],
            "fingerprint": fingerprint(store.db),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        (state_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        ingestion.json = original_json
        store.close()


def capture(output, scenario, port, limit):
    path = output / "observed_http.ndjson"
    with (output / "server.log").open("w") as log:
        print("Generating simulator history for one HTTP capture.", flush=True)
        server = subprocess.Popen([sys.executable, "-m", "sim", "serve", "--scenario", scenario,
            "--run-dir", str(output / "simulator"), "--port", str(port)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180, trust_env=False) as client:
                wait_ready(client, server)
                state = client.get("/sim/state").raise_for_status().json()
                chunks = client.get("/events/chunks", params={"kind": "history", "after": -1}).raise_for_status().json()
                count, last, kinds = 0, -1, Counter()
                with path.open("w") as stream:
                    for chunk in sorted(chunks, key=lambda row: row["chunk"]):
                        with client.stream("GET", f"/events/chunks/{chunk['chunk']}") as response:
                            response.raise_for_status()
                            for line in response.iter_lines():
                                if not line:
                                    continue
                                event = json.loads(line)
                                if event["event_id"] <= last:
                                    raise ValueError("HTTP capture is not in strictly increasing event order")
                                last = event["event_id"]
                                kinds[event["type"]] += 1
                                stream.write(line + "\n")
                                count += 1
                                if count == limit:
                                    break
                        if count == limit:
                            break
                if count < limit:
                    raise ValueError("not enough history for the requested fixed sample")
                with path.open("rb") as captured:
                    source_hash = hashlib.file_digest(captured, "sha256").hexdigest()
                return {"events": count, "last_event_id": last, "event_types": dict(kinds),
                    "origin": timestamp_minute(state["ts"]) - state["sim_minute"],
                    "sha256": source_hash}
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def command(path, state_dir, origin, batch_size):
    return [sys.executable, str(Path(__file__).resolve()), "--replay", str(path),
            "--state-dir", str(state_dir), "--origin", str(origin), "--batch-size", str(batch_size)]


def check_result(result, source, reference=None):
    assert result["durable_events"] == source["events"]
    assert result["watermark"] == source["last_event_id"]
    assert result["integrity_check"] == "ok"
    assert result["journal_mode"] == "wal" and result["synchronous"] == 2
    assert result["peak_rss_kib"] < 256 * 1024, "component trial exceeded its 256-MiB RSS ceiling"
    if reference:
        assert result["fingerprint"] == reference


def crash_recovery(path, state_dir, origin, batch_size, source, reference):
    worker = subprocess.Popen(command(path, state_dir, origin, batch_size), cwd=ROOT)
    deadline = time.monotonic() + 180
    checkpoint = None
    try:
        while worker.poll() is None and time.monotonic() < deadline:
            try:
                with sqlite3.connect((state_dir / "state.db").resolve().as_uri() + "?mode=ro", uri=True) as db:
                    row = db.execute("SELECT value FROM meta WHERE key='events_ingested'").fetchone()
                if row and batch_size * 2 <= json.loads(row[0]) < source["events"]:
                    checkpoint = json.loads(row[0])
                    worker.send_signal(signal.SIGKILL)
                    worker.wait(timeout=10)
                    break
            except sqlite3.Error:
                pass
            time.sleep(0.02)
        if checkpoint is None or worker.returncode != -9:
            raise RuntimeError("did not capture the intended mid-ingestion SIGKILL boundary")
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=10)
    subprocess.run(command(path, state_dir, origin, batch_size), cwd=ROOT, check=True)
    result = json.loads((state_dir / "result.json").read_text())
    check_result(result, source, reference)
    return {"signal": "SIGKILL", "returncode": worker.returncode,
            "committed_events_before_kill": checkpoint, "recovered": result}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name")
    p.add_argument("--scenario", default="scenarios/public_03_load.yaml")
    p.add_argument("--port", type=int, default=18730)
    p.add_argument("--events", type=int, default=500000)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--cpus", type=int, default=4)
    p.add_argument("--batch-sizes", type=int, nargs="+", default=[4000, 8000, 16000])
    p.add_argument("--replay", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--state-dir", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--origin", type=int, help=argparse.SUPPRESS)
    p.add_argument("--batch-size", type=int, default=4000, help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.replay:
        replay(a.replay, a.state_dir, a.origin, a.batch_size)
        return
    if not a.name or Path(a.name).name != a.name or a.repeats < 3 or min(a.batch_sizes) <= 0 or 4000 not in a.batch_sizes:
        p.error("use a simple unique name, >=3 repeats, positive batch sizes and a 4000-event baseline")
    if a.events <= max(a.batch_sizes) * 4 or a.cpus <= 0:
        p.error("capture more than four largest batches and use a positive CPU count")
    if hasattr(os, "sched_getaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:a.cpus])
    output = ROOT / "runs" / a.name
    states = ROOT / ".controller-state" / a.name
    if output.exists() or states.exists():
        p.error("choose a new benchmark name; existing evidence is never overwritten")
    output.mkdir(parents=True)
    source = capture(output, a.scenario, a.port, a.events)
    print(f"Captured {source['events']:,} HTTP events: {source['event_types']}", flush=True)
    trials, reference = [], None
    for repetition in range(a.repeats):
        order = a.batch_sizes[repetition % len(a.batch_sizes):] + a.batch_sizes[:repetition % len(a.batch_sizes)]
        for size in order:
            state_dir = states / f"batch_{size}_repeat_{repetition}"
            subprocess.run(command(output / "observed_http.ndjson", state_dir, source["origin"], size), cwd=ROOT, check=True)
            result = json.loads((state_dir / "result.json").read_text())
            check_result(result, source, reference)
            reference = reference or result["fingerprint"]
            trials.append(result | {"repetition": repetition})
            print(f"Batch {size:5}, repeat {repetition + 1}: {result['elapsed_seconds']:.2f}s, {result['peak_rss_kib']/1024:.1f} MiB", flush=True)
    medians = {str(size): statistics.median(t["elapsed_seconds"] for t in trials if t["batch_size"] == size)
               for size in a.batch_sizes}
    fastest = int(min(medians, key=medians.get))
    baseline = medians["4000"]
    reduction = 1 - medians[str(fastest)] / baseline
    paired_wins = sum(next(t["elapsed_seconds"] for t in trials if t["batch_size"] == fastest and t["repetition"] == r)
                      < next(t["elapsed_seconds"] for t in trials if t["batch_size"] == 4000 and t["repetition"] == r)
                      for r in range(a.repeats))
    candidate = fastest if reduction >= 0.05 and paired_wins > a.repeats / 2 else 4000
    recovery = crash_recovery(output / "observed_http.ndjson", states / "crash_recovery", source["origin"],
                              fastest, source, reference)
    report = {"kind": "instrumented_http_prefix_replay", "is_full_scenario": False,
        "name": a.name, "scenario": a.scenario, "source": source, "repeats": a.repeats,
        "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "python": sys.version, "sqlite": sqlite3.sqlite_version,
        "adoption_rule": ">=5% median runtime reduction and a majority of paired wins; full scenario validation still required",
        "trials": trials, "median_seconds": medians, "fastest_batch_size": fastest,
        "candidate_batch_size": candidate, "median_runtime_reduction": reduction,
        "all_materializations_identical": True, "crash_recovery": recovery,
        "limitations": "fixed history prefix and warm local file replay; component timers add overhead; excludes HTTP, simulator startup and decisions; not a full-run speed claim"}
    (ROOT / "reports" / (a.name + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"median_seconds": medians, "candidate_batch_size": candidate,
                      "median_runtime_reduction": reduction, "crash_recovery": "passed"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
