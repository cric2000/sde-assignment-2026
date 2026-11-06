"""Deterministic ratio estimates with person-clustered uncertainty.

Normal confidence limits are asymptotic. They are not an exact finite-sample
guarantee, and conditional engagement does not establish an unconditional effect.
"""

import math
from statistics import NormalDist


def rate(counts, metric):
    numerator, denominator = {"pickup_rate": ("connected", "dials"),
                              "engagement_rate": ("engaged", "connected"),
                              "flag_rate": ("flagged", "connected")}[metric]
    n = counts.get(denominator, 0)
    return counts.get(numerator, 0) / n if n else None


def contrast(rows, candidate, control, metric):
    numerator, denominator = {"pickup_rate": ("connected", "dials"),
                              "engagement_rate": ("engaged", "connected")}[metric]
    people = {}
    totals = {candidate: [0, 0], control: [0, 0]}
    support = {candidate: 0, control: 0}
    for row in rows:
        arm = row["arm"]
        if arm not in totals:
            continue
        y, n = row[numerator], row[denominator]
        people.setdefault(row["person"], {})[arm] = (y, n)
        totals[arm][0] += y
        totals[arm][1] += n
        support[arm] += int(n > 0)
    if min(t[1] for t in totals.values()) == 0 or len(people) < 2:
        return None
    rates = {arm: y / n for arm, (y, n) in totals.items()}
    variance = 0
    for arms in people.values():
        influence = 0
        for arm, sign in ((candidate, 1), (control, -1)):
            y, n = arms.get(arm, (0, 0))
            influence += sign * (y - rates[arm] * n) / totals[arm][1]
        variance += influence * influence
    variance *= len(people) / (len(people) - 1)
    return {"difference": rates[candidate] - rates[control], "se": math.sqrt(variance),
            "candidate_rate": rates[candidate], "control_rate": rates[control],
            "candidate_n": totals[candidate][1], "control_n": totals[control][1],
            "candidate_people": support[candidate], "control_people": support[control]}


def lower_bound(result, alpha):
    return result["difference"] - NormalDist().inv_cdf(1 - alpha) * result["se"]


def sample_target(baseline, alpha, effect=0.15, power=0.8):
    z = NormalDist().inv_cdf(1 - alpha) + NormalDist().inv_cdf(power)
    return max(200, math.ceil(2 * baseline * (1 - baseline) * z * z / effect ** 2))


def proportion_drop(old, new, metric, effect, z=3):
    """Population alarm; repeated use is NOT a family-wise significance guarantee."""
    a, b = rate(old, metric), rate(new, metric)
    denominator = "dials" if metric == "pickup_rate" else "connected"
    if a is None or b is None or min(old[denominator], new[denominator]) < 100:
        return False
    se = math.sqrt(a * (1 - a) / old[denominator] + b * (1 - b) / new[denominator])
    return a - b >= effect and a - b > z * se
