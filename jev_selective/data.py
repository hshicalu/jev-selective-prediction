from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    EXPECTED_DEV_ROWS,
    EXPECTED_TEST_ROWS,
    LABELS,
    PROTOCOL_PATH,
    SOURCE_COMMIT,
    SOURCE_FILE,
    TEST_SOURCE_FILE,
    EvalError,
    sha256_file,
)


def load_dev(path: Path, allow_count_mismatch: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if "test" in path.name.lower() or path.name != "valid-v1.3.json":
        raise EvalError(f"Expected official dev file named {SOURCE_FILE}; test paths are rejected.")
    payload: list[Any] = []
    line_number = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if line.strip():
                    payload.append(json.loads(line))
    except json.JSONDecodeError as exc:
        raise EvalError(f"Invalid JSON Lines data at line {line_number}.") from exc
    if len(payload) != EXPECTED_DEV_ROWS and not allow_count_mismatch:
        raise EvalError(f"Expected {EXPECTED_DEV_ROWS} dev rows, found {len(payload)}.")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise EvalError(f"Row {index} is not an object.")
        key = str(raw.get("sentence_pair_id", ""))
        premise, hypothesis, label = raw.get("sentence1"), raw.get("sentence2"), raw.get("label")
        if not key or key in seen or not isinstance(premise, str) or not isinstance(hypothesis, str):
            raise EvalError(f"Row {index} has missing or duplicate identity/text fields.")
        if label not in LABELS:
            raise EvalError(f"Row {index} has an unexpected label.")
        seen.add(key)
        rows.append({"row_key": key, "premise": premise, "hypothesis": hypothesis, "gold": label})
    return rows, {
        "dataset": "JGLUE JNLI v1.3 dev",
        "source_repository": "https://github.com/yahoojapan/JGLUE",
        "source_commit": SOURCE_COMMIT,
        "source_file": SOURCE_FILE,
        "local_path": str(path.resolve()),
        "sha256": sha256_file(path),
        "row_count": len(rows),
        "retrieved_at": datetime.now(timezone.utc).date().isoformat(),
    }


def load_test(path: Path, expected_sha256: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load only the pinned official test file after the explicit preflight gates."""
    if path.name != "test-v1.3.json":
        raise EvalError(f"Expected official test file named {TEST_SOURCE_FILE}.")
    repo_root = PROTOCOL_PATH.parent.parent
    try:
        path.resolve().relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise EvalError("Keep the benchmark test file outside this repository.")
    if not expected_sha256 or len(expected_sha256) != 64:
        raise EvalError("An expected test SHA-256 recorded before the run is required.")
    actual_sha256 = sha256_file(path)
    if actual_sha256.lower() != expected_sha256.lower():
        raise EvalError("Test dataset checksum does not match the pre-recorded expected checksum.")

    payload: list[Any] = []
    line_number = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if line.strip():
                    payload.append(json.loads(line))
    except json.JSONDecodeError as exc:
        raise EvalError(f"Invalid JSON Lines test data at line {line_number}.") from exc
    if len(payload) != EXPECTED_TEST_ROWS:
        raise EvalError(f"Expected {EXPECTED_TEST_ROWS} test rows, found {len(payload)}.")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise EvalError(f"Test row {index} is not an object.")
        key = str(raw.get("sentence_pair_id", ""))
        premise, hypothesis, label = raw.get("sentence1"), raw.get("sentence2"), raw.get("label")
        if not key or key in seen or not isinstance(premise, str) or not isinstance(hypothesis, str):
            raise EvalError(f"Test row {index} has missing or duplicate identity/text fields.")
        if label not in LABELS:
            raise EvalError(f"Test row {index} has an unexpected label.")
        seen.add(key)
        rows.append({"row_key": key, "premise": premise, "hypothesis": hypothesis, "gold": label})
    return rows, {
        "dataset": "JGLUE JNLI v1.3 test",
        "source_repository": "https://github.com/yahoojapan/JGLUE",
        "source_commit": SOURCE_COMMIT,
        "source_file": TEST_SOURCE_FILE,
        "local_path": str(path.resolve()),
        "sha256": actual_sha256,
        "row_count": len(rows),
        "retrieved_at": datetime.now(timezone.utc).date().isoformat(),
    }
