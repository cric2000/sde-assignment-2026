import json

import httpx

from controller.governance import Governance
from controller.incidents import Incidents


def test_ambiguous_paid_request_is_never_blindly_repeated(store, api):
    incidents = Incidents(api, store, Governance(api, store))
    calls = []
    def lose(path, body):
        calls.append(path)
        raise httpx.ReadError("charged but response lost")
    api.post = lose
    body = {"workflow_id": "test", "problem": "population spike"}
    assert incidents.paid_request("operation", "test", 0, "suggest", body, 3) is None
    assert incidents.paid_request("operation", "test", 60, "suggest", body, 3) is None
    assert calls == ["/llm/suggest-fixes"]
    assert store.db.execute("SELECT status FROM paid").fetchone()[0] == "unknown"


def test_cached_validation_is_reused_without_repayment(store, api):
    incidents = Incidents(api, store, Governance(api, store))
    calls = []
    def validation(path, body):
        calls.append(path)
        return {"validation_run_id": "val", "pass_rate": 0.99}
    api.post = validation
    body = {"workflow_id": "test", "prompt_version": "P2", "n": 500}
    assert incidents.paid_request("validation", "test", 0, "validation", body, 5)["pass_rate"] == 0.99
    incidents.paid_request("validation", "test", 60, "validation", body, 5)
    assert len(calls) == 1


def test_one_flagged_call_cannot_trigger_incident(store, api, workflow):
    incidents = Incidents(api, store, Governance(api, store))
    store.counts = lambda *a, **kw: {"all": {"dials": 1, "connected": 1, "engaged": 0, "flagged": 1}}
    incidents.step(workflow, api.current, 120)
    incidents.step(workflow, api.current, 240)
    assert store.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_supervisor_prompt_edit_supersedes_old_fix(store, api, workflow):
    incidents = Incidents(api, store, Governance(api, store))
    incident = incidents.create("test", "hallucination", 100, {"trigger_prompt": "P1"})
    api.current["config"]["prompt_version"] = "P2"
    incidents.respond_prompt(workflow, api.current, 120, incident, {"trigger_prompt": "P1"})
    assert store.db.execute("SELECT status FROM incidents").fetchone()[0] == "superseded"
    assert store.db.execute("SELECT COUNT(*) FROM paid").fetchone()[0] == 0


def test_time_of_day_baseline_uses_matching_buckets(store):
    with store.transaction():
        for bucket, dials, connected in [(-14, 1000, 500), (-6, 1000, 100), (18, 1000, 100)]:
            store.db.execute("INSERT INTO rollups VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             ("test", bucket, "default", "W1", "V1", "D1", "P1", 1, dials, connected, 0, 0))
    baseline = store.counts("test", -1440, 0, "caller_id", hours=[18, 19])
    assert baseline["D1"]["connected"] / baseline["D1"]["dials"] == 0.1


def test_quarantined_caller_is_not_reintroduced(store, api, workflow):
    incidents = Incidents(api, store, Governance(api, store))
    with store.transaction():
        store.set("caller_quarantine:test", ["D2"])
    payload = {"suspects": ["D1"], "baseline": {"D2": {"dials": 1000, "connected": 900}}, "recent": {}}
    incident = incidents.create("test", "caller_id", 100, payload)
    incidents.respond_caller(workflow, api.current, 100, incident, payload)
    proposed = json.loads(store.db.execute("SELECT body FROM proposals").fetchone()[0])["proposed_config"]
    assert proposed["allocations"]["default"]["caller_id"] == {"D3": 1}


def test_overlapping_prompt_checks_require_fresh_answered_calls(store, api, workflow):
    incidents = Incidents(api, store, Governance(api, store))
    current_n = [400]
    def counts(wid, start, end, group=None, **kwargs):
        if group:
            return {}
        return {"all": {"dials": 10000, "connected": 5000, "engaged": 2500, "flagged": 50}} if start < 0 else {
            "all": {"dials": current_n[0] * 2, "connected": current_n[0], "engaged": current_n[0] // 2, "flagged": current_n[0] // 10}}
    store.counts = counts
    incidents.respond_prompt = lambda *args: None
    incidents.step(workflow, api.current, 120)
    incidents.step(workflow, api.current, 180)
    incidents.step(workflow, api.current, 180)
    assert store.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    current_n[0] = 600
    incidents.step(workflow, api.current, 240)
    assert store.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
