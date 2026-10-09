from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import EvalError
from .runner import command_dev, command_estimate, command_smoke


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / ".local" / "jnli-dev"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Guarded Jev/JNLI dev runner.")
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
    dev.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
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
