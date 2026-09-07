# Unresolved Project Decisions

These items require sponsor or project-team authority. None was silently resolved by the implementation.

1. **Prospective validation design.** Decide what sponsor patient, clinician, assignment, capacity, scheduling, ZIP-history, outcome, and experience data can be collected, under what governance, and what success criteria would justify deployment.
2. **Area-type ordering.** The locked configuration gives metropolitan a positive modeled-need contribution and micropolitan zero. The structural audit consequently reports 540 metropolitan-to-micropolitan monotonicity flags. Confirm whether this is intentional, change the category ordering, or revise the configured contributions before interpreting the factor clinically.
3. **Pilot threshold and calibration objective.** The runtime remains at percentile 0.75. Confirm the desired high-risk capacity target and the expert-configured 10 TPS-point/unit target-share penalty, then decide a prospective capacity/outcome calibration protocol and minimum evidence before considering a change; simulation-only thresholds are not production recommendations.
4. **Hard versus soft ZIP routing.** P4 hard ZIP routing remains active. Decide whether P5 soft ZIP or another continuity formulation should enter a controlled pilot after reviewing real travel, continuity, and coverage data.
5. **Capacity optimization.** P6 remains experimental. Decide whether an operational optimizer is desirable and, if so, specify scheduling windows, visit duration, clinician skills, continuity rules, travel times, overtime, and fairness constraints.
6. **Clinician-score uncertainty and shrinkage.** Shrinkage is off by default because metric-specific denominators are unavailable. Decide which denominator/provenance fields are acceptable and what empirical-Bayes model should be validated.
7. **Normalization population.** Same-discipline request-roster normalization prevents cross-discipline contamination but remains composition-sensitive. Decide whether production should use a frozen reference roster or external benchmark distribution.
8. **Lower-risk route vocabulary.** Structural canonical data use `lower_risk_zip_policy`, while batch audit output uses the more explicit `zip_history_or_fallback_lower_risk`. The semantic classification is identical; decide whether to standardize labels in a future schema version.
9. **Public-source refresh.** Current artifacts use an explicitly labeled offline public/demo fallback where live source retrieval was unavailable. Decide who owns authenticated retrieval, refresh cadence, cache retention, and checksum review.
10. **CMS-year migration.** CY2025 performance / CY2027 payment configuration is intentionally frozen. Plan CY2026 migration as a separate reviewed change with new aliases, weights, fixtures, and contract evidence.
11. **Fairness review.** Technical counterfactuals are not legal or clinical conclusions. Decide which protected-class, access, clinical-appropriateness, and governance reviews are required before any field use.
12. **Report sign-off.** Assign owners to approve the sponsor-facing wording, simulation evidence boundary, and PDF release manifest.
