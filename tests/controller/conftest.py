from copy import deepcopy

import pytest

from controller.store import Store


@pytest.fixture
def config():
    return {"cohorts": [], "allocations": {"default": {"welcome_message": {"W1": 1},
            "voice": {"V1": 1}, "caller_id": {"D1": 0.5, "D2": 0.5}}},
            "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}


@pytest.fixture
def workflow():
    return {"workflow_id": "test", "tenant_id": "client", "mid_flight": "allowed",
            "dialling_window": [9, 21], "objective": {"primary": "engagement_rate", "guardrail": {"pickup_rate_max_drop": 0.02}},
            "options": {"voice": ["V1", "V2"], "welcome_message": ["W1", "W2"],
                        "caller_id": ["D1", "D2", "D3"], "prompt_version": ["P1", "P2"]},
            "governance": {"auto_apply": {"enabled": False, "allowed_dims": [], "max_shift": 0},
                           "approver_hours": [10, 19], "max_requests_per_24h": 2,
                           "proposal_ttl_hours": 24, "apply_window_hours": 2, "min_validation_pass_rate": 0.97}}


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "test.db")
    with result.transaction():
        result.set("origin", 0)
    yield result
    result.close()


class FakeAPI:
    """Independent stateful HTTP-contract double, including lost acknowledgements."""

    def __init__(self, config):
        self.current = {"workflow_id": "test", "version": 1, "actor": "business_default", "config": deepcopy(config)}
        self.history = [deepcopy(self.current)]
        self.approvals = []
        self.drop = None
        self.applies = 0
        self.submissions = 0

    def get(self, path, **params):
        if path.endswith("/config/history"):
            return deepcopy(self.history)
        if path.endswith("/config"):
            return deepcopy(self.current)
        if path == "/approvals":
            return deepcopy(self.approvals)
        if path.startswith("/approvals/"):
            return deepcopy(next(a for a in self.approvals if a["approval_id"] == path.rsplit("/", 1)[1]))
        if path == "/sim/state":
            return {"credits_remaining": 100}
        raise AssertionError(path)

    def commit(self, body, approval=None):
        self.applies += 1
        self.current = {"workflow_id": "test", "version": self.current["version"] + 1,
                        "actor": "controller_approved" if approval else "controller_auto",
                        "proposal_id": body["proposal_id"], "approval_id": approval,
                        "config": deepcopy(body["proposed_config"])}
        self.history.append(deepcopy(self.current))

    def maybe_drop(self, operation):
        if self.drop == operation:
            import httpx
            self.drop = None
            raise httpx.ReadError("response lost after remote commit")

    def post(self, path, body):
        if path == "/approvals":
            self.submissions += 1
            item = dict(body, approval_id="apr1", status="pending")
            self.approvals.append(item)
            self.maybe_drop("submit")
            return deepcopy(item)
        if path.endswith("/apply"):
            approval = next(a for a in self.approvals if a["approval_id"] == body["approval_id"])
            self.commit(approval, approval["approval_id"])
            approval["status"] = "applied"
            self.maybe_drop("apply")
            return deepcopy(self.current)
        raise AssertionError(path)

    def put(self, path, body):
        self.commit(body)
        self.maybe_drop("auto")
        return deepcopy(self.current)


@pytest.fixture
def api(config):
    return FakeAPI(config)
