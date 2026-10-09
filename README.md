# Jev Selective Prediction

Experiments measuring Jev's confidence and selective prediction coverage on Japanese-language benchmarks.

Repository guidance is in [`AGENTS.md`](AGENTS.md).

Use [uv](https://docs.astral.sh/uv/) for Python dependencies and commands. Run `uv sync --locked` once, then use `uv run` as shown in the [runner guide](docs/runner.md).

The JNLI evaluation design is documented in [`docs/jnli-selective-protocol.md`](docs/jnli-selective-protocol.md). A dev-only runner and safe setup instructions are in [`docs/runner.md`](docs/runner.md). The runner does not contain benchmark payloads and cannot execute the locked test split.
