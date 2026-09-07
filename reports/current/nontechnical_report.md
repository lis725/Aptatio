# Aptatio Assignment Model: Nontechnical Explanation

**Model lock:** Expanded HHVBP, CY2025 performance / CY2027 payment, measure set CY2025 (version locked).

**Validation boundary:** Public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated.

These results describe configured model scenarios. They do not establish real-world clinical effects, payment effects, or a uniquely best assignment policy.

## What the model does

The prototype recommends RN, PT, and OT clinicians for a home-health patient. It uses seven patient-side factors: health status, age group, ZIP house-price group, area type, housing type, distance to clinic, and driving condition. The active quality weights are frozen to CY2025 performance for CY2027 payment.

## Two numbers that should not be confused

The raw score, `risk_score_rho_raw`, is the weighted sum of the model's ten metric need scores. It is an index from the configured model, not a probability and not a historical rate.

The reference percentile, `risk_percentile_u`, says where that raw index falls relative to a checked-in 10,000-profile public-only semi-synthetic reference. A percentile is a relative position; it does not turn the raw index into a clinical probability.

## How clinician ranking works

RN clinicians are compared only with RN clinicians, and PT clinicians only with PT clinicians. Metric percentiles are calculated on each discipline's full request pool before any ZIP filter. This prevents unrelated discipline rosters or a small ZIP pool from redefining capability ranks. Ties use stable clinician identifiers and names.

These ranks are roster-relative. Adding or removing a clinician in the same discipline can change percentiles unless a stable external reference distribution is supplied.

## The strict pilot routing rule

A patient is in the high-risk route only when `risk_percentile_u > 0.75`. At exactly 0.75, the patient remains in the lower-risk route. High-risk cases use the full discipline pool. Lower-risk cases first use clinicians with history in the patient's assignment ZIP.

If that ZIP-history pool is too small, the policy falls back to the full pool. The minimums are RN=3, PT=3, and OT=1. ZIP history records past service coverage; it is different from the Reno ZIP labels used only for distribution profiling in the Reno reports.

Inside the selected RN/PT pool, the corrected mirrored anchor uses the reference percentile. OT remains a separate deterministic rotation because OT is not scored in the current HHVBP objective.

## Why capacity and scenario simulation are still needed

The one-patient interface does not consume future caseload capacity. Repeated recommendations can therefore concentrate work even when every individual ranking is computed correctly. Batch simulation is needed to measure fallback, continuity, travel, workload spread, overload, and sensitivity to different assumptions.

## What the available data cannot answer

No usable sponsor patient, clinician, assignment, ZIP-history, OASIS, HHCAHPS, scheduling, capacity, or outcome dataset was available. Public agency-level data can help configure marginal distributions, but it cannot identify patient-clinician effects. The 0.75 threshold remains a capacity-oriented pilot setting with provenance `public_only_semi_synthetic_capacity_pilot_not_outcome_validated`.
