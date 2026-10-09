# JNLI evaluation runner

The runner implements JGLUE JNLI v1.3 dev evaluation and a separately gated, one-time test evaluation under the frozen [protocol](jnli-selective-protocol.md). Dev requires the exact official filename and row count. Test requires the official test filename, 2,508 rows, a pre-recorded SHA-256, the complete dev artifacts, matching frozen thresholds, the reviewed protocol and code hashes, a documented cost estimate and cap, explicit run confirmation, and a second-reviewer attestation.

Implementation is separated by responsibility: `jev_selective/api.py` handles TypeSafe requests and schema validation, `data.py` validates the local JNLI split, `metrics.py` computes selective/calibration metrics and persists artifacts, `runner.py` coordinates the commands, and `cli.py` defines the command line. `jev_eval.py` is the small executable entry point.

## Environment

Install [uv](https://docs.astral.sh/uv/) and sync the pinned project dependencies once:

```sh
uv sync --locked
```

Run all commands below with `uv run`; it uses the project environment and `uv.lock`.

For API commands, create a local environment file once and fill in the key:

```sh
cp -n .env.sample .env
# Edit .env and set TYPESAFE_API_KEY
```

The sample lists the only required API environment variable. `.env` is ignored by Git. `uv run --env-file .env` loads it into the process environment; the runner still reads the key only from `TYPESAFE_API_KEY` and never parses or stores the file itself.

## Local tests

The tests use Python's standard `unittest` and mocked API responses; they make no network calls and require no API key. Run them locally with:

```sh
uv run --locked python -m unittest discover -s tests -v
```

There is no GitHub Actions workflow; run the checks locally before pushing changes.

## Data

Download the official [JGLUE repository](https://github.com/yahoojapan/JGLUE) and provide `datasets/jnli-v1.3/valid-v1.3.json` locally. Although the extension is `.json`, the v1.3 file is JSON Lines (one object per line). The expected source commit is `6f071c09316baae89c3d083a90985b4b1cb9968c`, the release/v1.3.0 commit. Keep both dev and test data outside this repository. The dev runner verifies 2,434 rows, required fields, unique `sentence_pair_id`, labels, and records SHA-256 and source metadata. The test runner verifies 2,508 rows and the pre-recorded checksum.

## Local estimate (no API requests)

```sh
uv run jev_eval.py estimate --dev-file /path/to/valid-v1.3.json
```

This dev-only command prints the planned request count, input-token estimate, approximate cost for dev and both splits, a retry reserve, and the dev checksum. It is local-only and cannot accept a test-file argument. The estimate uses the official published input price of $0.042 per million tokens and a UTF-8 byte-length heuristic; calibrate it with observed API usage before recording it in Issue #5.

## Synthetic API smoke check

Set `TYPESAFE_API_KEY` in `.env`, check the current account price/balance, then request 3–5 synthetic checks:

```sh
uv run --env-file .env jev_eval.py smoke --count 3 --confirm-api-calls
```

Smoke mode contains fixed synthetic examples and cannot read benchmark files. It records response status, attempts, resolved model, probabilities, latency, and usage in terminal output. It exits nonzero if schema validation fails. Do not copy environment or authorization values into logs.

## Full dev run

After successful smoke validation, confirm the account agreement and data processing/telemetry terms, record the estimate in Issue #5, and review its actual account cost. The runner saves results to `.local/jnli-dev/` inside the repository by default. `.local/` is ignored by Git, so predictions and metrics stay with this checkout without being committed. The source dataset should remain outside the repository. Use `--out-dir` only when you want a different local destination.

```sh
uv sync --locked
uv run --env-file .env jev_eval.py dev \
  --dev-file /path/to/valid-v1.3.json \
  --max-cost-usd <approved-retry-reserved-budget> \
  --estimate-recorded --confirm-dev-run
```

The runner appends and flushes each result to `predictions.jsonl` and updates its manifest and metrics. Predictions contain no source sentence text; the manifest records the source file checksum and prompt/schema hashes. API errors, invalid schemas, and valid model predictions remain distinct. Retries use exponential backoff; the official API reference does not document idempotency support, so the estimate reserves for up to three billable attempts.

## Locked test run

The dev evaluation completed on 2026-10-09: 2,434 valid responses, no API/invalid failures, and 86.73% overall accuracy. Frozen thresholds are 0.69 for the 90% target and 0.92 for the 95% target; dev selected no qualifying threshold for 99%. Measured dev usage was 1,346,191 input tokens and 104,012 output tokens. Extrapolating to test gives about 1,387,119 input tokens/$0.0583 for one attempt and a $0.1748 reserve for up to three attempts. Output tokens are listed as free. Recheck current pricing and account terms before the eventual test request.

The test runner will not access test text or the API until every preflight check passes. Record the exact test file SHA-256, test cost estimate, and cap in Issue #11. Have a second reviewer confirm that the test split remains unopened and the run configuration is frozen. Then provide the reviewed dev manifest SHA-256, protocol SHA-256, code commit, and issue references:

```sh
uv run --env-file .env jev_eval.py test \
  --test-file /path/to/test-v1.3.json \
  --expected-test-sha256 <recorded-test-sha256> \
  --expected-dev-manifest-sha256 <reviewed-dev-manifest-sha256> \
  --expected-protocol-sha256 <reviewed-protocol-sha256> \
  --expected-code-commit <reviewed-code-commit> \
  --max-cost-usd 0.1748 \
  --cost-estimate-reference <issue-comment-reference> \
  --test-run-reference <issue-comment-reference> \
  --second-reviewer <reviewer-handle> \
  --estimate-recorded --confirm-test-run
```

Before opening the test file, the runner verifies the pre-recorded protocol/dev/code hashes, a clean matching Git worktree, complete successful dev results and frozen thresholds, and the retry reserve. It then verifies the test file's recorded checksum and schema before reading `TYPESAFE_API_KEY` or sending a request. It writes to ignored `.local/jnli-test/`. Each request is marked in-flight before sending and completed responses are persisted immediately; a completed test evaluation cannot be repeated, and interrupted/ambiguous outcomes require reconciliation instead of automatic resend. Test analysis applies dev thresholds unchanged and never selects thresholds from test.

Official references: [TypeSafe quick start](https://docs.typesafe.ai/introduction/quickstart), [API reference](https://docs.typesafe.ai/api), [Choice](https://docs.typesafe.ai/primitives/choice), [confidence](https://docs.typesafe.ai/confidence), [pricing announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev), and [Master Customer Agreement](https://typesafe.ai/legal/mca).
