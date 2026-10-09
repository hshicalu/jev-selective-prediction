import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from jev_selective import api


def response_result(**answer_overrides):
    answer = {
        "type": "choice",
        "choice": "entailment",
        "probabilities": {"entailment": 0.8, "contradiction": 0.1, "neutral": 0.1},
        "confidence": 0.72,
    }
    answer.update(answer_overrides)
    return {
        "status": 200,
        "response": {
            "model": "jev-test-version",
            "answers": {"relation": answer},
            "usage": {"input_tokens": 12, "output_tokens": 3},
        },
        "attempts": [{"attempt": 1, "status": 200, "latency_ms": 10.0}],
    }


class ValidateResponseTests(unittest.TestCase):
    def test_valid_choice_keeps_vendor_confidence_separate(self):
        valid, reason, fields = api.validate_response(response_result())

        self.assertTrue(valid)
        self.assertIsNone(reason)
        self.assertEqual(fields["top_choice_confidence"], 0.8)
        self.assertEqual(fields["returned_confidence"], 0.72)
        self.assertEqual(fields["probabilities"]["neutral"], 0.1)

    def test_rejects_probability_sum_outside_tolerance(self):
        result = response_result(probabilities={"entailment": 0.8, "contradiction": 0.1, "neutral": 0.2})

        valid, reason, _ = api.validate_response(result)

        self.assertFalse(valid)
        self.assertEqual(reason, "probabilities_do_not_sum_to_one")

    def test_rejects_non_maximum_choice(self):
        result = response_result(choice="neutral")

        valid, reason, _ = api.validate_response(result)

        self.assertFalse(valid)
        self.assertEqual(reason, "choice_is_not_a_maximum_probability_option")

    def test_reads_only_the_documented_environment_variable(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-secret"}, clear=True):
            self.assertEqual(api.assert_api_key(), "test-secret")
        with patch.dict(os.environ, {"JEV_API_KEY": "legacy-secret"}, clear=True):
            with self.assertRaises(api.EvalError):
                api.assert_api_key()

    def test_retries_transient_status_then_succeeds(self):
        first = (429, {"error": "rate limited"}, 2.0)
        second = (200, {"ok": True}, 3.0)
        with patch.object(api, "request_once", side_effect=[first, second]) as request, patch.object(api.time, "sleep"):
            result = api.call_with_retries("synthetic", "secret", 1)

        self.assertEqual(request.call_count, 2)
        self.assertEqual([item["status"] for item in result["attempts"]], [429, 200])
        self.assertEqual(result["response"], {"ok": True})

    def test_stops_after_three_ambiguous_transport_failures(self):
        error = urllib.error.URLError("offline")
        with patch.object(api, "request_once", side_effect=error) as request, patch.object(api.time, "sleep"):
            result = api.call_with_retries("synthetic", "secret", 1)

        self.assertEqual(request.call_count, 3)
        self.assertEqual(result["status"], "transport_error")
        self.assertEqual(len(result["attempts"]), 3)


if __name__ == "__main__":
    unittest.main()
