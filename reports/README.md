# Evaluation evidence

| Files | Purpose |
|---|---|
| [public_01.md](public_01.md), [public_02.md](public_02.md) | Unedited official grader outputs for the completed development/recovery and incident runs |
| [public_03.md](public_03.md) | Complete load benchmark and measurement limits |
| [welcome_message.md](welcome_message.md) | Unedited official grader output for the separate welcome experiment mode |
| `*_metrics.json` | Completion, integrity, watermarks, timings, RSS and crash measurements |
| `*_overview.json` | Controller-owned proposals, experiments, incidents, credit operations and metrics |
| `*_audit.json` | Controller-owned why-change queries; public_02 captures the prompt fix and public_03 the promotion |
| [statistical_calibration.json](statistical_calibration.json) | Synthetic null, power and guardrail calibration with reproducible seeds |
| [crash_recovery.md](crash_recovery.md) | Real SIGKILL/restart method, checkpoint and outcome |
| [provenance.json](provenance.json) | Evaluation names, controller-state directories and raw log locations |

These are the latest completed verification runs, selected by completion rather than highest lift. Stable filenames preserve the official grader text unchanged; metrics retain the original evaluation names. The separate welcome run uses the same development scenario with a different experiment dimension. Harmful changes, inconclusive experiments and incident latency are disclosed in SUBMISSION.md.

Raw simulator outputs and runtime databases are ignored. The controller's audit state is separate under `.controller-state/`; it never reads simulator outputs or grader reports. Earlier reports, an interrupted welcome run and internal planning notes are retained locally under ignored `runs/` archives. They are not submission evidence.

See [the controller guide](../docs/CONTROLLER.md) for setup, reproduction and inspection. Each evaluation needs a fresh name and state directory.

```bash
python scripts/verify_evidence.py
```

This checks the committed package without runtime databases or networking. On the original evaluation workspace, `python scripts/verify_evidence.py --live` also checks retained audit/exposure state and starts a local read-only API smoke test. Runtime databases are not included in a fresh clone.
