import json
import tempfile
import unittest
from pathlib import Path

from jev_selective.config import EvalError
from jev_selective.metrics import (
    calibration_metrics,
    class_metrics,
    load_existing_predictions,
    match_existing_predictions,
    resume_completed,
    evaluate_frozen_thresholds,
    select_thresholds,
)


def prediction(gold="entailment", predicted="entailment", confidence=0.9, valid=True):
    return {
        "valid": valid,
        "gold": gold,
        "prediction": predicted,
        "top_choice_confidence": confidence,
        "probabilities": {"entailment": 0.9, "contradiction": 0.05, "neutral": 0.05},
    }


class SelectiveMetricsTests(unittest.TestCase):
    def test_selects_maximum_coverage_threshold_with_exact_lower_bound(self):
        rows = [prediction() for _ in range(100)]

        result = select_thresholds(rows)

        self.assertEqual(result["0.90"]["accepted_count"], 100)
        self.assertEqual(result["0.95"]["accepted_count"], 100)
        self.assertGreaterEqual(result["0.95"]["one_sided_95pct_cp_lower"], 0.95)
        self.assertEqual(result["0.99"]["status"], "no_qualifying_threshold")

    def test_equal_confidences_are_accepted_as_one_tied_group(self):
        rows = [prediction(confidence=0.8), prediction(gold="neutral", predicted="neutral", confidence=0.8)]

        result = select_thresholds(rows)

        self.assertEqual(result["coverage_accuracy_curve"][0]["accepted_count"], 2)

    def test_invalid_responses_are_excluded_from_accuracy(self):
        rows = [prediction(), prediction(predicted="contradiction", valid=False)]

        result = class_metrics(rows)

        self.assertEqual(result["confusion_matrix"]["entailment"]["entailment"], 1)
        self.assertEqual(sum(result["confusion_matrix"]["entailment"].values()), 1)

    def test_calibration_uses_top_confidence_bins(self):
        rows = [prediction(confidence=1.0)]

        result = calibration_metrics(rows)

        self.assertEqual(result["reliability_bins"][-1]["count"], 1)
        self.assertEqual(result["sample_count"], 1)

    def test_frozen_test_thresholds_are_applied_without_threshold_selection(self):
        rows = [
            prediction(gold="entailment", predicted="entailment", confidence=0.99),
            prediction(gold="neutral", predicted="entailment", confidence=0.80),
            prediction(gold="neutral", predicted="neutral", confidence=0.70),
        ]

        result = evaluate_frozen_thresholds(rows, {"0.90": 0.80, "0.95": 0.95, "0.99": None})

        self.assertEqual(result["0.90"]["threshold"], 0.80)
        self.assertEqual(result["0.90"]["accepted_count"], 2)
        self.assertEqual(result["0.90"]["accepted_accuracy"], 0.5)
        self.assertEqual(result["0.95"]["accepted_count"], 1)
        self.assertEqual(result["0.99"]["status"], "no_qualifying_dev_threshold")


class ResumeTests(unittest.TestCase):
    def test_successful_or_definitive_responses_are_not_repeated(self):
        self.assertTrue(resume_completed({"valid": True}))
        self.assertTrue(resume_completed({"valid": False, "attempts": [{"status": 200}]}))
        self.assertTrue(resume_completed({"valid": False, "attempts": [{"status": 401}]}))

    def test_ambiguous_transport_result_requires_reconciliation(self):
        self.assertFalse(resume_completed({"valid": False, "attempts": [{"status": "transport_error"}]}))

    def test_loads_and_matches_saved_dev_predictions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "predictions.jsonl"
            record = {"row_key": "id-1", "split": "dev", "gold": "neutral", "valid": True}
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            loaded = load_existing_predictions(path)
            matched = match_existing_predictions(loaded, [{"row_key": "id-1", "gold": "neutral"}])

        self.assertIn("id-1", matched)

    def test_loads_and_matches_saved_test_prediction_without_repeating_success(self):
        record = {"row_key": "test-1", "split": "test", "gold": "neutral", "valid": True}
        matched = match_existing_predictions([record], [{"row_key": "test-1", "gold": "neutral"}], split="test")
        self.assertIn("test-1", matched)

    def test_rejects_existing_prediction_from_another_split(self):
        record = {"row_key": "id-1", "split": "test", "gold": "neutral", "valid": True}
        with self.assertRaises(EvalError):
            match_existing_predictions([record], [{"row_key": "id-1", "gold": "neutral"}])


if __name__ == "__main__":
    unittest.main()
