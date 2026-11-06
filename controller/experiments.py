"""One-factor, finite-look experiments. No LLM participates in winner selection."""

from copy import deepcopy

from .model import Cohorts, distribution
from .statistics import contrast, lower_bound, rate, sample_target


class Experiments:
    def __init__(self, store, governance, dimension="voice"):
        self.store, self.governance, self.dimension = store, governance, dimension

    def step(self, workflow, current, minute):
        wid = workflow["workflow_id"]
        experiment = self.store.experiment(wid)
        if self.governance.active(wid):
            return
        if experiment:
            if experiment["status"] in ("exploring", "promoted"):
                if current["config"] != experiment["expected_config"]:
                    # Preserve new config, including supervisor changes. Never overwrite by snapshot.
                    with self.store.transaction():
                        experiment["status"] = "invalidated"
                        self.store.save_experiment(wid, experiment)
                        self.store.decision(wid, minute, "experiment_invalidated", {
                            "reason": "configuration changed outside this experiment; evidence epoch ended",
                            "observed_version": current["version"], "experiment": experiment})
                    return
                if experiment["status"] == "exploring":
                    self.evaluate(workflow, current, minute, experiment)
                else:
                    self.decay(workflow, current, minute, experiment)
            return
        # One predeclared experiment per workflow/run; no resetting alpha on repeated attempts.
        if workflow["objective"]["primary"] != "engagement_rate":
            return
        config = current["config"]
        options = sorted(workflow["options"][self.dimension])
        if len(options) < 2:
            return
        baseline = self.store.counts(wid, -100000, 0, "cohort")
        cohorts = [c for c in Cohorts(config).ids() if baseline.get(c, {}).get("connected", 0) >= 500]
        if not cohorts:
            return
        alpha = 0.05 / (2 * (len(options) - 1) * len(cohorts))
        controls, targets = {}, {}
        proposed = deepcopy(config)
        for cid in cohorts:
            weights = distribution(config, cid, self.dimension)
            control = sorted(weights, key=lambda v: (-weights[v], v))[0]
            controls[cid] = control
            targets[cid] = sample_target(rate(baseline[cid], "engagement_rate"), alpha)
            allocation = {v: (0.5 if v == control else 0.5 / (len(options) - 1)) for v in options}
            proposed["allocations"].setdefault(cid, {})[self.dimension] = allocation
        experiment = {"status": "exploring", "dimension": self.dimension, "options": options,
                      "controls": controls, "targets": targets, "look": 0, "alpha": alpha,
                      "guardrail_alpha": 0.05 / ((len(options) - 1) * len(cohorts) * 2),
                      "minimum_effect": 0.01, "design_mde": 0.15,
                      "original_config": config, "prompt": config["prompt_version"], "winners": {}}
        proposal = self.governance.propose(workflow, current, proposed, minute, "experiment_start",
                    f"Test {self.dimension} alternatives on supported cohorts; retain 50% reference traffic. "
                    "No variant is a winner until measured primary lift and guardrail safety pass.",
                    {"plan": experiment, "historical_support": baseline}, {"experiment": experiment}, impact=0.1)
        # Proposal creation is durable; experiment is saved only after actual application.
        if proposal:
            with self.store.transaction():
                self.store.set("experiment_attempt:" + wid, minute)

    def evaluate(self, workflow, current, minute, experiment):
        wid, dimension = workflow["workflow_id"], experiment["dimension"]
        if experiment.get("recommendation"):
            recommendation = experiment["recommendation"]
            if minute - recommendation["minute"] > 1440:
                with self.store.transaction():
                    experiment["status"] = "recommendation_expired"
                    self.store.save_experiment(wid, experiment)
                return
            if minute - experiment.get("last_recommendation_attempt", -10000) >= 180:
                self.governance.propose(workflow, current, recommendation["config"], minute,
                    recommendation["kind"], recommendation["rationale"], recommendation["evidence"],
                    {"experiment": recommendation["experiment"]}, impact=recommendation["impact"])
                with self.store.transaction():
                    experiment["last_recommendation_attempt"] = minute
                    self.store.save_experiment(wid, experiment)
            return
        observations = {}
        for cid, control in experiment["controls"].items():
            rows = self.store.person_arms(wid, experiment["start"], minute, dimension, cid,
                                           experiment["prompt"], experiment["version"])
            counts = {arm: sum(r["connected"] for r in rows if r["arm"] == arm)
                      for arm in experiment["options"]}
            needed = experiment["targets"][cid] * (experiment["look"] + 1)
            if any(n < needed for n in counts.values()):
                return
            observations[cid] = rows
        results, winners = {}, {}
        guardrail_metric, max_drop = next(iter(workflow["objective"]["guardrail"].items()))
        guardrail_metric = guardrail_metric.removesuffix("_max_drop")
        for cid, rows in observations.items():
            control = experiment["controls"][cid]
            results[cid] = {}
            candidates = []
            for arm in experiment["options"]:
                if arm == control:
                    continue
                primary = contrast(rows, arm, control, workflow["objective"]["primary"])
                guardrail = contrast(rows, arm, control, guardrail_metric)
                supported = primary and guardrail and min(primary["candidate_people"], primary["control_people"]) >= 100
                primary_lb = lower_bound(primary, experiment["alpha"]) if primary else None
                guardrail_lb = lower_bound(guardrail, experiment["guardrail_alpha"]) if guardrail else None
                passed = bool(supported and primary_lb > experiment["minimum_effect"] and guardrail_lb > -max_drop)
                results[cid][arm] = {"primary": primary, "guardrail": guardrail,
                                     "primary_lower_bound": primary_lb, "guardrail_lower_bound": guardrail_lb,
                                     "passed": passed}
                if passed:
                    candidates.append((primary_lb, arm))
            if candidates:
                winners[cid] = sorted(candidates, key=lambda x: (-x[0], x[1]))[0][1]
        experiment["look"] += 1
        with self.store.transaction():
            self.store.decision(wid, minute, "experiment_look", {
                "look": experiment["look"], "alpha": experiment["alpha"], "results": results,
                "winners": winners, "dimension": dimension})
            self.store.save_experiment(wid, experiment)
        if not winners and experiment["look"] < 2:
            return
        proposed = deepcopy(current["config"])
        for cid, control in experiment["controls"].items():
            proposed["allocations"].setdefault(cid, {})[dimension] = (
                {winners[cid]: 0.8, control: 0.2} if cid in winners
                else deepcopy(experiment["original_config"]["allocations"].get(cid, {}).get(
                    dimension, experiment["original_config"]["allocations"]["default"][dimension])))
        next_experiment = deepcopy(experiment)
        next_experiment.update(status="promoted" if winners else "inconclusive", winners=winners,
                               promotion_results=results, decay_hits=0)
        experiment["recommendation"] = {"minute": minute, "config": proposed,
            "kind": "experiment_promotion" if winners else "experiment_inconclusive_restore",
            "rationale": f"Promote supported {dimension} winners with 20% reference traffic." if winners else
                         f"No conclusive {dimension} winner after two planned looks; restore the reference allocation.",
            "evidence": {"look": experiment["look"], "results": results, "alpha": experiment["alpha"]},
            "experiment": next_experiment, "impact": min(1, max(
                [results[cid][arm]["primary_lower_bound"] / 0.2 for cid, arm in winners.items()] or [0.05]))}
        with self.store.transaction():
            self.store.save_experiment(wid, experiment)
        self.evaluate(workflow, current, minute, experiment)

    def decay(self, workflow, current, minute, experiment):
        # Evaluate non-overlapping 12-hour observation blocks, not hourly re-peeks.
        block = minute // 720
        if block <= experiment.get("last_decay_block", experiment["start"] // 720):
            return
        experiment["last_decay_block"] = block
        signals = {}
        for cid, winner in experiment["winners"].items():
            rows = self.store.person_arms(workflow["workflow_id"], max(experiment["start"], minute - 720),
                minute, experiment["dimension"], cid, experiment["prompt"], experiment["version"])
            result = contrast(rows, winner, experiment["controls"][cid], workflow["objective"]["primary"])
            if result and min(result["candidate_people"], result["control_people"]) >= 100:
                signals[cid] = result
        bad = any(r["difference"] < -0.05 and r["difference"] + 3 * r["se"] < 0 for r in signals.values())
        experiment["decay_hits"] = experiment.get("decay_hits", 0) + 1 if bad else 0
        with self.store.transaction():
            self.store.save_experiment(workflow["workflow_id"], experiment)
            self.store.decision(workflow["workflow_id"], minute, "decay_check", {"signals": signals, "bad": bad})
        if experiment["decay_hits"] < 2:
            return
        proposed = deepcopy(current["config"])
        for cid in experiment["winners"]:
            proposed["allocations"].setdefault(cid, {})[experiment["dimension"]] = distribution(
                experiment["original_config"], cid, experiment["dimension"])
        rolled_back = deepcopy(experiment)
        rolled_back["status"] = "rolled_back"
        self.governance.propose(workflow, current, proposed, minute, "decay_rollback",
            "Promoted variant degraded in two supported observation windows; restore reference allocation.",
            {"decay_signals": signals, "windows": 2}, {"experiment": rolled_back}, impact=0.8)
