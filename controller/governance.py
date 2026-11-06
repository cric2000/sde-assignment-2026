"""Persisted proposal state machine. All controller changes pass this boundary."""

import json
import uuid
from copy import deepcopy
from datetime import datetime

import httpx

from .client import APIError
from .model import Cohorts, auto_permission, changed_dimensions, config_diff, dumps, validate_config

ACTIVE = ("draft", "sending", "sending_auto", "pending", "approved", "applying")


class Governance:
    def __init__(self, api, store, preferences=None):
        self.api, self.store = api, store
        self.preferences = preferences or {}

    def active(self, wid):
        return self.store.db.execute("SELECT * FROM proposals WHERE workflow=? AND status IN (?,?,?,?,?,?) "
                                     "ORDER BY minute", (wid, *ACTIVE)).fetchall()

    def transition(self, row, status, minute, detail=None):
        self.store.db.execute("UPDATE proposals SET status=?,updated=? WHERE id=?", (status, minute, row["id"]))
        self.store.decision(row["workflow"], minute, "proposal_" + status,
                            {"proposal_id": row["id"], "kind": row["kind"], "detail": detail})

    def propose(self, workflow, current, config, minute, kind, rationale, evidence,
                context=None, impact=0.2):
        wid = workflow["workflow_id"]
        validate_config(workflow, config)
        if current["config"] == config:
            return None
        if self.active(wid):
            return None
        proposal_id = "ctl_" + uuid.uuid4().hex
        # Count observed active leads only; exact remaining dial eligibility is not exposed.
        exposure = self.store.db.execute("SELECT COUNT(*) leads,COUNT(DISTINCT person) people FROM leads "
                                         "WHERE workflow=? AND concluded=0", (wid,)).fetchone()
        cohort_counts = {}
        matcher = Cohorts(config)
        for row in self.store.db.execute("SELECT features,COUNT(*) n FROM leads WHERE workflow=? "
                                          "AND concluded=0 GROUP BY features", (wid,)):
            cid = matcher.match(json.loads(row["features"]))
            cohort_counts[cid] = cohort_counts.get(cid, 0) + row["n"]
        body = {"proposal_id": proposal_id, "workflow_id": wid, "base_version": current["version"],
                "proposed_config": config, "change_summary": rationale[:240],
                "diff": config_diff(current["config"], config), "affected_leads": exposure["leads"],
                "dry_run": {"kind": "observed_population_exposure_replay", "active_leads_upper_bound": exposure["leads"],
                            "unique_people": exposure["people"], "cohorts": cohort_counts,
                            "cohort_definition": matcher.version, "counterfactual_lift": "unknown",
                            "mid_flight": workflow["mid_flight"],
                            "limitation": "includes cadence-expired leads; exact remaining eligibility is not exposed"},
                "impact_score": max(0, min(1, impact)), "rationale": rationale,
                "evidence": evidence, "expires_in_hours": workflow["governance"]["proposal_ttl_hours"]}
        context = deepcopy(context or {})
        context["original_config"] = current["config"]
        context["evidence"] = evidence
        with self.store.transaction():
            self.store.db.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                  (proposal_id, wid, minute, kind, "draft", current["version"], dumps(body),
                                   dumps(context), None, None, minute))
            self.store.decision(wid, minute, "proposal_created", {"proposal_id": proposal_id, "kind": kind,
                                  "dimensions": changed_dimensions(current["config"], config), "evidence": evidence})
        return proposal_id

    def supersede(self, wid, minute, reason):
        with self.store.transaction():
            for row in self.active(wid):
                self.transition(row, "superseded", minute, reason)

    def _finish(self, row, current, minute):
        context = json.loads(row["context"])
        with self.store.transaction():
            self.transition(row, "applied", minute, {"version": current["version"], "approval_id": row["approval"]})
            self.store.save_config(row["workflow"], current, minute)
            if "experiment" in context:
                experiment = context["experiment"]
                experiment.update(start=minute, version=current["version"], expected_config=current["config"])
                self.store.save_experiment(row["workflow"], experiment)
            if "incident_id" in context:
                incident = self.store.db.execute("SELECT payload FROM incidents WHERE id=?", (context["incident_id"],)).fetchone()
                payload = json.loads(incident[0])
                payload.update(applied_minute=minute, applied_version=current["version"],
                               prompt=current["config"]["prompt_version"], proposal_id=row["id"])
                self.store.db.execute("UPDATE incidents SET status='verifying',payload=? WHERE id=?",
                                      (dumps(payload), context["incident_id"]))

    def _refresh_current(self, wid, minute):
        current = self.api.get(f"/workflows/{wid}/config")
        with self.store.transaction():
            self.store.save_config(wid, current, minute)
        return current

    def _request_allowed(self, workflow, minute, ts):
        policy = workflow["governance"]
        preference = self.preferences.get(workflow["tenant_id"], {})
        hour = datetime.fromisoformat(ts).hour + datetime.fromisoformat(ts).minute / 60
        start, end = policy["approver_hours"]
        start = max(start, preference.get("not_before_hour", start))
        if not start <= hour < end:
            return False
        limit = min(policy["max_requests_per_24h"], preference.get("human_requests_per_24h", 10**6))
        count = self.store.db.execute("SELECT COUNT(*) FROM proposals WHERE workflow=? AND submitted>?",
                                     (workflow["workflow_id"], minute - 1440)).fetchone()[0]
        return count < limit

    def reconcile(self, workflows, minute, ts):
        rows = self.store.db.execute("SELECT * FROM proposals WHERE status IN (?,?,?,?,?,?) ORDER BY minute",
                                     ACTIVE).fetchall()
        for row in rows:
            wid = row["workflow"]
            workflow = workflows[wid]
            body, context = json.loads(row["body"]), json.loads(row["context"])
            current = self._refresh_current(wid, minute)
            # A remote commit can be followed by a supervisor edit before recovery.
            history = None
            if row["status"] in ("sending_auto", "applying"):
                history = self.api.get(f"/workflows/{wid}/config/history")
                if isinstance(history, dict):
                    history = history.get("history", history.get("versions", []))
                matching = [h for h in history if h.get("proposal_id") == row["id"]
                            or (row["approval"] and h.get("approval_id") == row["approval"])]
                if matching:
                    applied = matching[-1]
                    applied.setdefault("config", body["proposed_config"])
                    self._finish(row, applied, minute)
                    continue
                if current["config"] == body["proposed_config"] and current["version"] > row["base"]:
                    self._finish(row, current, minute)
                    continue
            if current["version"] != row["base"]:
                with self.store.transaction():
                    self.transition(row, "stale", minute, {"current_version": current["version"]})
                continue
            try:
                if row["status"] in ("draft", "sending", "sending_auto"):
                    permitted, reasons = auto_permission(workflow, current["config"], body["proposed_config"])
                    preference = self.preferences.get(workflow["tenant_id"], {})
                    if preference.get("human_for_agent_changes") and any(
                        d in changed_dimensions(current["config"], body["proposed_config"])
                        for d in ("voice", "welcome_message", "prompt_version", "cohorts")
                    ):
                        permitted = False
                        reasons.append("client_preference:human_for_agent_changes")
                    if permitted and row["status"] != "sending":
                        with self.store.transaction():
                            self.transition(row, "sending_auto", minute, {"authority": "workflow_auto_policy"})
                        self.api.put(f"/workflows/{wid}/config", body)
                        self._finish(row, self._refresh_current(wid, minute), minute)
                        continue
                    # Even an ambiguous request must first be looked up by durable proposal ID.
                    remote = None
                    if row["status"] == "sending":
                        remote = next((a for a in self.api.get("/approvals", workflow_id=wid)
                                       if a.get("proposal_id") == row["id"]), None)
                    if remote is None:
                        if row["status"] == "draft" and not self._request_allowed(workflow, minute, ts):
                            continue
                        with self.store.transaction():
                            self.transition(row, "sending", minute, {"auto_not_used": reasons})
                            self.store.db.execute("UPDATE proposals SET submitted=COALESCE(submitted,?) WHERE id=?", (minute, row["id"]))
                        remote = self.api.post("/approvals", body)
                    with self.store.transaction():
                        self.store.db.execute("UPDATE proposals SET approval=?,status='pending',updated=? WHERE id=?",
                                              (remote["approval_id"], minute, row["id"]))
                        self.store.decision(wid, minute, "approval_submitted", {"proposal_id": row["id"], "approval_id": remote["approval_id"]})
                    # Poll on the next tick; time does not advance inside this operation.
                    continue
                remote = self.api.get("/approvals/" + row["approval"])
                if remote["status"] in ("rejected", "expired", "lapsed"):
                    with self.store.transaction():
                        self.transition(row, remote["status"], minute, remote)
                elif remote["status"] == "applied":
                    self._finish(row, current, minute)
                elif remote["status"] == "approved":
                    with self.store.transaction():
                        self.transition(row, "applying", minute, {"approval_id": row["approval"], "remote_approval": remote})
                    self.api.post(f"/workflows/{wid}/apply", {"approval_id": row["approval"]})
                    self._finish(row, self._refresh_current(wid, minute), minute)
            except APIError as error:
                if error.status >= 500:
                    raise
                # A duplicate proposal is not rejection: look it up next reconciliation.
                if error.status == 409 and "duplicate" in str(error.body):
                    continue
                status = "stale" if error.status == 409 else "lapsed" if error.status == 410 else "blocked"
                with self.store.transaction():
                    self.transition(row, status, minute, {"http_status": error.status, "body": error.body})
            except httpx.TransportError:
                # Intent state is already committed. Caller must retry reconciliation before advance.
                raise
