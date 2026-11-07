# Running and extending the controller

The original README is the assignment brief. This guide describes the implementation; SUBMISSION.md records scope, evidence and trade-offs. The controller uses only HTTP to interact with the simulator. Evaluation scripts start the official simulator/grader separately.

## Setup and run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-tested.txt
python -m pytest -q tests
```

Use Python >=3.12 for the pinned dependencies. The measured environment was Python 3.14.4 on Linux. requirements-tested.txt pins the six top-level versions used for the measurements. The original requirements.txt retains broader version ranges; other combinations were not separately validated. Core code uses Python >=3.10 and standard-library SQLite with JSON functions. No extra stateful service or Docker is required.

In terminal one:

```bash
python -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/my_dev --port 8700
```

In terminal two, from the repository:

```bash
CONTROLLER_STATE_DIR=.controller-state/my_dev ./run.sh http://127.0.0.1:8700
```

The controller ingests history, continues automatically and exits when finished. History ingestion can take minutes in the load scenario; checkpoint logs and SQLite meta watermarks show progress. run.sh selects CONTROLLER_PYTHON, then .venv/bin/python, then python3. Never install packages during a judged run.

To recover a killed controller, run exactly the same command with the same state directory while the simulator is still alive. To start a new simulator world, choose a new run directory and a new controller state directory. The API has no unique run UUID; mixing worlds with the same directory is unsupported. A backwards clock/origin mismatch fails explicitly rather than silently destroying evidence. A file lock prevents two controllers using one state directory; operators must also avoid running different state directories against the same simulator.

## Reproduce the evidence

These harness commands run baseline, simulator, controller and official grading. Each name must be new: existing results are never overwritten.

```bash
python scripts/evaluate.py --name check_dev --baseline --crash --port 18700
python scripts/evaluate.py --scenario scenarios/public_02_incidents.yaml --name check_incidents --baseline --port 18701
python scripts/evaluate.py --scenario scenarios/public_03_load.yaml --name check_load --port 18702
python scripts/evaluate.py --name check_welcome --baseline --dimension welcome_message --port 18703
```

By default the Linux harness restricts itself and its child processes to four available CPU cores. It samples combined simulator/controller RSS and records controller peak RSS. It does not impose an 8-GB memory cgroup; the report states this. For an isolated load measurement, run it without other evaluation processes.

The --crash mode waits for a persisted pending approval, sends SIGKILL, restarts against the same world and validates completion, SQLite integrity, final watermark, joined calls and duplicate local application records. Unit tests separately inject lost remote acknowledgements, partial transactions, stale supervisor edits and paid-call ambiguity. Together these exercise different recovery boundaries.

Evaluation scripts may pass scenario paths to the official simulator and run the official grader. Controller modules never read those paths, import sim, or inspect simulator run directories. Reports are not controller inputs. Grade labels cannot be used to select variants.

The harness requires completion before grading. Do not stop the simulator while the controller is running; a simulator restart creates a new world and requires a fresh evaluation name and state directory. Evaluation names appear in the metrics even when reports are later packaged under stable names.

```bash
python scripts/calibrate.py
python scripts/verify_evidence.py
```

Calibration is a synthetic statistics check. The evidence verifier checks the submitted package offline, including required files, completion/integrity, provenance, exact grade tables, documentation links and the HTTP import boundary. `python scripts/verify_evidence.py --live` additionally needs the retained incident database listed in reports/provenance.json and an unused localhost port 18800; it checks audit/exposure queries and the read-only API. Runtime databases are ignored and must be recreated on a fresh clone.

scripts/package_evidence.py selects completed evaluation reports under stable public_01/public_02/public_03 names, with optional `--welcome`, and archives superseded reports under ignored runs/report-archive/. It rejects incomplete/inconsistent evidence before changing files. After selecting another set, update the submission tables and the load/crash narratives from that set, then run the evidence verifier.

## Inspect and supervise

```bash
python -m controller.inspect --db .controller-state/check_incidents/state.db overview
python -m controller.inspect --db .controller-state/check_incidents/state.db why --workflow ks_cancel --at 2027-01-08T14:00:00+05:30
python -m controller.inspect --db .controller-state/check_incidents/state.db timeline --workflow ks_cancel
python -m controller.inspect --db .controller-state/check_incidents/state.db exposure --lead <lead_id>
```

`why` follows the latest actual configuration-change event at/before the requested time. Supervisor edits are identified as supervisor changes, with no invented rationale. Controller changes link full proposals, authority, evidence and descriptive before/after measurements. `exposure` records actual call variants, campaign config snapshots and prompt-change authority separately, because immediate prompts can coexist with campaign-locked allocations.

Optional local read-only API:

```bash
CONTROLLER_DB=.controller-state/check_incidents/state.db python -m uvicorn controller.view:app --host 127.0.0.1 --port 8800
```

Open /docs for GET /overview, /workflows/{id}/timeline and /metrics. This is a local operational surface; authentication, RBAC and external exposure are production work. There are no config write endpoints.

## Configuration and extension

- controller/preferences.json contains client preferences supplied by the stakeholder memos, separately from discovered workflow policy. Kestrel is capped at two human requests per rolling 24 hours, after 11:00; customer-facing agent changes voluntarily use human approval. This is business preference data, not hidden scenario truth.
- Runtime workflow options, objectives, enforced policy and configs are fetched through HTTP. Refresh happens hourly and after observed config changes.
- One-factor experimentation defaults to voice. Both `./run.sh <SIM_URL> --dimension welcome_message` and the evaluation harness support a separate welcome run; never launch two drivers against one simulator. The controller does not optimise both dimensions jointly.
- Shared cohort logic is in model.Cohorts. At most one supported language cohort is newly formed, plus default, to limit traffic dilution. Existing configured cohorts are honoured. Every cohort creation goes through governance.
- Statistics and policy functions are pure enough to test independently. Detector thresholds and protocol constants are explicit in incidents.py and experiments.py and appear in decision evidence; changes require re-running calibration and integration reports.
- Paid requests have their own durable ledger. Done results are reused. In-flight/unknown responses are not automatically paid again, because the API has no documented idempotency or result lookup. A needs_review incident remains visible while monitoring continues.

## State and performance

SQLite WAL, synchronous=FULL and one writer provide local durable transactions. Event IDs and the watermark commit with calls, lead joins and rollups. Ingestion consumes globally ordered chunks from both history and events, so no later chunk can advance the watermark past an unprocessed earlier chunk. A partial chunk is replayed safely. The HTTP schema promises strictly increasing event IDs; it does not need contiguous IDs.

The events table stores replayable control events; lead/call records are normalised into dedicated tables to avoid duplicating large JSON payloads. Hourly rollups keep routine detection inexpensive. Experiment comparisons group actual calls by person to account for repeated attempts. Raw history joins for initial cohort support and person-level comparisons are measurable growth costs; SQLite is not production HA.

Simulated decision lag is measured from ingested call timestamps to the hourly decision cycle, with a weighted histogram. It includes cadence but cannot prove real production freshness because simulator time waits for the controller. HTTP/ingestion/decision wall time and startup are separate measurements. Decisions don't occur every 15-minute tick; approval reconciliation does.

At production scale, use the existing Postgres estate for durable state, transactional job claiming/outbox, workflow-level fencing and event partitioning. Keep one mutation owner per workflow and tenant quotas. Add a broker only after establishing replay/backpressure needs; Redis is a cache rather than the authority for approvals.
