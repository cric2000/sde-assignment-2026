"""Pure configuration/cohort rules shared by proposals, learning and inspection."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

DIMENSIONS = ("welcome_message", "voice", "caller_id")


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()[:24]


def timestamp_minute(ts):
    return int(datetime.fromisoformat(ts).timestamp() // 60)


def distribution(config, cohort, dimension):
    allocations = config.get("allocations", {})
    weights = allocations.get(cohort, {}).get(dimension)
    if weights is None:
        weights = allocations.get("default", {}).get(dimension, {})
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total else {}


def total_variation(a, b):
    return sum(abs(a.get(k, 0) - b.get(k, 0)) for k in a.keys() | b.keys()) / 2


@dataclass(frozen=True)
class Cohort:
    id: str
    where: dict

    def matches(self, features):
        return all(features.get(key) in values for key, values in self.where.items())


class Cohorts:
    def __init__(self, config):
        self.items = [Cohort(c["id"], c["where"]) for c in config.get("cohorts", [])]
        self.version = digest(config.get("cohorts", []))

    def match(self, features):
        return next((c.id for c in self.items if c.matches(features)), "default")

    def ids(self):
        return [c.id for c in self.items] + ["default"]

    def describe(self):
        return [{"id": c.id, "where": c.where} for c in self.items] + [{"id": "default", "where": {}}]


def changed_dimensions(old, new):
    changed = []
    if old.get("cohorts", []) != new.get("cohorts", []):
        changed.append("cohorts")
    ids = set(Cohorts(old).ids()) | set(Cohorts(new).ids())
    for dim in DIMENSIONS:
        if any(distribution(old, cid, dim) != distribution(new, cid, dim) for cid in ids):
            changed.append(dim)
    for dim in ("prompt_version", "schedule"):
        if old.get(dim) != new.get(dim):
            changed.append(dim)
    return changed


def auto_permission(workflow, old, new):
    policy = workflow["governance"]["auto_apply"]
    changed = changed_dimensions(old, new)
    reasons = []
    if not policy["enabled"]:
        reasons.append("auto_apply_disabled")
    for dim in changed:
        if dim not in policy["allowed_dims"]:
            reasons.append("human_required:" + dim)
    ids = set(Cohorts(old).ids()) | set(Cohorts(new).ids())
    for cid in ids:
        for dim in DIMENSIONS:
            shift = total_variation(distribution(old, cid, dim), distribution(new, cid, dim))
            if shift > policy["max_shift"] + 1e-9:
                reasons.append(f"shift:{cid}:{dim}:{shift:.4f}")
    return not reasons, reasons


def config_diff(old, new):
    return {key: {"before": old.get(key), "after": new.get(key)}
            for key in old.keys() | new.keys() if old.get(key) != new.get(key)}


def validate_config(workflow, config):
    """Local preflight. HTTP remains authoritative."""
    if set(config) - {"cohorts", "allocations", "prompt_version", "schedule"}:
        raise ValueError("unknown config field")
    cohorts = Cohorts(config)
    if len(cohorts.items) > 20 or len(set(cohorts.ids())) != len(cohorts.ids()):
        raise ValueError("invalid cohort IDs/count")
    if config["prompt_version"] not in workflow["options"]["prompt_version"]:
        raise ValueError("unknown prompt")
    for cid in config.get("allocations", {}):
        if cid not in cohorts.ids():
            raise ValueError("allocation for unknown cohort")
        for dim, weights in config["allocations"][cid].items():
            if dim not in DIMENSIONS or not weights or any(
                key not in workflow["options"][dim] or weight < 0 for key, weight in weights.items()
            ) or sum(weights.values()) <= 0:
                raise ValueError("invalid allocation")
    start, end = workflow["dialling_window"]
    for table in config.get("schedule", {}).get("hour_weights", {}).values():
        if any(not start <= int(hour) < end or weight < 0 for hour, weight in table.items()):
            raise ValueError("invalid schedule hour/weight")
