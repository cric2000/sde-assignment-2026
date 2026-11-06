"""Population-based safety/caller-ID detection and bounded credit-backed response."""

import json
import math
from copy import deepcopy

import httpx

from .client import APIError
from .model import Cohorts, digest, distribution, dumps
from .statistics import proportion_drop, rate


class Incidents:
    def __init__(self, api, store, governance):
        self.api, self.store, self.governance = api, store, governance

    def open(self, wid, kind=None):
        sql = "SELECT * FROM incidents WHERE workflow=? AND status NOT IN ('recovered','superseded','closed')"
        args = [wid]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return self.store.db.execute(sql + " ORDER BY minute", args).fetchall()

    def save(self, incident, status, payload, minute, detail=None):
        with self.store.transaction():
            self.store.db.execute("UPDATE incidents SET status=?,payload=? WHERE id=?", (status, dumps(payload), incident["id"]))
            self.store.decision(incident["workflow"], minute, "incident_" + status,
                                {"incident_id": incident["id"], "kind": incident["kind"], "detail": detail})

    def create(self, wid, kind, minute, payload):
        iid = "inc_" + digest([wid, kind, minute, payload])
        with self.store.transaction():
            self.store.db.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?)",
                                  (iid, wid, kind, "detected", minute, dumps(payload)))
            self.store.decision(wid, minute, "incident_detected", {"incident_id": iid, "kind": kind, "evidence": payload})
        self.governance.supersede(wid, minute, "incident takes priority; obsolete remote approvals must not be applied")
        return self.store.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()

    def step(self, workflow, current, minute):
        wid = workflow["workflow_id"]
        for incident in self.open(wid):
            payload = json.loads(incident["payload"])
            if incident["status"] == "needs_review":
                continue
            if incident["status"] == "verifying":
                self.verify(workflow, current, minute, incident, payload)
            elif incident["kind"] == "hallucination":
                self.respond_prompt(workflow, current, minute, incident, payload)
            elif incident["kind"] == "caller_id":
                self.respond_caller(workflow, current, minute, incident, payload)
        # Independent non-overlapping 2-hour windows; a single call never triggers action.
        if minute < 120 or minute % 120:
            return
        historical = self.store.counts(wid, -100000, 0).get("all", {})
        recent = self.store.counts(wid, minute - 120, minute, prompt=current["config"]["prompt_version"]).get("all", {})
        if not self.open(wid, "hallucination"):
            old_rate, new_rate = rate(historical, "flag_rate"), rate(recent, "flag_rate")
            enough = recent.get("connected", 0) >= 200 and recent.get("flagged", 0) >= 10 and old_rate is not None
            se = math.sqrt(old_rate * (1 - old_rate) / max(1, historical.get("connected", 0)) +
                           (new_rate or 0) * (1 - (new_rate or 0)) / max(1, recent.get("connected", 0))) if old_rate is not None else 1
            signal = bool(enough and new_rate - old_rate >= 0.03 and new_rate - old_rate > 3 * se)
            key = "hallucination_hits:" + wid + ":" + current["config"]["prompt_version"]
            hits = self.store.get(key, 0) + 1 if signal else 0
            with self.store.transaction():
                self.store.set(key, hits)
            if hits >= 2:
                incident = self.create(wid, "hallucination", minute, {
                    "trigger_prompt": current["config"]["prompt_version"], "baseline": historical,
                    "recent": recent, "windows": 2, "window_minutes": 120,
                    "detector": "minimum 200 connected/10 flags; +3pp and 3-SE rise for two windows"})
                self.respond_prompt(workflow, current, minute, incident, json.loads(incident["payload"]))
        if not self.open(wid, "caller_id"):
            baseline = self.store.counts(wid, -100000, max(0, minute - 360), "caller_id")
            latest = self.store.counts(wid, minute - 120, minute, "caller_id")
            suspects = []
            for did, counts in latest.items():
                signal = counts["dials"] >= 200 and proportion_drop(baseline.get(did, {}), counts, "pickup_rate", 0.05)
                key = "caller_hits:" + wid + ":" + did
                hits = self.store.get(key, 0) + 1 if signal else 0
                with self.store.transaction():
                    self.store.set(key, hits)
                if hits >= 2:
                    suspects.append(did)
            if suspects:
                incident = self.create(wid, "caller_id", minute, {
                    "suspects": suspects, "baseline": baseline, "recent": latest,
                    "before": self.store.counts(wid, minute - 120, minute).get("all", {}),
                    "window_minutes": 120, "windows": 2})
                self.respond_caller(workflow, current, minute, incident, json.loads(incident["payload"]))

    def respond_caller(self, workflow, current, minute, incident, payload):
        if self.governance.active(workflow["workflow_id"]):
            return
        if minute - payload.get("last_attempt", -10000) < 180:
            return
        suspects = set(payload["suspects"])
        alternatives = [did for did in workflow["options"]["caller_id"] if did not in suspects]
        if not alternatives:
            self.save(incident, "needs_review", payload, minute, "no permitted caller-ID alternative")
            return
        # Prefer an observed healthy ID; never assume an untried option is healthy.
        observed = [did for did in alternatives if payload["baseline"].get(did, {}).get("dials", 0) >= 200]
        chosen = max(observed, key=lambda d: (rate(payload["baseline"][d], "pickup_rate"), d)) if observed else sorted(alternatives)[0]
        proposed = deepcopy(current["config"])
        changed = False
        for cid in Cohorts(proposed).ids():
            weights = distribution(proposed, cid, "caller_id")
            if not any(weights.get(did, 0) > 0 for did in suspects):
                continue
            # A full rotation seeks human approval if above max_shift; do not staircase around policy.
            remaining = {did: weight for did, weight in weights.items() if did not in suspects and weight > 0}
            if not remaining:
                remaining = {chosen: 1.0}
            total = sum(remaining.values())
            proposed["allocations"].setdefault(cid, {})["caller_id"] = {d: w / total for d, w in remaining.items()}
            changed = True
        if not changed:
            self.save(incident, "superseded", payload, minute, "suspect IDs no longer allocated in latest config")
            return
        pid = self.governance.propose(workflow, current, proposed, minute, "caller_id_rotation",
            f"Remove caller IDs {', '.join(sorted(suspects))}: sustained pickup degradation in two population windows. "
            + ("Alternative has historical support." if observed else "Alternative is untried; monitor exposure."),
            {"incident_id": incident["id"], "baseline": payload["baseline"], "recent": payload["recent"],
             "alternative_observed": bool(observed)}, {"incident_id": incident["id"]}, impact=0.8)
        if pid:
            payload.update(last_attempt=minute, proposal_id=pid)
            self.save(incident, "awaiting_permission", payload, minute)

    def paid_request(self, key, wid, minute, kind, request, cost):
        row = self.store.db.execute("SELECT * FROM paid WHERE id=?", (key,)).fetchone()
        if row:
            if row["status"] == "done":
                return json.loads(row["response"])
            return None  # Ambiguous payment is retained, never blindly retried.
        remaining = self.api.get("/sim/state")["credits_remaining"]
        if remaining < cost + 2:
            return None
        with self.store.transaction():
            self.store.db.execute("INSERT INTO paid VALUES (?,?,?,?,?,?,?,?)",
                (key, wid, minute, kind, cost, "in_flight", dumps(request), None))
        try:
            result = self.api.post("/llm/suggest-fixes" if kind == "suggest" else "/validation/runs", request)
        except (httpx.TransportError, APIError) as error:
            with self.store.transaction():
                self.store.db.execute("UPDATE paid SET status='unknown',response=? WHERE id=?", (dumps({"error": str(error)}), key))
                self.store.decision(wid, minute, "paid_operation_unresolved", {"operation": key, "cost_reserved": cost})
            return None
        with self.store.transaction():
            self.store.db.execute("UPDATE paid SET status='done',response=? WHERE id=?", (dumps(result), key))
        return result

    def respond_prompt(self, workflow, current, minute, incident, payload):
        wid = workflow["workflow_id"]
        if current["config"]["prompt_version"] != payload["trigger_prompt"]:
            self.save(incident, "superseded", payload, minute, "prompt changed externally; old fix intent discarded")
            return
        if self.governance.active(wid) or minute - payload.get("last_attempt", -10000) < 180:
            return
        remaining = self.api.get("/sim/state")["credits_remaining"]
        if remaining < 13 and not payload.get("validated"):
            if incident["status"] != "credit_blocked":
                self.save(incident, "credit_blocked", payload, minute, "reserve 13 credits for suggestion + two n=500 validations")
            return
        if not payload.get("validated"):
            result = self.paid_request(incident["id"] + ":suggest", wid, minute, "suggest",
                {"workflow_id": wid, "problem": f"Population QA flags rose on prompt {payload['trigger_prompt']}; "
                 f"baseline={payload['baseline']}, recent={payload['recent']}. Propose grounded fixes; protect engagement."}, 3)
            if result is None:
                self.save(incident, "needs_review", payload, minute, "paid operation ambiguous or budget insufficient")
                return
            candidates = result.get("suggestions", result.get("candidates", result.get("fixes", [])))
            candidates = [c for c in candidates if c.get("prompt_version") in workflow["options"]["prompt_version"]
                          and c["prompt_version"] != payload["trigger_prompt"]]
            def priority(candidate):
                description = (candidate.get("summary", "") + " " + workflow.get("prompt_catalog", {}).get(candidate["prompt_version"], "")).lower()
                return ("ground" not in description, "strict" in description or "verbatim" in description, candidate["prompt_version"])
            candidates.sort(key=priority)
            for candidate in candidates[:2]:
                prompt = candidate["prompt_version"]
                validation = self.paid_request(incident["id"] + ":validate:" + prompt, wid, minute,
                    "validation", {"workflow_id": wid, "prompt_version": prompt, "n": 500}, 5)
                if validation and validation["pass_rate"] >= workflow["governance"]["min_validation_pass_rate"]:
                    validation_id = validation.get("validation_run_id", validation.get("run_id"))
                    if not validation_id:
                        raise ValueError("validation response omitted validation_run_id/run_id")
                    payload["validated"] = {"prompt": prompt, "validation_run_id": validation_id, "result": validation}
                    break
            if not payload.get("validated"):
                self.save(incident, "needs_review", payload, minute, "no candidate passed bounded validation; no unsafe application")
                return
        proposed = deepcopy(current["config"])
        proposed["prompt_version"] = payload["validated"]["prompt"]
        pid = self.governance.propose(workflow, current, proposed, minute, "hallucination_fix",
            "Apply a validated grounded prompt to mitigate a sustained QA-flag spike. "
            "Prompt affects all campaigns immediately; engagement must be verified after application.",
            {"validation_run_id": payload["validated"]["validation_run_id"], "incident_id": incident["id"],
             "population_evidence": {"baseline": payload["baseline"], "recent": payload["recent"]}},
            {"incident_id": incident["id"]}, impact=0.95)
        if pid:
            payload.update(last_attempt=minute, proposal_id=pid)
            self.save(incident, "awaiting_permission", payload, minute)

    def verify(self, workflow, current, minute, incident, payload):
        start = payload["applied_minute"]
        if minute - start < 120:
            return
        if current["version"] != payload["applied_version"]:
            self.save(incident, "superseded", payload, minute, "configuration changed during verification; new epoch required")
            return
        latest = self.store.counts(workflow["workflow_id"], ((start + 59) // 60) * 60, minute,
                                  prompt=payload["prompt"]).get("all", {})
        if latest.get("connected", 0) < 200:
            return
        payload["post_apply"] = latest
        if incident["kind"] == "hallucination":
            baseline = payload["baseline"]
            safe = rate(latest, "flag_rate") <= rate(baseline, "flag_rate") + 0.03
            guardrail_bad = proportion_drop(baseline, latest, "engagement_rate", 0.02)
            if guardrail_bad:
                self.save(incident, "needs_review", payload, minute,
                          "engagement degraded after safety fix; do not revert to an unvalidated unsafe prompt")
            elif safe:
                self.save(incident, "recovered", payload, minute,
                          "flags returned near baseline; no supported engagement degradation observed (not proof of noninferiority)")
        else:
            before = payload["before"]
            improved = rate(latest, "pickup_rate") >= (rate(before, "pickup_rate") or 0) + 0.03
            if improved and not proportion_drop(before, latest, "engagement_rate", 0.02):
                self.save(incident, "recovered", payload, minute, "pickup recovered without detected engagement deterioration")
        if minute - start >= 1440 and incident["status"] == "verifying":
            # Re-read because the above may have closed the incident in this invocation.
            status = self.store.db.execute("SELECT status FROM incidents WHERE id=?", (incident["id"],)).fetchone()[0]
            if status == "verifying":
                self.save(incident, "needs_review", payload, minute, "insufficient recovery after 24h; intervention required")
