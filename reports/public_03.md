# Public_03 load benchmark

Complete five-day run of `scenarios/public_03_load.yaml`; this is a performance report, not an official baseline/outcome grade. Raw measurements: [public_03_metrics.json](public_03_metrics.json). Source selection: [provenance.json](provenance.json).

| Measure | Recorded result |
|---|---:|
| CPU allocation | Four-core Linux affinity; host exposes 16 CPUs |
| Ingest throughput (HTTP/parse/store/rollups, including bootstrap) | 32,921 events/sec |
| Controller bootstrap wall time | 177.60 s |
| Simulator history generation/startup | 46.91 s |
| Full controller-driven five days, including controller bootstrap and simulator advance time | 402.27 s |
| Mean per simulated day, amortising controller bootstrap | 80.45 s |
| Day processing excluding bootstrap | 58.66, 43.81, 36.77, 40.29, 44.79 s |
| First day including both simulator and controller bootstraps | 283.18 s; below the 600-second target |
| Peak controller RSS | 126,872 KiB (123.90 MiB) |
| Sampled combined simulator/controller RSS | 584,832 KiB (571.12 MiB) |
| Event-to-decision simulated lag p50 / p95 / max | 30 / 58 / 60 min |
| Maximum decision-cycle wall time | 11.85 s, dominated by initial history/cohort work |
| SQLite DB size | 2,184,712,192 bytes |
| Final integrity / duplicates / missing feature joins | ok / 0 / 0 |

Four-core affinity was enforced for the harness, simulator and controller. A shared 8-GB memory cap was not enforced. Combined RSS was sampled every 0.5 seconds and may miss short peaks; controller peak RSS also comes from getrusage.

Throughput uses 295.78 seconds of ingester wall time, including chunk discovery, HTTP, parsing, transactions and rollups. It excludes decisions and simulator advances; the complete-run measurement includes both. Simulated lag measures call timestamps against the hourly decision cadence. The simulator waits for the controller, so this does not establish production stream latency.

The approved V4 promotion on `arav_cancel_2` occurred at minute 2,115, retaining 20% reference traffic. Seven subsequent decay checks found no adverse signal; the first lacked sufficient fresh evidence. [public_03_audit.json](public_03_audit.json) records the promotion and authority. This run does not demonstrate an adverse decay rollback.

At 10x, the ten-minute ingestion requirement alone is about 14,167 events/sec. Measured ingest headroom does not prove a 10x end-to-end pass. Initial history joins, person-level grouping, growing SQLite indexes and retention need separate growth tests. Production needs precomputed aggregates, partitioned Postgres, durable outbox/job claiming, workflow fencing and paced replay.

Reproduce in isolation with a new name:

```bash
python scripts/evaluate.py --scenario scenarios/public_03_load.yaml --name check_load --port 18702
```
