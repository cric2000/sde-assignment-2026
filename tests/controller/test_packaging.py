"""Submission packaging must not accept partial or inconsistent evaluations."""

import json

import pytest

from scripts import package_evidence


def evaluation(reports, name, *, finished=True, duplicates=0):
    reports.mkdir(exist_ok=True)
    metrics = {
        "final_state": {"finished": finished, "sim_minute": 1440, "end_minute": 1440, "last_event_id": 10},
        "integrity_check": "ok", "source_last_event_matches_watermark": True,
        "metrics": {"event_watermark": 10, "events_ingested": 10}, "durable_event_count": 10,
        "duplicate_proposal_applications": duplicates, "unjoined_calls": 0, "pending_proposals_at_finish": 0,
    }
    (reports / f"{name}_metrics.json").write_text(json.dumps(metrics))
    for suffix in ("_overview.json", "_audit.json"):
        (reports / (name + suffix)).write_text("{}")
    (reports / f"{name}.md").write_text("official grade")


@pytest.mark.parametrize("problem", ["unfinished", "duplicates", "missing_audit"])
def test_invalid_packaging_leaves_existing_evidence_untouched(tmp_path, monkeypatch, problem):
    reports = tmp_path / "reports"
    evaluation(reports, "valid")
    evaluation(reports, "invalid", finished=problem != "unfinished", duplicates=int(problem == "duplicates"))
    if problem == "missing_audit":
        (reports / "invalid_audit.json").unlink()
    existing = reports / "public_01.md"
    existing.write_text("previous evidence")
    before = {path.name: path.read_bytes() for path in reports.iterdir()}
    monkeypatch.setattr(package_evidence, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["package_evidence.py", "--dev", "valid", "--incidents", "invalid", "--load", "valid"])
    with pytest.raises((ValueError, FileNotFoundError)):
        package_evidence.main()
    assert {path.name: path.read_bytes() for path in reports.iterdir()} == before
    assert not (tmp_path / "runs").exists()


def test_complete_welcome_evidence_is_packaged_with_provenance(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    for name in ("dev", "incidents", "load", "welcome"):
        evaluation(reports, name)
    (reports / "interrupted.md").write_text("incomplete run")
    monkeypatch.setattr(package_evidence, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["package_evidence.py", "--dev", "dev", "--incidents", "incidents",
                                     "--load", "load", "--welcome", "welcome"])
    package_evidence.main()
    assert (reports / "welcome_message.md").read_text() == "official grade"
    assert json.loads((reports / "provenance.json").read_text())["welcome_message"]["evaluation_name"] == "welcome"
    assert not (reports / "interrupted.md").exists()
    assert list((tmp_path / "runs" / "report-archive").glob("*/interrupted.md"))
