# Crash-recovery evidence

Source: public_01_metrics.json, evaluation `public_01_recovery_final`. The external harness starts the unmodified simulator, waits for a persisted pending client approval, sends SIGKILL to the controller, restarts it with the same state directory/HTTP world, and runs through completion before official grading.

```bash
source .venv/bin/activate
python scripts/evaluate.py --name check_recovery --baseline --crash --port 18700
```

The captured process exit was **-9**. Immediately before the kill:

```json
{
  "pending_proposal": [
    "ctl_680439a360914af5b48a728ebe48ce7c",
    "apr_00001",
    "pending"
  ],
  "checkpoint_before": {
    "event_watermark": "191403",
    "events_ingested": "191403",
    "sim_minute": "585"
  }
}
```

The last persisted loop-minute precedes the pending-request write; remote time is always re-read rather than inferred from that checkpoint. The same proposal/approval IDs survived restart. The client eventually rejected that request, and that rejection is preserved, rather than misrepresented as application. A later request used fresh observed population data and a new proposal ID.

Final results:

| Check | Observed |
|---|---|
| Simulator finished | true; minute 11,520 |
| Controller process starts | 2 |
| Durable events / final watermark | 297,029 / 297,029 |
| HTTP last_event_id | 297,029 |
| SQLite integrity_check | ok |
| Duplicate local proposal application records | 0 |
| Pending remote-intent states | 0 |
| Unjoined call records | 0 |
| Official controller applications | 2; one beneficial, one neutral |
| Own wall-clock markers | Monotonic across restart |

A local inconclusive-restore draft remains at scenario end, outside approver hours. It remains in the overview and is not counted as an applied/approved treatment. The complete real records are in .controller-state/public_01_recovery_final/state.db and runs/public_01_recovery_final/controller.log (not committed).

Unit tests additionally cover lost acknowledgement after approval submission, lost acknowledgement after remote apply/auto-apply, a supervisor edit before recovery, failed transaction rollback, prefix/chunk replay, old campaign versions, unresolved paid operations and preserved statistical looks. The real process test validates a pending-approval boundary; it does not claim to SIGKILL at every possible instruction.

Committed batches, learned decisions and authority survive. An uncommitted batch replays; the killed segment's final timing/log observations can be lost. Harness wall time includes kill/restart overhead. Loss of the disk/machine and distributed failover are outside SQLite's local guarantee.
