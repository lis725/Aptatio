from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_local_externality import AGE_GROUP_ORDER, METRIC_ORDER, RAW_DIRECTION  # noqa: E402
from hhvbp_reporting import (  # noqa: E402
    CONTROLLED_RENO_AGE_GROUP,
    ExperimentEvidenceValidationError,
    PILOT_THRESHOLD_PERCENTILE,
    UnsupportedReportClaimError,
    _load_experiment_evidence,
    _load_reporting_context,
    assert_no_unsupported_claims,
    build_reno_age_sensitivity_profiles,
    build_reno_age_sensitivity_rows,
    build_reno_geographic_profiles,
    build_reno_geographic_rows,
    describe_distance_to_anchors,
    scan_unsupported_claims,
    write_report_bundle,
)


CONFIG_DIR = PROJECT_ROOT / "config"
REFERENCE_PATH = PROJECT_ROOT / "data" / "reference" / "hhvbp_risk_reference.json"
EXPERIMENT_ROOT = PROJECT_ROOT / "reports" / "experiments"


class RenoScenarioDesignTests(unittest.TestCase):
    def test_geographic_profiles_control_age_and_do_not_route_assignment_zip(self) -> None:
        profiles = build_reno_geographic_profiles()
        self.assertEqual(len(profiles), 10)
        for scenario in profiles:
            self.assertEqual(scenario["profile"]["age_group"], CONTROLLED_RENO_AGE_GROUP)
            self.assertIsNone(scenario["patient_zip_for_assignment_routing"])
            self.assertFalse(scenario["assignment_zip_routing_applied"])
            self.assertRegex(scenario["profiling_zip"], r"^\d{5}$")

    def test_age_sensitivity_varies_only_age_across_all_five_groups(self) -> None:
        profiles = build_reno_age_sensitivity_profiles()
        self.assertEqual([row["profile"]["age_group"] for row in profiles], AGE_GROUP_ORDER)
        fixed_factor_snapshots = []
        for scenario in profiles:
            profile = dict(scenario["profile"])
            profile.pop("age_group")
            fixed_factor_snapshots.append(profile)
            self.assertIsNone(scenario["patient_zip_for_assignment_routing"])
            self.assertFalse(scenario["assignment_zip_routing_applied"])
        self.assertTrue(all(snapshot == fixed_factor_snapshots[0] for snapshot in fixed_factor_snapshots))

    def test_distance_language_respects_metric_direction(self) -> None:
        pph = describe_distance_to_anchors("PPH", mean=11.5, best=10.0, worst=14.0)
        self.assertEqual(pph["distance_from_best_label"], "points_above_best")
        self.assertEqual(pph["distance_from_worst_label"], "points_below_worst")
        self.assertEqual(pph["relative_to_anchor_language"], "11.50% is 1.50 points above best and 2.50 points below worst.")

        dfs = describe_distance_to_anchors("DFS", mean=75.0, best=80.0, worst=70.0)
        self.assertEqual(dfs["distance_from_best_label"], "points_below_best")
        self.assertEqual(dfs["distance_from_worst_label"], "points_above_worst")
        self.assertEqual(dfs["relative_to_anchor_language"], "75.00% is 5.00 points below best and 5.00 points above worst.")


class ReportDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.geographic_rows = build_reno_geographic_rows(CONFIG_DIR, REFERENCE_PATH)
        cls.age_rows = build_reno_age_sensitivity_rows(CONFIG_DIR, REFERENCE_PATH)

    def test_reno_geographic_rows_cover_all_metrics_with_controlled_age(self) -> None:
        self.assertEqual(len(self.geographic_rows), 10 * len(METRIC_ORDER))
        self.assertEqual({row["metric_key"] for row in self.geographic_rows}, set(METRIC_ORDER))
        self.assertTrue(all(row["age_group"] == CONTROLLED_RENO_AGE_GROUP for row in self.geographic_rows))
        self.assertTrue(all(not row["assignment_zip_routing_applied"] for row in self.geographic_rows))
        self.assertTrue(all(row["patient_zip_for_assignment_routing"] is None for row in self.geographic_rows))

    def test_anchor_labels_are_correct_for_every_metric(self) -> None:
        for row in self.geographic_rows:
            if RAW_DIRECTION[row["metric_key"]] == "lower_is_better":
                self.assertEqual(row["distance_from_best_label"], "points_above_best")
                self.assertEqual(row["distance_from_worst_label"], "points_below_worst")
            else:
                self.assertEqual(row["distance_from_best_label"], "points_below_best")
                self.assertEqual(row["distance_from_worst_label"], "points_above_worst")

    def test_age_rows_cover_five_ages_and_hold_other_factors_fixed(self) -> None:
        self.assertEqual(len(self.age_rows), len(AGE_GROUP_ORDER) * len(METRIC_ORDER))
        self.assertEqual({row["age_group"] for row in self.age_rows}, set(AGE_GROUP_ORDER))
        fixed_factors = [
            "health_status",
            "house_price_zip",
            "area_type",
            "housing_type",
            "distance_to_clinic",
            "driving_condition",
        ]
        for factor in fixed_factors:
            self.assertEqual(len({row[factor] for row in self.age_rows}), 1)

    def test_strict_threshold_classification_is_mechanical(self) -> None:
        for row in self.geographic_rows + self.age_rows:
            expected = (
                "above_pilot_threshold"
                if row["risk_percentile_u"] > PILOT_THRESHOLD_PERCENTILE
                else "at_or_below_pilot_threshold"
            )
            self.assertEqual(row["risk_threshold_classification"], expected)


class UnsupportedClaimScannerTests(unittest.TestCase):
    def test_scanner_finds_unsupported_affirmative_claims(self) -> None:
        text = (
            "This policy guarantees payment improvement.\n"
            "The threshold is empirically optimal.\n"
            "The model improves patient outcomes.\n"
            "The model estimates causal clinician effects.\n"
            "We used a six-factor model."
        )
        rule_ids = {finding["rule_id"] for finding in scan_unsupported_claims(text)}
        self.assertIn("guaranteed_improvement", rule_ids)
        self.assertIn("real_world_optimality", rule_ids)
        self.assertIn("patient_outcome_benefit", rule_ids)
        self.assertIn("causal_clinician_effect", rule_ids)
        self.assertIn("stale_six_factor_wording", rule_ids)

    def test_scanner_allows_explicit_limitations(self) -> None:
        text = (
            "The model does not guarantee payment improvement.\n"
            "This is not an optimal threshold.\n"
            "The model does not establish causal clinician effects.\n"
            "Reno values are configured scenario distributions."
        )
        self.assertEqual(scan_unsupported_claims(text), [])

    def test_assertion_reports_source_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "unsafe.md"
            path.write_text("Header\nThis guarantees patient benefit.\n", encoding="utf-8")
            with self.assertRaises(UnsupportedReportClaimError) as caught:
                assert_no_unsupported_claims([path])
            self.assertIn(":2", str(caught.exception))


class ExperimentEvidenceProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.context = _load_reporting_context(CONFIG_DIR, REFERENCE_PATH)

    def _copy_fine_tier(self, destination_root: Path) -> Path:
        source = EXPERIMENT_ROOT / "threshold_fine_grid"
        destination = destination_root / "threshold_fine_grid"
        destination.mkdir(parents=True)
        for filename in (
            "experiment_metadata.json",
            "threshold_recommendations.json",
            "threshold_summary.csv",
        ):
            shutil.copy2(source / filename, destination / filename)
        metadata_path = destination / "experiment_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["implementation_file_hashes_sha256"] = {
            relative_path: hashlib.sha256((PROJECT_ROOT / relative_path).read_bytes()).hexdigest()
            for relative_path in (
                "scripts/run_batch_policy_experiments.py",
                "src/hhvbp_batch_experiments.py",
            )
        }
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return destination

    def test_validated_fine_tier_records_held_out_role_and_metadata_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tier = self._copy_fine_tier(root)
            evidence = _load_experiment_evidence(root, self.context)
            self.assertEqual(evidence["validation_status"], "passed")
            self.assertEqual(
                evidence["validated_tiers"]["threshold_fine_grid"],
                "held_out_fine_threshold_selection",
            )
            self.assertIn(tier / "experiment_metadata.json", evidence["source_paths"])

    def test_risk_reference_mismatch_fails_before_evidence_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tier = self._copy_fine_tier(root)
            metadata_path = tier / "experiment_metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["risk_reference_id"] = "0" * 64
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ExperimentEvidenceValidationError, "current persisted risk_reference_id"):
                _load_experiment_evidence(root, self.context)

    def test_fine_grid_mislabelled_as_fixed_threshold_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tier = self._copy_fine_tier(root)
            metadata_path = tier / "experiment_metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["thresholds"] = [0.75]
            metadata["threshold_optimization_evaluated"] = False
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ExperimentEvidenceValidationError, "thresholds do not match role"):
                _load_experiment_evidence(root, self.context)

    def test_result_without_metadata_is_rejected_as_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tier = self._copy_fine_tier(root)
            (tier / "experiment_metadata.json").unlink()
            with self.assertRaisesRegex(ExperimentEvidenceValidationError, "incomplete"):
                _load_experiment_evidence(root, self.context)


class ReportBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.output_dir = Path(cls.temp_dir.name) / "current"
        cls.experiment_root = Path(cls.temp_dir.name) / "empty_experiments"
        cls.experiment_root.mkdir()
        cls.manifest = write_report_bundle(
            output_dir=cls.output_dir,
            config_dir=CONFIG_DIR,
            reference_path=REFERENCE_PATH,
            generated_at="2026-07-15T00:00:00Z",
            experiment_root=cls.experiment_root,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_expected_current_artifacts_and_claim_scan(self) -> None:
        expected = {
            "README.md",
            "reno_pph_report.md",
            "reno_other_metrics_report.md",
            "reno_age_sensitivity_report.md",
            "nontechnical_report.md",
            "current_model_report.md",
            "sponsor_report.md",
            "reno_pph_scenarios.csv",
            "reno_pph_scenarios.json",
            "reno_other_metrics_scenarios.csv",
            "reno_other_metrics_scenarios.json",
            "reno_age_sensitivity.csv",
            "reno_age_sensitivity.json",
            "report_manifest.json",
        }
        self.assertEqual({path.name for path in self.output_dir.iterdir()}, expected)
        self.assertEqual(self.manifest["unsupported_claim_scan"]["status"], "passed")
        self.assertFalse(self.manifest["pdf_or_latex_generated"])
        assert_no_unsupported_claims(
            path
            for path in self.output_dir.iterdir()
            if path.suffix.lower() in {".md", ".csv", ".json"}
        )

    def test_machine_readable_rows_match_between_csv_and_json(self) -> None:
        for stem in ("reno_pph_scenarios", "reno_other_metrics_scenarios", "reno_age_sensitivity"):
            with (self.output_dir / f"{stem}.csv").open(encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            json_payload = json.loads((self.output_dir / f"{stem}.json").read_text(encoding="utf-8"))
            self.assertEqual(len(csv_rows), json_payload["row_count"])
            self.assertEqual(len(csv_rows), len(json_payload["rows"]))

    def test_required_explanations_are_present(self) -> None:
        nontechnical = (self.output_dir / "nontechnical_report.md").read_text(encoding="utf-8")
        self.assertIn("risk_score_rho_raw", nontechnical)
        self.assertIn("risk_percentile_u", nontechnical)
        self.assertIn("risk_percentile_u > 0.75", nontechnical)
        self.assertIn("RN=3, PT=3, and OT=1", nontechnical)
        self.assertIn("full request pool before any ZIP filter", nontechnical)
        self.assertIn("capacity", nontechnical.lower())
        self.assertIn("not outcome-validated", nontechnical)

        pph = (self.output_dir / "reno_pph_report.md").read_text(encoding="utf-8")
        self.assertIn("age_group=age_70_80", pph)
        self.assertIn("assignment_zip_routing_applied=false", pph)
        self.assertIn("points above best", pph)
        self.assertIn("points below worst", pph)

        other = (self.output_dir / "reno_other_metrics_report.md").read_text(encoding="utf-8")
        self.assertIn("points below best", other)
        self.assertIn("points above worst", other)


if __name__ == "__main__":
    unittest.main()
