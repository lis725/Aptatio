from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_assignment_service import (  # noqa: E402
    assign_clinicians,
    clinician_records_to_dataframe,
    prepare_clinician_panel,
    stable_hash_to_int,
)
from hhvbp_local_externality import METRIC_ORDER  # noqa: E402
from public_synthetic_data import (  # noqa: E402
    clinician_rows_to_request,
    generate_public_synthetic_clinicians,
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def example_request() -> dict:
    return load_json(PROJECT_ROOT / "examples" / "example_assignment_request.json")


def ranking_ids(result: dict, discipline: str) -> list[int]:
    return [int(row["clinician_id"]) for row in result["discipline_rankings"][discipline]]


def recommended_ids(result: dict, discipline: str) -> list[int | None]:
    return [
        None if value is None else int(value)
        for value in result["threshold_routing"]["recommended_clinician_ids"][discipline]
    ]


def configure_zip_pools(
    request: dict,
    patient_zip: object,
    selected_counts: dict[str, int],
) -> tuple[dict, dict[str, set[int]]]:
    out = copy.deepcopy(request)
    out["patient"]["zip_code"] = patient_zip
    out["patient"]["local_externalities"]["zip_code"] = patient_zip
    seen = {"RN": 0, "PT": 0, "OT": 0}
    expected: dict[str, set[int]] = {"RN": set(), "PT": set(), "OT": set()}
    zip_representations: list[object] = ["01234", 1234, " 01234 "]
    for clinician in out["clinicians"]:
        discipline = clinician["discipline"]
        if seen[discipline] < selected_counts[discipline]:
            clinician["zip_codes_treated"] = zip_representations[seen[discipline] % len(zip_representations)]
            expected[discipline].add(int(clinician["clinician_id"]))
        else:
            clinician["zip_codes_treated"] = "99999"
        seen[discipline] += 1
    out["options"] = {"risk_percentile_override": 0.50, "top_k_groups": 3}
    return out, expected


class InputScaleAndMissingnessTests(unittest.TestCase):
    def test_shrinkage_requires_an_explicit_opt_in(self) -> None:
        params = load_json(PROJECT_ROOT / "config" / "assignment_parameters.json")
        params.pop("enable_shrinkage", None)
        raw, _ = clinician_records_to_dataframe(example_request(), params)
        prepared, _ = prepare_clinician_panel(raw, params)

        for metric in METRIC_ORDER:
            self.assertTrue((prepared[f"{metric}_lambda"] == 1.0).all())
            self.assertTrue(
                prepared[f"{metric}_score_ready"].equals(prepared[metric].astype(float))
            )

    def test_percentage_and_proportion_requests_are_assignment_equivalent(self) -> None:
        proportion_request = example_request()
        percentage_request = copy.deepcopy(proportion_request)
        for clinician in percentage_request["clinicians"]:
            clinician["metric_scale"] = "percentage"
            clinician["metrics"] = {
                metric: None if value is None else float(value) * 100.0
                for metric, value in clinician["metrics"].items()
            }

        proportion = assign_clinicians(proportion_request, config_dir=PROJECT_ROOT / "config")
        percentage = assign_clinicians(percentage_request, config_dir=PROJECT_ROOT / "config")

        for discipline in ["RN", "PT", "OT"]:
            with self.subTest(discipline=discipline):
                self.assertEqual(ranking_ids(proportion, discipline), ranking_ids(percentage, discipline))
                self.assertEqual(recommended_ids(proportion, discipline), recommended_ids(percentage, discipline))
                left = proportion["discipline_rankings"][discipline]
                right = percentage["discipline_rankings"][discipline]
                for left_row, right_row in zip(left, right, strict=True):
                    self.assertAlmostEqual(
                        float(left_row["discipline_score"]),
                        float(right_row["discipline_score"]),
                        places=14,
                    )

    def test_all_and_partial_missing_clinicians_have_metric_level_audit_flags(self) -> None:
        request = example_request()
        all_missing = next(row for row in request["clinicians"] if int(row["clinician_id"]) == 101)
        partial_missing = next(row for row in request["clinicians"] if int(row["clinician_id"]) == 102)
        observed = next(row for row in request["clinicians"] if int(row["clinician_id"]) == 105)
        all_missing["metrics"] = {metric: None for metric in METRIC_ORDER}
        partial_missing["metrics"]["DTC"] = None

        result = assign_clinicians(request, config_dir=PROJECT_ROOT / "config")
        records = {
            int(row["clinician_id"]): row
            for row in result["discipline_rankings"]["RN"]
        }

        all_missing_record = records[101]
        self.assertFalse(all_missing_record["all_metrics_observed"])
        self.assertTrue(all_missing_record["any_metric_imputed"])
        self.assertEqual(all_missing_record["imputation_confidence"], "moderate_discipline_median_imputation")
        self.assertEqual(
            set(all_missing_record["metric_value_sources"].values()),
            {"imputed_discipline_median"},
        )

        partial_record = records[102]
        self.assertFalse(partial_record["all_metrics_observed"])
        self.assertTrue(partial_record["any_metric_imputed"])
        self.assertEqual(partial_record["metric_value_sources"]["DTC"], "imputed_discipline_median")
        self.assertTrue(
            all(
                source == "observed"
                for metric, source in partial_record["metric_value_sources"].items()
                if metric != "DTC"
            )
        )

        observed_record = records[int(observed["clinician_id"])]
        self.assertTrue(observed_record["all_metrics_observed"])
        self.assertFalse(observed_record["any_metric_imputed"])
        self.assertEqual(observed_record["imputation_confidence"], "observed_no_imputation")
        self.assertEqual(set(observed_record["metric_value_sources"].values()), {"observed"})

        for metric in METRIC_ORDER:
            summary = result["preparation_summary"]["imputation_by_metric"][metric]
            self.assertEqual(sum(summary.values()), len(request["clinicians"]))

    def test_discipline_with_no_observed_metrics_uses_org_benchmark_and_low_confidence(self) -> None:
        request = example_request()
        rn_count = 0
        for clinician in request["clinicians"]:
            if clinician["discipline"] == "RN":
                clinician["metrics"] = {metric: None for metric in METRIC_ORDER}
                rn_count += 1

        result = assign_clinicians(request, config_dir=PROJECT_ROOT / "config")
        for record in result["discipline_rankings"]["RN"]:
            self.assertFalse(record["all_metrics_observed"])
            self.assertTrue(record["any_metric_imputed"])
            self.assertEqual(record["imputation_confidence"], "low_org_benchmark_imputation")
            self.assertEqual(set(record["metric_value_sources"].values()), {"imputed_org_benchmark"})
        for metric in METRIC_ORDER:
            self.assertEqual(
                result["preparation_summary"]["imputation_by_metric"][metric]["imputed_org_benchmark"],
                rn_count,
            )


class DeterminismZipAndProfileTests(unittest.TestCase):
    def test_exact_score_ties_use_stable_ids_across_shuffle_and_repeated_runs(self) -> None:
        request = example_request()
        benchmarks = load_json(PROJECT_ROOT / "config" / "assignment_parameters.json")["metric_benchmarks"]
        for clinician in request["clinicians"]:
            if clinician["discipline"] == "RN":
                clinician["metrics"] = {metric: float(benchmarks[metric]) for metric in METRIC_ORDER}

        shuffled = copy.deepcopy(request)
        random.Random(20260715).shuffle(shuffled["clinicians"])
        first = assign_clinicians(request, config_dir=PROJECT_ROOT / "config")
        second = assign_clinicians(request, config_dir=PROJECT_ROOT / "config")
        shuffled_result = assign_clinicians(shuffled, config_dir=PROJECT_ROOT / "config")

        expected_ids = sorted(
            int(row["clinician_id"])
            for row in request["clinicians"]
            if row["discipline"] == "RN"
        )
        self.assertEqual(ranking_ids(first, "RN"), expected_ids)
        self.assertEqual(ranking_ids(second, "RN"), expected_ids)
        self.assertEqual(ranking_ids(shuffled_result, "RN"), expected_ids)
        self.assertEqual(recommended_ids(first, "RN"), recommended_ids(second, "RN"))
        self.assertEqual(recommended_ids(first, "RN"), recommended_ids(shuffled_result, "RN"))
        scores = [float(row["discipline_score"]) for row in first["discipline_rankings"]["RN"]]
        self.assertTrue(all(math.isclose(scores[0], score, rel_tol=0.0, abs_tol=1e-15) for score in scores[1:]))

    def test_numeric_leading_zero_zip_routes_against_string_histories(self) -> None:
        request, expected = configure_zip_pools(
            example_request(),
            patient_zip=1234,
            selected_counts={"RN": 3, "PT": 3, "OT": 1},
        )
        result = assign_clinicians(request, config_dir=PROJECT_ROOT / "config")

        self.assertEqual(result["patient"]["patient_zip"], "01234")
        self.assertEqual(result["assignment_audit"]["patient_zip"], "01234")
        self.assertEqual(
            result["threshold_routing"]["route_by_discipline"],
            {"RN": "zip_history", "PT": "zip_history", "OT": "zip_history"},
        )
        self.assertEqual(
            result["threshold_routing"]["selected_pool_size_by_discipline"],
            {"RN": 3, "PT": 3, "OT": 1},
        )
        for discipline in ["RN", "PT", "OT"]:
            routed_ids = {
                int(row["clinician_id"])
                for row in result["routed_discipline_rankings"][discipline]
            }
            self.assertEqual(routed_ids, expected[discipline])

    def test_ot_rotation_is_deterministic_and_confined_to_selected_zip_pool(self) -> None:
        request, expected = configure_zip_pools(
            example_request(),
            patient_zip="01234",
            selected_counts={"RN": 3, "PT": 3, "OT": 2},
        )
        first = assign_clinicians(request, config_dir=PROJECT_ROOT / "config")
        second = assign_clinicians(request, config_dir=PROJECT_ROOT / "config")

        selected_rows = sorted(
            first["routed_discipline_rankings"]["OT"],
            key=lambda row: (str(row["clinician_name"]), int(row["clinician_id"])),
        )
        ordered_ids = [int(row["clinician_id"]) for row in selected_rows]
        offset = stable_hash_to_int(
            f"{request['request_id']}|{request['patient']['patient_id']}|OT"
        ) % len(ordered_ids)
        rotated = ordered_ids[offset:] + ordered_ids[:offset]
        expected_rotation = [rotated[index % len(rotated)] for index in range(3)]

        self.assertEqual(first["threshold_routing"]["route_by_discipline"]["OT"], "zip_history")
        self.assertEqual(first["mirrored_assignment_context"]["ot_context"]["pool_size"], 2)
        self.assertEqual(set(ordered_ids), expected["OT"])
        self.assertEqual(recommended_ids(first, "OT"), expected_rotation)
        self.assertEqual(recommended_ids(second, "OT"), expected_rotation)
        self.assertEqual(len(set(recommended_ids(first, "OT")[:2])), 2)
        self.assertTrue(set(recommended_ids(first, "OT")).issubset(expected["OT"]))

    def test_best_and_worst_factor_profiles_are_finite_bounded_and_ordered(self) -> None:
        global_config = load_json(PROJECT_ROOT / "config" / "hhvbp_global_config.json")
        results: dict[str, dict] = {}
        for profile_name in ["best_case", "worst_case"]:
            request = example_request()
            request["patient"]["local_externalities"] = copy.deepcopy(
                global_config["profiles"][profile_name]
            )
            request["patient"]["local_externalities"]["zip_code"] = "89502"
            results[profile_name] = assign_clinicians(request, config_dir=PROJECT_ROOT / "config")

        for profile_name, result in results.items():
            with self.subTest(profile=profile_name):
                severity = result["patient_severity"]
                self.assertTrue(math.isfinite(float(severity["risk_score_rho_raw"])))
                self.assertTrue(math.isfinite(float(severity["risk_percentile_u"])))
                self.assertGreaterEqual(float(severity["risk_score_rho_raw"]), 0.0)
                self.assertLessEqual(float(severity["risk_score_rho_raw"]), 1.0)
                self.assertGreaterEqual(float(severity["risk_percentile_u"]), 0.0)
                self.assertLessEqual(float(severity["risk_percentile_u"]), 1.0)
                self.assertEqual(len(result["patient_need"]), len(METRIC_ORDER))
                for metric_row in result["patient_need"]:
                    self.assertTrue(math.isfinite(float(metric_row["need_mean"])))
                    self.assertGreaterEqual(float(metric_row["need_mean"]), 0.0)
                    self.assertLessEqual(float(metric_row["need_mean"]), 1.0)

        self.assertLess(
            float(results["best_case"]["patient_severity"]["risk_score_rho_raw"]),
            float(results["worst_case"]["patient_severity"]["risk_score_rho_raw"]),
        )
        self.assertLess(
            float(results["best_case"]["patient_severity"]["risk_percentile_u"]),
            float(results["worst_case"]["patient_severity"]["risk_percentile_u"]),
        )

    def test_all_required_metric_aliases_preserve_scores_and_recommendations(self) -> None:
        aliases = {
            "DFS": "DC Function",
            "DTC": "DTC-PAC",
            "Agency_Rating": "Overall Rating",
            "Recommend": "Willingness to Recommend",
            "Care_Issues": "Specific Care Issues",
            "Communications": "Communications Between Providers and Patients",
        }
        canonical_request = example_request()
        alias_request = copy.deepcopy(canonical_request)
        for clinician in alias_request["clinicians"]:
            clinician["metrics"] = {
                aliases.get(metric, metric): value
                for metric, value in clinician["metrics"].items()
            }

        canonical = assign_clinicians(canonical_request, config_dir=PROJECT_ROOT / "config")
        aliased = assign_clinicians(alias_request, config_dir=PROJECT_ROOT / "config")
        for discipline in ["RN", "PT", "OT"]:
            self.assertEqual(ranking_ids(canonical, discipline), ranking_ids(aliased, discipline))
            self.assertEqual(recommended_ids(canonical, discipline), recommended_ids(aliased, discipline))
            for left, right in zip(
                canonical["discipline_rankings"][discipline],
                aliased["discipline_rankings"][discipline],
                strict=True,
            ):
                self.assertAlmostEqual(
                    float(left["discipline_score"]),
                    float(right["discipline_score"]),
                    places=14,
                )
        self.assertEqual(set(aliased["input_summary"]["metric_aliases_used"]), set(aliases.values()))
        self.assertEqual(aliased["input_summary"]["duplicate_alias_values_deduplicated"], 0)


class ProvenanceAndClaimsTests(unittest.TestCase):
    def test_assignment_audit_is_cryptographically_bound_to_exact_risk_reference(self) -> None:
        result = assign_clinicians(example_request(), config_dir=PROJECT_ROOT / "config")
        audit = result["assignment_audit"]["risk_reference"]
        artifact_path = Path(audit["artifact_path"])
        persisted = load_json(artifact_path)

        self.assertEqual(audit["reference_id"], persisted["metadata"]["reference_id"])
        self.assertEqual(audit["config_hash"], persisted["metadata"]["config_hash"])
        self.assertEqual(
            audit["config_file_hashes_sha256"],
            persisted["metadata"]["config_file_hashes_sha256"],
        )
        self.assertEqual(audit["sample_size"], len(persisted["sorted_risk_scores"]))
        self.assertEqual(audit["artifact_sha256"], hashlib.sha256(artifact_path.read_bytes()).hexdigest())

    def test_offline_manifest_is_complete_and_does_not_pose_as_retrieved_data(self) -> None:
        manifest = load_json(PROJECT_ROOT / "data" / "manifests" / "public_data_manifest.example.json")
        self.assertTrue(manifest["public_data_only"])
        self.assertEqual(manifest["source_mode"], "offline_demo_fallback")
        self.assertIn("No public source files were downloaded", manifest["download_status"])
        self.assertTrue(manifest["sources"])

        required_fields = {
            "title",
            "purpose",
            "url",
            "dataset_id",
            "retrieval_date",
            "retrieval_status",
            "geography",
            "checksum_sha256",
            "columns_used",
            "transformation",
            "allow_online",
        }
        for source_name, source in manifest["sources"].items():
            with self.subTest(source=source_name):
                self.assertTrue(required_fields.issubset(source))
                self.assertTrue(str(source["title"]).strip())
                self.assertTrue(str(source["url"]).strip())
                self.assertTrue(str(source["dataset_id"]).strip())
                self.assertTrue(str(source["geography"]).strip())
                self.assertIsInstance(source["columns_used"], list)
                self.assertTrue(str(source["transformation"]).strip())
                self.assertTrue(
                    str(source.get("local_cache_path") or source.get("cache_raw_under") or "").strip(),
                    "Every source must record its intended local cache path.",
                )
                self.assertTrue(
                    str(source.get("year") or source.get("reporting_period") or "").strip(),
                    "Every source must record a year/reporting-period field, including an explicit offline N/A value.",
                )
                self.assertFalse(source["allow_online"])
                self.assertIsNone(source["retrieval_date"])
                self.assertIsNone(source["checksum_sha256"])
                self.assertRegex(source["retrieval_status"], r"(?:not_retrieved|disabled)")

    def test_reference_artifacts_have_explicit_offline_public_only_provenance(self) -> None:
        reference = load_json(PROJECT_ROOT / "data" / "reference" / "hhvbp_risk_reference.json")
        metadata = reference["metadata"]
        self.assertEqual(metadata["source_mode"], "offline_demo_fallback")
        self.assertEqual(metadata["data_source_warning"], "offline_fallback_distributions")
        self.assertEqual(
            metadata["provenance_label"],
            "public_only_semi_synthetic_capacity_pilot_not_outcome_validated",
        )
        self.assertGreaterEqual(int(metadata["sample_size"]), 10_000)
        self.assertEqual(int(metadata["structural_grid_size"]), 2_160)
        for key in ["public_data_only", "offline", "semi_synthetic", "synthetic_data", "expert_configured"]:
            self.assertTrue(metadata[key])
        for key in ["sponsor_data_used", "historical_sponsor_data", "outcome_validated", "production_validated"]:
            self.assertFalse(metadata[key])

        structural = load_json(
            PROJECT_ROOT / "data" / "reference" / "hhvbp_structural_grid.metadata.json"
        )
        self.assertEqual(int(structural["row_count"]), 2_160)
        self.assertTrue(structural["public_data_only"])
        self.assertTrue(structural["semi_synthetic"])
        self.assertTrue(structural["synthetic_data"])
        self.assertFalse(structural["sponsor_data_used"])
        self.assertFalse(structural["outcome_validated"])
        self.assertFalse(structural["production_validated"])

    def test_synthetic_clinician_zip_history_is_labeled_synthetic_public_only(self) -> None:
        config = {
            "mode": "offline_demo_fallback",
            "seed": 919,
            "roster_size_by_discipline": {"RN": 1, "PT": 1, "OT": 1},
        }
        clinicians, zip_history, report = generate_public_synthetic_clinicians(config)
        request_rows = clinician_rows_to_request(clinicians, zip_history)
        self.assertTrue(report["synthetic_zip_history"])
        self.assertTrue(zip_history["synthetic_zip_history"].all())
        self.assertTrue(zip_history["public_data_only"].all())
        self.assertEqual({row["data_origin"] for row in request_rows}, {"synthetic_public_only"})
        self.assertTrue(all(row["synthetic_clinician_data"] for row in request_rows))
        self.assertTrue(all(row["public_data_only"] for row in request_rows))

    def test_current_text_reports_contain_no_positive_unsupported_claims(self) -> None:
        patterns = {
            "guaranteed_or_proven_improvement": re.compile(
                r"\b(?:model|algorithm|policy|threshold|assignment)\s+"
                r"(?:is\s+proven\s+to\s+|has\s+been\s+shown\s+to\s+|guarantees?\s+|will\s+)"
                r"(?:improve|increase|maximize)s?\b",
                re.IGNORECASE,
            ),
            "claimed_proven_benefit": re.compile(
                r"\b(?:proven|demonstrated)\s+(?:patient|clinical|payment|HHVBP|TPS)\s+"
                r"(?:benefit|improvement|gain)s?\b",
                re.IGNORECASE,
            ),
            "claimed_real_world_optimum": re.compile(
                r"\b(?:empirically|real[- ]world|clinically|causally)\s+optimal\b|"
                r"\bpayment[- ]maximizing\b",
                re.IGNORECASE,
            ),
            "claimed_sponsor_validation": re.compile(
                r"\bvalidated\s+(?:on|against|with|using)\s+"
                r"(?:sponsor|patient[- ]level|clinician[- ]level|real[- ]world)\s+"
                r"(?:data|outcomes?)\b",
                re.IGNORECASE,
            ),
        }
        negation = re.compile(
            r"\b(?:not|no|never|cannot|can't|do\s+not|does\s+not|without|unvalidated)\b",
            re.IGNORECASE,
        )
        candidate_files = [PROJECT_ROOT / "README.md"]
        for root in [PROJECT_ROOT / "docs", PROJECT_ROOT / "reports"]:
            if root.exists():
                candidate_files.extend(
                    path
                    for path in root.rglob("*")
                    if path.is_file() and path.suffix.lower() in {".md", ".tex", ".txt", ".rst"}
                )

        findings: list[str] = []
        for path in sorted(set(candidate_files)):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                for label, pattern in patterns.items():
                    for match in pattern.finditer(line):
                        prefix = line[max(0, match.start() - 100) : match.start()]
                        if negation.search(prefix):
                            continue
                        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
                        findings.append(f"{relative_path}:{line_number} [{label}] {line.strip()}")

        self.assertEqual(findings, [], "Unsupported positive claims found:\n" + "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
