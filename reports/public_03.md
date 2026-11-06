# Public_03 load benchmark

Measured complete five-day run of scenarios/public_03_load.yaml. This is a performance report, not an official baseline/outcome grade. Raw measurements: [public_03_metrics.json](public_03_metrics.json). Selection/provenance: [provenance.json](provenance.json).

| Measure | Result |
|---|---:|
| Environment | Linux; Python 3.14.4; four-core affinity (host has 16 CPUs) |
| Workflows / simulated days | 12 / 5 |
| Durable events | 9,737,471 |
| Historical / subsequent events | 5,537,379 / 4,200,092 |
| HTTP ingestion + parsing + durable materialisation throughput | 33,241.4 events/sec |
| Ingestion wall time, including history and empty polls | 292.93 s |
| Controller startup/history/initial decision | 178.39 s |
| Simulator history generation/startup | 51.95 s |
| Complete controller-driven run (server ready through completion) | 412.80 s |
| Average per simulated day, amortising controller bootstrap | 82.56 s |
| Day processing excluding bootstrap | 57.30 / 39.86 / 40.69 / 45.35 / 50.96 s |
| First day including both bootstraps | 287.64 s |
| SRE target | <600 s per simulated day; met in this run |
| Peak controller RSS | 129,980 KiB = 126.93 MiB |
| Sampled combined simulator/controller RSS | 606,204 KiB = 592.00 MiB |
| SQLite database size | 2,184,790,016 bytes |
| Call timestamp to decision, simulated p50 / p95 / max | 30 / 57 / 60 min |
| Maximum decision-cycle wall time | 14.48 s |
| Maximum loop tick wall time (includes bootstrap) | 178.36 s |
| Final event watermark equals HTTP source last event | true |
| SQLite integrity / duplicate application records / unjoined calls | ok / 0 / 0 |
| Pending remote-intent states at finish | 0 |

Four-core affinity is enforced by the external harness and inherited by both children. No shared 8-GB memory cgroup was imposed; RSS is observed, not a memory-cap test. Combined RSS is sampled every 0.5 seconds after controller startup and can miss brief peaks; controller peak RSS also comes from getrusage.

Throughput uses ingester wall time, which includes chunk discovery, HTTP streaming, JSON parsing, transactions and rollups; it excludes decision processing and /sim/advance. The complete-run time includes simulator advances and controller work, while simulator initial history generation is separately measured. Core decision cadence is hourly and approvals reconcile each 15-minute tick. Simulated lag is measured with a weighted histogram of call timestamps; it is not a paced production-stream benchmark because the simulator waits for the controller.

The 10x ten-minute ingestion requirement alone is about 14,167 events/sec, below measured ingest throughput. This does not prove a 10x end-to-end pass. Raw-history cohort joins, person-level statistical grouping, SQLite index/storage growth and unbounded retention need separate growth tests. Next steps are precomputed feature aggregates, managed Postgres partitioning/outbox, workflow fencing and a paced input replay. The current DB is local durability, not production HA.

Reproduce with a new name:

```bash
source .venv/bin/activate
python scripts/evaluate.py --scenario scenarios/public_03_load.yaml --name check_load --port 18702
```

The simulator source/scenarios remain unchanged; controller input is HTTP only. No grader truth feeds controller decisions.
