from copy import deepcopy

from controller.experiments import Experiments
from controller.governance import Governance


def experiment(config):
    return {"status": "exploring", "expected_config": deepcopy(config), "dimension": "voice", "start": 0,
            "version": 1, "look": 1, "alpha": 0.01, "prompt": "P1", "controls": {"default": "V1"},
            "options": ["V1", "V2"], "targets": {"default": 200}, "original_config": deepcopy(config),
            "minimum_effect": 0.01, "guardrail_alpha": 0.01}


def test_pending_unapplied_plan_produces_no_treatment_learning(store, api, workflow):
    gov = Governance(api, store)
    planned = deepcopy(api.current["config"])
    planned["allocations"]["default"]["voice"] = {"V1": 0.5, "V2": 0.5}
    gov.propose(workflow, api.current, planned, 0, "experiment_start", "experiment", {"planned": True},
                {"experiment": experiment(planned)})
    engine = Experiments(store, gov)
    engine.step(workflow, api.current, 60)
    assert store.experiment("test") is None
    assert store.db.execute("SELECT COUNT(*) FROM decisions WHERE kind='experiment_look'").fetchone()[0] == 0


def test_supervisor_owned_dimension_wins(store, api, workflow):
    with store.transaction():
        store.save_experiment("test", experiment(api.current["config"]))
    api.current["config"]["allocations"]["default"]["voice"] = {"V2": 1}
    engine = Experiments(store, Governance(api, store))
    engine.step(workflow, api.current, 60)
    assert store.experiment("test")["status"] == "invalidated"
    assert api.applies == 0


def test_prompt_change_starts_new_epoch_without_new_alpha(store, api, workflow):
    with store.transaction():
        store.save_experiment("test", experiment(api.current["config"]))
    api.current["config"]["prompt_version"] = "P2"
    api.current["version"] = 2
    engine = Experiments(store, Governance(api, store))
    engine.step(workflow, api.current, 60)
    saved = store.experiment("test")
    assert saved["start"] == 60 and saved["prompt"] == "P2"
    assert saved["look"] == 1 and saved["alpha"] == 0.01


def test_campaign_locked_old_versions_are_excluded_from_experiment(store):
    def call(eid, version, prompt):
        return (eid, "test", 100, str(eid), str(eid), "campaign", version, "default", "W1", "V2", "D1", prompt, 1, 1, 0)
    with store.transaction():
        store.db.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [call(1, 1, "P2"), call(2, 2, "P2"), call(3, 2, "P1")])
    observations = store.person_arms("test", 0, 200, "voice", "default", "P2", 2)
    assert len(observations) == 1 and observations[0]["person"] == "2"


def test_rejected_recommendation_does_not_add_statistical_looks(store, api, workflow):
    saved = experiment(api.current["config"])
    proposed = deepcopy(api.current["config"])
    proposed["allocations"]["default"]["voice"] = {"V2": 0.8, "V1": 0.2}
    saved["look"] = 2
    saved["recommendation"] = {"minute": 0, "config": proposed, "kind": "experiment_promotion",
        "rationale": "previously supported decision", "evidence": {"look": 2},
        "experiment": {"status": "promoted"}, "impact": 0.2}
    with store.transaction():
        store.save_experiment("test", saved)
    gov = Governance(api, store)
    engine = Experiments(store, gov)
    engine.evaluate(workflow, api.current, 60, saved)
    gov.reconcile({"test": workflow}, 600, "2027-01-04T10:00:00+05:30")
    api.approvals[0]["status"] = "rejected"
    gov.reconcile({"test": workflow}, 615, "2027-01-04T10:15:00+05:30")
    engine.evaluate(workflow, api.current, 700, store.experiment("test"))
    assert store.experiment("test")["look"] == 2


def test_supported_language_creates_one_shared_governed_cohort(store, api, workflow):
    with store.transaction():
        store.db.execute("INSERT INTO campaigns VALUES (?,?,?,?,?)", ("history", "test", -60, 2000, 1))
        for language in ("en", "hi"):
            for i in range(1000):
                person = language + str(i)
                eid = i + (1 if language == "en" else 1001)
                store.db.execute("INSERT INTO leads(id,workflow,campaign,person,features) VALUES (?,?,?,?,?)",
                                  (person, "test", "history", person, '{"lang":"' + language + '"}'))
                store.db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (eid, "test", -60, person, person, "history", 1, "default", "W1", "V1", "D1", "P1", 1, i % 2, 0))
    engine = Experiments(store, Governance(api, store))
    engine.step(workflow, api.current, 0)
    import json
    body = json.loads(store.db.execute("SELECT body FROM proposals").fetchone()[0])
    assert body["proposed_config"]["cohorts"] == [{"id": "language_en", "where": {"lang": ["en"]}}]
    assert body["dry_run"]["cohorts"] == {"language_en": 1000, "default": 1000}


def test_decay_restores_only_owned_dimension(store, api, workflow):
    original = deepcopy(api.current["config"])
    api.current["config"]["allocations"]["default"]["voice"] = {"V1": 0.2, "V2": 0.8}
    api.current["config"]["prompt_version"] = "P2"
    saved = experiment(api.current["config"])
    saved.update(status="promoted", winners={"default": "V2"}, original_config=original, decay_hits=0, prompt="P2")
    observations = []
    for i in range(300):
        for arm, successes in (("V1", 8), ("V2", 1)):
            observations.append({"person": str(i), "arm": arm, "dials": 10, "connected": 10, "engaged": successes})
    store.person_arms = lambda *args: observations
    engine = Experiments(store, Governance(api, store))
    engine.decay(workflow, api.current, 720, saved)
    engine.decay(workflow, api.current, 1440, store.experiment("test"))
    import json
    row = store.db.execute("SELECT kind,body FROM proposals").fetchone()
    assert row["kind"] == "decay_rollback"
    proposed = json.loads(row["body"])["proposed_config"]
    assert proposed["prompt_version"] == "P2"
    assert proposed["allocations"]["default"]["voice"] == {"V1": 1}


def test_expired_recommendation_keeps_safety_monitor_without_new_looks(store, api, workflow):
    saved = experiment(api.current["config"])
    saved["look"] = 2
    saved["recommendation"] = {"minute": 0}
    with store.transaction():
        store.save_experiment("test", saved)
    engine = Experiments(store, Governance(api, store))
    engine.evaluate(workflow, api.current, 1500, saved)
    assert store.experiment("test")["status"] == "monitor_only"
    checks = []
    engine.monitor_harm = lambda *args: checks.append(True) or False
    engine.evaluate = lambda *args: (_ for _ in ()).throw(AssertionError("no third look"))
    engine.step(workflow, api.current, 1560)
    assert checks == [True]
