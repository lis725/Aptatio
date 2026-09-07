# Implementation Summary

## Outcome

The repository now implements the corrected CY2025 sponsor policy behind the unchanged simplified backend interface. The root `backend_interface.py` remains protected by its baseline SHA-256 contract. Internal services expose a richer audit without changing the default `{RN, PT, OT}` response.

## Runtime changes

- Explicit Expanded HHVBP CY2025 / payment year 2027 lock and exact 1.00 weight total.
- Canonical metric aliases with duplicate/conflict handling.
- Exact seven-factor verification and complete age boundary/raw-band normalization.
- Within-discipline capability percentiles computed before ZIP filtering.
- Stable ID/name tie-breaking and row-order invariance.
- Separate raw need index and persisted-reference empirical percentile.
- Strict `risk_percentile_u > 0.75` global routing; equality remains ZIP-first.
- RN=3, PT=3, OT=1 ZIP-pool minimums with audited full-pool fallback.
- Percentile-based mirrored anchor and deterministic neighbor order.
- Deterministic OT rotation retained inside the routed OT pool.
- Rich audit fields for risks, thresholds, routes/pools/fallbacks/ranks, imputation, and provenance.
- Clinician shrinkage is opt-in and disabled safely when its configuration key is absent.

## Analysis infrastructure

- 10,000-row deterministic weighted reference and exact 2,160-profile grid.
- Persisted-reference ECDF use in runtime, generated cohorts, calibration, and structural analysis.
- Fail-fast binding of the reference/grid to current CY2025 configuration and artifact checksums; exact reference identity appears in the rich assignment audit.
- Full-rerun threshold calibration with common random numbers and held-out seed evaluation.
- Capacity/workload/availability batch simulator.
- P0-P7 comparison and scenarios A-I, with P7 explicitly treated as a feasible comparator.
- A separate analytical unconstrained latent-information upper bound for valid regret.
- Collision-resistant rerun IDs incorporating canonical design hashes.
- Candidate-specific raw threshold equivalents and exact input-content rerun fingerprints.
- Actual-policy structural ablation and counterfactual reruns against a fixed roster.
- Public-data manifest hardening, data dictionary, and reproducible build commands.
- Current technical, nontechnical, sponsor, experiment, and audit report sources plus PDF generation.
- Fail-closed experiment-metadata validation and cryptographic Markdown-to-PDF manifest binding.

## Validation boundary

The implementation is public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated. It does not establish a causal clinician effect, proven patient benefit, payment improvement, or a real-world optimal threshold.

## Default versus alternatives

P4 hard-ZIP routing remains the default. P5 soft-ZIP and P6 capacity optimization are evaluated but not activated. Any experiment threshold different from 0.75 is simulation-only. Fixed-threshold and one-seed sensitivity runs cannot emit an optimization recommendation.

See `EXPERIMENT_RESULTS.md` for generated results and `LIMITATIONS.md` for unresolved evidence limits.
