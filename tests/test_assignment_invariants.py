from __future__ import annotations

import copy
import json
import random
import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hhvbp_assignment_service import (  # noqa: E402
    RequestValidationError,
    anchor_index_from_risk_percentile,
    assign_clinicians,
    candidate_indices_from_anchor,
    clinician_records_to_dataframe,
    load_assignment_parameters,
    parse_zip_history_value,
    prepare_clinician_panel,
)


def example_request() -> dict:
    return json.loads((PROJECT_ROOT / "examples" / "example_assignment_request.json").read_text(encoding="utf-8"))


def append_clones(request: dict, discipline: str, count: int, id_start: int) -> dict:
    out = copy.deepcopy(request)
    template = next(row for row in out["clinicians"] if row["discipline"] == discipline)
    for offset in range(count):
        row = copy.deepcopy(template)
        row["clinician_id"] = id_start + offset
        row["clinician_name"] = f"Unrelated {discipline} clinician {chr(65 + offset)}"
        row["blinded_name"] = row["clinician_name"]
        out["clinicians"].append(row)
    return out


def configure_zip_history(request: dict, zip5: str, counts: dict[str, int]) -> dict:
    out = copy.deepcopy(request)
    seen = {"RN": 0, "PT": 0, "OT": 0}
    out["patient"]["zip_code"] = zip5
    for clinician in out["clinicians"]:
        discipline = clinician["discipline"]
        clinician["zip_codes_treated"] = zip5 if seen[discipline] < counts[discipline] else "01234"
        seen[discipline] += 1
    return out


class AssignmentInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config_dir = PROJECT_ROOT / "config"

    def ranked_ids(self, response: dict, discipline: str) -> list[int]:
        return [int(row["clinician_id"]) for row in response["discipline_rankings"][discipline]]

    def selected_ids(self, response: dict, discipline: str) -> list[int]:
        return [int(value) for value in response["threshold_routing"]["recommended_clinician_ids"][discipline]]

    def test_unrelated_disciplines_cannot_change_rn(self) -> None:
        base = assign_clinicians(example_request(), config_dir=self.config_dir)
        with_ot = assign_clinicians(append_clones(example_request(), "OT", 20, 350), config_dir=self.config_dir)
        with_pt = assign_clinicians(append_clones(example_request(), "PT", 20, 250), config_dir=self.config_dir)
        self.assertEqual(self.ranked_ids(base, "RN"), self.ranked_ids(with_ot, "RN"))
        self.assertEqual(self.ranked_ids(base, "RN"), self.ranked_ids(with_pt, "RN"))
        self.assertEqual(self.selected_ids(base, "RN"), self.selected_ids(with_ot, "RN"))
        self.assertEqual(self.selected_ids(base, "RN"), self.selected_ids(with_pt, "RN"))

    def test_rn_cannot_change_pt_normalization(self) -> None:
        base = assign_clinicians(example_request(), config_dir=self.config_dir)
        perturbed = assign_clinicians(append_clones(example_request(), "RN", 20, 150), config_dir=self.config_dir)
        self.assertEqual(self.ranked_ids(base, "PT"), self.ranked_ids(perturbed, "PT"))

    def test_input_row_shuffle_is_invariant(self) -> None:
        request = example_request()
        shuffled = copy.deepcopy(request)
        random.Random(20250715).shuffle(shuffled["clinicians"])
        base = assign_clinicians(request, config_dir=self.config_dir)
        other = assign_clinicians(shuffled, config_dir=self.config_dir)
        for discipline in ["RN", "PT", "OT"]:
            self.assertEqual(self.ranked_ids(base, discipline), self.ranked_ids(other, discipline))
            self.assertEqual(self.selected_ids(base, discipline), self.selected_ids(other, discipline))

    def test_capabilities_are_within_discipline(self) -> None:
        params = load_assignment_parameters(self.config_dir)
        raw, _ = clinician_records_to_dataframe(example_request(), params)
        prepared, _ = prepare_clinician_panel(raw, params)
        for discipline, group in prepared.groupby("discipline"):
            for metric in ["PPH", "DFS", "Recommend"]:
                self.assertGreaterEqual(group[f"{metric}_capability"].min(), 1.0 / len(group))
                self.assertLessEqual(group[f"{metric}_capability"].max(), 1.0)
            self.assertTrue(discipline in {"RN", "PT", "OT"})

    def test_percentile_is_computed_before_zip_filter(self) -> None:
        request = configure_zip_history(example_request(), "89502", {"RN": 3, "PT": 3, "OT": 1})
        request["options"] = {"risk_percentile_override": 0.50, "top_k_groups": 3}
        result = assign_clinicians(request, config_dir=self.config_dir)
        self.assertEqual(
            result["threshold_routing"]["capability_percentiles_scope"],
            "within_discipline_on_full_request_pool_before_zip_filtering",
        )
        full_rank = {row["clinician_id"]: row["full_pool_rank"] for row in result["discipline_rankings"]["RN"]}
        for row in result["routed_discipline_rankings"]["RN"]:
            self.assertEqual(row["full_pool_rank"], full_rank[row["clinician_id"]])

    def test_strict_percentile_threshold_and_zip_pool_minima(self) -> None:
        request = configure_zip_history(example_request(), "89502", {"RN": 3, "PT": 3, "OT": 1})
        request["options"] = {"risk_percentile_override": 0.75, "top_k_groups": 1}
        equal = assign_clinicians(request, config_dir=self.config_dir)
        self.assertEqual(equal["threshold_routing"]["route_by_discipline"], {
            "RN": "zip_history", "PT": "zip_history", "OT": "zip_history"
        })

        request["options"]["risk_percentile_override"] = np.nextafter(0.75, 1.0).item()
        above = assign_clinicians(request, config_dir=self.config_dir)
        self.assertEqual(set(above["threshold_routing"]["route_by_discipline"].values()), {"global_high_risk"})

        fallback_request = configure_zip_history(example_request(), "89502", {"RN": 2, "PT": 2, "OT": 0})
        fallback_request["options"] = {"risk_percentile_override": 0.50, "top_k_groups": 1}
        fallback = assign_clinicians(fallback_request, config_dir=self.config_dir)
        self.assertEqual(set(fallback["threshold_routing"]["route_by_discipline"].values()), {"zip_history_fallback_full_pool"})

    def test_missing_zip_preserves_compatibility_with_audited_fallback(self) -> None:
        request = example_request()
        request["patient"].pop("zip", None)
        request["patient"].pop("zip_code", None)
        request["patient"].get("local_externalities", {}).pop("zip", None)
        request["patient"].get("local_externalities", {}).pop("zip_code", None)
        request["options"] = {"risk_percentile_override": 0.50, "top_k_groups": 1}
        response = assign_clinicians(request, config_dir=self.config_dir)
        self.assertEqual(set(response["threshold_routing"]["fallback_reason_by_discipline"].values()), {"patient_zip_missing"})

    def test_zip_arrays_numeric_values_dedup_and_leading_zeroes(self) -> None:
        zips, audit = parse_zip_history_value(np.array(["01234", 1234, 1234.0, "12345", "bad"], dtype=object))
        self.assertEqual(zips, {"01234", "12345"})
        self.assertEqual(audit["duplicate_zip_tokens"], 2)
        self.assertEqual(audit["invalid_zip_tokens"], ["bad"])

    def test_mirrored_anchor_rounding_and_neighbor_order(self) -> None:
        anchor = anchor_index_from_risk_percentile(0.625, 5)
        self.assertEqual(anchor, 2)  # round_half_up(1.5)
        self.assertEqual(candidate_indices_from_anchor(anchor, 5, 5), [2, 1, 3, 0, 4])

    def test_metric_aliases_deduplicate_and_conflicts_fail(self) -> None:
        params = load_assignment_parameters(self.config_dir)
        request = example_request()
        first = request["clinicians"][0]
        first["metrics"]["DC Function"] = first["metrics"]["DFS"]
        _, summary = clinician_records_to_dataframe(request, params)
        self.assertEqual(summary["duplicate_alias_values_deduplicated"], 1)

        first["metrics"]["DC Function"] = 0.001
        with self.assertRaisesRegex(RequestValidationError, "conflicting aliases for DFS"):
            clinician_records_to_dataframe(request, params)

    def test_internal_audit_has_required_provenance(self) -> None:
        result = assign_clinicians(example_request(), config_dir=self.config_dir)
        audit = result["assignment_audit"]
        for key in [
            "risk_score_rho_raw", "risk_percentile_u", "threshold_percentile",
            "threshold_raw_equivalent", "threshold_source", "patient_zip",
            "route_by_discipline", "full_pool_size", "zip_pool_size",
            "selected_pool_size", "fallback_reason", "selected_clinician_ranks",
            "value_provenance", "public_data_only", "production_validated",
        ]:
            self.assertIn(key, audit)
        self.assertTrue(audit["public_data_only"])
        self.assertFalse(audit["production_validated"])


if __name__ == "__main__":
    unittest.main()
