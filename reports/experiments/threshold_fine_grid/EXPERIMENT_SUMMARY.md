# Batch Capacity and Policy Experiment Summary

These results are public-data-only, expert-configured, semi-synthetic, simulation-based, and not outcome-validated. They do not establish causal patient benefit, payment improvement, or real-world optimality.

- CMS model lock: Expanded HHVBP, CY2025 performance year / CY2027 payment year
- Production/demo threshold retained: 0.75
- Simulation minimax-regret threshold for P4: 0.99 (selected on training seeds; not activated)
- Held-out threshold evaluation: yes
- Analytical scenario-upper-bound regret available: yes
- Result rows: 1,818
- Common random numbers: yes
- Every candidate resets workload and reruns route/pool/assignment/capacity/outcome: yes

## Policy averages

| Policy | TPS proxy | Utility | Upper-bound regret | Best-included regret | ZIP fallback | ZIP continuity | Workload Gini | Unassigned | Travel proxy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P4 | 58.095 | 0.5824 | 0.0582 | 0.0000 | 0.029 | 0.857 | 0.316 | 0.048 | 16.85 |

## Scenario-specific simulation thresholds

- Scenario A: 0.95 (selected on training seeds)
- Scenario B: 0.95 (selected on training seeds)
- Scenario C: 0.95 (selected on training seeds)
- Scenario D: 0.17 (selected on training seeds)
- Scenario E: 0.51 (selected on training seeds)
- Scenario F: 0.95 (selected on training seeds)
- Scenario G: 1.00 (selected on training seeds)
- Scenario H: 1.00 (selected on training seeds)
- Scenario I: 1.00 (selected on training seeds)

These are simulation-specific recommendations only. The configured 0.75 sponsor pilot remains unchanged.

## Structural audit

Structural suite was not requested for this run.

## Reproduction

C:\Users\iced_lemonade\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe "C:\Users\iced_lemonade\Documents\Aptatio Project\scripts\run_batch_policy_experiments.py" --output-dir reports/experiments/threshold_fine_grid --n-patients 100 --n-seeds 2 --thresholds 0:1:0.01 --scenarios A,B,C,D,E,F,G,H,I --policies P4
