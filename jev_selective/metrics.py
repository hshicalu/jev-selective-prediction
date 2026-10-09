from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .config import LABELS, EvalError


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
        raise EvalError("Threshold analysis requires SciPy; run `uv sync --locked`.") from exc

    valid = [r for r in rows if r.get("valid") and r.get("prediction") in LABELS]
    result: dict[str, Any] = {}
    curve: list[dict[str, Any]] = []
    candidates = sorted({r["top_choice_confidence"] for r in valid}, reverse=True)
    accepted_by_threshold = [
        (threshold, [r for r in valid if r["top_choice_confidence"] >= threshold])
        for threshold in candidates
    ]
    for threshold, accepted in accepted_by_threshold:
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
        for threshold, accepted in accepted_by_threshold:
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


def evaluate_frozen_thresholds(rows: list[dict[str, Any]], thresholds: dict[str, float | None]) -> dict[str, Any]:
    """Evaluate test outcomes at dev-selected thresholds without selecting new ones."""
    try:
        from scipy.stats import beta
    except ImportError as exc:
        raise EvalError("Threshold analysis requires SciPy; run `uv sync --locked`.") from exc

    valid = [r for r in rows if r.get("valid") and r.get("prediction") in LABELS]
    curve: list[dict[str, Any]] = []
    candidates = sorted({r["top_choice_confidence"] for r in valid}, reverse=True)
    for threshold in candidates:
        accepted = [r for r in valid if r["top_choice_confidence"] >= threshold]
        correct = sum(r["prediction"] == r["gold"] for r in accepted)
        curve.append({
            "threshold": threshold,
            "accepted_count": len(accepted),
            "coverage": len(accepted) / len(valid) if valid else 0.0,
            "correct_count": correct,
            "accepted_accuracy": correct / len(accepted) if accepted else None,
        })

    evaluated: dict[str, Any] = {}
    for target, threshold in thresholds.items():
        if threshold is None:
            evaluated[target] = {
                "target_accuracy": float(target),
                "threshold": None,
                "accepted_count": 0,
                "valid_response_count": len(valid),
                "coverage": 0.0,
                "status": "no_qualifying_dev_threshold",
            }
            continue
        accepted = [r for r in valid if r["top_choice_confidence"] >= threshold]
        n = len(accepted)
        correct = sum(r["prediction"] == r["gold"] for r in accepted)
        lower = 0.0 if not correct or not n else float(beta.ppf(0.05, correct, n - correct + 1))
        ci_low = 0.0 if not correct or not n else float(beta.ppf(0.025, correct, n - correct + 1))
        ci_high = 1.0 if correct == n else float(beta.ppf(0.975, correct + 1, n - correct))
        evaluated[target] = {
            "target_accuracy": float(target),
            "threshold": threshold,
            "accepted_count": n,
            "valid_response_count": len(valid),
            "correct_count": correct,
            "accepted_accuracy": correct / n if n else None,
            "coverage": n / len(valid) if valid else 0.0,
            "one_sided_95pct_cp_lower": lower,
            "two_sided_95pct_cp_interval": [ci_low, ci_high],
            "accepted_set_class_metrics": class_metrics(accepted) if accepted else {},
        }
    evaluated["coverage_accuracy_curve"] = curve
    return evaluated


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


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in records if r.get("valid")]
    return {
        "source_examples": len(records),
        "valid_responses": len(valid),
        "failures_or_invalid": len(records) - len(valid),
        "full_valid_response_accuracy": (
            sum(r["prediction"] == r["gold"] for r in valid) / len(valid) if valid else None
        ),
        "class_metrics": class_metrics(valid) if valid else {},
        "calibration_metrics": calibration_metrics(records),
    }


def write_outputs(
    out_dir: Path,
    records: list[dict[str, Any]],
    manifest: dict[str, Any],
    finalize: bool = True,
    thresholds: dict[str, float | None] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if finalize and records:
        manifest["thresholds"] = (
            evaluate_frozen_thresholds(records, thresholds)
            if thresholds is not None
            else select_thresholds(records)
        )
    else:
        manifest["thresholds"] = {}
    manifest["summary"] = summarize_records(records)
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
                if not isinstance(row_key, str) or not row_key or row_key in seen or not isinstance(record, dict):
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
    if final_status == 200:
        return True
    return isinstance(final_status, int) and 400 <= final_status < 500 and final_status not in {408, 429}


def match_existing_predictions(
    records: list[dict[str, Any]], rows: list[dict[str, Any]], split: str = "dev"
) -> dict[str, dict[str, Any]]:
    rows_by_key = {row["row_key"]: row for row in rows}
    existing: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record["row_key"]
        expected = rows_by_key.get(key)
        if expected is None:
            raise EvalError(f"Existing predictions contain row keys outside the supplied official {split} split.")
        if record.get("split") != split or record.get("gold") != expected["gold"]:
            raise EvalError(f"Existing prediction split or gold label does not match the supplied official {split} split.")
        if not resume_completed(record):
            raise EvalError("Existing predictions include an ambiguous request outcome; reconcile it before resuming to avoid duplicate billing.")
        existing[key] = record
    return existing
