# Running and extending the controller

The original README is the assignment brief. This guide describes the implementation; SUBMISSION.md records scope, evidence and trade-offs. The controller uses only HTTP to interact with the simulator. Evaluation scripts start the official simulator/grader separately.

## Setup and run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q tests
```

The recorded test environment is Python 3.14 on Linux. requirements-tested.txt pins the six top-level versions used for the measurements and needs Python >=3.12 because of NumPy. The original requirements.txt allows the installer to select compatible versions on older Python; that combination is not separately validated here. Core code uses Python >=3.10 and standard-library SQLite with JSON functions. No extra stateful service or Docker is required.

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
```

By default the Linux harness restricts itself and its child processes to four available CPU cores. It samples combined simulator/controller RSS and records controller peak RSS. It does not impose an 8-GB memory cgroup; the report states this. For an isolated load measurement, run it without other evaluation processes.

The --crash mode waits for a persisted pending approval, sends SIGKILL, restarts against the same world and validates completion, SQLite integrity, final watermark, joined calls and duplicate local application records. Unit tests separately inject lost remote acknowledgements, partial transactions, stale supervisor edits and paid-call ambiguity. Together these exercise different recovery boundaries.

Evaluation scripts may pass scenario paths to the official simulator and run the official grader. Controller modules never read those paths, import sim, or inspect simulator run directories. Reports are not controller inputs. Grade labels cannot be used to select variants.

## Inspect and supervise

```bash
python -m controller.inspect --db .controller-state/check_incidents/state.db overview
python -m controller.inspect --db .controller-state/check_incidents/state.db why --workflow ks_cancel --at 2027-01-08T12:45:00+05:30
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
- One-factor experimentation defaults to voice. The Python CLI supports `--dimension welcome_message` for a separate run; never launch two drivers against one simulator. The default submission does not optimise both dimensions or their interaction.
- Shared cohort logic is in model.Cohorts. At most one supported language cohort is newly formed, plus default, to limit traffic dilution. Existing configured cohorts are honoured. Every cohort creation goes through governance.
- Statistics and policy functions are pure enough to test independently. Detector thresholds and protocol constants are explicit in incidents.py and experiments.py and appear in decision evidence; changes require re-running calibration and integration reports.
- Paid requests have their own durable ledger. Done results are reused. In-flight/unknown responses are not automatically paid again, because the API has no documented idempotency or result lookup. A needs_review incident remains visible while monitoring continues.

## State and performance

SQLite WAL, synchronous=FULL and one writer provide local durable transactions. Event IDs and the watermark commit with calls, lead joins and rollups. Ingestion consumes globally ordered chunks from both history and events, so no later chunk can advance the watermark past an unprocessed earlier chunk. A partial chunk is replayed safely. The HTTP schema promises strictly increasing event IDs; it does not need contiguous IDs.

The events table stores replayable control events; lead/call records are normalised into dedicated tables to avoid duplicating large JSON payloads. Hourly rollups keep routine detection inexpensive. Experiment comparisons group actual calls by person to account for repeated attempts. Raw history joins for initial cohort support and person-level comparisons are measurable growth costs; SQLite is not production HA.

Simulated decision lag is measured from ingested call timestamps to the hourly decision cycle, with a weighted histogram. It includes cadence but cannot prove real production freshness because simulator time waits for the controller. HTTP/ingestion/decision wall time and startup are separate measurements. Decisions don't occur every 15-minute tick; approval reconciliation does.

At production scale, use the existing Postgres estate for durable state, transactional job claiming/outbox, workflow-level fencing and event partitioning. Keep one mutation owner per workflow and tenant quotas. Add a broker only after establishing replay/backpressure needs; Redis is a cache rather than the authority for approvals.
