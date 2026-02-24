from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .codec import b64url_decode, canonical_json_bytes, canonical_json_dumps, parse_json_object
from .fingerprint import PRODUCT_NAME, get_device_id
from . import storage

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except Exception:
    InvalidSignature = Exception
    serialization = None
    Ed25519PublicKey = None

PUBLIC_KEY_B64 = "V6hbI9Sxwy/4DApuvISJBDNYlecgliJkTiMYYJhTUSA="
PUBLIC_KEY_ENV = "BLB_PUBLIC_KEY_B64"
EMERGENCY_CODE_HASH_DEFAULT = "d193e7a2be71b625c0e44b2733f69b01962bd5339d55a7693beabadc2668a0af"  # BLB-NOTFALL-2026
EMERGENCY_CODE_HASH_ENV = "BLB_EMERGENCY_CODE_SHA256"
EMERGENCY_CODE_ENV = "BLB_EMERGENCY_CODE"
EMERGENCY_CODE_DAYS_ENV = "BLB_EMERGENCY_CODE_DAYS"


@dataclass(frozen=True)
class LicenseValidationResult:
    valid: bool
    code: str
    message: str
    payload: dict[str, Any] | None = None
    device_id: str | None = None
    license_path: str | None = None


def _result(
    valid: bool,
    code: str,
    message: str,
    *,
    payload: dict[str, Any] | None = None,
    device_id: str | None = None,
    license_path: str | None = None,
) -> LicenseValidationResult:
    return LicenseValidationResult(
        valid=valid,
        code=code,
        message=message,
        payload=payload,
        device_id=device_id,
        license_path=license_path,
    )


def _decode_base64_loose(value: str) -> bytes:
    text = (value or "").strip()
    if not text:
        raise ValueError("empty key")
    try:
        return b64url_decode(text)
    except Exception:
        pass
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.b64decode(text + padding)


def _resolve_public_key_bytes(
    public_key_b64: str | None = None,
    public_key_bytes: bytes | None = None,
) -> bytes:
    if public_key_bytes is not None:
        raw = bytes(public_key_bytes)
        if len(raw) != 32:
            raise ValueError("Ed25519 public key must be 32 bytes.")
        return raw

    key_text = (
        (public_key_b64 or "").strip()
        or os.environ.get(PUBLIC_KEY_ENV, "").strip()
        or (PUBLIC_KEY_B64 or "").strip()
    )
    if not key_text or "PASTE_ED25519_PUBLIC_KEY_BASE64_HERE" in key_text:
        raise ValueError("PUBLIC_KEY_B64 is not configured.")

    if key_text.startswith("-----BEGIN"):
        if serialization is None or Ed25519PublicKey is None:
            raise ValueError("cryptography package is not available.")
        key_obj = serialization.load_pem_public_key(key_text.encode("utf-8"))
        if not isinstance(key_obj, Ed25519PublicKey):
            raise ValueError("PEM key is not an Ed25519 public key.")
        return key_obj.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    raw = _decode_base64_loose(key_text)
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes after Base64 decode.")
    return raw


def _load_public_key(
    public_key_b64: str | None = None,
    public_key_bytes: bytes | None = None,
):
    if Ed25519PublicKey is None:
        raise RuntimeError("cryptography package is missing.")
    resolved_bytes = _resolve_public_key_bytes(
        public_key_b64=public_key_b64,
        public_key_bytes=public_key_bytes,
    )
    return Ed25519PublicKey.from_public_bytes(resolved_bytes)


def _parse_iso_datetime(value: Any) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("empty datetime")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _resolve_emergency_code_hashes() -> set[str]:
    hashes: set[str] = set()
    hashes.add(EMERGENCY_CODE_HASH_DEFAULT)
    env_hash = os.environ.get(EMERGENCY_CODE_HASH_ENV, "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", env_hash):
        hashes.add(env_hash)
    env_plain = os.environ.get(EMERGENCY_CODE_ENV, "").strip()
    if env_plain:
        hashes.add(_sha256_hex(env_plain))
    return hashes


def _resolve_emergency_days() -> int:
    raw = os.environ.get(EMERGENCY_CODE_DAYS_ENV, "").strip()
    try:
        value = int(raw) if raw else 3
    except Exception:
        value = 3
    return max(1, min(30, value))


def _validate_emergency_override(now_utc: datetime, current_device_id: str) -> LicenseValidationResult:
    data = storage.load_emergency_override()
    if not isinstance(data, dict) or not data:
        return _result(False, "no_emergency_override", "No emergency override found.", device_id=current_device_id)

    expires_raw = data.get("expires_at")
    activated_raw = data.get("activated_at")
    code_hash = str(data.get("code_hash", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", code_hash):
        storage.clear_emergency_override()
        return _result(False, "invalid_emergency_override", "Emergency override is corrupt.", device_id=current_device_id)
    try:
        expires_at = _parse_iso_datetime(expires_raw)
        activated_at = _parse_iso_datetime(activated_raw)
    except Exception:
        storage.clear_emergency_override()
        return _result(False, "invalid_emergency_override", "Emergency override is corrupt.", device_id=current_device_id)

    if now_utc > expires_at:
        storage.clear_emergency_override()
        return _result(False, "emergency_expired", "Emergency code expired.", device_id=current_device_id)

    payload = {
        "customer_id": "EMERGENCY",
        "seat_index": "emergency",
        "issued_at": activated_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "features": ["emergency_override"],
    }
    return _result(
        True,
        "emergency_ok",
        "Emergency code active.",
        payload=payload,
        device_id=current_device_id,
    )


def parse_license_container(license_text: str) -> dict[str, str]:
    try:
        obj = parse_json_object(license_text)
    except Exception as exc:
        raise ValueError("Corrupt file: invalid license JSON.") from exc

    payload = obj.get("payload")
    sig = obj.get("sig")
    if not isinstance(payload, str) or not isinstance(sig, str):
        raise ValueError("Corrupt file: missing payload/sig.")
    return {"payload": payload, "sig": sig}


def _write_status_cache(result: LicenseValidationResult) -> None:
    payload = result.payload or {}
    data = {
        "last_checked": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "valid": result.valid,
        "code": result.code,
        "message": result.message,
        "customer_id": payload.get("customer_id"),
        "seat_index": payload.get("seat_index"),
        "expires_at": payload.get("expires_at"),
    }
    try:
        storage.save_status_cache(data)
    except Exception:
        pass


def _validate_payload(payload: dict[str, Any], current_device_id: str, now_utc: datetime) -> LicenseValidationResult:
    product = str(payload.get("product", "")).strip()
    if product != PRODUCT_NAME:
        return _result(False, "wrong_product", "Wrong product.", payload=payload, device_id=current_device_id)

    customer_id = str(payload.get("customer_id", "")).strip()
    if not customer_id:
        return _result(False, "invalid_payload", "Corrupt file: missing customer_id.", payload=payload, device_id=current_device_id)

    try:
        seat_index = int(payload.get("seat_index"))
    except Exception:
        return _result(False, "invalid_seat", "Corrupt file: invalid seat_index.", payload=payload, device_id=current_device_id)
    if seat_index not in (1, 2):
        return _result(False, "invalid_seat", "Corrupt file: seat_index must be 1 or 2.", payload=payload, device_id=current_device_id)

    try:
        issued_at_raw = payload.get("issued_at")
        _parse_iso_datetime(issued_at_raw)
    except Exception:
        return _result(False, "invalid_payload", "Corrupt file: invalid issued_at.", payload=payload, device_id=current_device_id)

    device_id_hash = str(payload.get("device_id_hash", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", device_id_hash):
        return _result(False, "invalid_payload", "Corrupt file: invalid device_id_hash.", payload=payload, device_id=current_device_id)

    expected_hash = hashlib.sha256(current_device_id.encode("utf-8")).hexdigest()
    if device_id_hash != expected_hash:
        return _result(False, "wrong_device", "Wrong device.", payload=payload, device_id=current_device_id)

    expires_raw = payload.get("expires_at")
    if expires_raw not in (None, "", "null"):
        try:
            expires_at = _parse_iso_datetime(expires_raw)
        except Exception:
            return _result(False, "invalid_payload", "Corrupt file: invalid expires_at.", payload=payload, device_id=current_device_id)
        if now_utc > expires_at:
            return _result(False, "expired", "Expired.", payload=payload, device_id=current_device_id)

    features = payload.get("features")
    if features is not None and not isinstance(features, list):
        return _result(False, "invalid_payload", "Corrupt file: features must be a list.", payload=payload, device_id=current_device_id)

    return _result(True, "ok", "License activated.", payload=payload, device_id=current_device_id)


def validate_license_object(
    license_obj: dict[str, Any],
    *,
    current_device_id: str | None = None,
    public_key_b64: str | None = None,
    public_key_bytes: bytes | None = None,
    now: datetime | None = None,
) -> LicenseValidationResult:
    try:
        resolved_device_id = current_device_id or get_device_id()
    except Exception as exc:
        return _result(False, "device_error", f"Could not determine device ID: {exc}")

    if not isinstance(license_obj, dict):
        return _result(False, "corrupt", "Corrupt file.", device_id=resolved_device_id)
    payload_b64 = license_obj.get("payload")
    sig_b64 = license_obj.get("sig")
    if not isinstance(payload_b64, str) or not isinstance(sig_b64, str):
        return _result(False, "corrupt", "Corrupt file.", device_id=resolved_device_id)

    try:
        payload_bytes = b64url_decode(payload_b64)
        signature = b64url_decode(sig_b64)
    except Exception:
        return _result(False, "corrupt", "Corrupt file.", device_id=resolved_device_id)

    try:
        pub_key = _load_public_key(public_key_b64=public_key_b64, public_key_bytes=public_key_bytes)
    except Exception as exc:
        return _result(False, "key_error", f"Public Key Fehler: {exc}", device_id=resolved_device_id)

    try:
        pub_key.verify(signature, payload_bytes)
    except InvalidSignature:
        return _result(False, "invalid_signature", "Invalid signature.", device_id=resolved_device_id)
    except Exception:
        return _result(False, "invalid_signature", "Invalid signature.", device_id=resolved_device_id)

    try:
        payload_raw = payload_bytes.decode("utf-8")
        payload_obj = json.loads(payload_raw)
        if not isinstance(payload_obj, dict):
            return _result(False, "corrupt", "Corrupt file.", device_id=resolved_device_id)
    except Exception:
        return _result(False, "corrupt", "Corrupt file.", device_id=resolved_device_id)

    expected_payload_bytes = canonical_json_bytes(payload_obj)
    if payload_bytes != expected_payload_bytes:
        return _result(False, "corrupt", "Corrupt file: payload is not canonical.", device_id=resolved_device_id)

    now_utc = now.astimezone(timezone.utc) if isinstance(now, datetime) and now.tzinfo else (now or datetime.now(timezone.utc))
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return _validate_payload(payload_obj, resolved_device_id, now_utc.astimezone(timezone.utc))


def validate_license_text(
    license_text: str,
    *,
    current_device_id: str | None = None,
    public_key_b64: str | None = None,
    public_key_bytes: bytes | None = None,
    now: datetime | None = None,
) -> LicenseValidationResult:
    try:
        container = parse_license_container(license_text)
    except ValueError as exc:
        try:
            resolved_device_id = current_device_id or get_device_id()
        except Exception:
            resolved_device_id = current_device_id
        return _result(False, "corrupt", str(exc), device_id=resolved_device_id)
    return validate_license_object(
        container,
        current_device_id=current_device_id,
        public_key_b64=public_key_b64,
        public_key_bytes=public_key_bytes,
        now=now,
    )


def validate_installed_license(
    *,
    current_device_id: str | None = None,
    public_key_b64: str | None = None,
    public_key_bytes: bytes | None = None,
    now: datetime | None = None,
) -> LicenseValidationResult:
    now_utc = now.astimezone(timezone.utc) if isinstance(now, datetime) and now.tzinfo else (now or datetime.now(timezone.utc))
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    try:
        resolved_device_id = current_device_id or get_device_id()
    except Exception as exc:
        result = _result(False, "device_error", f"Could not determine device ID: {exc}")
        _write_status_cache(result)
        return result

    license_path = str(storage.get_license_file_path())
    raw = storage.load_license_text()
    if not raw or not raw.strip():
        emergency_result = _validate_emergency_override(now_utc.astimezone(timezone.utc), resolved_device_id)
        if emergency_result.valid:
            emergency_result = replace(emergency_result, license_path=license_path)
            _write_status_cache(emergency_result)
            return emergency_result
        result = _result(
            False,
            "not_activated",
            "Not activated: no license found.",
            device_id=resolved_device_id,
            license_path=license_path,
        )
        _write_status_cache(result)
        return result

    result = validate_license_text(
        raw,
        current_device_id=resolved_device_id,
        public_key_b64=public_key_b64,
        public_key_bytes=public_key_bytes,
        now=now_utc,
    )
    result = replace(result, license_path=license_path)
    if not result.valid:
        emergency_result = _validate_emergency_override(now_utc.astimezone(timezone.utc), resolved_device_id)
        if emergency_result.valid:
            emergency_result = replace(emergency_result, license_path=license_path)
            _write_status_cache(emergency_result)
            return emergency_result
    _write_status_cache(result)
    return result


def activate_emergency_code(
    emergency_code: str,
    *,
    current_device_id: str | None = None,
    now: datetime | None = None,
) -> LicenseValidationResult:
    try:
        resolved_device_id = current_device_id or get_device_id()
    except Exception as exc:
        result = _result(False, "device_error", f"Could not determine device ID: {exc}")
        _write_status_cache(result)
        return result

    code = (emergency_code or "").strip()
    if not code:
        result = _result(False, "invalid_emergency_code", "Emergency code is empty.", device_id=resolved_device_id)
        _write_status_cache(result)
        return result

    code_hash = _sha256_hex(code)
    valid_hashes = _resolve_emergency_code_hashes()
    if code_hash not in valid_hashes:
        result = _result(False, "invalid_emergency_code", "Emergency code is invalid.", device_id=resolved_device_id)
        _write_status_cache(result)
        return result

    now_utc = now.astimezone(timezone.utc) if isinstance(now, datetime) and now.tzinfo else (now or datetime.now(timezone.utc))
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    expires_at = now_utc + timedelta(days=_resolve_emergency_days())
    payload = {
        "version": 1,
        "activated_at": now_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "code_hash": code_hash,
    }
    try:
        storage.save_emergency_override(payload)
    except Exception as exc:
        result = _result(False, "storage_error", f"Could not save emergency override: {exc}", device_id=resolved_device_id)
        _write_status_cache(result)
        return result

    result = _validate_emergency_override(now_utc, resolved_device_id)
    _write_status_cache(result)
    return result


def activate_license_text(
    license_text: str,
    *,
    current_device_id: str | None = None,
    public_key_b64: str | None = None,
    public_key_bytes: bytes | None = None,
    now: datetime | None = None,
) -> LicenseValidationResult:
    result = validate_license_text(
        license_text,
        current_device_id=current_device_id,
        public_key_b64=public_key_b64,
        public_key_bytes=public_key_bytes,
        now=now,
    )
    if not result.valid:
        _write_status_cache(result)
        return result

    try:
        container = parse_license_container(license_text)
        normalized = canonical_json_dumps({"payload": container["payload"], "sig": container["sig"]})
        path = storage.save_license_text(normalized)
        storage.clear_emergency_override()
        result = replace(result, license_path=str(path))
    except Exception as exc:
        result = _result(
            False,
            "storage_error",
            f"Could not save license: {exc}",
            payload=result.payload,
            device_id=result.device_id,
        )
    _write_status_cache(result)
    return result


def require_license_or_block(
    on_block: Callable[[LicenseValidationResult], None] | None = None,
    *,
    current_device_id: str | None = None,
    public_key_b64: str | None = None,
    public_key_bytes: bytes | None = None,
    now: datetime | None = None,
) -> LicenseValidationResult:
    result = validate_installed_license(
        current_device_id=current_device_id,
        public_key_b64=public_key_b64,
        public_key_bytes=public_key_bytes,
        now=now,
    )
    if not result.valid and on_block is not None:
        try:
            on_block(result)
        except Exception:
            pass
    return result


def format_license_details(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "-"
    customer_id = payload.get("customer_id") or "-"
    seat_index = payload.get("seat_index") or "-"
    issued_at = payload.get("issued_at") or "-"
    expires_at = payload.get("expires_at")
    expires_text = expires_at if expires_at not in (None, "") else "unbegrenzt"
    features = payload.get("features")
    if isinstance(features, list) and features:
        feature_text = ",".join(str(x) for x in features)
    else:
        feature_text = "-"
    return (
        f"Customer: {customer_id}\n"
        f"Seat: {seat_index}\n"
        f"Issued: {issued_at}\n"
        f"Expires: {expires_text}\n"
        f"Features: {feature_text}"
    )
