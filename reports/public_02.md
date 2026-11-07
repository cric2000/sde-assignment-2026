# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.290 | +17.1% | 0.203 | +10.4% | 0.501 | +5.1% | 35% | 94 (330) |
| nw_kyc | 0.389 | +3.9% | 0.248 | +2.6% | 0.597 | +0.8% | 0% | 33 (31) |
| nw_renewal | 0.415 | -2.8% | 0.260 | -0.8% | 0.538 | -1.9% | 0% | 53 (55) |
| ALL | 0.353 | +5.5% | 0.231 | +4.2% | 0.535 | +0.9% | 14% | 180 (416) |

## Changes applied by the controller

4 applied — harmful: 1, neutral: 1, beneficial: 2

- nw_renewal v2 (controller_approved) dims=['cohorts', 'voice'] engageΔ=-0.012 didΔ=+0.000 → **harmful**
- ks_cancel v2 (controller_approved) dims=['cohorts', 'voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- ks_cancel v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.481 → **beneficial**
- ks_cancel v5 (controller_approved) dims=['prompt_version'] engageΔ=+0.136 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 5,
  "decisions": {
    "approved": 4,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 4,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "ks_cancel": 2
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.271,
      "3": 0.0,
      "4": 0.0,
      "5": 0.0,
      "6": 0.0,
      "7": 0.0,
      "8": 0.0,
      "9": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 1590,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 8 · validation runs: 1 · LLM fix calls: 1