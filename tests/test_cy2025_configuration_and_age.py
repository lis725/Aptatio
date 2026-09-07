from __future__ import annotations

import copy
import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_local_externality import (  # noqa: E402
    AGE_EXPERT_ELICITATION,
    AGE_GROUP_RAW_ALIASES,
    CMS_VERSION_LOCK,
    CY2025_METRIC_WEIGHTS,
    SEVEN_FACTOR_ORDER,
    derive_age_group,
    load_json,
    normalize_age_group,
    validate_model_config,
    validate_patient_profile,
)


class CY2025ConfigurationLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_cfg = load_json(PROJECT_ROOT / "config" / "hhvbp_global_config.json")
        cls.metric_library = load_json(PROJECT_ROOT / "config" / "metric_parameter_library.json")

    def test_locked_metadata_is_explicit_and_consistent(self) -> None:
        for config in (self.global_cfg, self.metric_library):
            self.assertEqual({key: config[key] for key in CMS_VERSION_LOCK}, CMS_VERSION_LOCK)
        self.assertEqual(self.metric_library["measure_set_version"], "CY2025")
        self.assertNotIn("CY2026", self.metric_library["metrics"])

    def test_exact_ten_metrics_and_weights_sum_to_one(self) -> None:
        metrics = self.metric_library["metrics"]
        self.assertEqual(set(metrics), set(CY2025_METRIC_WEIGHTS))
        actual = {key: metrics[key]["composite_weight"] for key in metrics}
        self.assertEqual(actual, CY2025_METRIC_WEIGHTS)
        self.assertEqual(sum(Decimal(str(weight)) for weight in actual.values()), Decimal("1.00"))

    def test_canonical_alias_lists_cover_each_measure_without_conflicts(self) -> None:
        aliases_by_metric = self.metric_library["canonical_metric_aliases"]
        self.assertEqual(set(aliases_by_metric), set(CY2025_METRIC_WEIGHTS))
        required = {
            "DFS": {"DFS", "DC Function", "Discharge Function Score"},
            "DTC": {"DTC", "DTC-PAC"},
            "Agency_Rating": {"Agency_Rating", "Overall Rating"},
            "Recommend": {"Recommend", "Willingness to Recommend"},
            "Care_Issues": {"Care_Issues", "Specific Care Issues"},
            "Communications": {"Communications", "Communications Between Providers and Patients"},
        }
        claimed: dict[str, str] = {}
        for canonical, aliases in aliases_by_metric.items():
            self.assertIn(canonical, aliases)
            self.assertTrue(required.get(canonical, {canonical}).issubset(set(aliases)))
            for alias in aliases:
                normalized = " ".join(alias.strip().casefold().split())
                self.assertNotIn(normalized, claimed, f"{alias!r} is claimed by multiple canonical metrics")
                claimed[normalized] = canonical

    def test_exact_seven_factor_configuration(self) -> None:
        self.assertEqual(self.global_cfg["factor_order"], SEVEN_FACTOR_ORDER)
        self.assertEqual(set(self.global_cfg["reliability"]), set(SEVEN_FACTOR_ORDER))
        self.assertEqual(set(self.global_cfg["base_favorability"]), set(SEVEN_FACTOR_ORDER))
        self.assertEqual(self.global_cfg["reliability"]["age_group"], 0.95)
        self.assertEqual(
            self.global_cfg["base_favorability"]["age_group"],
            {
                "age_80_plus": 0.22,
                "age_70_80": 0.36,
                "age_60_70": 0.50,
                "age_50_60": 0.62,
                "age_40_50": 0.72,
            },
        )
        self.assertEqual(self.global_cfg["profiles"]["best_case"]["age_group"], "age_40_50")
        self.assertEqual(self.global_cfg["profiles"]["worst_case"]["age_group"], "age_80_plus")

    def test_metric_level_age_elicitation_is_locked(self) -> None:
        for metric, (importance, low, mode, high) in AGE_EXPERT_ELICITATION.items():
            metric_cfg = self.metric_library["metrics"][metric]
            self.assertEqual(metric_cfg["importance_levels"]["age_group"], importance)
            self.assertEqual(
                metric_cfg["expert_elicitation"]["age_group"],
                {
                    "best_category": "age_40_50",
                    "worst_category": "age_80_plus",
                    "low": low,
                    "mode": mode,
                    "high": high,
                },
            )

    def test_validator_enforces_the_version_measure_and_alias_locks(self) -> None:
        validate_model_config(self.global_cfg, self.metric_library)

        wrong_version = copy.deepcopy(self.metric_library)
        wrong_version["measure_set_version"] = "CY2026"
        with self.assertRaisesRegex(ValueError, "measure_set_version"):
            validate_model_config(self.global_cfg, wrong_version)

        wrong_weight = copy.deepcopy(self.metric_library)
        wrong_weight["metrics"]["DFS"]["composite_weight"] = 0.19
        with self.assertRaisesRegex(ValueError, "composite weights"):
            validate_model_config(self.global_cfg, wrong_weight)

        extra_factor = copy.deepcopy(self.global_cfg)
        extra_factor["reliability"]["unlocked_factor"] = 0.50
        with self.assertRaisesRegex(ValueError, "exactly the seven locked factors"):
            validate_model_config(extra_factor, self.metric_library)

        alias_conflict = copy.deepcopy(self.metric_library)
        alias_conflict["canonical_metric_aliases"]["DTC"].append("DFS")
        with self.assertRaisesRegex(ValueError, "assigned to both"):
            validate_model_config(self.global_cfg, alias_conflict)


class AgeNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_cfg = load_json(PROJECT_ROOT / "config" / "hhvbp_global_config.json")

    def test_all_required_numeric_boundaries(self) -> None:
        cases = {
            40: "age_40_50",
            49: "age_40_50",
            50: "age_50_60",
            59: "age_50_60",
            60: "age_60_70",
            69: "age_60_70",
            70: "age_70_80",
            80: "age_70_80",
            81: "age_80_plus",
            90: "age_80_plus",
        }
        for age, expected in cases.items():
            with self.subTest(age=age):
                self.assertEqual(derive_age_group(age), expected)

    def test_all_raw_database_band_aliases(self) -> None:
        self.assertEqual(self.global_cfg["age_configuration"]["raw_band_aliases"], AGE_GROUP_RAW_ALIASES)
        for raw_band, expected in AGE_GROUP_RAW_ALIASES.items():
            with self.subTest(raw_band=raw_band):
                self.assertEqual(normalize_age_group(raw_band, self.global_cfg), expected)
                self.assertEqual(derive_age_group(raw_band), expected)

                profile = dict(self.global_cfg["profiles"]["best_case"])
                profile["age_group"] = raw_band
                validate_patient_profile(profile, self.global_cfg)

    def test_numeric_80_and_indivisible_80_89_are_intentionally_distinct(self) -> None:
        self.assertEqual(derive_age_group(80), "age_70_80")
        self.assertEqual(normalize_age_group("80_89", self.global_cfg), "age_80_plus")
        self.assertIn("Numeric age 80", self.global_cfg["age_configuration"]["boundary_distinction"])

    def test_unsupported_values_keep_existing_validation_behavior(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported range age >= 40"):
            derive_age_group(39)
        with self.assertRaisesRegex(ValueError, "must be numeric"):
            derive_age_group("not_an_age_or_band")
        with self.assertRaisesRegex(ValueError, "must be finite"):
            derive_age_group(float("inf"))

        invalid_profile = dict(self.global_cfg["profiles"]["best_case"])
        invalid_profile["age_group"] = "39_49"
        with self.assertRaisesRegex(ValueError, "Invalid category for age_group"):
            validate_patient_profile(invalid_profile, self.global_cfg)


if __name__ == "__main__":
    unittest.main()
