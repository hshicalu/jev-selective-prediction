# JNLI dev runner

This runner implements only the JNLI v1.3 development path from the amended [protocol](jnli-selective-protocol.md). It has no test split mode and requires the exact dev filename and row count.

Implementation is separated by responsibility: `jev_selective/api.py` handles TypeSafe requests and schema validation, `data.py` validates the local JNLI split, `metrics.py` computes selective/calibration metrics and persists artifacts, `runner.py` coordinates the commands, and `cli.py` defines the command line. `jev_eval.py` is the small executable entry point.

## Environment

Install [uv](https://docs.astral.sh/uv/) and sync the pinned project dependencies once:

```sh
uv sync --locked
```

Run all commands below with `uv run`; it uses the project environment and `uv.lock`.

## Data

Download the official [JGLUE repository](https://github.com/yahoojapan/JGLUE) and provide `datasets/jnli-v1.3/valid-v1.3.json` locally. Although the extension is `.json`, the v1.3 file is JSON Lines (one object per line). The expected source commit is `6f071c09316baae89c3d083a90985b4b1cb9968c`, the release/v1.3.0 commit. Keep the dataset outside this repository. The runner verifies the 2,434 row count, required fields, unique `sentence_pair_id`, labels, and records SHA-256 and source metadata.

## Local estimate (no API requests)

```sh
uv run jev_eval.py estimate --dev-file /path/to/valid-v1.3.json
```

This prints the planned request count, input-token estimate, approximate cost for dev and both splits, a retry reserve, and the data checksum. It is a local-only command. The estimate uses the official published input price of $0.042 per million tokens and a UTF-8 byte-length heuristic; compare the estimate with actual usage from the smoke calls and your TypeSafe account before recording it in Issue #5.

## Synthetic API smoke check

Set the official API key in the environment as `TYPESAFE_API_KEY` (the tool never accepts a key in a command argument or file), check the current account price/balance, then request 3–5 synthetic checks:

```sh
export TYPESAFE_API_KEY='…'
uv run jev_eval.py smoke --count 3 --confirm-api-calls
```

Smoke mode contains fixed synthetic examples and cannot read benchmark files. It records response status, attempts, resolved model, probabilities, latency, and usage in terminal output. It exits nonzero if schema validation fails. Do not copy environment or authorization values into logs.

## Full dev run

After successful smoke validation, confirm the account agreement and data processing/telemetry terms, record the estimate in Issue #5, and review its actual account cost. Install the analysis dependency and run only after those gates:

```sh
uv sync --locked
uv run jev_eval.py dev \
  --dev-file /path/to/valid-v1.3.json \
  --out-dir /path/outside/this/repository/jnli-dev-run \
  --max-cost-usd <approved-retry-reserved-budget> \
  --estimate-recorded --confirm-dev-run
```

The runner appends and flushes each result to `predictions.jsonl`; it updates `manifest.json` and `metrics.json` every 50 rows and on completion. Predictions contain no source sentence text; the manifest records the source file checksum and prompt/schema hashes. API errors, invalid schemas, and valid model predictions remain distinct. Retries use exponential backoff; the official API reference does not document idempotency support, so the estimate reserves for up to three billable attempts. The runner never opens a test file or makes test requests.

## Current blockers

As of 2026-10-09, no `TYPESAFE_API_KEY` is available in this execution environment. No API request is made by this documentation or by `estimate`. The runner targets TypeSafe's official endpoint and published token pricing; recheck the current price and account balance before any paid request.

Official references: [TypeSafe quick start](https://docs.typesafe.ai/introduction/quickstart), [API reference](https://docs.typesafe.ai/api), [Choice](https://docs.typesafe.ai/primitives/choice), [confidence](https://docs.typesafe.ai/confidence), [pricing announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev), and [Master Customer Agreement](https://typesafe.ai/legal/mca).
