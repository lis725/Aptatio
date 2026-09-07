# Batch Capacity and Policy Experiment Summary

These results are public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated. They do not establish causal patient benefit, payment improvement, or real-world optimality.

- CMS model lock: Expanded HHVBP, CY2025 performance year / CY2027 payment year
- Production/demo threshold retained: 0.75
- Threshold sensitivity: 2 candidates evaluated in-sample; no recommendation emitted without held-out seeds
- Held-out threshold evaluation: no
- Analytical scenario-upper-bound regret available: yes
- Result rows: 144
- Common random numbers: yes
- Every candidate resets workload and reruns route/pool/assignment/capacity/outcome: yes

## Policy averages

| Policy | TPS proxy | Utility | Upper-bound regret | Best-included regret | ZIP fallback | ZIP continuity | Workload Gini | Unassigned | Travel proxy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P4 | 49.338 | 0.4940 | 0.1695 | 0.0057 | 0.062 | 0.748 | 0.309 | 0.223 | 15.90 |
| P5 | 48.113 | 0.4817 | 0.1818 | 0.0179 | 0.000 | 0.468 | 0.310 | 0.223 | 17.53 |
| P6 | 48.876 | 0.4893 | 0.1741 | 0.0103 | 0.000 | 0.900 | 0.322 | 0.223 | 11.11 |

## Scenario-specific simulation thresholds

Multiple candidates were screened in-sample; no recommendation was emitted.

These are simulation-specific recommendations only. The configured 0.75 sponsor pilot remains unchanged.

## Structural audit

Structural suite was not requested for this run.

## Reproduction

C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe "C:\Users\iced_lemonade\Documents\Aptatio Project\scripts\run_batch_policy_experiments.py" --output-dir reports\experiments\operational_stress_grid --n-patients 200 --n-seeds 1 --thresholds 0.25,0.75 --scenarios F,G,H --policies P4,P5,P6 --roster-scales 0.75,1.25 --capacity-levels 0.7,1.2 --zip-sparsity-levels 0.2,0.8
