# CMS Version Notes

The production/demo algorithm is intentionally frozen to the **Expanded HHVBP CY2025 performance-year** configuration for sponsor review:

- CMS model: Expanded HHVBP
- Performance year: 2025
- Payment year: 2027
- Measure-set version: CY2025
- CMS version locked: true

The active library contains exactly the ten CY2025 measures and all-measures weights documented in [CY2025_CONFIGURATION.md](algorithm_audit/CY2025_CONFIGURATION.md). Configuration validation requires those measures and weights to sum to exactly 1.00 and rejects a version-lock mismatch.

Migration to CY2026 is explicitly out of scope for this implementation. No CY2026 measure has been added to the active metric library. Any future migration must be a separately reviewed configuration change with new measure aliases, weights, tests, and sponsor approval; it must not silently alter this locked review baseline.

This version lock is a configuration and reproducibility statement, not an outcome-validation claim. The prototype is public-data-only, expert-configured, semi-synthetic/simulation-based where data are needed, and not validated on sponsor outcomes or for production payment optimization.
