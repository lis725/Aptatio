# Batch Capacity and Policy Experiment Summary

These results are public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated. They do not establish causal patient benefit, payment improvement, or real-world optimality.

- CMS model lock: Expanded HHVBP, CY2025 performance year / CY2027 payment year
- Production/demo threshold retained: 0.75
- Threshold optimization: not evaluated (fixed candidate 0.75 only)
- Held-out threshold evaluation: no
- Analytical scenario-upper-bound regret available: yes
- Result rows: 144
- Common random numbers: yes
- Every candidate resets workload and reruns route/pool/assignment/capacity/outcome: yes

## Policy averages

| Policy | TPS proxy | Utility | Upper-bound regret | Best-included regret | ZIP fallback | ZIP continuity | Workload Gini | Unassigned | Travel proxy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P0 | 57.686 | 0.5783 | 0.0621 | 0.0153 | 0.000 | 0.746 | 0.310 | 0.049 | 17.27 |
| P1 | 58.629 | 0.5878 | 0.0526 | 0.0058 | 0.000 | 0.920 | 0.323 | 0.049 | 12.28 |
| P2 | 57.613 | 0.5776 | 0.0628 | 0.0160 | 0.000 | 0.738 | 0.322 | 0.049 | 17.49 |
| P3 | 57.605 | 0.5775 | 0.0629 | 0.0161 | 0.000 | 0.731 | 0.320 | 0.049 | 17.31 |
| P4 | 58.190 | 0.5834 | 0.0570 | 0.0102 | 0.029 | 0.914 | 0.318 | 0.049 | 16.47 |
| P5 | 57.539 | 0.5769 | 0.0635 | 0.0167 | 0.000 | 0.713 | 0.319 | 0.049 | 17.37 |
| P6 | 58.351 | 0.5850 | 0.0554 | 0.0086 | 0.000 | 0.963 | 0.318 | 0.049 | 10.28 |
| P7 | 59.030 | 0.5918 | 0.0487 | 0.0018 | 0.000 | 0.919 | 0.325 | 0.049 | 11.93 |

## Scenario-specific simulation thresholds

Threshold optimization was not evaluated in this fixed-threshold run.

These are simulation-specific recommendations only. The configured 0.75 sponsor pilot remains unchanged.

## Structural audit

Structural suite was not requested for this run.

## Reproduction

C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe "C:\Users\iced_lemonade\Documents\Aptatio Project\scripts\run_batch_policy_experiments.py" --output-dir reports/experiments/policy_bakeoff --n-patients 500 --n-seeds 2 --thresholds 0.75 --scenarios A,B,C,D,E,F,G,H,I --policies P0,P1,P2,P3,P4,P5,P6,P7
