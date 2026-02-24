#!/usr/bin/env python
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from licensing.codec import b64url_encode, canonical_json_bytes, canonical_json_dumps

PRODUCT_NAME = "BurningLotusBot"


def _decode_base64_loose(value: str) -> bytes:
    text = (value or "").strip()
    if not text:
        raise ValueError("empty key input")
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except Exception:
        return base64.b64decode(text + padding)


def _normalize_iso(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        datetime.fromisoformat(text[:-1] + "+00:00")
        return text
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_private_key_from_bytes(raw: bytes) -> Ed25519PrivateKey:
    data = bytes(raw).strip()
    if len(data) == 64:
        data = data[:32]
    if len(data) != 32:
        raise ValueError("Ed25519 private key must be 32 bytes (or 64 bytes seed+pub).")
    return Ed25519PrivateKey.from_private_bytes(data)


def _load_private_key(private_key_file: str | None) -> Ed25519PrivateKey:
    env_value = os.environ.get("BLB_PRIVATE_KEY_B64", "").strip()
    if private_key_file:
        file_path = Path(private_key_file).expanduser().resolve()
        blob = file_path.read_bytes()
        if blob.startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(blob, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("PEM key is not an Ed25519 private key.")
            return key
        try:
            text = blob.decode("utf-8").strip()
            if text:
                return _load_private_key_from_bytes(_decode_base64_loose(text))
        except UnicodeDecodeError:
            pass
        return _load_private_key_from_bytes(blob)

    if not env_value:
        raise ValueError("Private key missing. Set BLB_PRIVATE_KEY_B64 or pass --private-key-file.")
    return _load_private_key_from_bytes(_decode_base64_loose(env_value))


def _build_payload(args: argparse.Namespace) -> dict:
    issued_at = _normalize_iso(args.issued_at) or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    expires_at = _normalize_iso(args.expires_at)

    if args.device_id_hash:
        device_hash = args.device_id_hash.strip().lower()
    else:
        device_hash = hashlib.sha256(args.device_id.strip().encode("utf-8")).hexdigest()

    payload = {
        "product": PRODUCT_NAME,
        "customer_id": args.customer_id.strip(),
        "seat_index": int(args.seat_index),
        "device_id_hash": device_hash,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    if args.features:
        payload["features"] = [x.strip() for x in args.features.split(",") if x.strip()]
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate offline BurningLotusBot license (*.bllic)")
    parser.add_argument("--customer-id", "--customer_id", dest="customer_id", required=True, help="Customer identifier")
    parser.add_argument("--seat-index", "--seat_index", dest="seat_index", required=True, type=int, choices=[1, 2], help="Seat index (1 or 2)")
    parser.add_argument("--device-id", "--device_id", dest="device_id", help="Device ID (A-Z2-7...)")
    parser.add_argument("--device-id-hash", "--device_id_hash", dest="device_id_hash", help="SHA256(device_id) as hex")
    parser.add_argument("--issued-at", "--issued_at", dest="issued_at", help="ISO datetime (default: now UTC)")
    parser.add_argument("--expires-at", "--expires_at", dest="expires_at", help="ISO datetime (optional)")
    parser.add_argument("--features", help='Optional CSV list, e.g. "full"')
    parser.add_argument("--private-key-file", help="Path to private key (raw/base64/PEM)")
    parser.add_argument("--out", help="Output .bllic file path")
    parser.add_argument("--print", dest="print_license", action="store_true", help="Print compact JSON license for copy/paste")
    args = parser.parse_args()

    if not args.device_id and not args.device_id_hash:
        parser.error("Either --device-id or --device-id-hash is required.")
    if args.device_id and args.device_id_hash:
        parser.error("Use either --device-id or --device-id-hash, not both.")
    return args


def main() -> int:
    args = _parse_args()
    private_key = _load_private_key(args.private_key_file)
    payload = _build_payload(args)
    payload_bytes = canonical_json_bytes(payload)
    signature = private_key.sign(payload_bytes)

    license_obj = {
        "payload": b64url_encode(payload_bytes),
        "sig": b64url_encode(signature),
    }
    compact_license = canonical_json_dumps(license_obj)

    out_path = args.out
    if not out_path:
        out_name = f"{payload['customer_id']}_seat{payload['seat_index']}.bllic"
        out_path = str(REPO_ROOT / out_name)

    target = Path(out_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(compact_license + "\n", encoding="utf-8")

    print(f"License written: {target}")
    print(f"device_id_hash: {payload['device_id_hash']}")
    if args.print_license:
        print(compact_license)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
