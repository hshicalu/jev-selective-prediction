import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jev_selective import api, runner
from jev_selective.cli import DEFAULT_OUTPUT_DIR, parser
from jev_selective.config import EvalError


class RunnerGuardTests(unittest.TestCase):
    def test_dev_defaults_to_ignored_project_local_output_directory(self):
        args = parser().parse_args(["dev", "--dev-file", "valid-v1.3.json", "--max-cost-usd", "1"])

        self.assertEqual(args.out_dir, DEFAULT_OUTPUT_DIR)
        self.assertEqual(DEFAULT_OUTPUT_DIR.parts[-2:], (".local", "jnli-dev"))

    def test_test_defaults_to_a_separate_ignored_output_directory(self):
        args = parser().parse_args([
            "test", "--test-file", "test-v1.3.json", "--expected-test-sha256", "a" * 64,
            "--expected-dev-manifest-sha256", "b" * 64, "--expected-protocol-sha256", "c" * 64,
            "--expected-code-commit", "deadbeef", "--max-cost-usd", "0.2",
            "--cost-estimate-reference", "issue comment", "--test-run-reference", "issue comment",
            "--second-reviewer", "reviewer",
        ])

        self.assertEqual(args.out_dir.parts[-2:], (".local", "jnli-test"))

    def test_test_requires_recorded_preflight_before_reading_test_or_key(self):
        args = argparse.Namespace(
            estimate_recorded=False,
            cost_estimate_reference="",
            second_reviewer="",
            test_run_reference="",
            confirm_test_run=False,
            expected_test_sha256="unused",
            expected_dev_manifest_sha256="unused",
            expected_protocol_sha256="unused",
            expected_code_commit="unused",
            dev_dir="unused",
            test_file="unused",
            out_dir="unused",
            max_cost_usd=1,
            timeout=1,
        )
        with patch.object(runner, "load_test") as load_data, patch.object(runner, "assert_api_key") as get_key:
            with self.assertRaisesRegex(EvalError, "cost estimate"):
                runner.command_test(args)
        load_data.assert_not_called()
        get_key.assert_not_called()

    def test_test_requires_second_reviewer_and_explicit_run_confirmation(self):
        args = argparse.Namespace(
            estimate_recorded=True,
            cost_estimate_reference="issue comment",
            second_reviewer="",
            test_run_reference="issue comment",
            confirm_test_run=False,
            expected_test_sha256="unused",
            expected_dev_manifest_sha256="unused",
            expected_protocol_sha256="unused",
            expected_code_commit="unused",
            dev_dir="unused",
            test_file="unused",
            out_dir="unused",
            max_cost_usd=1,
            timeout=1,
        )
        with patch.object(runner, "load_test") as load_data, patch.object(runner, "assert_api_key") as get_key:
            with self.assertRaisesRegex(EvalError, "second-reviewer"):
                runner.command_test(args)
        load_data.assert_not_called()
        get_key.assert_not_called()

    def test_locked_test_uses_dev_thresholds_and_completed_run_cannot_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dev_dir = root / "dev"
            dev_dir.mkdir()
            dev_manifest = {
                "split": "dev",
                "run_status": "completed",
                "source": {
                    "dataset": "JGLUE JNLI v1.3 dev",
                    "source_commit": "6f071c09316baae89c3d083a90985b4b1cb9968c",
                    "source_file": "datasets/jnli-v1.3/valid-v1.3.json",
                    "row_count": 2434,
                    "sha256": "ca0353efc7c2eebfb6de4e13f16295053c8b1ee65e7b0849190c90426fbc495f",
                },
                "protocol_sha256": "9453c85d8889b089a47205cb02b24b23fbb3c4dac91dc5c4974772cd6af871e3",
                "prompt_sha256": "3cde5097153d5df7e1caa865e5d57cfe65acbac6dfeef78745e885d9c29163e0",
                "criteria_sha256": "b154cebc489c3d5ad013e276ae7a206ef64036b94cf77a185a4a7b6f8c50d350",
                "model_requested": "jev-latest",
                "api_url": "https://api.typesafe.ai/v1/systemone",
                "summary": {"valid_responses": 2434, "failures_or_invalid": 0},
                "observed_usage": {"input_tokens": 243400, "output_tokens": 10000},
                "run_id": "dev-run",
            }
            (dev_dir / "manifest.json").write_text(json.dumps(dev_manifest), encoding="utf-8")
            (dev_dir / "predictions.jsonl").write_text("", encoding="utf-8")
            dev_predictions_hash = runner.sha256_file(dev_dir / "predictions.jsonl")
            (dev_dir / "metrics.json").write_text(json.dumps({
                "0.90": {"threshold": 0.69},
                "0.95": {"threshold": 0.92},
                "0.99": {"status": "no_qualifying_threshold", "threshold": None},
            }), encoding="utf-8")
            args = argparse.Namespace(
                estimate_recorded=True,
                cost_estimate_reference="https://github.com/hshicalu/jev-selective-prediction/issues/11#issuecomment-1",
                second_reviewer="reviewer",
                test_run_reference="https://github.com/hshicalu/jev-selective-prediction/issues/11#issuecomment-1",
                confirm_test_run=True,
                expected_test_sha256="a" * 64,
                expected_dev_manifest_sha256=runner.sha256_file(dev_dir / "manifest.json"),
                expected_protocol_sha256=runner.sha256_file(runner.PROTOCOL_PATH),
                expected_code_commit="c" * 40,
                dev_dir=dev_dir,
                test_file=root / "test-v1.3.json",
                out_dir=root / "test-out",
                max_cost_usd=0.01,
                timeout=1,
            )
            rows = [{"row_key": "test-1", "gold": "neutral", "premise": "p", "hypothesis": "h"}]
            source = {
                "dataset": "JGLUE JNLI v1.3 test",
                "source_commit": "6f071c09316baae89c3d083a90985b4b1cb9968c",
                "source_file": "datasets/jnli-v1.3/test-v1.3.json",
                "sha256": args.expected_test_sha256,
                "row_count": 1,
            }
            response = {"status": 200, "response": {}, "attempts": [{"attempt": 1, "status": 200, "latency_ms": 10}]}
            fields = {
                "prediction": "neutral",
                "probabilities": {"entailment": 0.01, "contradiction": 0.01, "neutral": 0.98},
                "top_choice_confidence": 0.98,
                "returned_confidence": 0.96,
                "model": "jev-1.13.0",
                "usage": {"input_tokens": 100, "output_tokens": 10},
                "raw_response": {},
            }
            with patch.object(runner, "EXPECTED_TEST_ROWS", 1), \
                    patch.object(runner, "DEV_PREDICTIONS_SHA256", dev_predictions_hash), \
                    patch.object(runner, "_current_git_commit", return_value=args.expected_code_commit), \
                    patch.object(runner, "load_test", return_value=(rows, source)) as load_data, \
                    patch.object(runner, "assert_api_key", return_value="dummy-key") as get_key, \
                    patch.object(runner, "call_with_retries", return_value=response), \
                    patch.object(runner, "validate_response", return_value=(True, None, fields)):
                runner.command_test(args)

            load_data.assert_called_once()
            get_key.assert_called_once()
            saved = json.loads((args.out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["thresholds"]["0.90"]["threshold"], 0.69)
            self.assertEqual(saved["thresholds"]["0.90"]["accepted_count"], 1)

            with patch.object(runner, "EXPECTED_TEST_ROWS", 1), \
                    patch.object(runner, "DEV_PREDICTIONS_SHA256", dev_predictions_hash), \
                    patch.object(runner, "_current_git_commit", return_value=args.expected_code_commit), \
                    patch.object(runner, "load_test") as load_data, \
                    patch.object(runner, "assert_api_key") as get_key:
                with self.assertRaisesRegex(EvalError, "already complete"):
                    runner.command_test(args)
            load_data.assert_not_called()
            get_key.assert_not_called()

    def test_smoke_requires_explicit_confirmation_before_reading_key(self):
        args = argparse.Namespace(confirm_api_calls=False, count=3, timeout=1)
        with patch.object(runner, "assert_api_key") as get_key:
            with self.assertRaises(EvalError):
                runner.command_smoke(args)
        get_key.assert_not_called()

    def test_smoke_cli_does_not_accept_a_dataset_path(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            parser().parse_args(["smoke", "--dev-file", "/tmp/benchmark.json"])

    def test_dev_requires_estimate_recorded_before_loading_data_or_key(self):
        args = argparse.Namespace(
            confirm_dev_run=True,
            estimate_recorded=False,
            dev_file="unused",
            max_cost_usd=1.0,
            out_dir="unused",
            timeout=1,
        )
        with patch.object(runner, "load_dev") as load_data, patch.object(runner, "assert_api_key") as get_key:
            with self.assertRaisesRegex(EvalError, "estimate"):
                runner.command_dev(args)
        load_data.assert_not_called()
        get_key.assert_not_called()

    def test_legacy_key_name_is_not_accepted(self):
        with patch.dict("os.environ", {"JEV_API_KEY": "legacy"}, clear=True):
            with self.assertRaises(api.EvalError):
                api.assert_api_key()


if __name__ == "__main__":
    unittest.main()
