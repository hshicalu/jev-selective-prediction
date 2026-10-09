import json
import tempfile
import unittest
from pathlib import Path

from jev_selective.config import EvalError
from jev_selective.data import load_dev


def write_rows(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class LoadDevTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_loads_jsonl_and_records_checksum(self):
        path = self.root / "valid-v1.3.json"
        write_rows(path, [{
            "sentence_pair_id": "pair-1",
            "sentence1": "前提",
            "sentence2": "仮説",
            "label": "neutral",
        }])

        rows, source = load_dev(path, allow_count_mismatch=True)

        self.assertEqual(rows, [{"row_key": "pair-1", "premise": "前提", "hypothesis": "仮説", "gold": "neutral"}])
        self.assertEqual(source["row_count"], 1)
        self.assertEqual(len(source["sha256"]), 64)

    def test_rejects_test_named_path(self):
        path = self.root / "test-v1.3.json"
        write_rows(path, [])

        with self.assertRaises(EvalError):
            load_dev(path, allow_count_mismatch=True)

    def test_rejects_duplicate_ids_and_unknown_labels(self):
        path = self.root / "valid-v1.3.json"
        row = {"sentence_pair_id": "same", "sentence1": "p", "sentence2": "h", "label": "neutral"}
        write_rows(path, [row, row])
        with self.assertRaisesRegex(EvalError, "duplicate"):
            load_dev(path, allow_count_mismatch=True)

        row["sentence_pair_id"] = "different"
        row["label"] = "unknown"
        write_rows(path, [row])
        with self.assertRaisesRegex(EvalError, "label"):
            load_dev(path, allow_count_mismatch=True)

    def test_requires_official_dev_row_count_unless_estimate_override_is_set(self):
        path = self.root / "valid-v1.3.json"
        write_rows(path, [])

        with self.assertRaisesRegex(EvalError, "Expected 2434"):
            load_dev(path, allow_count_mismatch=False)


if __name__ == "__main__":
    unittest.main()
