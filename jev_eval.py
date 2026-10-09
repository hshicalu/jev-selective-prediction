#!/usr/bin/env python3
"""Guarded Jev/JNLI dev runner; SciPy is required only for exact threshold intervals."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
SOURCE_COMMIT = "6f071c09316baae89c3d083a90985b4b1cb9968c"
SOURCE_FILE = "datasets/jnli-v1.3/valid-v1.3.json"
PROTOCOL_PATH = Path(__file__).resolve().parent / "docs" / "jnli-selective-protocol.md"
EXPECTED_DEV_ROWS = 2434
PRICE_USD_PER_MILLION_INPUT_TOKENS = 0.042
LABELS = ("entailment", "contradiction", "neutral")
INSTRUCTIONS = (
    "前提文と仮説文の意味関係を判定してください。前提文が真であるとき、"
    "仮説文が必ず真なら entailment、仮説文と両立しないなら contradiction、"
    "どちらとも言えないなら neutral を選んでください。文に明示されていない情報を補わず、"
    "最も適切な関係を1つ選んでください。"
)
CRITERIA = {
    "entailment": "前提文が真なら、仮説文も必ず真である",
    "contradiction": "前提文が真なら、仮説文は成り立たない",
    "neutral": "前提文だけでは、仮説文が真か偽か決まらない",
}
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504, 529}
SMOKE_STATES = (
    "前提文: 猫が動物である。\n仮説文: 猫は動物である。",
    "前提文: 雨が降っている。\n仮説文: 地面が乾いている。",
    "前提文: 人が公園を歩いている。\n仮説文: その人は音楽を聞いている。",
    "前提文: 車が赤い。\n仮説文: 車の色は青い。",
    "前提文: 店が開いている。\n仮説文: 店が営業中である。",
)


class EvalError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_body(state: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "state": state,
        "questions": {
            "relation": {
                "type": "choice",
                "instructions": INSTRUCTIONS,
                "criteria": CRITERIA,
            }
        },
    }


def serialize_request(state: str) -> bytes:
    return json.dumps(request_body(state), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def request_once(state: str, key: str, timeout: float) -> tuple[int, dict[str, Any], float]:
    body = serialize_request(state)
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.status
            data = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        data = error.read()
    latency_ms = (time.monotonic() - started) * 1000
    try:
        parsed = json.loads(data.decode("utf-8")) if data else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {"_unparsed_body": data.decode("utf-8", errors="replace")[:2000]}
    return status, parsed, latency_ms


def call_with_retries(state: str, key: str, timeout: float) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, 4):
        attempt_started = time.monotonic()
        try:
            status, response, latency_ms = request_once(state, key, timeout)
            attempts.append({"attempt": attempt, "status": status, "latency_ms": round(latency_ms, 2)})
            if status == 200:
                return {"status": status, "response": response, "attempts": attempts}
            retryable = status in TRANSIENT_HTTP
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # Exception messages can contain URLs or environment-dependent details; do not persist them.
            attempts.append({"attempt": attempt, "status": "transport_error", "latency_ms": round((time.monotonic() - attempt_started) * 1000, 2)})
            retryable = True
        if not retryable or attempt == 3:
            break
        time.sleep(min(30.0, (2 ** (attempt - 1)) + random.random()))
    return {"status": attempts[-1]["status"], "response": {}, "attempts": attempts}


def validate_response(result: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any]]:
    if result["status"] != 200:
        return False, "http_or_transport_failure", {}
    response = result["response"]
    fields: dict[str, Any] = {
        "model": response.get("model") if isinstance(response, dict) else None,
        "usage": response.get("usage", {}) if isinstance(response, dict) else {},
        "raw_response": response,
    }
    try:
        answer = response["answers"]["relation"]
        prediction = answer["choice"]
        probs = answer["probabilities"]
        returned_confidence = float(answer["confidence"])
        model = response["model"]
        usage = response["usage"]
    except (KeyError, TypeError, ValueError):
        return False, "missing_or_malformed_fields", fields
    if prediction not in LABELS or not isinstance(probs, dict) or set(probs) != set(LABELS):
        return False, "unexpected_labels", fields
    if answer.get("type") != "choice" or not isinstance(model, str):
        return False, "unexpected_answer_type_or_model", fields
    if not isinstance(usage, dict) or any(not isinstance(usage.get(key), int) or usage[key] < 0 for key in ("input_tokens", "output_tokens")):
        return False, "invalid_usage", fields
    fields["usage"] = usage
    try:
        probabilities = {label: float(probs[label]) for label in LABELS}
    except (TypeError, ValueError):
        return False, "non_numeric_probability", fields
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in probabilities.values()):
        return False, "probability_out_of_range", fields
    top_probability = max(probabilities.values())
    if abs(sum(probabilities.values()) - 1.0) > 0.01:
        return False, "probabilities_do_not_sum_to_one", fields
    if not math.isfinite(returned_confidence) or not 0 <= returned_confidence <= 1:
        return False, "confidence_out_of_range", fields
    if probabilities[prediction] < top_probability - 1e-12:
        return False, "choice_is_not_a_maximum_probability_option", fields
    return True, None, {
        "prediction": prediction,
        "probabilities": probabilities,
        "top_choice_confidence": top_probability,
        "returned_confidence": returned_confidence,
        "model": model,
        "usage": response.get("usage", {}),
        "raw_response": response,
    }


def load_dev(path: Path, allow_count_mismatch: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if "test" in path.name.lower() or path.name != "valid-v1.3.json":
        raise EvalError(f"Expected official dev file named {SOURCE_FILE}; test paths are rejected.")
    payload: list[Any] = []
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


def class_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r.get("valid")]
    result: dict[str, Any] = {"confusion_matrix": {gold: {pred: 0 for pred in LABELS} for gold in LABELS}}
    by_class: dict[str, Any] = {}
    for row in valid:
        result["confusion_matrix"][row["gold"]][row["prediction"]] += 1
    f1_values = []
    for label in LABELS:
        tp = result["confusion_matrix"][label][label]
        support = sum(result["confusion_matrix"][label].values())
        predicted = sum(result["confusion_matrix"][gold][label] for gold in LABELS)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        by_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": support}
        f1_values.append(f1)
    result["per_class"] = by_class
    result["macro_f1"] = sum(f1_values) / len(f1_values)
    return result


def select_thresholds(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # scipy supplies the beta quantile used by the one-sided exact Clopper-Pearson bound.
    try:
        from scipy.stats import beta
    except ImportError as exc:
        raise EvalError("Threshold analysis requires scipy; install requirements.txt.") from exc

    valid = [r for r in rows if r.get("valid") and r.get("prediction") in LABELS]
    result: dict[str, Any] = {}
    curve: list[dict[str, Any]] = []
    for threshold in sorted({r["top_choice_confidence"] for r in valid}, reverse=True):
        accepted = [r for r in valid if r["top_choice_confidence"] >= threshold]
        n = len(accepted)
        correct = sum(r["prediction"] == r["gold"] for r in accepted)
        curve.append({
            "threshold": threshold,
            "accepted_count": n,
            "coverage": n / len(valid) if valid else 0.0,
            "correct_count": correct,
            "accepted_accuracy": correct / n if n else None,
        })
    for target in (0.90, 0.95, 0.99):
        best: dict[str, Any] | None = None
        for threshold in sorted({r["top_choice_confidence"] for r in valid}, reverse=True):
            accepted = [r for r in valid if r["top_choice_confidence"] >= threshold]
            n = len(accepted)
            k = sum(r["prediction"] == r["gold"] for r in accepted)
            lower = 0.0 if k == 0 else float(beta.ppf(0.05, k, n - k + 1))
            if lower >= target and (best is None or n > best["accepted_count"] or (n == best["accepted_count"] and threshold < best["threshold"])):
                ci_low = 0.0 if k == 0 else float(beta.ppf(0.025, k, n - k + 1))
                ci_high = 1.0 if k == n else float(beta.ppf(0.975, k + 1, n - k))
                best = {
                    "threshold": threshold,
                    "accepted_count": n,
                    "valid_response_count": len(valid),
                    "coverage": n / len(valid) if valid else 0.0,
                    "correct_count": k,
                    "accepted_accuracy": k / n,
                    "one_sided_95pct_cp_lower": lower,
                    "two_sided_95pct_cp_interval": [ci_low, ci_high],
                    "target_accuracy": target,
                    "accepted_set_class_metrics": class_metrics(accepted),
                }
        result[f"{target:.2f}"] = best or {"target_accuracy": target, "accepted_count": 0, "coverage": 0.0, "status": "no_qualifying_threshold"}
    result["coverage_accuracy_curve"] = curve
    return result


def calibration_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r.get("valid")]
    if not valid:
        return {"sample_count": 0}
    brier_sum = 0.0
    log_loss_sum = 0.0
    bin_sums = [{"count": 0, "confidence_sum": 0.0, "accuracy_sum": 0.0} for _ in range(10)]
    for row in valid:
        probabilities = row["probabilities"]
        brier_sum += sum((probabilities[label] - (1.0 if row["gold"] == label else 0.0)) ** 2 for label in LABELS)
        log_loss_sum -= math.log(max(probabilities[row["gold"]], 1e-15))
        confidence = row["top_choice_confidence"]
        correct = 1.0 if row["prediction"] == row["gold"] else 0.0
        index = min(9, int(confidence * 10))
        bin_sums[index]["count"] += 1
        bin_sums[index]["confidence_sum"] += confidence
        bin_sums[index]["accuracy_sum"] += correct
    bins = []
    ece = 0.0
    n = len(valid)
    for i, values in enumerate(bin_sums):
        count = values["count"]
        avg_confidence = values["confidence_sum"] / count if count else None
        avg_accuracy = values["accuracy_sum"] / count if count else None
        if count:
            ece += count / n * abs(avg_accuracy - avg_confidence)
        bins.append({"lower": i / 10, "upper": (i + 1) / 10, "count": count, "mean_confidence": avg_confidence, "accuracy": avg_accuracy})
    return {
        "sample_count": n,
        "multiclass_brier_score": brier_sum / n,
        "multiclass_log_loss": log_loss_sum / n,
        "expected_calibration_error_10_bins": ece,
        "reliability_bins": bins,
    }


def write_outputs(out_dir: Path, records: list[dict[str, Any]], manifest: dict[str, Any], finalize: bool = True) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest["thresholds"] = select_thresholds(records) if finalize and records else {}
    valid = [r for r in records if r.get("valid")]
    manifest["summary"] = {
        "source_examples": len(records),
        "valid_responses": len(valid),
        "failures_or_invalid": len(records) - len(valid),
        "full_valid_response_accuracy": (
            sum(r["prediction"] == r["gold"] for r in valid) / len(valid) if valid else None
        ),
        "class_metrics": class_metrics(valid) if valid else {},
        "calibration_metrics": calibration_metrics(records),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "metrics.json").write_text(json.dumps(manifest["thresholds"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_existing_predictions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                row_key = record.get("row_key") if isinstance(record, dict) else None
                if not isinstance(row_key, str) or not row_key or row_key in seen:
                    raise EvalError(f"Invalid or duplicate row_key in predictions.jsonl line {line_number}.")
                seen.add(row_key)
                records.append(record)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError(f"Cannot safely resume from predictions.jsonl: {exc}") from exc
    return records


def resume_completed(record: dict[str, Any]) -> bool:
    if record.get("valid") is True:
        return True
    attempts = record.get("attempts", [])
    final_status = attempts[-1].get("status") if attempts and isinstance(attempts[-1], dict) else None
    return isinstance(final_status, int) and 400 <= final_status < 500 and final_status not in {408, 429}


def assert_api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise EvalError("Set TYPESAFE_API_KEY in the environment. The key is never read from a file or argument.")
    return key


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
        raise EvalError("Threshold analysis requires scipy; install requirements.txt before making API requests.") from exc
    key = assert_api_key()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = out_dir / "predictions.jsonl"
    records = load_existing_predictions(prediction_path)
    expected_keys = {row["row_key"] for row in rows}
    existing_keys = {record["row_key"] for record in records}
    if not existing_keys.issubset(expected_keys):
        raise EvalError("Existing predictions contain row keys outside the supplied official dev split.")
    existing = {record["row_key"]: record for record in records}
    for record in records:
        if record.get("split") != "dev":
            raise EvalError("Existing predictions are not exclusively from the dev split.")
        if record.get("gold") != next(row["gold"] for row in rows if row["row_key"] == record["row_key"]):
            raise EvalError("Existing prediction gold labels do not match the supplied dev split.")
        if not resume_completed(record):
            raise EvalError("Existing predictions include an ambiguous request outcome; reconcile it before resuming to avoid duplicate billing.")
    pending_rows = [row for row in rows if row["row_key"] not in existing or not resume_completed(existing[row["row_key"]])]
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


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    smoke = sub.add_parser("smoke", help="3-5 synthetic-only API schema requests")
    smoke.add_argument("--count", type=int, default=3)
    smoke.add_argument("--timeout", type=float, default=60)
    smoke.add_argument("--confirm-api-calls", action="store_true")
    smoke.set_defaults(func=command_smoke)

    estimate = sub.add_parser("estimate", help="local-only request-size estimate for official dev JSON")
    estimate.add_argument("--dev-file", required=True, type=Path)
    estimate.add_argument("--allow-count-mismatch", action="store_true")
    estimate.set_defaults(func=command_estimate)

    dev = sub.add_parser("dev", help="run the full official dev split; never reads test")
    dev.add_argument("--dev-file", required=True, type=Path)
    dev.add_argument("--out-dir", required=True, type=Path)
    dev.add_argument("--max-cost-usd", required=True, type=float)
    dev.add_argument("--estimate-recorded", action="store_true")
    dev.add_argument("--confirm-dev-run", action="store_true")
    dev.add_argument("--timeout", type=float, default=60)
    dev.set_defaults(func=command_dev)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except EvalError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; partial outputs are preserved without credentials.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
