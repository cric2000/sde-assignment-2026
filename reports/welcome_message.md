# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.333 | +0.0% | 0.241 | +1.3% | 0.483 | -1.5% | -9% | 59 (47) |
| nw_kyc | 0.358 | -1.6% | 0.239 | -1.2% | 0.589 | -0.3% | 0% | 19 (21) |
| nw_renewal | 0.428 | +1.9% | 0.261 | -0.9% | 0.562 | +3.0% | 15% | 43 (42) |
| ALL | 0.370 | +0.4% | 0.248 | +0.0% | 0.532 | +0.3% | 2% | 121 (110) |

## Changes applied by the controller

4 applied — harmful: 1, beneficial: 2, neutral: 1

- ks_cancel v2 (controller_approved) dims=['cohorts', 'welcome_message'] engageΔ=-0.017 didΔ=+0.000 → **harmful**
- nw_renewal v2 (controller_approved) dims=['cohorts', 'welcome_message'] engageΔ=+0.049 didΔ=+0.000 → **beneficial**
- nw_renewal v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- ks_cancel v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.018 didΔ=+0.000 → **beneficial**

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
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "3": 0.255,
    "4": 0.253,
    "5": 0.078,
    "6": 0.0,
    "7": 0.0
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0