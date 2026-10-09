import argparse
import contextlib
import io
import unittest
from unittest.mock import patch

from jev_selective import api, runner
from jev_selective.cli import DEFAULT_OUTPUT_DIR, parser
from jev_selective.config import EvalError


class RunnerGuardTests(unittest.TestCase):
    def test_dev_defaults_to_ignored_project_local_output_directory(self):
        args = parser().parse_args(["dev", "--dev-file", "valid-v1.3.json", "--max-cost-usd", "1"])

        self.assertEqual(args.out_dir, DEFAULT_OUTPUT_DIR)
        self.assertEqual(DEFAULT_OUTPUT_DIR.parts[-2:], (".local", "jnli-dev"))

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
