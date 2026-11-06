# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.351 | +5.5% | 0.241 | +1.0% | 0.508 | +3.4% | 10% | 54 (47) |
| nw_kyc | 0.356 | -2.2% | 0.232 | -4.2% | 0.604 | +2.2% | 0% | 26 (21) |
| nw_renewal | 0.411 | -2.0% | 0.259 | -1.5% | 0.542 | -0.7% | 1% | 47 (42) |
| ALL | 0.372 | +1.0% | 0.245 | -0.9% | 0.538 | +1.5% | 4% | 127 (110) |

## Changes applied by the controller

2 applied — beneficial: 1, neutral: 1

- ks_cancel v2 (controller_approved) dims=['cohorts', 'voice'] engageΔ=+0.014 didΔ=+0.000 → **beneficial**
- nw_renewal v2 (controller_approved) dims=['cohorts', 'voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 3,
  "decisions": {
    "approved": 2,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 2,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0