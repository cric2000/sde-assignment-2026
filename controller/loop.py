"""Deterministic orchestration; no scenario files or simulator imports."""

import json
import logging
import resource
import time

import httpx

from .client import APIError
from .experiments import Experiments
from .governance import Governance
from .incidents import Incidents
from .ingest import Ingester
from .model import timestamp_minute

LOG = logging.getLogger("controller")


class Loop:
    def __init__(self, api, store, preferences=None, dimension="voice"):
        self.api, self.store = api, store
        self.governance = Governance(api, store, preferences)
        self.ingester = Ingester(api, store)
        self.experiments = Experiments(store, self.governance, dimension)
        self.incidents = Incidents(api, store, self.governance)
        self.workflows, self.configs = {}, {}

    def discover(self, minute):
        self.workflows = {w["workflow_id"]: w for w in self.api.get("/workflows")}
        for wid in self.workflows:
            self.configs[wid] = self.api.get(f"/workflows/{wid}/config")
            history = self.api.get(f"/workflows/{wid}/config/history")
            if isinstance(history, dict):
                history = history.get("history", history.get("versions", []))
            with self.store.transaction():
                prompt = self.configs[wid]["config"]["prompt_version"]
                if self.store.get("observed_prompt:" + wid) != prompt:
                    self.store.set("observed_prompt:" + wid, prompt)
                    self.store.set("qa_epoch:" + wid, minute)
                    self.store.set("qa_evaluated_answers:" + wid, 0)
                    self.store.set("hallucination_hits:" + wid + ":" + prompt, 0)
                self.store.set("workflow:" + wid, self.workflows[wid])
                for snapshot in history:
                    if "config" in snapshot:
                        observed = timestamp_minute(snapshot["applied_at"]) - self.store.get("origin") if snapshot.get("applied_at") else minute
                        self.store.save_config(wid, snapshot, observed)
                self.store.save_config(wid, self.configs[wid], minute)

    def run(self):
        started = time.monotonic()
        state = self.api.get("/sim/state")
        origin = timestamp_minute(state["ts"]) - state["sim_minute"]
        if self.store.get("origin", origin) != origin or state["sim_minute"] < self.store.get("sim_minute", 0):
            raise RuntimeError("Simulator world reset or wrong state directory. Use a new CONTROLLER_STATE_DIR for a new run.")
        with self.store.transaction():
            self.store.set("origin", origin)
            self.store.set("scenario", state["scenario"])
            self.store.set("started_ts", self.store.get("started_ts", state["ts"]))
        self.discover(state["sim_minute"])
        failures = 0
        try:
            while True:
                tick_started = time.monotonic()
                try:
                    state = self.api.get("/sim/state")
                    minute = state["sim_minute"]
                    n, ingest_elapsed = self.ingester.drain()
                    # Reconcile authority before decisions, and before advancing the world's clock.
                    self.governance.reconcile(self.workflows, minute, state["ts"])
                    latest_change = self.store.db.execute("SELECT MAX(id) FROM events WHERE type='config_changed'").fetchone()[0] or 0
                    changed = latest_change != self.store.get("refreshed_config_event", -1)
                    if changed or minute % 60 == 0 or not self.configs:
                        self.discover(minute)
                        with self.store.transaction():
                            self.store.set("refreshed_config_event", latest_change)
                    if minute % 60 == 0 and not state["finished"]:
                        decision_started = time.monotonic()
                        # Rotate across tenants first, then workflows: deterministic bounded fairness.
                        tenants = sorted({w["tenant_id"] for w in self.workflows.values()})
                        offset = (minute // 60) % max(1, len(tenants))
                        tenants = tenants[offset:] + tenants[:offset]
                        queue = {tenant: sorted(wid for wid, w in self.workflows.items() if w["tenant_id"] == tenant) for tenant in tenants}
                        ordered = []
                        while any(queue.values()):
                            for tenant in tenants:
                                if queue[tenant]:
                                    ordered.append(queue[tenant].pop(0))
                        for wid in ordered:
                            self.incidents.step(self.workflows[wid], self.configs[wid], minute)
                        for wid in ordered:
                            if not self.incidents.open(wid):
                                self.experiments.step(self.workflows[wid], self.configs[wid], minute)
                        self.governance.reconcile(self.workflows, minute, state["ts"])
                        decision_elapsed = time.monotonic() - decision_started
                        with self.store.transaction():
                            histogram = self.store.get("decision_lag_histogram", {})
                            for row in self.store.db.execute("SELECT minute,COUNT(*) n FROM events WHERE id>? "
                                "AND type='call' AND minute>=0 GROUP BY minute", (self.store.get("last_decision_event", -1),)):
                                lag = str(max(0, minute - row["minute"]))
                                histogram[lag] = histogram.get(lag, 0) + row["n"]
                            self.store.set("decision_lag_histogram", histogram)
                            self.store.set("last_decision_event", self.store.get("event_watermark", -1))
                            self.store.set("decision_seconds", self.store.get("decision_seconds", 0) + decision_elapsed)
                            self.store.set("decision_cycles", self.store.get("decision_cycles", 0) + 1)
                            self.store.set("decision_lag_minutes_max", max([int(k) for k in histogram] or [0]))
                            self.store.set("decision_compute_seconds_max", max(decision_elapsed, self.store.get("decision_compute_seconds_max", 0)))
                    elapsed = time.monotonic() - tick_started
                    with self.store.transaction():
                        self.store.set("sim_minute", minute)
                        self.store.set("state", state)
                        self.store.set("finished", state["finished"])
                        self.store.set("peak_rss_kib", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                        self.store.set("tick_seconds_max", max(elapsed, self.store.get("tick_seconds_max", 0)))
                    if minute % 1440 == 0 or state["finished"]:
                        LOG.info(json.dumps({"event": "progress", "sim_minute": minute, "finished": state["finished"],
                                             "events": self.store.get("events_ingested", 0), "credits": state["credits_remaining"],
                                             "last_ingest_events": n, "last_ingest_seconds": round(ingest_elapsed, 3)}))
                    if state["finished"]:
                        return
                    # Not retried blindly. Next iteration reads time and rediscovers all chunks.
                    self.api.post("/sim/advance", {"minutes": 15})
                    failures = 0
                except (httpx.TransportError, httpx.HTTPStatusError, APIError) as error:
                    if isinstance(error, APIError) and error.status < 500:
                        if error.status == 409 and "finished" in str(error.body):
                            continue
                        raise
                    failures += 1
                    LOG.error(json.dumps({"event": "http_retry_reconcile", "failures": failures, "error": str(error)}))
                    if failures >= 8:
                        raise
                    time.sleep(min(2 ** failures, 15))
        finally:
            with self.store.transaction():
                self.store.set("controller_wall_seconds", self.store.get("controller_wall_seconds", 0) + time.monotonic() - started)
                self.store.set("peak_rss_kib", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
