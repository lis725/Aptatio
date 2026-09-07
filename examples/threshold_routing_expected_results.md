# Threshold Routing Test Examples

These four requests reuse the bundled clinician scorecard rows from `example_assignment_request.json`.

For testing only, each request sets:

- `options.threshold_routing_enabled=true`
- `options.risk_threshold=0.50` (a percentile threshold)
- `patient.severity_score_override` to force a known raw modeled-need index

The raw override is mapped through the checked-in 10,000-profile ECDF before routing and mirrored anchoring. For the 0.50 percentile candidate, the current raw-score equivalent is `0.4391795612263141`. In production, do not send `severity_score_override` unless an explicit debug override is intended.

## 1. High Risk, Full Pool 01

Command:

```powershell
python run_simple.py --request examples\threshold_high_full_pool_01.json --value-key clinician_id
```

Expected simplified output:

```json
{
  "RN": [104, 108, 105],
  "PT": [205, 203, 204],
  "OT": [303, 301, 302]
}
```

Audit expectation:

- `risk_score_rho_raw=0.90`
- `risk_percentile_u=0.9994552964706721`
- percentile threshold `0.50`; raw equivalent `0.4391795612263141`
- route: `global_high_risk` for RN/PT/OT
- selected pool sizes: RN 8, PT 7, OT 3

## 2. High Risk, Full Pool 02

Command:

```powershell
python run_simple.py --request examples\threshold_high_full_pool_02.json --value-key clinician_id
```

Expected simplified output:

```json
{
  "RN": [104, 108, 105],
  "PT": [205, 203, 204],
  "OT": [303, 301, 302]
}
```

Audit expectation:

- `risk_score_rho_raw=0.70`
- `risk_percentile_u=0.9324091134741784`
- percentile threshold `0.50`; raw equivalent `0.4391795612263141`
- route: `global_high_risk` for RN/PT/OT
- selected pool sizes: RN 8, PT 7, OT 3

## 3. Low Risk, Sufficient ZIP Pool

Command:

```powershell
python run_simple.py --request examples\threshold_low_zip_history_01.json --value-key clinician_id
```

Expected simplified output:

```json
{
  "RN": [102, 105, 108],
  "PT": [207, 204, 203],
  "OT": [302, 302, 302]
}
```

Audit expectation:

- `risk_score_rho_raw=0.20`
- `risk_percentile_u=0.047473817428407485`
- percentile threshold `0.50`; raw equivalent `0.4391795612263141`
- patient ZIP: `89502`
- route: `zip_history` for RN/PT/OT
- selected pool sizes: RN 3, PT 3, OT 1

## 4. Low Risk, ZIP Pool Fallback

Command:

```powershell
python run_simple.py --request examples\threshold_low_fallback_02.json --value-key clinician_id
```

Expected simplified output:

```json
{
  "RN": [103, 101, 107],
  "PT": [201, 202, 207],
  "OT": [303, 301, 302]
}
```

Audit expectation:

- `risk_score_rho_raw=0.20`
- `risk_percentile_u=0.047473817428407485`
- percentile threshold `0.50`; raw equivalent `0.4391795612263141`
- patient ZIP: `89506`
- route: `zip_history_fallback_full_pool` for RN/PT/OT
- fallback reasons:
  - RN ZIP pool size 1 below minimum 3
  - PT ZIP pool size 1 below minimum 3
  - OT ZIP pool size 0 below minimum 1

The high-risk and fallback RN/PT ordering differs intentionally from the pre-audit fixture because the corrected mirrored anchor uses `risk_percentile_u`, not raw rho.
