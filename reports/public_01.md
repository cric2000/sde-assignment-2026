# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.345 | +3.5% | 0.240 | +0.8% | 0.501 | +2.2% | 10% | 51 (47) |
| nw_kyc | 0.360 | -0.9% | 0.241 | -0.5% | 0.588 | -0.4% | 0% | 27 (21) |
| nw_renewal | 0.415 | -1.1% | 0.261 | -0.8% | 0.544 | -0.3% | 1% | 49 (42) |
| ALL | 0.372 | +0.8% | 0.248 | -0.0% | 0.533 | +0.6% | 4% | 127 (110) |

## Changes applied by the controller

2 applied — beneficial: 1, neutral: 1

- ks_cancel v2 (controller_approved) dims=['cohorts', 'voice'] engageΔ=+0.014 didΔ=+0.000 → **beneficial**
- nw_renewal v2 (controller_approved) dims=['cohorts', 'voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 3,
  "decisions": {
    "rejected": 1,
    "approved": 2,
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
    "nw_renewal": 2,
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