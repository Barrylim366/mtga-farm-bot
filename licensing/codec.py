import base64
import json
from typing import Any


def canonical_json_dumps(data: Any) -> str:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_json_bytes(data: Any) -> bytes:
    return canonical_json_dumps(data).encode("utf-8")


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    cleaned = (text or "").strip()
    padding = "=" * ((4 - len(cleaned) % 4) % 4)
    return base64.urlsafe_b64decode(cleaned + padding)


def parse_json_object(text: str) -> dict[str, Any]:
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object.")
    return obj
