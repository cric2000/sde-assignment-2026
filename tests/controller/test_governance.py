import json
from copy import deepcopy

import httpx
import pytest

from controller.governance import Governance
from controller.model import Cohorts, auto_permission, changed_dimensions, validate_config


def propose(gov, workflow, api):
    config = deepcopy(api.current["config"])
    config["allocations"]["default"]["voice"] = {"V1": 0.5, "V2": 0.5}
    return gov.propose(workflow, api.current, config, 600, "experiment_start", "test voice on supported population",
                       {"sample": 500})


def test_lost_approval_ack_is_reconciled_without_resubmission(store, workflow, api):
    gov = Governance(api, store)
    propose(gov, workflow, api)
    api.drop = "submit"
    with pytest.raises(httpx.ReadError):
        gov.reconcile({"test": workflow}, 600, "2027-01-04T10:00:00+05:30")
    gov.reconcile({"test": workflow}, 615, "2027-01-04T10:15:00+05:30")
    assert api.submissions == 1
    assert gov.active("test")[0]["status"] == "pending"


@pytest.mark.parametrize("supervisor_edit", [False, True])
def test_remote_apply_then_response_loss_never_double_applies(store, workflow, api, supervisor_edit):
    gov = Governance(api, store)
    propose(gov, workflow, api)
    gov.reconcile({"test": workflow}, 600, "2027-01-04T10:00:00+05:30")
    api.approvals[0]["status"] = "approved"
    api.drop = "apply"
    with pytest.raises(httpx.ReadError):
        gov.reconcile({"test": workflow}, 615, "2027-01-04T10:15:00+05:30")
    if supervisor_edit:
        api.current["version"] = 3
        api.current["actor"] = "supervisor"
        api.current["config"]["prompt_version"] = "P2"
        api.history.append(deepcopy(api.current))
    gov.reconcile({"test": workflow}, 630, "2027-01-04T10:30:00+05:30")
    gov.reconcile({"test": workflow}, 645, "2027-01-04T10:45:00+05:30")
    assert api.applies == 1
    assert store.db.execute("SELECT status FROM proposals").fetchone()[0] == "applied"
    if supervisor_edit:
        assert api.current["config"]["prompt_version"] == "P2"


def test_supervisor_stale_base_is_not_overwritten(store, workflow, api):
    gov = Governance(api, store)
    propose(gov, workflow, api)
    gov.reconcile({"test": workflow}, 600, "2027-01-04T10:00:00+05:30")
    api.current["version"] = 2
    api.current["config"]["prompt_version"] = "P2"
    api.approvals[0]["status"] = "approved"
    gov.reconcile({"test": workflow}, 615, "2027-01-04T10:15:00+05:30")
    assert api.applies == 0
    assert store.db.execute("SELECT status FROM proposals").fetchone()[0] == "stale"


def test_auto_apply_response_loss_reconciles(store, workflow, api):
    workflow["governance"]["auto_apply"] = {"enabled": True, "allowed_dims": ["voice"], "max_shift": 0.5}
    gov = Governance(api, store)
    propose(gov, workflow, api)
    api.drop = "auto"
    with pytest.raises(httpx.ReadError):
        gov.reconcile({"test": workflow}, 600, "2027-01-04T10:00:00+05:30")
    gov.reconcile({"test": workflow}, 615, "2027-01-04T10:15:00+05:30")
    assert api.applies == 1 and api.submissions == 0


def test_hours_and_stricter_client_preference(store, workflow, api):
    gov = Governance(api, store, {"client": {"not_before_hour": 11, "human_requests_per_24h": 1}})
    propose(gov, workflow, api)
    gov.reconcile({"test": workflow}, 600, "2027-01-04T10:00:00+05:30")
    assert api.submissions == 0
    gov.reconcile({"test": workflow}, 660, "2027-01-04T11:00:00+05:30")
    assert api.submissions == 1
    api.approvals[0]["status"] = "rejected"
    gov.reconcile({"test": workflow}, 675, "2027-01-04T11:15:00+05:30")
    propose(gov, workflow, api)
    gov.reconcile({"test": workflow}, 700, "2027-01-04T11:40:00+05:30")
    assert api.submissions == 1


@pytest.mark.parametrize("terminal", ["expired", "lapsed", "rejected"])
def test_approval_terminal_status_preserved(store, workflow, api, terminal):
    gov = Governance(api, store)
    propose(gov, workflow, api)
    gov.reconcile({"test": workflow}, 600, "2027-01-04T10:00:00+05:30")
    api.approvals[0]["status"] = terminal
    gov.reconcile({"test": workflow}, 615, "2027-01-04T10:15:00+05:30")
    assert store.db.execute("SELECT status FROM proposals").fetchone()[0] == terminal


def test_total_variation_uses_normalisation_and_inheritance(workflow, config):
    workflow["governance"]["auto_apply"] = {"enabled": True, "allowed_dims": ["voice"], "max_shift": 0.2}
    config["cohorts"] = [{"id": "hi", "where": {"lang": ["hi"]}}]
    new = deepcopy(config)
    new["allocations"]["default"]["voice"] = {"V1": 8, "V2": 2}
    assert auto_permission(workflow, config, new)[0]
    new["allocations"]["default"]["voice"] = {"V1": 7, "V2": 3}
    assert not auto_permission(workflow, config, new)[0]
    new = deepcopy(config)
    new["cohorts"].append({"id": "en", "where": {"lang": ["en"]}})
    assert "cohorts" in changed_dimensions(config, new)
    assert not auto_permission(workflow, config, new)[0]


def test_shared_cohort_order_and_schedule_validation(workflow, config):
    config["cohorts"] = [{"id": "senior", "where": {"age_band": ["60+"]}},
                         {"id": "hi", "where": {"lang": ["hi"]}}]
    matcher = Cohorts(config)
    assert matcher.match({"age_band": "60+", "lang": "hi"}) == "senior"
    assert matcher.match({"lang": "en"}) == "default"
    config["schedule"]["hour_weights"] = {"default": {"8": 1}}
    with pytest.raises(ValueError):
        validate_config(workflow, config)


def test_rejected_intent_requires_cooldown_and_new_observations(store, workflow, api):
    gov = Governance(api, store)
    first = propose(gov, workflow, api)
    gov.reconcile({"test": workflow}, 600, "2027-01-04T10:00:00+05:30")
    api.approvals[0]["status"] = "rejected"
    gov.reconcile({"test": workflow}, 615, "2027-01-04T10:15:00+05:30")
    assert propose(gov, workflow, api) is None
    config = deepcopy(api.current["config"])
    config["allocations"]["default"]["voice"] = {"V1": 0.5, "V2": 0.5}
    store.counts = lambda *args: {"all": {"dials": 500, "connected": 250, "engaged": 100, "flagged": 1}}
    successor = gov.propose(workflow, api.current, config, 900, "experiment_start", "new evidence", {"plan": "unchanged"})
    assert successor and successor != first
    body = json.loads(store.db.execute("SELECT body FROM proposals WHERE id=?", (successor,)).fetchone()[0])
    assert body["evidence"]["follow_up"]["prior_proposal_id"] == first
    assert body["evidence"]["follow_up"]["treatment_outcomes_claimed"] is False


def test_exposure_estimate_filters_expired_campaigns(store, workflow, api):
    with store.transaction():
        for name, minute in (("old", -14400), ("current", 0)):
            store.db.execute("INSERT INTO campaigns VALUES (?,?,?,?,?)", (name, "test", minute, 1, 1))
            store.db.execute("INSERT INTO leads(id,workflow,campaign,person,features) VALUES (?,?,?,?,?)",
                              (name, "test", name, name, '{"lang":"en"}'))
    gov = Governance(api, store)
    pid = propose(gov, workflow, api)
    body = json.loads(store.db.execute("SELECT body FROM proposals WHERE id=?", (pid,)).fetchone()[0])
    assert body["affected_leads"] == 1
    assert body["dry_run"]["cohorts"] == {"default": 1}
    assert "priority_components" in body["dry_run"]
