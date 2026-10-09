import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EnvironmentTemplateTests(unittest.TestCase):
    def test_sample_contains_only_the_supported_api_key_and_no_value(self):
        assignments = [
            line.strip()
            for line in (ROOT / ".env.sample").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertEqual(assignments, ["TYPESAFE_API_KEY="])

    def test_real_env_is_ignored_but_sample_is_trackable(self):
        ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

        self.assertIn(".env", ignore_rules)
        self.assertIn("!.env.sample", ignore_rules)
        self.assertIn(".local/", ignore_rules)


if __name__ == "__main__":
    unittest.main()
