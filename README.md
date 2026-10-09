# Jev Selective Prediction

Experiments measuring Jev's confidence and selective prediction coverage on Japanese-language benchmarks.

Repository guidance is in [`AGENTS.md`](AGENTS.md).

Use [uv](https://docs.astral.sh/uv/) for Python dependencies and commands. Run `uv sync --locked` once, then use `uv run` as shown in the [runner guide](docs/runner.md).

For API commands, copy `.env.sample` to `.env` and set `TYPESAFE_API_KEY` there. `.env` is ignored by Git.

The JNLI evaluation design and frozen dev thresholds are documented in [`docs/jnli-selective-protocol.md`](docs/jnli-selective-protocol.md). Runner setup, the dev results, and the guarded one-time test procedure are in [`docs/runner.md`](docs/runner.md). Benchmark payloads stay outside the repository.
