# Current CY2025 Assignment Model

**Model lock:** Expanded HHVBP, CY2025 performance / CY2027 payment, measure set CY2025 (version locked).

**Validation boundary:** Public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated.

These results describe configured model scenarios. They do not establish real-world clinical effects, payment effects, or a uniquely best assignment policy.

## Locked measure set

| Canonical metric | Accepted name context | CY2025 weight | Direction |
| --- | --- | --- | --- |
| DFS | Discharge Function Score (DFS/DC Function) | 0.20 | higher_is_better |
| Dyspnea | Improvement in Dyspnea | 0.06 | higher_is_better |
| Oral_Meds | Improvement in Management of Oral Medications | 0.09 | higher_is_better |
| DTC | Discharge to Community–Post Acute Care (DTC-PAC) | 0.09 | higher_is_better |
| PPH | Potentially Preventable Hospitalization (PPH) | 0.26 | lower_is_better |
| Care_of_Patients | Care of Patients | 0.06 | higher_is_better |
| Communications | Communications Between Providers and Patients | 0.06 | higher_is_better |
| Care_Issues | Specific Care Issues | 0.06 | higher_is_better |
| Agency_Rating | Overall / Agency Rating | 0.06 | higher_is_better |
| Recommend | Willingness to Recommend | 0.06 | higher_is_better |

The canonical name map treats DFS/DC Function, DTC/DTC-PAC, Agency Rating/Overall Rating, Recommend/Willingness to Recommend, Care Issues/Specific Care Issues, and Communications aliases as single metrics so they are neither duplicated nor omitted.

## Patient severity context

For each of the ten metrics, expert-elicited factor importance and category favorability produce an analytic Beta moment-matched distribution. Direction-corrected distance between configured best and worst anchors becomes metric need. CY2025 weights then produce `risk_score_rho_raw`; the empirical reference converts it to `risk_percentile_u`.

## Assignment sequence

1. Normalize metric names, score directions, ages, and ZIP inputs.
2. Compute capability percentiles within each discipline on its full request pool.
3. Classify high risk only when `risk_percentile_u > 0.75`.
4. Use the full pool for high-risk cases; otherwise try the assignment ZIP-history pool.
5. Fall back to the full pool when ZIP coverage is below RN=3, PT=3, or OT=1.
6. Apply percentile-mirrored RN/PT ordering in the selected pool and deterministic OT rotation.

ZIP selection does not recompute capability percentiles. The request-pool reference means same-discipline roster changes can still move percentile values.

## Audit fields

The internal audit pathway records raw rho, reference percentile, percentile and raw thresholds, threshold provenance, normalized patient ZIP, route and pool sizes by discipline, fallback reason, selected ranks, and observed/imputed/synthetic value status. The simplified RN/PT/OT frontend response remains separate.

## Limits requiring operational evaluation

The online policy does not jointly schedule a cohort or consume capacity. Clinician sample sizes are not available for production uncertainty estimation. Travel, fairness, continuity, workload, and threshold behavior therefore require explicit batch scenarios and eventual validation with authorized operational and outcome data.
