from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api import assert_api_key, call_with_retries, validate_response
from .config import API_URL, CRITERIA, DEV_PREDICTIONS_SHA256, DEV_RUN_CRITERIA_SHA256, DEV_RUN_PROMPT_SHA256, DEV_RUN_PROTOCOL_SHA256, DEV_SOURCE_SHA256, EXPECTED_DEV_ROWS, EXPECTED_TEST_ROWS, FROZEN_DEV_THRESHOLDS, INSTRUCTIONS, LABELS, MODEL, PRICE_USD_PER_MILLION_INPUT_TOKENS, PROTOCOL_PATH, SOURCE_COMMIT, SOURCE_FILE, SMOKE_STATES, EvalError, request_body, serialize_request, sha256_file, utc_now
from .data import load_dev, load_test
from .metrics import load_existing_predictions, match_existing_predictions, resume_completed, write_outputs


def command_smoke(args: argparse.Namespace) -> None:
    if not args.confirm_api_calls:
        raise EvalError("Smoke mode makes billable synthetic requests; pass --confirm-api-calls after reviewing current account pricing.")
    if not 3 <= args.count <= 5:
        raise EvalError("Smoke count must be between 3 and 5.")
    key = assert_api_key()
    states = SMOKE_STATES[: args.count]
    output: list[dict[str, Any]] = []
    errors = 0
    for i, state in enumerate(states, start=1):
        result = call_with_retries(state, key, args.timeout)
        valid, reason, fields = validate_response(result)
        output.append({
            "case": i,
            "status": result["status"],
            "attempts": result["attempts"],
            "valid_schema": valid,
            "invalid_reason": reason,
            "prediction": fields.get("prediction"),
            "probabilities": fields.get("probabilities"),
            "top_choice_confidence": fields.get("top_choice_confidence"),
            "returned_confidence": fields.get("returned_confidence"),
            "model": fields.get("model"),
            "usage": fields.get("usage"),
            "computed_cost_usd": (
                fields["usage"]["input_tokens"] * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000
                if isinstance(fields.get("usage"), dict) and isinstance(fields["usage"].get("input_tokens"), int)
                else None
            ),
        })
        errors += not valid
    print(json.dumps({"api_url": API_URL, "model_requested": MODEL, "results": output, "failed_or_invalid": errors}, ensure_ascii=False, indent=2))
    if errors:
        raise EvalError("One or more synthetic smoke responses failed validation; stop before using JNLI data.")


def command_estimate(args: argparse.Namespace) -> None:
    rows, source = load_dev(args.dev_file, allow_count_mismatch=args.allow_count_mismatch)
    bodies = [serialize_request(f"前提文: {r['premise']}\n仮説文: {r['hypothesis']}") for r in rows]
    dev_tokens = sum(math.ceil(len(body) / 4) for body in bodies)
    test_tokens = math.ceil(dev_tokens / len(rows) * 2508) if rows else 0
    smoke_tokens = sum(
        math.ceil(len(serialize_request(state)) / 4)
        for state in SMOKE_STATES
    )
    total_tokens = dev_tokens + test_tokens + smoke_tokens
    usage = {
        "source": source,
        "pricing_source": "https://typesafe.ai/blog/introducing-system-one-models-and-jev",
        "input_price_usd_per_million_tokens": PRICE_USD_PER_MILLION_INPUT_TOKENS,
        "token_estimate_method": "ceil(UTF-8 serialized request bytes / 4); planning heuristic to calibrate against smoke usage",
        "dev": {"successful_example_requests": len(rows), "estimated_input_tokens": dev_tokens, "estimated_cost_usd": dev_tokens * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000},
        "test_estimate_from_dev_mean": {"successful_example_requests": 2508, "estimated_input_tokens": test_tokens, "estimated_cost_usd": test_tokens * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000},
        "five_synthetic_smoke_requests": {"successful_example_requests": 5, "estimated_input_tokens": smoke_tokens, "estimated_cost_usd": smoke_tokens * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000},
        "both_splits_plus_5_smoke_requests": {"successful_example_requests": len(rows) + 2508 + 5, "estimated_input_tokens": total_tokens, "estimated_cost_usd": total_tokens * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000},
        "retry_reserve": {"max_attempts_per_example": 3, "dev_worst_case_estimated_cost_usd": dev_tokens * 3 * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000, "all_requests_worst_case_estimated_cost_usd": total_tokens * 3 * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000},
        "account_balance_note": "Confirm current account price and balance; estimate-only makes no API requests.",
    }
    print(json.dumps(usage, ensure_ascii=False, indent=2))


def command_dev(args: argparse.Namespace) -> None:
    if not args.confirm_dev_run:
        raise EvalError("Dev mode sends 2,434 benchmark examples to the external API; pass --confirm-dev-run only after recording the cost estimate in Issue #5.")
    if not args.estimate_recorded:
        raise EvalError("Record the request/cost estimate in Issue #5 and pass --estimate-recorded before starting.")
    rows, source = load_dev(args.dev_file, allow_count_mismatch=False)
    request_bytes = [len(json.dumps(request_body(f"前提文: {r['premise']}\n仮説文: {r['hypothesis']}"), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for r in rows]
    estimated_tokens = sum(math.ceil(size / 4) for size in request_bytes)
    worst_case_cost = estimated_tokens * 3 * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000
    if not math.isfinite(args.max_cost_usd) or args.max_cost_usd < 0 or worst_case_cost > args.max_cost_usd:
        raise EvalError(f"Conservative retry-reserved estimate (${worst_case_cost:.6f}) exceeds --max-cost-usd.")
    try:
        import scipy.stats  # noqa: F401
    except ImportError as exc:
        raise EvalError("Threshold analysis requires SciPy; run `uv sync --locked` before making API requests.") from exc
    key = assert_api_key()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = out_dir / "predictions.jsonl"
    records = load_existing_predictions(prediction_path)
    existing = match_existing_predictions(records, rows)
    existing_keys = set(existing)
    pending_rows = [row for row in rows if row["row_key"] not in existing_keys]
    manifest: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "protocol": "docs/jnli-selective-protocol.md",
        "protocol_sha256": sha256_file(PROTOCOL_PATH),
        "split": "dev",
        "source": source,
        "model_requested": MODEL,
        "api_url": API_URL,
        "input_price_usd_per_million_tokens": PRICE_USD_PER_MILLION_INPUT_TOKENS,
        "request_started_at": utc_now(),
        "request_started_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "local_timezone": time.tzname,
        "python_version": sys.version,
        "prompt_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
        "criteria_sha256": hashlib.sha256(json.dumps(CRITERIA, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        "cost_estimate_issue_recorded": True,
        "max_cost_usd": args.max_cost_usd,
        "estimated_input_tokens": estimated_tokens,
        "retry_reserved_cost_usd": worst_case_cost,
        "run_status": "running",
    }
    manifest["resumed_examples"] = sum(resume_completed(record) for record in records)
    total_input_tokens = sum(
        record.get("usage", {}).get("input_tokens", 0)
        for record in records if isinstance(record.get("usage"), dict) and isinstance(record["usage"].get("input_tokens"), int)
    )
    total_output_tokens = sum(
        record.get("usage", {}).get("output_tokens", 0)
        for record in records if isinstance(record.get("usage"), dict) and isinstance(record["usage"].get("output_tokens"), int)
    )
    write_outputs(out_dir, records, manifest, finalize=False)
    with prediction_path.open("a", encoding="utf-8") as prediction_file:
        for i, row in enumerate(pending_rows, start=1):
            state = f"前提文: {row['premise']}\n仮説文: {row['hypothesis']}"
            request_hash = hashlib.sha256(serialize_request(state)).hexdigest()
            started_at = utc_now()
            result = call_with_retries(state, key, args.timeout)
            valid, reason, fields = validate_response(result)
            record = {
                "split": "dev",
                "row_key": row["row_key"],
                "gold": row["gold"],
                "request_status": result["status"],
                "request_started_at": started_at,
                "request_sha256": request_hash,
                "attempts": result["attempts"],
                "valid": valid,
                "invalid_reason": reason,
                "request_timestamp": utc_now(),
                **fields,
            }
            input_tokens = fields.get("usage", {}).get("input_tokens") if isinstance(fields.get("usage"), dict) else None
            record["computed_cost_usd"] = (
                input_tokens * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000
                if isinstance(input_tokens, int) and input_tokens >= 0 else None
            )
            if isinstance(input_tokens, int) and input_tokens >= 0:
                total_input_tokens += input_tokens
            output_tokens = fields.get("usage", {}).get("output_tokens") if isinstance(fields.get("usage"), dict) else None
            if isinstance(output_tokens, int) and output_tokens >= 0:
                total_output_tokens += output_tokens
            manifest["observed_usage"] = {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "estimated_input_cost_usd": total_input_tokens * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000,
            }
            records.append(record)
            prediction_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            prediction_file.flush()
            write_outputs(out_dir, records, {**manifest, "completed_examples": len(records), "updated_at": utc_now()}, finalize=False)
            print(f"Completed {len(records)}/{len(rows)} dev examples", file=sys.stderr)
            status = result["status"]
            if isinstance(status, int) and 400 <= status < 500 and status not in {408, 429}:
                write_outputs(out_dir, records, {**manifest, "stopped_after_client_error": status, "updated_at": utc_now()}, finalize=False)
                raise EvalError(f"Stopped after non-retryable HTTP {status}; partial results are saved.")
    if len(records) != len(rows):
        raise EvalError(f"Dev run stopped with {len(records)}/{len(rows)} responses persisted.")
    manifest["request_finished_at"] = utc_now()
    manifest["run_status"] = "completed"
    write_outputs(out_dir, records, manifest)


def _require_sha256(value: str, option: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise EvalError(f"{option} must be a 64-character SHA-256 value.")


def _current_git_commit() -> str:
    repo_root = PROTOCOL_PATH.parent.parent
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        if status.strip():
            raise EvalError("Test execution requires a clean Git worktree at the reviewed code revision.")
    except EvalError:
        raise
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvalError("Test execution requires a clean Git worktree at the reviewed code revision.") from exc
    return commit


def command_test(args: argparse.Namespace) -> None:
    """Run the disclosed exploratory test split after all preflight checks pass."""
    if not args.estimate_recorded or not args.cost_estimate_reference:
        raise EvalError("Record the final test request/cost estimate in the issue and provide --cost-estimate-reference.")
    if not args.second_reviewer or not args.test_run_reference:
        raise EvalError("A second-reviewer preflight confirmation and test-run issue reference are required.")
    if not args.confirm_test_run:
        raise EvalError("Test mode sends the 2,508 exploratory examples; pass --confirm-test-run after review.")
    _require_sha256(args.expected_test_sha256, "--expected-test-sha256")
    _require_sha256(args.expected_dev_manifest_sha256, "--expected-dev-manifest-sha256")
    _require_sha256(args.expected_protocol_sha256, "--expected-protocol-sha256")
    if not args.expected_code_commit:
        raise EvalError("Pin the reviewed code commit with --expected-code-commit.")

    protocol_sha256 = sha256_file(PROTOCOL_PATH)
    if protocol_sha256.lower() != args.expected_protocol_sha256.lower():
        raise EvalError("Protocol checksum does not match the reviewed, pre-recorded checksum.")
    code_commit = _current_git_commit()
    if code_commit != args.expected_code_commit:
        raise EvalError("Current clean Git commit does not match the reviewed code commit.")

    dev_manifest_path = args.dev_dir / "manifest.json"
    dev_predictions_path = args.dev_dir / "predictions.jsonl"
    dev_metrics_path = args.dev_dir / "metrics.json"
    if not all(path.is_file() for path in (dev_manifest_path, dev_predictions_path, dev_metrics_path)):
        raise EvalError("Completed dev manifest, predictions, and metrics are required before the test run.")
    if sha256_file(dev_manifest_path).lower() != args.expected_dev_manifest_sha256.lower():
        raise EvalError("Dev manifest checksum differs from the checksum reviewed and recorded before test.")
    if sha256_file(dev_predictions_path) != DEV_PREDICTIONS_SHA256:
        raise EvalError("Dev prediction checksum differs from the complete, reviewed dev run.")
    try:
        dev_manifest = json.loads(dev_manifest_path.read_text(encoding="utf-8"))
        dev_metrics = json.loads(dev_metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError("Cannot read the pinned dev artifacts safely.") from exc
    if dev_manifest.get("split") != "dev" or dev_manifest.get("run_status") != "completed":
        raise EvalError("The pinned dev run must be complete and identify the dev split.")
    dev_source = dev_manifest.get("source", {})
    dev_summary = dev_manifest.get("summary", {})
    if (
        dev_source.get("dataset") != "JGLUE JNLI v1.3 dev"
        or dev_source.get("source_commit") != SOURCE_COMMIT
        or dev_source.get("source_file") != SOURCE_FILE
        or dev_source.get("row_count") != EXPECTED_DEV_ROWS
        or dev_source.get("sha256") != DEV_SOURCE_SHA256
        or dev_manifest.get("protocol_sha256") != DEV_RUN_PROTOCOL_SHA256
        or dev_manifest.get("prompt_sha256") != DEV_RUN_PROMPT_SHA256
        or dev_manifest.get("criteria_sha256") != DEV_RUN_CRITERIA_SHA256
        or dev_manifest.get("model_requested") != MODEL
        or dev_manifest.get("api_url") != API_URL
    ):
        raise EvalError("The dev manifest does not identify the complete official JGLUE v1.3 dev split.")
    if dev_summary.get("valid_responses") != EXPECTED_DEV_ROWS or dev_summary.get("failures_or_invalid") != 0:
        raise EvalError("The pinned dev run must have a valid response for every official dev example.")
    actual_thresholds = {
        key: dev_metrics.get(key, {}).get("threshold")
        for key in FROZEN_DEV_THRESHOLDS
    }
    if actual_thresholds != FROZEN_DEV_THRESHOLDS:
        raise EvalError("Dev metrics do not contain the frozen thresholds selected for this test protocol.")
    usage = dev_manifest.get("observed_usage", {})
    valid_responses = dev_manifest.get("summary", {}).get("valid_responses", 0)
    input_tokens = usage.get("input_tokens")
    if not isinstance(input_tokens, int) or not isinstance(valid_responses, int) or valid_responses <= 0:
        raise EvalError("Dev token usage is missing; cannot compute the test retry reserve.")
    estimated_test_tokens = math.ceil(input_tokens / valid_responses * EXPECTED_TEST_ROWS)
    retry_reserved_cost = estimated_test_tokens * 3 * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000
    if not math.isfinite(args.max_cost_usd) or args.max_cost_usd < retry_reserved_cost:
        raise EvalError(
            f"Three-attempt test reserve (${retry_reserved_cost:.6f}) exceeds --max-cost-usd."
        )

    repo_root = PROTOCOL_PATH.parent.parent
    test_file = args.test_file.resolve()
    try:
        test_file.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise EvalError("Keep the benchmark test file outside the repository.")

    out_dir = args.out_dir
    prediction_path = out_dir / "predictions.jsonl"
    manifest_path = out_dir / "manifest.json"
    inflight_path = out_dir / "inflight.json"
    existing_manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvalError("Existing test manifest is unreadable; refusing to risk duplicate test requests.") from exc
        if (
            existing_manifest.get("split") != "test"
            or existing_manifest.get("source", {}).get("sha256") != args.expected_test_sha256.lower()
            or existing_manifest.get("dev_manifest_sha256") != args.expected_dev_manifest_sha256
            or existing_manifest.get("protocol_sha256") != protocol_sha256
            or existing_manifest.get("code_commit") != code_commit
        ):
            raise EvalError("Existing test output belongs to different frozen inputs; refusing to reuse it.")
        if existing_manifest.get("run_status") == "completed":
            raise EvalError("This test evaluation is already complete; successful test requests cannot be repeated.")
    elif prediction_path.exists():
        raise EvalError("Test predictions exist without a manifest; refusing to risk duplicate test requests.")
    if inflight_path.exists():
        raise EvalError("An earlier test request may have been interrupted mid-flight; reconcile its status before resuming.")

    rows, source = load_test(test_file, args.expected_test_sha256)
    records = load_existing_predictions(prediction_path)
    existing = match_existing_predictions(records, rows, split="test")
    pending_rows = [row for row in rows if row["row_key"] not in existing]

    key = assert_api_key()
    manifest: dict[str, Any] = existing_manifest or {
        "run_id": str(uuid.uuid4()),
        "protocol": "docs/jnli-selective-protocol.md",
        "protocol_sha256": protocol_sha256,
        "split": "test",
        "source": source,
        "model_requested": MODEL,
        "api_url": API_URL,
        "input_price_usd_per_million_tokens": PRICE_USD_PER_MILLION_INPUT_TOKENS,
        "request_started_at": utc_now(),
        "request_started_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "local_timezone": time.tzname,
        "python_version": sys.version,
        "prompt_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
        "criteria_sha256": hashlib.sha256(json.dumps(CRITERIA, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        "run_status": "running",
        "estimated_input_tokens_from_dev_mean": estimated_test_tokens,
        "retry_reserved_cost_usd": retry_reserved_cost,
        "max_cost_usd": args.max_cost_usd,
        "cost_estimate_reference": args.cost_estimate_reference,
        "test_run_reference": args.test_run_reference,
        "second_reviewer": args.second_reviewer,
        "dev_manifest_sha256": args.expected_dev_manifest_sha256,
        "dev_run_id": dev_manifest.get("run_id"),
        "code_commit": code_commit,
        "frozen_dev_thresholds": FROZEN_DEV_THRESHOLDS,
    }
    total_input_tokens = sum(
        record.get("usage", {}).get("input_tokens", 0)
        for record in records if isinstance(record.get("usage"), dict) and isinstance(record["usage"].get("input_tokens"), int)
    )
    total_output_tokens = sum(
        record.get("usage", {}).get("output_tokens", 0)
        for record in records if isinstance(record.get("usage"), dict) and isinstance(record["usage"].get("output_tokens"), int)
    )
    write_outputs(out_dir, records, manifest, finalize=False)
    with prediction_path.open("a", encoding="utf-8") as prediction_file:
        for row in pending_rows:
            state = f"前提文: {row['premise']}\n仮説文: {row['hypothesis']}"
            request_hash = hashlib.sha256(serialize_request(state)).hexdigest()
            started_at = utc_now()
            inflight_path.write_text(json.dumps({
                "split": "test",
                "row_key": row["row_key"],
                "request_sha256": request_hash,
                "request_started_at": started_at,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = call_with_retries(state, key, args.timeout)
            valid, reason, fields = validate_response(result)
            record = {
                "split": "test",
                "row_key": row["row_key"],
                "gold": row["gold"],
                "request_status": result["status"],
                "request_started_at": started_at,
                "request_sha256": request_hash,
                "attempts": result["attempts"],
                "valid": valid,
                "invalid_reason": reason,
                "request_timestamp": utc_now(),
                **fields,
            }
            input_used = fields.get("usage", {}).get("input_tokens") if isinstance(fields.get("usage"), dict) else None
            record["computed_cost_usd"] = (
                input_used * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000
                if isinstance(input_used, int) and input_used >= 0 else None
            )
            if isinstance(input_used, int) and input_used >= 0:
                total_input_tokens += input_used
            output_used = fields.get("usage", {}).get("output_tokens") if isinstance(fields.get("usage"), dict) else None
            if isinstance(output_used, int) and output_used >= 0:
                total_output_tokens += output_used
            records.append(record)
            prediction_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            prediction_file.flush()
            inflight_path.unlink()
            manifest["observed_usage"] = {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "estimated_input_cost_usd": total_input_tokens * PRICE_USD_PER_MILLION_INPUT_TOKENS / 1_000_000,
            }
            manifest["completed_examples"] = len(records)
            manifest["updated_at"] = utc_now()
            write_outputs(out_dir, records, manifest, finalize=False)
            print(f"Completed {len(records)}/{len(rows)} test examples", file=sys.stderr)
    if len(records) != len(rows):
        raise EvalError(f"Test run stopped with {len(records)}/{len(rows)} responses persisted.")
    manifest["request_finished_at"] = utc_now()
    manifest["run_status"] = "completed"
    write_outputs(out_dir, records, manifest, thresholds=FROZEN_DEV_THRESHOLDS)
