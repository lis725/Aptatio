# Batch Capacity and Policy Experiment Summary

These results are public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated. They do not establish causal patient benefit, payment improvement, or real-world optimality.

- CMS model lock: Expanded HHVBP, CY2025 performance year / CY2027 payment year
- Production/demo threshold retained: 0.75
- Threshold optimization: not evaluated (fixed candidate 0.75 only)
- Held-out threshold evaluation: no
- Analytical scenario-upper-bound regret available: yes
- Result rows: 1
- Common random numbers: yes
- Every candidate resets workload and reruns route/pool/assignment/capacity/outcome: yes

## Policy averages

| Policy | TPS proxy | Utility | Upper-bound regret | Best-included regret | ZIP fallback | ZIP continuity | Workload Gini | Unassigned | Travel proxy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P4 | 62.780 | 0.6272 | 0.0400 | 0.0000 | 0.000 | 0.967 | 0.519 | 0.000 | 17.50 |

## Scenario-specific simulation thresholds

Threshold optimization was not evaluated in this fixed-threshold run.

These are simulation-specific recommendations only. The configured 0.75 sponsor pilot remains unchanged.

## Structural audit

- Full factorial profiles: 2,160
- Monotonicity violations: 540
- Unique raw-risk values: 2160
- Age + health configured joint weight: 0.53

## Reproduction

C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe "C:\Users\iced_lemonade\Documents\Aptatio Project\scripts\run_batch_policy_experiments.py" --output-dir reports\experiments\structural_audit --n-patients 10 --n-seeds 1 --thresholds 0.75 --scenarios A --policies P4 --structural
