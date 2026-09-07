# Batch Capacity and Policy Experiment Summary

These results are public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated. They do not establish causal patient benefit, payment improvement, or real-world optimality.

- CMS model lock: Expanded HHVBP, CY2025 performance year / CY2027 payment year
- Production/demo threshold retained: 0.75
- Threshold optimization: not evaluated (fixed candidate 0.75 only)
- Held-out threshold evaluation: no
- Analytical scenario-upper-bound regret available: yes
- Result rows: 9
- Common random numbers: yes
- Every candidate resets workload and reruns route/pool/assignment/capacity/outcome: yes

## Policy averages

| Policy | TPS proxy | Utility | Upper-bound regret | Best-included regret | ZIP fallback | ZIP continuity | Workload Gini | Unassigned | Travel proxy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P4 | 58.260 | 0.5842 | 0.0536 | 0.0000 | 0.025 | 0.925 | 0.307 | 0.041 | 16.75 |

## Scenario-specific simulation thresholds

Threshold optimization was not evaluated in this fixed-threshold run.

These are simulation-specific recommendations only. The configured 0.75 sponsor pilot remains unchanged.

## Structural audit

Structural suite was not requested for this run.

## Reproduction

C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe "C:\Users\iced_lemonade\Documents\Aptatio Project\scripts\run_batch_policy_experiments.py" --output-dir reports/experiments/major_scenarios_5000 --n-patients 5000 --n-seeds 1 --thresholds 0.75 --scenarios A,B,C,D,E,F,G,H,I --policies P4
