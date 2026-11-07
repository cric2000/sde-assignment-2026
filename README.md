# Outcome-First Agent Loop

## Reviewer guide

This submission implements an HTTP-only Python controller with SQLite WAL persistence, governed experiments, caller-ID incident handling, validated prompt fixes and a queryable audit store. Scope cuts and remaining gaps are explicit in **[SUBMISSION.md](SUBMISSION.md)**.

| Start here | What to check |
|---|---|
| [SUBMISSION.md](SUBMISSION.md) | Assumptions, architecture, stakeholder decisions, results and known weaknesses |
| [ASSIGNMENT.md](ASSIGNMENT.md) | Original assignment brief, requirements and evaluation criteria |
| [reports/README.md](reports/README.md) | Completed scenario evidence, load measurements and performance experiments |
| [reports/crash_recovery.md](reports/crash_recovery.md) | Real SIGKILL/restart checkpoint and recovery outcome |
| [docs/CONTROLLER.md](docs/CONTROLLER.md) | Detailed setup, controller usage, audit queries and read-only supervisor API |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Restart procedure, diagnostics and incident actions |

### Install and run the quick checks

From the repository root, use Python 3.12 or newer; the recorded environment used Python 3.14.4 on Linux. No Docker or separate database service is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-tested.txt
make check PYTHON=.venv/bin/python
```

The quick check runs the test suite and offline submission verifier. It checks the packaged evidence and documentation without starting full simulations or requiring retained runtime databases.

### Reproduce the end-to-end runs

With the virtual environment active, run these commands **sequentially**. Each starts its own simulator and controller, validates completion and writes fresh reports. The first two also run a no-op baseline and official grading; the development run includes an actual SIGKILL/restart.

```bash
python scripts/evaluate.py --name reviewer_dev --baseline --crash --port 18700
python scripts/evaluate.py --scenario scenarios/public_02_incidents.yaml --name reviewer_incidents --baseline --port 18701
python scripts/evaluate.py --scenario scenarios/public_03_load.yaml --name reviewer_load --port 18702
```

Use a new `--name` for every new evaluation and an unused port. Reports are written to `reports/<name>*`, controller state to `.controller-state/<name>/`, and logs to `runs/<name>/`. Full simulations take minutes; the load scenario generates and processes millions of events. Startup and history ingestion can take time before live progress appears. See the controller guide for the separate welcome-message evaluation and audit commands.

### Run against an existing simulator

The required entrypoint is `./run.sh <SIM_URL>`. For example, with a simulator already serving on port 8700:

```bash
CONTROLLER_STATE_DIR=.controller-state/reviewer_manual ./run.sh http://127.0.0.1:8700
```

The controller runs automatically until the scenario finishes. To recover a killed controller, rerun the same command with the same state directory and the same still-running simulator. Use a fresh state directory for a new simulator world, and run one controller per world. Manual simulator startup instructions are in [docs/CONTROLLER.md](docs/CONTROLLER.md).
