from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_batch_experiments import (  # noqa: E402
    CY2025_WEIGHTS,
    BatchExperimentConfig,
    POLICY_NAMES,
    PRODUCTION_PILOT_THRESHOLD,
    SCENARIOS,
    assert_nonconstant_threshold_sensitivity,
    counterfactual_fairness_experiment,
    expected_favorable_outcome,
    factor_ablation_experiment,
    generate_full_factorial_profiles,
    generate_simulation_inputs,
    reliability_sensitivity_experiment,
    run_policy_threshold_experiment,
    run_structural_suite,
    run_threshold_sweep,
    save_experiment_artifacts,
    select_simulation_thresholds,
    simulate_policy,
)
from hhvbp_risk_reference import empirical_risk_percentile, load_risk_reference  # noqa: E402
from hhvbp_threshold_calibration import sensitivity_table  # noqa: E402


def small_config(**overrides) -> BatchExperimentConfig:
    payload = {
        "n_patients": 12,
        "seeds": (17,),
        "roster_sizes": {"RN": 4, "PT": 4, "OT": 2},
        "capacity_ratio": 1.0,
        "zip_sparsity": 0.25,
        "availability_rate": 0.9,
        "thresholds": (0.0, 1.0),
        "policies": ("P4", "P7"),
        "design_label": "unit_test",
    }
    payload.update(overrides)
    return BatchExperimentConfig(**payload)


class BatchCapacityPolicyTests(unittest.TestCase):
    def test_cy2025_weight_lock(self) -> None:
        self.assertAlmostEqual(sum(CY2025_WEIGHTS.values()), 1.0, places=12)
        self.assertEqual(CY2025_WEIGHTS["PPH"], 0.26)
        self.assertEqual(PRODUCTION_PILOT_THRESHOLD, 0.75)

    def test_capacity_availability_and_discipline_constraints(self) -> None:
        config = small_config(n_patients=18, capacity_ratio=0.55, policies=("P6",), thresholds=(0.75,))
        patients, roster = generate_simulation_inputs(config, 17, "H")
        summary, assignments = simulate_policy(patients, roster, "H", "P6", 0.75, seed=17)
        assigned = assignments[assignments["assigned"]]
        lookup = roster.set_index("clinician_id")
        for row in assigned.itertuples(index=False):
            self.assertEqual(row.discipline, lookup.loc[row.clinician_id, "discipline"])
            self.assertTrue(bool(lookup.loc[row.clinician_id, "available"]))
        counts = assigned["clinician_id"].value_counts()
        for clinician_id, count in counts.items():
            available_capacity = int(lookup.loc[clinician_id, "capacity"]) - int(
                lookup.loc[clinician_id, "current_workload"]
            )
            self.assertLessEqual(int(count), available_capacity)
        self.assertEqual(summary["max_overload"], 0.0)
        self.assertGreater(summary["unassigned_rate"], 0.0)

    def test_policies_p0_through_p7_produce_required_metrics(self) -> None:
        config = small_config(
            n_patients=8,
            thresholds=(0.75,),
            policies=tuple(POLICY_NAMES),
            capacity_ratio=1.3,
        )
        results, _ = run_policy_threshold_experiment(config, scenarios=("A",))
        self.assertEqual(set(results["policy"]), set(POLICY_NAMES))
        required = {
            "tps_proxy",
            "simulated_outcome_utility",
            "regret_vs_scenario_oracle",
            "high_risk_full_pool_share",
            "zip_fallback_rate",
            "zip_continuity_rate",
            "workload_spread",
            "workload_gini",
            "max_overload",
            "travel_proxy",
            "fairness_gap_age_group",
            "fairness_gap_zip",
            "fairness_gap_area_type",
            "fairness_gap_housing_type",
            "fairness_gap_house_price_group",
            "fairness_gap_clinical_need_decile",
            "assignment_signature",
        }
        self.assertTrue(required.issubset(results.columns))
        self.assertTrue(results["public_data_only"].all())
        self.assertFalse(results["production_validated"].any())
        self.assertTrue(results["oracle_regret_available"].all())
        self.assertTrue(
            (results["scenario_oracle_utility"] + 1e-12 >= results["expected_outcome_utility"]).all()
        )
        self.assertTrue((results["regret_vs_scenario_oracle"] >= 0.0).all())

    def test_generated_and_structural_percentiles_use_persisted_reference(self) -> None:
        reference = load_risk_reference()
        config = small_config(n_patients=9, policies=("P4",), thresholds=(0.75,))
        patients, _ = generate_simulation_inputs(config, 44, "A")
        expected = patients["risk_score_rho_raw"].map(
            lambda value: empirical_risk_percentile(value, reference["sorted_risk_scores"])
        )
        np.testing.assert_allclose(patients["risk_percentile_u"], expected, rtol=0.0, atol=1e-12)

        profiles = generate_full_factorial_profiles()
        canonical = pd.read_csv(PROJECT_ROOT / "data" / "reference" / "hhvbp_structural_grid.csv")
        keys = [
            "health_status", "age_group", "house_price_zip", "area_type",
            "housing_type", "distance_to_clinic", "driving_condition",
        ]
        merged = profiles.merge(canonical, on=keys, suffixes=("_experiment", "_canonical"))
        np.testing.assert_allclose(merged["risk_score_rho_raw"], merged["rho_raw"], rtol=0.0, atol=1e-12)
        np.testing.assert_allclose(
            merged["risk_percentile_u_experiment"],
            merged["risk_percentile_u_canonical"],
            rtol=0.0,
            atol=1e-12,
        )

    def test_oracle_upper_bound_and_fixed_threshold_are_labeled(self) -> None:
        results, _ = run_policy_threshold_experiment(
            small_config(n_patients=7, policies=("P4",), thresholds=(0.75,)),
            scenarios=("A",),
        )
        self.assertTrue(results["oracle_regret_available"].all())
        self.assertTrue(results["scenario_oracle_utility"].notna().all())
        self.assertTrue((results["regret_vs_scenario_oracle"] >= 0.0).all())
        self.assertEqual(
            set(results["regret_reference"]),
            {"unconstrained_latent_information_upper_bound"},
        )
        self.assertTrue((results["regret_vs_best_included_policy"] == 0.0).all())
        recommendation, table = select_simulation_thresholds(results)
        self.assertFalse(recommendation["threshold_optimization_evaluated"])
        self.assertIsNone(recommendation["robust_minimax_regret_threshold"])
        self.assertEqual(set(table["threshold_evaluation_role"]), {"fixed_threshold_only_no_optimization"})

    def test_threshold_selection_uses_held_out_seed(self) -> None:
        results, _ = run_policy_threshold_experiment(
            small_config(
                n_patients=10,
                seeds=(11, 12),
                policies=("P4",),
                thresholds=(0.25, 0.75),
            ),
            scenarios=("F",),
        )
        recommendation, table = select_simulation_thresholds(results)
        self.assertTrue(recommendation["threshold_optimization_evaluated"])
        self.assertEqual(recommendation["selection_seeds"], [11])
        self.assertEqual(recommendation["held_out_evaluation_seeds"], [12])
        self.assertTrue(recommendation["held_out_evaluation_performed"])
        self.assertTrue(table["held_out_simulation_objective"].notna().all())

    def test_one_seed_sweep_is_sensitivity_only(self) -> None:
        results, _ = run_policy_threshold_experiment(
            small_config(n_patients=8, seeds=(11,), policies=("P4",), thresholds=(0.25, 0.75)),
            scenarios=("F",),
        )
        recommendation, table = select_simulation_thresholds(results)
        self.assertFalse(recommendation["threshold_optimization_evaluated"])
        self.assertTrue(recommendation["in_sample_sensitivity_evaluated"])
        self.assertEqual(recommendation["scenario_specific_best_thresholds"], {})
        self.assertIsNone(recommendation["robust_minimax_regret_threshold"])
        self.assertEqual(
            set(table["threshold_evaluation_role"]),
            {"in_sample_sensitivity_only_no_recommendation"},
        )

    def test_legacy_calibration_scenario_selector_changes_evaluated_design(self) -> None:
        cohort = generate_simulation_inputs(
            small_config(n_patients=6, policies=("P4",), thresholds=(0.75,)),
            91,
            "A",
        )[0]
        conservative = sensitivity_table(
            cohort,
            (0.75,),
            selected_scenario="conservative_capacity_top_15_percent",
        )
        balanced = sensitivity_table(
            cohort,
            (0.75,),
            selected_scenario="balanced_capacity_top_25_percent",
        )
        self.assertEqual(set(conservative["evaluated_simulator_scenario"]), {"H"})
        self.assertEqual(set(balanced["evaluated_simulator_scenario"]), {"F"})
        self.assertEqual(set(conservative["evaluated_capacity_ratio"]), {0.70})
        self.assertEqual(set(balanced["evaluated_capacity_ratio"]), {1.05})

    def test_threshold_sweep_reexecutes_assignments_and_outcomes(self) -> None:
        results = run_threshold_sweep(
            20,
            {"RN": 5, "PT": 5, "OT": 3},
            (0.0, 0.5, 1.0),
            scenario="F",
            seeds=(31,),
            policy="P4",
            config=small_config(
                n_patients=20,
                seeds=(31,),
                roster_sizes={"RN": 5, "PT": 5, "OT": 3},
                zip_sparsity=0.05,
                capacity_ratio=1.4,
            ),
        )
        self.assertEqual(len(results), 3)
        self.assertEqual(results["threshold_rerun_id"].nunique(), 3)
        self.assertEqual(results["threshold_raw_equivalent"].nunique(), 3)
        reference = load_risk_reference()
        raw_by_threshold = results.set_index("threshold_percentile")["threshold_raw_equivalent"]
        self.assertAlmostEqual(raw_by_threshold.loc[0.0], min(reference["sorted_risk_scores"]), places=15)
        self.assertAlmostEqual(raw_by_threshold.loc[1.0], max(reference["sorted_risk_scores"]), places=15)
        self.assertGreater(results["assignment_signature"].nunique(), 1)
        self.assertGreater(results["simulated_outcome_utility"].round(12).nunique(), 1)
        assert_nonconstant_threshold_sensitivity(results, policies=("P4",))

    def test_rerun_ids_are_unique_across_operational_designs(self) -> None:
        first, _ = run_policy_threshold_experiment(
            small_config(n_patients=5, thresholds=(0.75,), policies=("P4",), capacity_ratio=0.8),
            scenarios=("A",),
        )
        second, _ = run_policy_threshold_experiment(
            small_config(n_patients=5, thresholds=(0.75,), policies=("P4",), capacity_ratio=1.2),
            scenarios=("A",),
        )
        combined = pd.concat([first, second], ignore_index=True)
        self.assertEqual(combined["threshold_rerun_id"].nunique(), len(combined))

    def test_direct_simulation_rerun_id_binds_input_roster(self) -> None:
        config = small_config(n_patients=6, thresholds=(0.75,), policies=("P4",))
        patients, roster = generate_simulation_inputs(config, 17, "A")
        first, _ = simulate_policy(patients, roster, "A", "P4", 0.75, seed=17)
        changed = roster.copy(deep=True)
        changed.loc[changed.index[0], "true_quality"] = float(
            np.clip(changed.loc[changed.index[0], "true_quality"] + 0.1, 0.0, 1.0)
        )
        second, _ = simulate_policy(patients, changed, "A", "P4", 0.75, seed=17)
        self.assertNotEqual(first["rerun_design_fingerprint"], second["rerun_design_fingerprint"])
        self.assertNotEqual(first["threshold_rerun_id"], second["threshold_rerun_id"])

    def test_common_random_number_reproducibility(self) -> None:
        config = small_config(n_patients=7, policies=("P4", "P5", "P7"))
        first, _ = run_policy_threshold_experiment(config, scenarios=("B",))
        second, _ = run_policy_threshold_experiment(config, scenarios=("B",))
        pd.testing.assert_frame_equal(first, second)

    def test_strict_threshold_rule(self) -> None:
        config = small_config(n_patients=5, policies=("P4",), thresholds=(0.75,), capacity_ratio=2.0)
        patients, roster = generate_simulation_inputs(config, 17, "A")
        # Construct threshold equality explicitly; persisted-reference ECDF
        # values are not cohort ranks and need not contain exactly 0.75.
        patients.loc[:, "risk_percentile_u"] = np.where(
            np.isclose(patients["risk_percentile_u"].to_numpy(dtype=float), 0.75),
            0.750001,
            patients["risk_percentile_u"].to_numpy(dtype=float),
        )
        patients.loc[patients.index[0], "risk_percentile_u"] = 0.75
        _, assignments = simulate_policy(patients, roster, "A", "P4", 0.75, seed=17)
        patient_routes = assignments.groupby("patient_id", as_index=False).first()
        exact = patient_routes[patient_routes["risk_percentile_u"] == 0.75]
        self.assertEqual(len(exact), 1)
        self.assertFalse(bool(exact.iloc[0]["high_risk"]))
        self.assertTrue(patient_routes.loc[patient_routes["risk_percentile_u"] > 0.75, "high_risk"].all())

    def test_p4_preserves_ot_rotation_inside_selected_pool(self) -> None:
        config = small_config(n_patients=5, policies=("P4",), thresholds=(1.0,), capacity_ratio=3.0)
        patients, roster = generate_simulation_inputs(config, 22, "A")
        ot_mask = roster["discipline"] == "OT"
        all_patient_zips = tuple(sorted(set(patients["patient_zip"])))
        roster.loc[ot_mask, "service_zips"] = pd.Series(
            [all_patient_zips] * int(ot_mask.sum()), index=roster.index[ot_mask], dtype=object
        )
        roster.loc[ot_mask, "available"] = True
        roster.loc[ot_mask, "capacity"] = 100
        roster.loc[ot_mask, "current_workload"] = 0
        summary, assignments = simulate_policy(patients, roster, "A", "P4", 1.0, seed=22)
        ot_ids = assignments[assignments["discipline"] == "OT"]["clinician_id"].tolist()
        self.assertEqual(ot_ids, ["OT-001", "OT-002", "OT-001", "OT-002", "OT-001"])
        self.assertTrue(summary["ot_deterministic_rotation_preserved"])

    def test_all_required_scenarios_are_executable(self) -> None:
        self.assertEqual(set(SCENARIOS), set("ABCDEFGHI"))
        config = small_config(n_patients=3, policies=("P4",), thresholds=(0.75,), capacity_ratio=2.0)
        for scenario in SCENARIOS:
            patients, roster = generate_simulation_inputs(config, 4, scenario)
            summary, _ = simulate_policy(patients, roster, scenario, "P4", 0.75, seed=4)
            self.assertEqual(summary["scenario"], scenario)

    def test_scenario_d_has_no_clinician_or_routing_effect(self) -> None:
        scenario = SCENARIOS["D"]
        low = expected_favorable_outcome(0.6, 0.1, False, 45.0, scenario)
        high = expected_favorable_outcome(0.6, 0.9, True, 2.5, scenario)
        self.assertAlmostEqual(low, high, places=14)


class StructuralExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profiles = generate_full_factorial_profiles()

    def test_full_factorial_has_exactly_2160_profiles(self) -> None:
        self.assertEqual(len(self.profiles), 2160)
        self.assertTrue(self.profiles["risk_percentile_u"].between(0, 1).all())
        self.assertIn("metric_need_mean_PPH", self.profiles)
        self.assertIn("contribution_age_group", self.profiles)
        self.assertIn("anchor_rank_pool_10", self.profiles)

    def test_ablation_reliability_and_counterfactual_outputs(self) -> None:
        ablation = factor_ablation_experiment(self.profiles)
        self.assertEqual(
            set(ablation["variant"]),
            {
                "full_seven_factor",
                "no_age",
                "no_house_price",
                "no_area_type",
                "no_housing_type",
                "clinical_only",
                "environment_only",
            },
        )
        reliability = reliability_sensitivity_experiment(self.profiles, reliability_values=(0.1, 1.0))
        self.assertTrue((~reliability["reliability_affects_assignment_mean"]).all())
        self.assertTrue((reliability["route_change_rate"] == 0).all())
        fairness = counterfactual_fairness_experiment(self.profiles)
        self.assertEqual(set(fairness["attribute_varied"]), {"age_group", "zip_area", "house_price_zip", "housing_type"})
        self.assertIn("outcome_change", fairness)
        self.assertTrue(fairness["technical_fairness_audit_not_legal_conclusion"].all())

    def test_artifact_writer_provenance(self) -> None:
        config = small_config(n_patients=5, thresholds=(0.0, 1.0), policies=("P4", "P7"), capacity_ratio=2.0)
        results, _ = run_policy_threshold_experiment(config, scenarios=("A",))
        structural = run_structural_suite()
        with tempfile.TemporaryDirectory() as directory:
            paths = save_experiment_artifacts(
                results,
                directory,
                config=config,
                structural=structural,
                reproduction_command="python scripts/run_batch_policy_experiments.py --quick --structural",
            )
            self.assertTrue(all(path.exists() for path in paths.values()))
            metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
            self.assertTrue(metadata["common_random_numbers"])
            self.assertTrue(metadata["threshold_candidates_fully_rerun"])
            self.assertFalse(metadata["production_validated"])
            self.assertEqual(metadata["production_demo_threshold"], 0.75)


if __name__ == "__main__":
    unittest.main()
