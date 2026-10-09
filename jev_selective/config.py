from __future__ import annotations

import hashlib
import json
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
