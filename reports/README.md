# Submitted evidence

| File | Purpose |
|---|---|
| public_01.md | Official development-scenario baseline comparison from the final recovery run |
| public_02.md | Official incident-scenario baseline comparison from the final verification run |
| public_03.md | Complete load benchmark and measurement limits |
| public_01_metrics.json | SIGKILL checkpoint, completion, integrity and timing measurements |
| public_02_metrics.json | Incident-run measurements and invariants |
| public_03_metrics.json | Raw load timings, lag histogram, RSS, counts and invariants |
| public_*_overview.json | Controller-owned consolidated view, proposals, experiments, incidents and credit operations |
| public_*_audit.json | Real controller-owned why-change query output |
| statistical_calibration.json | Reproducible synthetic null/power/guardrail calibration |
| crash_recovery.md | Kill/restart procedure, IDs, checkpoint and observed result |
| provenance.json | Actual evaluation names, own-state directories and raw log locations |
| work_session.json | Honest AI-assisted elapsed-time/disclosure record |

Stable public_01/public_02 filenames are copies of the selected official grader outputs; their contents are not rewritten. The metrics' `name` field preserves the actual evaluation name. scripts/package_evidence.py supplies aliases and archives earlier reports under ignored runs/report-archive/. We selected the final verification runs, including their negative outcomes; no selection by highest lift was performed.

Raw simulator baseline/candidate directories and runtime databases are intentionally not committed. They remain locally reproducible with scripts/evaluate.py. Controller state is separate under .controller-state/, and no simulator output or grade report is read by controller modules. Reproduce with new evaluation names because the harness refuses to overwrite existing runs.

The full submission explains a harmful exploration change, inconclusive learning, delayed safety mitigation, lack of observed real decay/promotion, and the limitations of local storage and approximate statistics. Reported relative outcome lift is not a percentage-point lift or a causal A/B estimate.
