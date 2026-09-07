from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:  # The repository's full algorithm runtime requires NumPy/Pandas.
    from hhvbp_local_externality import build_patient_need_vector  # noqa: E402
except ModuleNotFoundError as exc:  # pragma: no cover - depends on the local test runtime.
    build_patient_need_vector = None
    LOCAL_EXTERNALITY_IMPORT_ERROR = str(exc)
else:
    LOCAL_EXTERNALITY_IMPORT_ERROR = ""
from hhvbp_risk_reference import (  # noqa: E402
    MINIMUM_MONTE_CARLO_SIZE,
    STRUCTURAL_GRID_SIZE,
    THRESHOLD_PERCENTILE,
    THRESHOLD_SOURCE,
    build_structural_grid,
    empirical_risk_percentile,
    generate_risk_reference,
    load_risk_reference,
    quantile_raw,
    risk_score_for_profile,
    write_risk_reference_artifacts,
)


class EmpiricalPercentileTests(unittest.TestCase):
    def test_clamps_and_quantile_interpolation(self) -> None:
        values = [0.0, 1.0, 2.0, 3.0, 4.0]
        self.assertEqual(empirical_risk_percentile(-1.0, values), 0.0)
        self.assertEqual(empirical_risk_percentile(5.0, values), 1.0)
        tau = quantile_raw(values, 0.75)
        self.assertEqual(tau, 3.0)
        self.assertAlmostEqual(empirical_risk_percentile(tau, values), 0.75)
        self.assertAlmostEqual(empirical_risk_percentile(2.5, values), 0.625)

    def test_ties_have_deterministic_average_rank(self) -> None:
        values = [0.0, 1.0, 1.0, 3.0]
        self.assertAlmostEqual(empirical_risk_percentile(1.0, values), 0.5)
        self.assertAlmostEqual(empirical_risk_percentile(2.0, values), 0.75)
        self.assertEqual(empirical_risk_percentile(-0.1, values), 0.0)
        self.assertEqual(empirical_risk_percentile(3.1, values), 1.0)

    def test_degenerate_reference_and_validation(self) -> None:
        self.assertEqual(empirical_risk_percentile(0.4, [0.5, 0.5]), 0.0)
        self.assertEqual(empirical_risk_percentile(0.5, [0.5, 0.5]), 0.5)
        self.assertEqual(empirical_risk_percentile(0.6, [0.5, 0.5]), 1.0)
        with self.assertRaises(ValueError):
            empirical_risk_percentile(math.nan, [0.0, 1.0])
        with self.assertRaises(ValueError):
            empirical_risk_percentile(0.5, [])
        with self.assertRaises(ValueError):
            quantile_raw([0.0, 1.0], 1.01)


class StructuralGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.grid = build_structural_grid(PROJECT_ROOT / "config")

    def test_exact_2160_cartesian_profiles(self) -> None:
        self.assertEqual(len(self.grid), STRUCTURAL_GRID_SIZE)
        factor_order = [
            "health_status",
            "age_group",
            "house_price_zip",
            "area_type",
            "housing_type",
            "distance_to_clinic",
            "driving_condition",
        ]
        profiles = {tuple(row[factor] for factor in factor_order) for row in self.grid}
        self.assertEqual(len(profiles), STRUCTURAL_GRID_SIZE)
        self.assertEqual(self.grid[0]["profile_id"], "profile_0001")
        self.assertEqual(self.grid[-1]["profile_id"], "profile_2160")
        self.assertTrue(all(0.0 <= float(row["rho_raw"]) <= 1.0 for row in self.grid))

    def test_compiled_score_matches_canonical_need_vector(self) -> None:
        if build_patient_need_vector is None:
            self.skipTest(f"Canonical runtime dependencies are unavailable: {LOCAL_EXTERNALITY_IMPORT_ERROR}")
        profiles = [
            {
                "health_status": "healthy",
                "age_group": "age_40_50",
                "house_price_zip": "high",
                "area_type": "micropolitan",
                "housing_type": "single_home",
                "distance_to_clinic": "near",
                "driving_condition": "good_summer",
            },
            {
                "health_status": "moderate",
                "age_group": "age_70_80",
                "house_price_zip": "average",
                "area_type": "metropolitan",
                "housing_type": "apartment",
                "distance_to_clinic": "medium",
                "driving_condition": "bad_winter",
            },
        ]
        for profile in profiles:
            canonical = build_patient_need_vector(PROJECT_ROOT / "config", profile)
            expected = float(canonical["weighted_need_mean"].sum())
            actual = risk_score_for_profile(profile, PROJECT_ROOT / "config")
            self.assertAlmostEqual(actual, expected, places=12)


class ReferenceGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.created_at = "2026-07-15T12:00:00Z"
        cls.reference, cls.grid = generate_risk_reference(
            PROJECT_ROOT / "config",
            sample_size=MINIMUM_MONTE_CARLO_SIZE,
            seed=424242,
            creation_timestamp=cls.created_at,
        )

    def test_reference_is_sorted_and_has_required_provenance(self) -> None:
        values = self.reference["sorted_risk_scores"]
        metadata = self.reference["metadata"]
        self.assertEqual(len(values), MINIMUM_MONTE_CARLO_SIZE)
        self.assertEqual(values, sorted(values))
        self.assertEqual(metadata["sample_size"], MINIMUM_MONTE_CARLO_SIZE)
        self.assertEqual(metadata["structural_grid_size"], STRUCTURAL_GRID_SIZE)
        self.assertEqual(metadata["creation_timestamp"], self.created_at)
        self.assertEqual(metadata["source_mode"], "offline_demo_fallback")
        self.assertEqual(metadata["threshold_source"], THRESHOLD_SOURCE)
        self.assertTrue(metadata["public_data_only"])
        self.assertTrue(metadata["offline"])
        self.assertTrue(metadata["semi_synthetic"])
        self.assertTrue(metadata["synthetic_data"])
        self.assertTrue(metadata["expert_configured"])
        self.assertFalse(metadata["sponsor_data_used"])
        self.assertFalse(metadata["outcome_validated"])
        self.assertFalse(metadata["production_validated"])
        self.assertEqual(len(metadata["config_hash"]), 64)

    def test_factor_distributions_and_realized_counts_are_complete(self) -> None:
        metadata = self.reference["metadata"]
        expected_factors = {
            "health_status",
            "age_group",
            "house_price_zip",
            "area_type",
            "housing_type",
            "distance_to_clinic",
            "driving_condition",
        }
        self.assertEqual(set(metadata["factor_distributions"]), expected_factors)
        for factor, distribution in metadata["factor_distributions"].items():
            self.assertAlmostEqual(sum(distribution.values()), 1.0)
            self.assertEqual(sum(metadata["realized_factor_counts"][factor].values()), MINIMUM_MONTE_CARLO_SIZE)

    def test_tau_raw_is_q075_and_grid_is_enriched(self) -> None:
        expected_tau = quantile_raw(self.reference["sorted_risk_scores"], THRESHOLD_PERCENTILE)
        self.assertAlmostEqual(self.reference["tau_raw"], expected_tau, places=15)
        self.assertAlmostEqual(self.reference["quantiles"]["0.75"], expected_tau, places=15)
        self.assertEqual(len(self.grid), STRUCTURAL_GRID_SIZE)
        self.assertTrue(all(0.0 <= row["risk_percentile_u"] <= 1.0 for row in self.grid))
        self.assertTrue(
            all(
                (row["risk_percentile_u"] > THRESHOLD_PERCENTILE)
                == (row["threshold_route"] == "global_high_risk")
                for row in self.grid
            )
        )

    def test_seed_and_config_produce_same_reference(self) -> None:
        second, second_grid = generate_risk_reference(
            PROJECT_ROOT / "config",
            sample_size=MINIMUM_MONTE_CARLO_SIZE,
            seed=424242,
            creation_timestamp="2099-01-01T00:00:00Z",
        )
        self.assertEqual(self.reference["sorted_risk_scores"], second["sorted_risk_scores"])
        self.assertEqual(self.reference["metadata"]["reference_id"], second["metadata"]["reference_id"])
        self.assertEqual(self.reference["metadata"]["config_hash"], second["metadata"]["config_hash"])
        self.assertEqual(
            [row["risk_percentile_u"] for row in self.grid],
            [row["risk_percentile_u"] for row in second_grid],
        )

    def test_sample_size_minimum_is_enforced(self) -> None:
        with self.assertRaises(ValueError):
            generate_risk_reference(PROJECT_ROOT / "config", sample_size=MINIMUM_MONTE_CARLO_SIZE - 1)

    def test_write_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_risk_reference_artifacts(self.reference, self.grid, temp_dir)
            self.assertTrue(paths["reference"].is_file())
            self.assertTrue(paths["structural_grid"].is_file())
            self.assertTrue(paths["structural_grid_metadata"].is_file())
            loaded = load_risk_reference(temp_dir)
            self.assertEqual(loaded["metadata"]["reference_id"], self.reference["metadata"]["reference_id"])
            self.assertEqual(loaded["sorted_risk_scores"], self.reference["sorted_risk_scores"])
            self.assertEqual(loaded["artifact_path"], str(paths["reference"].resolve()))
            self.assertEqual(
                loaded["artifact_sha256"],
                hashlib.sha256(paths["reference"].read_bytes()).hexdigest(),
            )
            grid_metadata = json.loads(
                paths["structural_grid_metadata"].read_text(encoding="utf-8")
            )
            self.assertEqual(
                grid_metadata["risk_reference_artifact_sha256"],
                hashlib.sha256(paths["reference"].read_bytes()).hexdigest(),
            )
            self.assertEqual(
                grid_metadata["structural_grid_csv_sha256"],
                hashlib.sha256(paths["structural_grid"].read_bytes()).hexdigest(),
            )
            with paths["structural_grid"].open(encoding="utf-8", newline="") as handle:
                self.assertEqual(sum(1 for _ in csv.DictReader(handle)), STRUCTURAL_GRID_SIZE)

    def test_loader_rejects_tampered_provenance_and_score_content(self) -> None:
        mutations = {
            "config_hash": lambda payload: payload["metadata"].__setitem__("config_hash", "0" * 64),
            "component_hash": lambda payload: payload["metadata"]["config_file_hashes_sha256"].__setitem__(
                "hhvbp_global_config.json", "0" * 64
            ),
            "reference_id": lambda payload: payload["metadata"].__setitem__("reference_id", "0" * 64),
            "score_content": lambda payload: payload["sorted_risk_scores"].__setitem__(
                0, float(payload["sorted_risk_scores"][0]) - 1e-9
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                payload = copy.deepcopy(self.reference)
                mutate(payload)
                path = Path(temp_dir) / "hhvbp_risk_reference.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_risk_reference(path)

    def test_loader_rejects_reference_when_current_component_config_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            reference_path = temp_root / "hhvbp_risk_reference.json"
            reference_path.write_text(json.dumps(self.reference), encoding="utf-8")
            copied_config = temp_root / "config"
            shutil.copytree(PROJECT_ROOT / "config", copied_config)
            global_path = copied_config / "hhvbp_global_config.json"
            global_config = json.loads(global_path.read_text(encoding="utf-8"))
            global_config["cms_model"] = "deliberately changed test value"
            global_path.write_text(json.dumps(global_config), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "component config files"):
                load_risk_reference(reference_path, config_dir=copied_config)


if __name__ == "__main__":
    unittest.main()
