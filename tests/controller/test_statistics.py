import numpy as np

from controller.statistics import contrast, lower_bound, rate, sample_target


def rows(seed, effect=0, people=1200):
    rng = np.random.default_rng(seed)
    result = []
    for person in range(people):
        # Each person can receive both arms and several attempts.
        propensity = rng.beta(5, 5)
        for arm in ("a", "b"):
            n = int(rng.integers(1, 4))
            connected = int(rng.binomial(n, 0.6))
            engaged = int(rng.binomial(connected, min(1, propensity + (effect if arm == "a" else 0))))
            result.append({"person": str(person), "arm": arm, "dials": n, "connected": connected, "engaged": engaged})
    return result


def test_denominators_and_empty_support():
    assert rate({"dials": 10, "connected": 5, "engaged": 2}, "engagement_rate") == 0.4
    assert rate({"dials": 10, "connected": 5, "engaged": 2}, "pickup_rate") == 0.5
    assert rate({}, "engagement_rate") is None
    assert contrast([], "a", "b", "engagement_rate") is None


def test_deterministic_cluster_contrast_and_power():
    observations = rows(43, 0.2)
    estimate = contrast(observations, "a", "b", "engagement_rate")
    assert lower_bound(estimate, 0.005) > 0.01
    assert estimate == contrast(observations, "a", "b", "engagement_rate")
    assert sample_target(0.5, 0.001) > sample_target(0.5, 0.01)


def test_synthetic_null_false_winners():
    # Regression bound, not a claim that this fixture proves the nominal FWER.
    false = sum(lower_bound(contrast(rows(seed), "a", "b", "engagement_rate"), 0.005) > 0.01
                for seed in range(100))
    assert false <= 4
