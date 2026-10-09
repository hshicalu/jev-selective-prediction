from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from typing import Any

from .config import API_URL, CRITERIA, INSTRUCTIONS, LABELS, MODEL, TRANSIENT_HTTP, EvalError, request_body, serialize_request


def request_once(state: str, key: str, timeout: float) -> tuple[int, dict[str, Any], float]:
    req = urllib.request.Request(
        API_URL,
        data=serialize_request(state),
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


def _response_metadata(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"model": None, "usage": {}, "raw_response": response}
    return {
        "model": response.get("model"),
        "usage": response.get("usage", {}),
        "raw_response": response,
    }


def validate_response(result: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any]]:
    if result["status"] != 200:
        return False, "http_or_transport_failure", {}
    response = result["response"]
    fields = _response_metadata(response)
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


def assert_api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise EvalError("Set TYPESAFE_API_KEY in the environment. The key is never read from a file or argument.")
    return key
