from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import error as urlerror
from urllib import request as urlrequest

from .codec import b64url_decode, parse_json_object
from .fingerprint import get_device_id
from . import storage

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
except Exception:
    InvalidSignature = Exception
    hashes = None
    ec = None
    utils = None

logger = logging.getLogger(__name__)

ACTIVATE_URL = "https://burninglotusbot.com/api/activate-license"
VALIDATE_URL = "https://burninglotusbot.com/api/validate-license"
HTTP_TIMEOUT_SECONDS = 12
VALIDATE_INTERVAL_ENV = "BLB_LICENSE_VALIDATE_INTERVAL_SECONDS"
VALIDATE_GRACE_ENV = "BLB_LICENSE_VALIDATE_GRACE_SECONDS"
DEFAULT_VALIDATE_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_VALIDATE_GRACE_SECONDS = 48 * 60 * 60

PUBLIC_JWK_DEFAULT = {
    "kty": "EC",
    "crv": "P-256",
    "x": "_2XtSnQ4ROOcOX1YSrmnj0db7tQpY-p-Jp0VrT4tgnE",
    "y": "L-IiEfn3M5NViPXQct0BkWar9atwroXku4uTQKwaMtk",
}
PUBLIC_JWK_ENV = "BLB_PUBLIC_JWK"
PUBLIC_JWK_FILE_ENV = "BLB_PUBLIC_JWK_FILE"


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


def _platform_code() -> str:
    if os.name == "nt":
        return "win"
    if os.sys.platform == "darwin":
        return "mac"
    return "linux"


def _now() -> int:
    return int(time.time())


def _decode_b64url_to_int(text: str) -> int:
    raw = b64url_decode(text)
    if not raw:
        raise ValueError("empty coordinate")
    return int.from_bytes(raw, "big")


def _resource_root_dir() -> Path:
    if getattr(os.sys, "frozen", False):
        meipass = getattr(os.sys, "_MEIPASS", "")
        if isinstance(meipass, str) and meipass and os.path.isdir(meipass):
            return Path(meipass).resolve()
        return Path(os.path.dirname(os.sys.executable)).resolve()
    return Path(__file__).resolve().parent.parent


def _candidate_jwk_paths() -> list[Path]:
    app_root = _resource_root_dir()
    return [
        app_root / "config" / "public_key.jwk",
        Path(__file__).resolve().parent / "public_key.jwk",
    ]


def _load_public_jwk() -> dict[str, Any]:
    env_json = (os.environ.get(PUBLIC_JWK_ENV, "") or "").strip()
    if env_json:
        data = parse_json_object(env_json)
        return data

    env_file = (os.environ.get(PUBLIC_JWK_FILE_ENV, "") or "").strip()
    if env_file:
        p = Path(env_file).expanduser().resolve()
        if p.is_file():
            return parse_json_object(p.read_text(encoding="utf-8"))

    for candidate in _candidate_jwk_paths():
        if candidate.is_file():
            try:
                return parse_json_object(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue

    return dict(PUBLIC_JWK_DEFAULT)


def _load_public_key():
    if ec is None or hashes is None:
        raise RuntimeError("cryptography package is missing")
    jwk = _load_public_jwk()
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("public key must be EC P-256 JWK")
    x = str(jwk.get("x", "")).strip()
    y = str(jwk.get("y", "")).strip()
    if not x or not y or "PASTE_" in x or "PASTE_" in y:
        raise ValueError("public key is not configured")
    numbers = ec.EllipticCurvePublicNumbers(_decode_b64url_to_int(x), _decode_b64url_to_int(y), ec.SECP256R1())
    return numbers.public_key()


def _ensure_der_signature(sig: bytes) -> list[bytes]:
    sig = bytes(sig)
    candidates = [sig]
    if len(sig) == 64 and utils is not None:
        r = int.from_bytes(sig[:32], "big")
        s = int.from_bytes(sig[32:], "big")
        candidates.append(utils.encode_dss_signature(r, s))
    return candidates


def _verify_signature(payload_b64: str, sig_raw: bytes) -> bool:
    key = _load_public_key()
    data = payload_b64.encode("utf-8")
    for candidate in _ensure_der_signature(sig_raw):
        try:
            key.verify(candidate, data, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            continue
        except Exception:
            continue
    return False


def loadLicenseState() -> dict[str, Any]:
    data = storage.load_license_state()
    return data if isinstance(data, dict) else {}


def _parse_token(token: str) -> tuple[str, bytes, dict[str, Any]]:
    parts = (token or "").strip().split(".")
    if len(parts) != 2:
        raise ValueError("token_format_invalid")
    payload_b64, sig_b64 = parts
    payload_json_bytes = b64url_decode(payload_b64)
    sig_raw = b64url_decode(sig_b64)
    payload_obj = json.loads(payload_json_bytes.decode("utf-8"))
    if not isinstance(payload_obj, dict):
        raise ValueError("token_payload_invalid")
    return payload_b64, sig_raw, payload_obj


def _reason_message(code: str) -> str:
    messages = {
        "not_activated": "License is not activated.",
        "token_format_invalid": "Stored token format is invalid.",
        "token_payload_invalid": "Stored token payload is invalid.",
        "signature_invalid": "Stored token signature is invalid.",
        "machine_mismatch": "License token belongs to a different machine.",
        "platform_mismatch": "License token belongs to a different platform.",
        "token_expired": "License token is expired.",
        "license_mismatch": "Stored license key does not match token payload.",
        "crypto_missing": "Required package 'cryptography' is missing.",
        "key_error": "Public key is not configured correctly.",
        "network_error": "License activation network error.",
        "activation_failed": "License activation failed.",
        "license_not_found": "License key was not found.",
        "license_inactive": "License key is inactive.",
        "license_revoked": "License key is revoked.",
        "license_expired": "License key is expired.",
        "machine_limit_reached": "Machine activation limit reached.",
        "platform_not_allowed": "License is not allowed on this platform.",
        "cloudflare_blocked": "Request was blocked by Cloudflare security.",
        "http_403": "Server denied activation request (HTTP 403).",
        "http_401": "Server denied activation request (HTTP 401).",
        "http_429": "Too many activation requests (HTTP 429).",
        "server_response_invalid": "Activation server returned invalid data.",
        "validation_failed": "License validation failed.",
        "ok_offline_grace": "License temporarily allowed during offline grace period.",
        "ok": "License active.",
    }
    return messages.get(code, code)


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return max(0, int(raw))
    except Exception:
        return int(default)


def _validate_interval_seconds() -> int:
    return _env_int(VALIDATE_INTERVAL_ENV, DEFAULT_VALIDATE_INTERVAL_SECONDS)


def _validate_grace_seconds() -> int:
    return _env_int(VALIDATE_GRACE_ENV, DEFAULT_VALIDATE_GRACE_SECONDS)


def _validation_anchor_ts(state: dict[str, Any]) -> int:
    try:
        last_validated = int(state.get("lastValidatedAt") or 0)
    except Exception:
        last_validated = 0
    if last_validated > 0:
        return last_validated
    try:
        saved_at = int(state.get("savedAt") or 0)
    except Exception:
        saved_at = 0
    return max(0, saved_at)


def _derive_http_reason(status_code: int, response: dict[str, Any], raw_text: str) -> str:
    reason = str(response.get("code") or response.get("error") or "").strip()
    if reason:
        return reason
    raw_low = (raw_text or "").lower()
    if "cloudflare" in raw_low or "ray id" in raw_low:
        return "cloudflare_blocked"
    return f"http_{status_code}"


def verifyLocalToken(
    *,
    state: dict[str, Any] | None = None,
    machine_id: str | None = None,
    platform: str | None = None,
    now_unix: int | None = None,
) -> LicenseValidationResult:
    machine = machine_id or get_device_id()
    plat = platform or _platform_code()
    now_ts = int(now_unix if now_unix is not None else _now())
    license_path = str(storage.get_license_file_path())

    st = state if isinstance(state, dict) else loadLicenseState()
    token = str(st.get("token", "") or "").strip()
    license_key = str(st.get("licenseKey", "") or "").strip()
    if not token or not license_key:
        return _result(False, "not_activated", _reason_message("not_activated"), device_id=machine, license_path=license_path)

    try:
        payload_b64, sig_raw, payload = _parse_token(token)
    except ValueError as exc:
        code = str(exc)
        logger.warning("license verify failed: %s", code)
        return _result(False, code, _reason_message(code), device_id=machine, license_path=license_path)
    except Exception:
        logger.warning("license verify failed: token_payload_invalid")
        return _result(False, "token_payload_invalid", _reason_message("token_payload_invalid"), device_id=machine, license_path=license_path)

    try:
        signature_ok = _verify_signature(payload_b64, sig_raw)
    except RuntimeError as exc:
        if "cryptography package is missing" in str(exc).lower():
            logger.warning("license verify failed: crypto_missing")
            return _result(False, "crypto_missing", _reason_message("crypto_missing"), device_id=machine, license_path=license_path)
        logger.warning("license verify failed: key_error")
        return _result(False, "key_error", _reason_message("key_error"), device_id=machine, license_path=license_path)
    except Exception:
        logger.warning("license verify failed: key_error")
        return _result(False, "key_error", _reason_message("key_error"), device_id=machine, license_path=license_path)

    if not signature_ok:
        logger.warning("license verify failed: signature_invalid")
        return _result(False, "signature_invalid", _reason_message("signature_invalid"), device_id=machine, license_path=license_path)

    payload_lic = str(payload.get("lic", "") or "")
    payload_mid = str(payload.get("mid", "") or "")
    payload_plat = str(payload.get("plat", "") or "")
    try:
        payload_exp = int(payload.get("exp"))
    except Exception:
        logger.warning("license verify failed: token_payload_invalid")
        return _result(False, "token_payload_invalid", _reason_message("token_payload_invalid"), payload=payload, device_id=machine, license_path=license_path)

    if payload_lic != license_key:
        logger.warning("license verify failed: license_mismatch")
        return _result(False, "license_mismatch", _reason_message("license_mismatch"), payload=payload, device_id=machine, license_path=license_path)
    if payload_mid != machine:
        logger.warning("license verify failed: machine_mismatch")
        return _result(False, "machine_mismatch", _reason_message("machine_mismatch"), payload=payload, device_id=machine, license_path=license_path)
    if payload_plat != plat:
        logger.warning("license verify failed: platform_mismatch")
        return _result(False, "platform_mismatch", _reason_message("platform_mismatch"), payload=payload, device_id=machine, license_path=license_path)
    if payload_exp <= now_ts:
        logger.warning("license verify failed: token_expired")
        return _result(False, "token_expired", _reason_message("token_expired"), payload=payload, device_id=machine, license_path=license_path)

    return _result(True, "ok", _reason_message("ok"), payload=payload, device_id=machine, license_path=license_path)


def _http_post_json(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any], str]:
    data = json.dumps(body).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Avoid being treated as default urllib bot traffic by edge protections.
            "User-Agent": "BurningLotusBot/1.0 (+https://burninglotusbot.com)",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            code = int(getattr(resp, "status", 200) or 200)
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        code = int(exc.code)
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
    except Exception as exc:
        raise RuntimeError(f"network_error:{exc}") from exc

    try:
        payload = parse_json_object(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    return code, payload, raw


def activateOnline(licenseKey: str) -> LicenseValidationResult:
    key = str(licenseKey or "").strip()
    machine = get_device_id()
    plat = _platform_code()
    if not key:
        return _result(False, "activation_failed", "License key is empty.", device_id=machine, license_path=str(storage.get_license_file_path()))

    body = {
        "licenseKey": key,
        "machineId": machine,
        "platform": plat,
    }
    try:
        status_code, response, raw_text = _http_post_json(ACTIVATE_URL, body)
    except Exception:
        logger.warning("license activation failed: network_error")
        return _result(False, "network_error", _reason_message("network_error"), device_id=machine, license_path=str(storage.get_license_file_path()))

    if status_code >= 400:
        reason = _derive_http_reason(status_code, response, raw_text)
        logger.warning("license activation failed: %s", reason)
        return _result(
            False,
            reason,
            f"{_reason_message(reason)} (http_{status_code})",
            device_id=machine,
            license_path=str(storage.get_license_file_path()),
        )

    ok = bool(response.get("ok"))
    token = str(response.get("token", "") or "").strip()
    exp_raw = response.get("exp")
    try:
        exp = int(exp_raw)
    except Exception:
        exp = 0
    if not ok or not token or exp <= 0:
        reason = str(response.get("code") or response.get("error") or "server_response_invalid")
        logger.warning("license activation failed: server_response_invalid")
        return _result(
            False,
            reason,
            _reason_message(reason),
            device_id=machine,
            license_path=str(storage.get_license_file_path()),
        )

    state = {
        "licenseKey": key,
        "token": token,
        "exp": exp,
        "platform": plat,
        "machineId": machine,
        "savedAt": _now(),
        "lastValidatedAt": _now(),
        "lastValidationCode": "ok",
    }
    local = verifyLocalToken(state=state, machine_id=machine, platform=plat)
    if not local.valid:
        return local

    try:
        storage.save_license_state(state)
    except Exception as exc:
        logger.warning("license activation failed: storage_error")
        return _result(False, "activation_failed", f"Could not save license state: {exc}", device_id=machine)

    return _result(True, "ok", _reason_message("ok"), payload=local.payload, device_id=machine, license_path=str(storage.get_license_file_path()))


def validateOnline(
    *,
    state: dict[str, Any] | None = None,
    machine_id: str | None = None,
    platform: str | None = None,
    now_unix: int | None = None,
) -> LicenseValidationResult:
    machine = machine_id or get_device_id()
    plat = platform or _platform_code()
    now_ts = int(now_unix if now_unix is not None else _now())
    license_path = str(storage.get_license_file_path())

    st = dict(state) if isinstance(state, dict) else loadLicenseState()
    token = str(st.get("token", "") or "").strip()
    license_key = str(st.get("licenseKey", "") or "").strip()
    if not token or not license_key:
        return _result(False, "not_activated", _reason_message("not_activated"), device_id=machine, license_path=license_path)

    body = {
        "licenseKey": license_key,
        "machineId": machine,
        "platform": plat,
        "token": token,
    }
    try:
        status_code, response, raw_text = _http_post_json(VALIDATE_URL, body)
    except Exception:
        logger.warning("license validation failed: network_error")
        return _result(False, "network_error", _reason_message("network_error"), device_id=machine, license_path=license_path)

    if status_code >= 400:
        reason = _derive_http_reason(status_code, response, raw_text)
        logger.warning("license validation failed: %s", reason)
        return _result(
            False,
            reason,
            f"{_reason_message(reason)} (http_{status_code})",
            device_id=machine,
            license_path=license_path,
        )

    ok = bool(response.get("ok"))
    if not ok:
        reason = str(response.get("code") or response.get("error") or "validation_failed").strip() or "validation_failed"
        logger.warning("license validation failed: %s", reason)
        return _result(False, reason, _reason_message(reason), device_id=machine, license_path=license_path)

    new_state = dict(st)
    returned_token = str(response.get("token", "") or "").strip()
    if returned_token:
        new_state["token"] = returned_token
    if response.get("exp") is not None:
        try:
            new_state["exp"] = int(response.get("exp"))
        except Exception:
            logger.warning("license validation failed: server_response_invalid")
            return _result(False, "server_response_invalid", _reason_message("server_response_invalid"), device_id=machine, license_path=license_path)
    new_state["platform"] = plat
    new_state["machineId"] = machine
    new_state["lastValidatedAt"] = now_ts
    new_state["lastValidationCode"] = "ok"

    local = verifyLocalToken(state=new_state, machine_id=machine, platform=plat, now_unix=now_ts)
    if not local.valid:
        logger.warning("license validation failed: %s", local.code)
        return local

    try:
        storage.save_license_state(new_state)
    except Exception as exc:
        logger.warning("license validation failed: storage_error")
        return _result(False, "validation_failed", f"Could not save license state: {exc}", device_id=machine, license_path=license_path)

    return _result(True, "ok", _reason_message("ok"), payload=local.payload, device_id=machine, license_path=license_path)


def ensureLicensedOrExit(
    prompt_license_key: Callable[[LicenseValidationResult | None], str] | None = None,
    on_locked: Callable[[LicenseValidationResult], None] | None = None,
) -> LicenseValidationResult:
    current = verifyLocalToken()
    if current.valid:
        return current

    if prompt_license_key is None:
        if on_locked is not None:
            on_locked(current)
        return current

    latest = current
    while True:
        entered = (prompt_license_key(latest) or "").strip()
        if not entered:
            locked = _result(
                False,
                "not_activated",
                "License activation cancelled.",
                device_id=latest.device_id,
                license_path=latest.license_path,
            )
            if on_locked is not None:
                on_locked(locked)
            return locked

        activated = activateOnline(entered)
        if activated.valid:
            return activated
        if on_locked is not None:
            try:
                on_locked(activated)
            except Exception:
                pass
        latest = activated


# Backward compatible wrappers used in existing UI/CLI code.
def load_license_state() -> dict[str, Any]:
    return loadLicenseState()


def validate_installed_license(*, now: Any | None = None, **_kwargs: Any) -> LicenseValidationResult:
    now_ts = None
    if now is not None:
        try:
            now_ts = int(getattr(now, "timestamp")())
        except Exception:
            now_ts = None
    return verifyLocalToken(now_unix=now_ts)


def activate_license_text(license_text: str, **_kwargs: Any) -> LicenseValidationResult:
    return activateOnline(license_text)


def require_license_or_block(on_block=None, **_kwargs: Any) -> LicenseValidationResult:
    result = verifyLocalToken()
    if result.valid:
        st = loadLicenseState()
        now_ts = _now()
        interval = _validate_interval_seconds()
        anchor_ts = _validation_anchor_ts(st)
        due = interval == 0 or anchor_ts <= 0 or (now_ts - anchor_ts) >= interval

        if due:
            online = validateOnline(state=st, now_unix=now_ts)
            if online.valid:
                return online
            if online.code == "network_error":
                grace = _validate_grace_seconds()
                if grace > 0 and anchor_ts > 0 and now_ts <= (anchor_ts + interval + grace):
                    return _result(
                        True,
                        "ok_offline_grace",
                        _reason_message("ok_offline_grace"),
                        payload=result.payload,
                        device_id=result.device_id,
                        license_path=result.license_path,
                    )
            result = online

    if not result.valid and on_block is not None:
        try:
            on_block(result)
        except Exception:
            pass
    return result


def format_license_details(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "-"
    lic = str(payload.get("lic") or "-")
    plat = str(payload.get("plat") or "-")
    mid = str(payload.get("mid") or "-")
    exp = payload.get("exp")
    try:
        exp_text = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(exp)))
    except Exception:
        exp_text = "-"
    return f"License: {lic} | Platform: {plat} | Machine: {mid[:8]}... | Expires: {exp_text}"


def validate_license_text(license_text: str, **_kwargs: Any) -> LicenseValidationResult:
    token = (license_text or "").strip()
    if not token:
        return _result(False, "not_activated", _reason_message("not_activated"))
    state = {
        "licenseKey": "",
        "token": token,
        "exp": 0,
        "platform": _platform_code(),
        "machineId": get_device_id(),
        "savedAt": _now(),
    }
    try:
        _payload_b64, _sig_raw, payload = _parse_token(token)
    except Exception:
        return _result(False, "token_format_invalid", _reason_message("token_format_invalid"))
    state["licenseKey"] = str(payload.get("lic", "") or "")
    state["exp"] = int(payload.get("exp") or 0)
    return verifyLocalToken(state=state)


def activate_emergency_code(*_args: Any, **_kwargs: Any) -> LicenseValidationResult:
    return _result(False, "activation_failed", "Emergency codes are no longer supported in this build.")
