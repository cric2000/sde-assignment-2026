# Ingestion batch experiment — 7 November 2026

Retain the existing 4,000-event batch size. Larger batches did not provide a repeatable benefit in this experiment, so no controller code, schema, dependencies, governance rules or statistical gates changed.

| Batch size | Median replay time | Median peak process RSS | Commits per trial |
|---|---:|---:|---:|
| 4,000 (current) | 8.54 s | 82.36 MiB | 127 |
| 8,000 | 10.49 s | 95.14 MiB | 65 |
| 16,000 | 9.43 s | 120.34 MiB | 34 |

Each size ran three times in rotated order on the same four-core affinity, with a fresh database for every trial. The input was one fixed prefix of 500,000 events captured through the public_03 simulator's HTTP API: 12 campaign creations, 192,000 leads and 307,988 calls. The simulator was stopped before replays. [Raw results](ingest_batches_v1.json) include every trial, component timers, source hash, environment and recovery checks. Runtime varied substantially, including a slower first repetition; these nine trials do not establish a general ranking across machines or complete scenarios. The predeclared adoption gate required at least 5% median improvement and a majority of paired wins, followed by full scenario regression checks before changing a default. No alternative qualified.

For the current size, median component times were 4.93 s in SQLite calls, 1.61 s decoding JSON, 1.31 s preparing/materializing records outside SQLite calls, and 0.68 s reading the file and doing ingestion bookkeeping. These are separately calculated medians, so they need not sum to median total time. SQLite timing includes execute/executemany and transaction completion; cursor fetching falls outside it. Timers themselves add overhead. Fewer commits alone did not produce faster replay.

Every trial produced the same SHA-256 fingerprint over all event, lead, campaign, call and rollup rows, with matching final watermark, 500,000 durable events and SQLite integrity `ok`. WAL and synchronous=FULL remained enabled. An additional worker was killed with SIGKILL after 8,000 committed events; restart against the same database completed the remaining 492,000 events and reproduced the full fingerprint. Three new parameterized tests also exercise interrupted streams and database reopen for all three batch sizes. The complete test suite passed (53 tests), along with the offline submission verifier.

This is an instrumented ingestion microbenchmark, not submission outcome evidence or an end-to-end speed claim. It excludes HTTP transport during replay, simulator startup, live polling, decisions, approvals and grading. The prefix contains more leads than the full load run, and cached local file replay differs from network ingestion. Existing completed scenario reports and the separate full-run [performance comparison](performance.md) remain the end-to-end evidence. No full scenario rerun was needed for this experiment because the runtime implementation is unchanged.

Reproduce without other evaluation processes running:

```bash
.venv/bin/python scripts/benchmark_ingest.py --name ingest_check_v1 --port 18730
make check PYTHON=.venv/bin/python
```

Use a fresh name and an unused port. Captures, simulator logs and trial databases stay under ignored `runs/` and `.controller-state/`; the controller never consumes the report. The benchmark can nominate a candidate, but does not modify any runtime default.
