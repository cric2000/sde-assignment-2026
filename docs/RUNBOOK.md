# Controller operations runbook

## Startup, shutdown and recovery

Use a state directory per simulator world. A normal SIGTERM/SIGKILL is recovered with the same run.sh command and state directory; the simulator must remain alive. SQLite commits are atomic; chunk prefixes replay. Persisted mutation intents reconcile before time advances. If a process holds the state-directory lock, check that it is the intended driver before starting another. A backwards simulator clock means the world/state directory do not match; select the correct directory rather than deleting the DB.

The controller is intentionally single-owner. Production needs workflow leases/fencing in Postgres. A second driver with a different state directory can still race the simulator and is an operational error.

## Pages and actions

| Condition | Impact | Inspect and act |
|---|---|---|
| Event watermark stops, transport failures persist | Decisions use stale evidence | Check simulator /health and controller logs; inspect meta/chunks; restore connectivity, restart with same state |
| Approval close to apply_by, repeated apply failure | Permission may lapse while harm persists | Inspect proposal, remote approval and current config; reconcile; never manually replay an old full config |
| needs_review safety incident | No supported permitted fix, or engagement degraded | Inspect incident evidence, paid ledger and supervisor config; obtain fresh validated permission; do not restore an unsafe prompt |
| Unknown paid response | Possible spent credits, no recoverable validation ID | Check ledger/credits; do not blindly repeat; approve a separately bounded replacement operation operationally |
| Disk full / SQLite failure | Durable progress cannot advance safely | Preserve DB/WAL; restore disk, run integrity_check and restart; do not delete WAL |
| Repeated unexpected 403/422 | Policy or API-contract mismatch | Inspect stored request and current policy; fix preflight; don't retry invalid requests unchanged |

Pending approvals in normal hours, routine rejections, inconclusive experiments and low sample support belong in the consolidated view, not a pager. The implementation emits structured logs and queryable state; external paging/notification delivery is not built.

## Useful queries

```sql
SELECT key,value FROM meta WHERE key IN
 ('sim_minute','event_watermark','events_ingested','ingest_seconds','peak_rss_kib');
SELECT id,workflow,kind,status,approval,submitted,updated FROM proposals ORDER BY minute DESC;
SELECT id,workflow,kind,status,payload FROM incidents WHERE status NOT IN ('recovered','closed','superseded');
SELECT id,workflow,kind,cost,status FROM paid WHERE status!='done';
PRAGMA integrity_check;
```

Use controller.inspect for the complete evidence/authority timeline. A config_changed supervisor event contains no business rationale; report that honestly. Review actual prompt and agent config attribution separately for next-campaign-only workflows.

## Change safety

All controller changes use governance, including rollback. Unknown remote result means reconcile by proposal/approval ID and config history, then record the observed result. A supervisor edit wins; never overwrite it with an old snapshot. Expired/lapsed/stale requests are terminal attempts, and any successor has fresh authority and a new ID. Quarantined caller IDs are not automatically reintroduced; controlled re-entry is intentionally cut.

The simulator has no pause-dialling API or notification acknowledgement. Operators must not pretend either exists. A production emergency stop and a real supervisor notification/acknowledgement integration need business-owned authority.

## Before production

Move persistence to managed Postgres; add service authentication, RBAC, tenant isolation, retention/backups, paid-operation idempotency/result lookup, notification delivery, on-call routing, canary rollout and a paced event-stream freshness benchmark. Shadow decisions first, then enable one approved workflow. Review statistical power and detector false alarms on representative production traffic before expanding.
