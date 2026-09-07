from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_adapters.census_acs import load_census_acs_zcta  # noqa: E402
from data_adapters.public_adapter_base import PublicDataAdapterError  # noqa: E402
from hhvbp_assignment_service import (  # noqa: E402
    RequestValidationError,
    assign_clinicians,
    assign_clinicians_simple,
    build_clinician_lookup,
    clinician_match_keys_from_values,
    clinician_records_to_dataframe,
    load_assignment_parameters,
    normalize_zip5,
    parse_zip_history_value,
)
from hhvbp_local_externality import derive_age_group, load_json, validate_model_config  # noqa: E402
from hhvbp_threshold_calibration import (  # noqa: E402
    DEFAULT_EQUITY_PENALTY_TPS,
    calibrate_public_synthetic_threshold,
    sensitivity_table,
)
from public_synthetic_data import (  # noqa: E402
    METRIC_ORDER,
    REQUIRED_EPISODE_COLUMNS,
    generate_public_semi_synthetic_dataset,
    generate_public_synthetic_clinicians,
)


def load_example_request() -> dict:
    return json.loads((PROJECT_ROOT / "examples" / "example_assignment_request.json").read_text(encoding="utf-8"))


def add_zip_history(req: dict, zip5: str = "89502", rn_count: int = 3, pt_count: int = 3, ot_count: int = 1) -> dict:
    out = copy.deepcopy(req)
    counts = {"RN": 0, "PT": 0, "OT": 0}
    limits = {"RN": rn_count, "PT": pt_count, "OT": ot_count}
    for clinician in out["clinicians"]:
        discipline = clinician["discipline"]
        if counts[discipline] < limits[discipline]:
            clinician["zip_codes_treated"] = f" {zip5}, {zip5}; 01234 | bad "
            counts[discipline] += 1
        else:
            clinician["zip_codes_treated"] = "01234"
    return out


class AgeZipThresholdTests(unittest.TestCase):
    def test_age_group_boundaries(self) -> None:
        cases = {
            40: "age_40_50",
            49.9: "age_40_50",
            50: "age_50_60",
            59.9: "age_50_60",
            60: "age_60_70",
            69.9: "age_60_70",
            70: "age_70_80",
            80: "age_70_80",
            80.1: "age_80_plus",
        }
        for age, expected in cases.items():
            self.assertEqual(derive_age_group(age), expected)
        with self.assertRaises(ValueError):
            derive_age_group(39.9)

    def test_model_config_completeness(self) -> None:
        global_cfg = load_json(PROJECT_ROOT / "config" / "hhvbp_global_config.json")
        metric_cfg = load_json(PROJECT_ROOT / "config" / "metric_parameter_library.json")
        validate_model_config(global_cfg, metric_cfg)
        self.assertEqual(global_cfg["factor_order"][1], "age_group")
        for metric in metric_cfg["metrics"].values():
            self.assertIn("age_group", metric["importance_levels"])
            self.assertIn("age_group", metric["expert_elicitation"])

    def test_zip_parser_and_normalizer(self) -> None:
        self.assertEqual(normalize_zip5(1234), "01234")
        zips, audit = parse_zip_history_value("01234, 12345;12345| 67890 | |abc")
        self.assertEqual(zips, {"01234", "12345", "67890"})
        self.assertEqual(audit["duplicate_zip_tokens"], 1)
        self.assertIn("abc", audit["invalid_zip_tokens"])

    def test_clinician_matching_convention(self) -> None:
        keys = clinician_match_keys_from_values(blinded_name="RN1")
        self.assertIn("id:101", keys)
        req = load_example_request()
        params = load_assignment_parameters(PROJECT_ROOT / "config")
        clinician_df, _ = clinician_records_to_dataframe(req, params)
        lookup = build_clinician_lookup(clinician_df)
        self.assertEqual(lookup["id:101"], 101)
        self.assertEqual(lookup["id:201"], 201)
        self.assertEqual(lookup["id:301"], 301)

    def test_simple_response_schema_unchanged(self) -> None:
        result = assign_clinicians_simple(load_example_request(), config_dir=PROJECT_ROOT / "config", value_key="clinician_id")
        self.assertEqual(set(result), {"RN", "PT", "OT"})
        self.assertTrue(all(isinstance(result[discipline], list) for discipline in ["RN", "PT", "OT"]))

    def test_default_threshold_is_locked_at_balanced_pilot(self) -> None:
        req = load_example_request()
        result = assign_clinicians(req, config_dir=PROJECT_ROOT / "config")
        self.assertTrue(result["threshold_routing"]["enabled"])
        self.assertEqual(result["threshold_routing"]["threshold_percentile"], 0.75)
        self.assertEqual(
            result["threshold_routing"]["threshold_source"],
            "public_only_semi_synthetic_capacity_pilot_not_outcome_validated",
        )

    def test_high_risk_route_uses_full_pool(self) -> None:
        req = add_zip_history(load_example_request(), rn_count=1, pt_count=1, ot_count=1)
        req["patient"]["severity_score_override"] = 0.9
        req["options"] = {"threshold_routing_enabled": True, "risk_threshold": 0.5, "risk_percentile_override": 0.9, "top_k_groups": 1}
        result = assign_clinicians(req, config_dir=PROJECT_ROOT / "config")
        routes = result["threshold_routing"]["route_by_discipline"]
        self.assertEqual(set(routes.values()), {"global_high_risk"})

    def test_low_risk_zip_pool_and_fallback_routes(self) -> None:
        zip_req = add_zip_history(load_example_request(), rn_count=3, pt_count=3, ot_count=1)
        zip_req["patient"]["severity_score_override"] = 0.1
        zip_req["options"] = {"threshold_routing_enabled": True, "risk_threshold": 0.5, "risk_percentile_override": 0.1, "top_k_groups": 1}
        zip_result = assign_clinicians(zip_req, config_dir=PROJECT_ROOT / "config")
        self.assertEqual(zip_result["threshold_routing"]["route_by_discipline"], {"RN": "zip_history", "PT": "zip_history", "OT": "zip_history"})
        self.assertEqual(zip_result["threshold_routing"]["selected_pool_size_by_discipline"]["RN"], 3)
        self.assertEqual(zip_result["threshold_routing"]["capability_percentiles_scope"], "within_discipline_on_full_request_pool_before_zip_filtering")

        fallback_req = add_zip_history(load_example_request(), rn_count=1, pt_count=1, ot_count=1)
        fallback_req["patient"]["severity_score_override"] = 0.1
        fallback_req["options"] = {"threshold_routing_enabled": True, "risk_threshold": 0.5, "risk_percentile_override": 0.1, "top_k_groups": 1}
        fallback_result = assign_clinicians(fallback_req, config_dir=PROJECT_ROOT / "config")
        self.assertEqual(fallback_result["threshold_routing"]["route_by_discipline"]["RN"], "zip_history_fallback_full_pool")
        self.assertEqual(fallback_result["threshold_routing"]["route_by_discipline"]["PT"], "zip_history_fallback_full_pool")


class PublicSyntheticPipelineTests(unittest.TestCase):
    def small_config(self) -> dict:
        return {
            "mode": "offline_demo_fallback",
            "seed": 123,
            "num_synthetic_episodes": 5,
            "roster_size_by_discipline": {"RN": 4, "PT": 4, "OT": 2},
            "routing": {"risk_threshold": 0.65},
        }

    def test_synthetic_clinician_reproducibility(self) -> None:
        cfg = self.small_config()
        clinicians_a, zips_a, _ = generate_public_synthetic_clinicians(cfg)
        clinicians_b, zips_b, _ = generate_public_synthetic_clinicians(cfg)
        pd.testing.assert_frame_equal(clinicians_a, clinicians_b)
        pd.testing.assert_frame_equal(zips_a, zips_b)
        self.assertTrue(clinicians_a["synthetic_clinician_data"].all())
        self.assertTrue(zips_a["synthetic_zip_history"].all())

    def test_public_dataset_columns_ranges_and_reproducibility(self) -> None:
        cfg = self.small_config()
        manifest = {"manifest_id": "test_manifest"}
        patients_a, clinicians_a, zips_a, episodes_a, _ = generate_public_semi_synthetic_dataset(cfg, manifest=manifest, config_dir=PROJECT_ROOT / "config")
        patients_b, clinicians_b, zips_b, episodes_b, _ = generate_public_semi_synthetic_dataset(cfg, manifest=manifest, config_dir=PROJECT_ROOT / "config")
        pd.testing.assert_frame_equal(patients_a, patients_b)
        pd.testing.assert_frame_equal(clinicians_a, clinicians_b)
        pd.testing.assert_frame_equal(zips_a, zips_b)
        pd.testing.assert_frame_equal(episodes_a, episodes_b)
        for column in REQUIRED_EPISODE_COLUMNS:
            self.assertIn(column, episodes_a.columns)
        for metric in METRIC_ORDER:
            column = f"outcome_{metric}"
            self.assertIn(column, episodes_a.columns)
            self.assertTrue(episodes_a[column].between(0, 1).all())
        self.assertTrue(episodes_a["synthetic_data"].all())
        self.assertTrue(episodes_a["public_data_only"].all())
        self.assertFalse(episodes_a["production_validated"].any())

    def test_public_adapter_offline_fallback_and_error(self) -> None:
        manifest = {
            "sources": {
                "census_acs_5yr_zcta": {
                    "title": "ACS test",
                    "url": "",
                    "local_file": "",
                    "allow_online": False,
                }
            }
        }
        df, metadata = load_census_acs_zcta(manifest, offline_demo_fallback=True)
        self.assertTrue(df.empty)
        self.assertEqual(metadata["data_source_warning"], "offline_fallback_distributions")
        with self.assertRaises(PublicDataAdapterError):
            load_census_acs_zcta(manifest, offline_demo_fallback=False)

    def test_threshold_calibration_metadata(self) -> None:
        cfg = self.small_config()
        _, _, _, episodes, _ = generate_public_semi_synthetic_dataset(cfg, manifest={"manifest_id": "test"}, config_dir=PROJECT_ROOT / "config")
        selected, table = calibrate_public_synthetic_threshold(episodes)
        self.assertEqual(selected["threshold_source"], "public_only_semi_synthetic_capacity_pilot_not_outcome_validated")
        self.assertEqual(selected["threshold_percentile"], 0.75)
        self.assertEqual(selected["risk_threshold"], 0.75)
        self.assertTrue(selected["public_data_only"])
        self.assertTrue(selected["synthetic_data"])
        self.assertFalse(selected["production_validated"])
        self.assertFalse(table.empty)
        self.assertFalse(table["production_validated"].any())
        self.assertTrue(table["threshold_fully_rerun"].all())
        self.assertGreater(table["assignment_signature"].nunique(), 1)
        self.assertGreater(table["simulated_outcome_utility"].nunique(), 1)
        self.assertTrue(selected["optimization_evaluated"])
        self.assertEqual(
            selected["simulation_recommendation_label"],
            "held_out_simulation_selection",
        )
        self.assertNotEqual(selected["selection_seed"], selected["held_out_evaluation_seed"])
        self.assertEqual(
            set(table["calibration_evaluation_design"]),
            {"held_out_simulation_selection"},
        )

    def test_capacity_target_has_default_tps_equity_penalty(self) -> None:
        cfg = self.small_config()
        _, _, _, episodes, _ = generate_public_semi_synthetic_dataset(
            cfg,
            manifest={"manifest_id": "test"},
            config_dir=PROJECT_ROOT / "config",
        )
        penalized = sensitivity_table(episodes, (0.25, 0.75))
        unpenalized = sensitivity_table(episodes, (0.25, 0.75), lambda_equity=0.0)
        self.assertTrue(
            (penalized["lambda_equity_tps_per_unit_share"] == DEFAULT_EQUITY_PENALTY_TPS).all()
        )
        pd.testing.assert_series_equal(
            penalized["objective"].reset_index(drop=True),
            (
                unpenalized["objective"]
                - DEFAULT_EQUITY_PENALTY_TPS
                * penalized["equity_gap_from_target"]
            ).reset_index(drop=True),
            check_names=False,
        )

    def test_incomplete_calibration_frame_lists_missing_risk_factors(self) -> None:
        cfg = self.small_config()
        _, _, _, episodes, _ = generate_public_semi_synthetic_dataset(
            cfg,
            manifest={"manifest_id": "test"},
            config_dir=PROJECT_ROOT / "config",
        )
        incomplete = episodes.drop(columns=["health_status", "age_group"])
        with self.assertRaises(ValueError) as raised:
            calibrate_public_synthetic_threshold(
                incomplete,
                candidate_threshold_grid=(0.75,),
            )
        message = str(raised.exception)
        self.assertIn("all seven patient risk factors", message)
        self.assertIn("health_status", message)
        self.assertIn("age_group", message)

    def test_single_candidate_is_fixed_threshold_not_recommendation(self) -> None:
        cfg = self.small_config()
        _, _, _, episodes, _ = generate_public_semi_synthetic_dataset(
            cfg,
            manifest={"manifest_id": "test"},
            config_dir=PROJECT_ROOT / "config",
        )
        selected, table = calibrate_public_synthetic_threshold(
            episodes,
            candidate_threshold_grid=(0.75,),
        )
        self.assertEqual(selected["risk_threshold"], 0.75)
        self.assertFalse(selected["optimization_evaluated"])
        self.assertIsNone(selected["simulation_recommended_threshold"])
        self.assertIsNone(selected["held_out_regret"])
        self.assertEqual(
            selected["simulation_recommendation_label"],
            "fixed_threshold_only_no_optimization",
        )
        self.assertEqual(
            set(table["calibration_evaluation_design"]),
            {"fixed_threshold_only_no_optimization"},
        )


if __name__ == "__main__":
    unittest.main()
