#!/usr/bin/env python3
"""Reproducible synthetic repeated-person, multiple-arm finite-look calibration.

This exercises the statistics, not simulator truth. Its rates describe this fixture;
they do not establish a distribution-free guarantee for production traffic.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controller.statistics import contrast, lower_bound


def trial(seed, treatment_effect, pickup_drop=0):
    rng = np.random.default_rng(seed)
    people = 800
    propensities = rng.beta(5, 5, people)
    rows = []
    for person, propensity in enumerate(propensities):
        for arm in ("control", "a", "b", "c"):
            dials = int(rng.integers(5, 16))
            connected = int(rng.binomial(dials, 0.6 - (pickup_drop if arm == "a" else 0)))
            engaged = int(rng.binomial(connected, min(0.99, propensity + (treatment_effect if arm == "a" else 0))))
            rows.append({"person": str(person), "arm": arm, "dials": dials, "connected": connected, "engaged": engaged})
    primary_won, fully_passed, arm_a_passed = False, False, False
    alpha = 0.05 / (3 * 2)
    for look in (rows[:len(rows) // 2], rows):
        for arm in ("a", "b", "c"):
            primary = contrast(look, arm, "control", "engagement_rate")
            guardrail = contrast(look, arm, "control", "pickup_rate")
            primary_pass = lower_bound(primary, alpha) > 0.01
            primary_won |= primary_pass
            fully_passed |= primary_pass and lower_bound(guardrail, alpha) > -0.02
            arm_a_passed |= arm == "a" and primary_pass and lower_bound(guardrail, alpha) > -0.02
    return bool(primary_won), bool(fully_passed), bool(arm_a_passed)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=200)
    p.add_argument("--out", default="reports/statistical_calibration.json")
    a = p.parse_args()
    started = time.monotonic()
    result = {"trials_per_case": a.trials, "seed_range": [0, a.trials - 1], "people_per_trial": 800,
              "arms": 4, "looks": 2, "per_comparison_alpha": 0.05 / 6,
              "nominal_primary_family_budget": 0.05, "practical_effect_threshold": 0.01,
              "guardrail_noninferiority_margin": 0.02,
              "limitations": "synthetic clustered normal-approximation calibration; not an exact finite-sample guarantee or incident-detector calibration"}
    for name, effect, drop in (("null", 0, 0), ("positive_effect", 0.15, 0), ("guardrail_harm", 0.15, 0.15)):
        outcomes = [trial(seed, effect, drop) for seed in range(a.trials)]
        result[name] = {"any_primary_pass": sum(x[0] for x in outcomes), "any_full_promotion_pass": sum(x[1] for x in outcomes),
                        "arm_a_full_promotion_pass": sum(x[2] for x in outcomes),
                        "primary_pass_fraction": sum(x[0] for x in outcomes) / a.trials,
                        "full_pass_fraction": sum(x[1] for x in outcomes) / a.trials}
    result["wall_seconds"] = time.monotonic() - started
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
