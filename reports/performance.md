# Performance comparison — 7 November 2026

The previous controller from commit `047823f` and the optimized working tree each completed the full public_03 load scenario over HTTP. This compares two controllers with the same statistical and governance rules; the previous controller is not a no-op baseline.

| Measure | Previous commit | Optimized |
|---|---:|---:|
| Controller-driven five-day runtime | 457.91 s | 419.35 s |
| Runtime reduction | — | 8.42% (38.55 s) |
| Simulator startup, measured separately | 53.24 s | 53.48 s |
| Controller bootstrap including history/initial decision | 214.23 s | 202.34 s |
| Ingest throughput including polls | 28,768 events/sec | 28,982 events/sec |
| Decision-cycle compute, total | 21.72 s | 20.72 s |
| Peak controller RSS | 122.27 MiB | 122.96 MiB |
| Sampled combined simulator/controller RSS | 631.71 MiB | 670.45 MiB |
| Durable events | 9,737,472 | 9,737,472 |
| Simulated lag p50 / p95 / max | 30 / 58 / 60 min | 30 / 58 / 60 min |
| Finished / integrity / source watermark | true / ok / matches | true / ok / matches |
| Duplicate applications / pending intents / unjoined calls | 0 / 0 / 0 | 0 / 0 / 0 |

Raw measurements: [previous](performance_before_metrics.json), [optimized](performance_after_metrics.json). Both used Python 3.14.4, the same four-core affinity and the unmodified simulator/scenario. They ran sequentially on the same host, optimized first, without profiling. A shared 8-GB memory cap was not imposed. Runtime covers controller launch through completion, including simulator advances; simulator startup, final evidence export and grading are outside that measurement.

This is one run per version, not a repeated-trial confidence estimate. The earlier selected public_03 verification measured 402.27 seconds under earlier conditions; it is not used to calculate this comparison's improvement. Cache state, machine load and randomized trajectories can affect timings and outcomes. No outcome-lift improvement is claimed from these changes.

## Changes

- A partial index covers configuration-change events. Finding the latest edit no longer scans ordinary events, including millions of calls since the previous edit.
- A bounded 4,096-entry cache reuses absolute timestamp-to-minute conversions. Relative simulation offsets are still computed for each world.
- Rollup counts reject experiments that cannot yet have enough observations before grouping calls by person. Boundary-hour overcounts are checked against exact timestamps; these counts never decide a winner or spend a statistical look.
- The evaluation harness prints startup/progress/validation stages, saves full metrics and prints a compact summary. `--verbose` prints full metrics; `--profile` writes a diagnostic trace and marks the run as unsuitable for benchmark packaging.
- `make check PYTHON=.venv/bin/python` runs unit tests and offline package checks without rerunning full simulations.

No dependencies, approval permissions, sample thresholds, observation windows or business cadence changed. SQLite remains WAL with synchronous=FULL. The existing replay/rollback tests and new readiness/boundary/promotion tests passed (50 tests total).

## Reproduction and provenance

The old-code checkout is retained locally under ignored `runs/perf_baseline_checkout/`. Its evaluation name is `perf_load_baseline_v1`; the optimized evaluation is `perf_load_optimized_v1`, with state in `.controller-state/perf_load_optimized_v1/`. Raw simulator outputs remain ignored and are not controller inputs. A diagnostic development profile is retained under `runs/perf_profile_dev_v1/` and excluded from this comparison.

```bash
make check PYTHON=.venv/bin/python
.venv/bin/python scripts/evaluate.py --scenario scenarios/public_03_load.yaml --name load_speed_check_v1 --port 18724
```

Use a fresh evaluation name and an unused port. To compare versions, reproduce the old commit in a separate checkout, use the same Python environment and CPU setting, and run the two versions sequentially. The full load run must still process millions of records; this does not turn it into a short smoke test.

Controller source fingerprints for this comparison:

| File | Previous SHA-256 | Optimized SHA-256 |
|---|---|---|
| `controller/model.py` | `056a4dff9ff53595d15a8bcbbd122df198c510ee21bd6b8be85b4252bd9148f0` | `45dda9c57d018636a6aca07b1e665423ccd065030bb93ebfc5140bc6775f48e0` |
| `controller/store.py` | `a01c0f611654c7ec3b9fe17bdc7b2bb1353bbb1875e939d416f31d603251773c` | `1e38fa2b8baa422ecf8030653fe26815ae90b34d9a59da8319d0e5621a9ce412` |
| `controller/experiments.py` | `8bc26a47f55e8572092aaf12d3bfb08afdf378387c0719dc2d032e41a6a5b49b` | `a200447d290e80ba6b50f246c5eec218c2aedef807d696d2737173992bd4b404` |

## End-to-end regression checks

[performance_regressions.json](performance_regressions.json) contains completion/recovery invariants, incident and credit records, and the complete official grade text for each post-optimization check.

| Evaluation | Result |
|---|---|
| `perf_dev_v1` | Public_01 completed after SIGKILL/restart; two process starts, no duplicate applications |
| `perf_incidents_v1` | Public_02 completed; both incidents recovered; 8/200 credits used |
| `perf_welcome_v1` | Welcome mode completed public_01; zero pending approvals at finish |

All three had matching source watermarks, SQLite integrity `ok`, no unjoined calls and no duplicate applications. They ran sequentially and were graded only after completion. Their times are functional checks, not paired speed comparisons; no development/incident/welcome speed-up is claimed.

The development grade recorded +0.8% relative conclusion rate, with four applications (two neutral, one beneficial, one harmful). The harmful change restored an inconclusive Kestrel experiment to its reference allocation, illustrating the documented stopping-policy trade-off. The incident grade recorded +5.6% with one harmful exploration and two beneficial incident fixes. The welcome grade recorded +1.3% with one harmful exploration. Outcome differences across trajectories do not establish a causal effect from performance tuning.
