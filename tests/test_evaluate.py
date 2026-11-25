import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import evaluate


ROOT = Path(__file__).resolve().parents[1]
DATASET = evaluate.load_json(ROOT / "dataset.json", "dataset")
IMPROVED = evaluate.load_json(ROOT / "examples" / "improved.json", "respostas")
BASELINE = evaluate.load_json(ROOT / "examples" / "baseline.json", "respostas")


def local_temp_directory():
    (ROOT / "local").mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=ROOT / "local")


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.document = copy.deepcopy(IMPROVED)

    def report(self):
        return evaluate.evaluate_responses(DATASET, self.document)

    def test_reference_fixture_and_reproducible_comparison(self):
        report = self.report()
        self.assertTrue(report["passed"])
        self.assertEqual(report["metrics"]["all_checks"], {"passed": 9, "total": 9, "rate": 1.0})
        self.assertEqual(report["metrics"]["abstention"], {"passed": 8, "total": 8, "rate": 1.0})
        baseline = evaluate.evaluate_responses(DATASET, BASELINE)
        self.assertEqual(baseline["metrics"]["all_checks"], {"passed": 3, "total": 9, "rate": 0.333333})
        comparison = evaluate.compare_reports(baseline, report)
        self.assertEqual(comparison["deltas"]["all_checks"]["passed_difference"], 6)
        self.assertAlmostEqual(comparison["deltas"]["all_checks"]["percentage_point_difference"], 66.666667)
        self.assertEqual(report, self.report())

    def test_missing_case_keeps_dataset_denominator(self):
        self.document["responses"].pop(0)
        report = self.report()
        self.assertEqual(report["metrics"]["all_checks"]["total"], 9)
        self.assertEqual(report["metrics"]["all_checks"]["passed"], 8)
        self.assertEqual(report["metrics"]["abstention"]["total"], 8)
        self.assertEqual(report["metrics"]["abstention"]["passed"], 7)
        self.assertFalse(report["cases"][0]["checks"]["schema"])

    def test_empty_responses_fail_every_applicable_case(self):
        self.document["responses"] = []
        report = self.report()
        self.assertEqual(report["metrics"]["all_checks"], {"passed": 0, "total": 9, "rate": 0.0})
        self.assertEqual(report["metrics"]["abstention"], {"passed": 0, "total": 8, "rate": 0.0})

    def test_absent_fields_do_not_count_as_abstention(self):
        self.document["responses"][0] = {"case_id": "SYN-01"}
        checks = self.report()["cases"][0]["checks"]
        self.assertFalse(checks["schema"])
        self.assertFalse(checks["cause"])
        self.assertFalse(checks["priority"])
        self.assertFalse(checks["abstention"])

    def test_invented_evidence_and_duplicate_evidence_are_rejected(self):
        for refs in (["UNKNOWN"], ["REP-01", "MET-01", "CHG-01", "REP-01"]):
            with self.subTest(refs=refs):
                self.document["responses"][0]["evidence_ids"] = refs
                self.assertFalse(self.report()["cases"][0]["checks"]["evidence_ids"])

    def test_numeric_value_units_and_omission_are_checked(self):
        for value in (0.01, 100, "1", True):
            with self.subTest(value=value):
                self.document["responses"][0]["numeric_facts"]["error_rate_percent"] = value
                self.assertFalse(self.report()["cases"][0]["checks"]["numeric_facts"])
        self.document = copy.deepcopy(IMPROVED)
        del self.document["responses"][0]["numeric_facts"]["p95_ms"]
        self.assertFalse(self.report()["cases"][0]["checks"]["numeric_facts"])

    def test_boolean_is_not_a_number_even_when_equal_to_expected_one(self):
        self.document["responses"][0]["numeric_facts"]["error_rate_percent"] = True
        checks = self.report()["cases"][0]["checks"]
        self.assertFalse(checks["schema"])
        self.assertFalse(checks["numeric_facts"])

    def test_integer_and_float_representations_of_same_value_pass(self):
        self.document["responses"][0]["numeric_facts"]["p95_ms"] = 900.0
        self.assertTrue(self.report()["cases"][0]["checks"]["numeric_facts"])

    def test_new_number_in_case_without_metrics_fails(self):
        self.document["responses"][5]["numeric_facts"] = {"p95_ms": 900}
        self.assertFalse(self.report()["cases"][5]["checks"]["numeric_facts"])

    def test_only_admissible_category_and_exact_missing_info_pass(self):
        self.document["responses"][0]["category"] = "availability"
        self.document["responses"][0]["missing_info"] = ["baseline"]
        checks = self.report()["cases"][0]["checks"]
        self.assertTrue(checks["schema"])
        self.assertFalse(checks["category"])
        self.assertFalse(checks["missing_info"])
        for category in ("errors", "insufficient_data"):
            self.document["responses"][7]["category"] = category
            self.assertTrue(self.report()["cases"][7]["checks"]["category"])

    def test_invented_cause_or_priority_breaks_abstention(self):
        for field, value in (("cause_id", "UNCONFIRMED"), ("priority", {"rule_id": "UNKNOWN", "level": "P1"})):
            with self.subTest(field=field):
                self.document = copy.deepcopy(IMPROVED)
                self.document["responses"][0][field] = value
                checks = self.report()["cases"][0]["checks"]
                self.assertFalse(checks["abstention"])
                self.assertFalse(checks["cause" if field == "cause_id" else "priority"])

    def test_confirmed_cause_requires_matching_id_and_is_not_abstention_case(self):
        self.document["responses"][6]["cause_id"] = None
        checks = self.report()["cases"][6]["checks"]
        self.assertFalse(checks["cause"])
        self.assertIsNone(checks["abstention"])

    def test_unknown_case_and_extra_fields_never_echo_received_content(self):
        marker = "FICTITIOUS-PRIVATE-VALUE-DO-NOT-ECHO"
        self.document["responses"].append({"case_id": marker, "note": marker})
        self.document["responses"][0][marker] = marker
        self.document["responses"][0]["numeric_facts"]["p95_ms"] = marker
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertEqual(report["ignored_response_entries"], 1)
        self.assertEqual(report["metrics"]["all_checks"]["total"], 9)
        self.assertNotIn(marker, json.dumps(report))

    def test_duplicate_case_is_not_silently_selected(self):
        self.document["responses"].append(copy.deepcopy(self.document["responses"][0]))
        report = self.report()
        self.assertFalse(report["cases"][0]["all_checks_passed"])
        self.assertEqual(report["metrics"]["all_checks"]["passed"], 8)

    def test_schema_rejects_boolean_version_bad_priority_and_extra_fields(self):
        self.document["format_version"] = True
        with self.assertRaises(evaluate.EvaluationInputError):
            self.report()
        for priority in (False, "P1", {}, {"rule_id": "RULE-SYN-AVAIL", "level": "P1", "extra": 1}):
            self.document = copy.deepcopy(IMPROVED)
            self.document["responses"][1]["priority"] = priority
            self.assertFalse(self.report()["cases"][1]["checks"]["schema"])
        self.document = copy.deepcopy(IMPROVED)
        self.document["responses"][0]["extra"] = "text"
        self.assertFalse(self.report()["cases"][0]["checks"]["schema"])

    def test_invalid_envelope_and_non_object_rows_are_handled(self):
        for document in ([], {}, {"format_version": 1, "source_type": "local_output", "responses": {} }):
            with self.subTest(document=document):
                with self.assertRaises(evaluate.EvaluationInputError):
                    evaluate.evaluate_responses(DATASET, document)
        self.document["responses"][0] = False
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertEqual(report["ignored_response_entries"], 1)

    def test_no_abstention_denominator_reports_null_rate(self):
        dataset = copy.deepcopy(DATASET)
        dataset["cases"] = [dataset["cases"][6]]
        self.document["responses"] = [self.document["responses"][6]]
        report = evaluate.evaluate_responses(dataset, self.document)
        self.assertEqual(report["metrics"]["abstention"], {"passed": 0, "total": 0, "rate": None})
        comparison = evaluate.compare_reports(report, report)
        self.assertIsNone(comparison["deltas"]["abstention"]["percentage_point_difference"])

    def test_comparison_requires_identical_dataset_not_just_same_case_count(self):
        other_dataset = copy.deepcopy(DATASET)
        other_dataset["cases"][0]["title"] = "Outro cenário fictício"
        other = evaluate.evaluate_responses(other_dataset, self.document)
        with self.assertRaises(evaluate.EvaluationInputError):
            evaluate.compare_reports(self.report(), other)

    def test_dataset_rejects_inconsistent_facts_and_unconfirmed_cause(self):
        for field, value in (("numeric_facts", {"p95_ms": 999}), ("cause_id", "CAUSE-SYN-THROTTLE")):
            dataset = copy.deepcopy(DATASET)
            dataset["cases"][0]["expected"][field] = value
            with self.assertRaises(evaluate.EvaluationInputError):
                evaluate.validate_dataset(dataset)

    def test_json_loader_rejects_duplicates_nonfinite_and_does_not_echo_value(self):
        with local_temp_directory() as directory:
            path = Path(directory) / "invalid.json"
            for content in ('{"value": NaN}', '{"value": Infinity}', '{"PRIVATE-MARKER": 1, "PRIVATE-MARKER": 2}', '{"PRIVATE-MARKER": broken}'):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(evaluate.EvaluationInputError) as caught:
                    evaluate.load_json(path, "respostas")
                self.assertNotIn("PRIVATE-MARKER", str(caught.exception))
        with self.assertRaises(evaluate.EvaluationInputError):
            evaluate.load_json(ROOT / "local" / "does-not-exist.json", "respostas")


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "evaluate.py"), *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})

    def test_exit_codes_and_output_file(self):
        common = ["--dataset", "dataset.json"]
        success = self.run_cli(*common, "--responses", "examples/improved.json", "--require-all")
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertTrue(json.loads(success.stdout)["passed"])
        failed_checks = self.run_cli(*common, "--responses", "examples/baseline.json", "--require-all")
        self.assertEqual(failed_checks.returncode, 1)
        malformed = self.run_cli(*common, "--responses", "local/does-not-exist.json")
        self.assertEqual(malformed.returncode, 2)
        self.assertIn("error", json.loads(malformed.stderr))
        with local_temp_directory() as directory:
            output = Path(directory) / "nested" / "report.json"
            saved = self.run_cli(*common, "--responses", "examples/improved.json", "--output", str(output))
            self.assertEqual(saved.returncode, 0, saved.stderr)
            self.assertEqual(saved.stdout, "")
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["passed"])

    def test_comparison_cli_is_reproducible(self):
        result = self.run_cli("--dataset", "dataset.json", "--responses", "examples/baseline.json", "--compare", "examples/improved.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        comparison = json.loads(result.stdout)
        self.assertEqual(comparison["base_report"]["metrics"]["all_checks"]["passed"], 3)
        self.assertEqual(comparison["comparison_report"]["metrics"]["all_checks"]["passed"], 9)


if __name__ == "__main__":
    unittest.main()
