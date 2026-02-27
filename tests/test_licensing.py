import json
import os
import time
import unittest
from unittest import mock

from licensing import codec, storage, validator

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    CRYPTO_AVAILABLE = True
except Exception:
    hashes = None
    ec = None
    CRYPTO_AVAILABLE = False


def _b64u(raw: bytes) -> str:
    return codec.b64url_encode(raw)


@unittest.skipUnless(CRYPTO_AVAILABLE, "cryptography is not installed")
class LicensingTests(unittest.TestCase):
    def _make_keypair(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key().public_numbers()
        x = int(public_key.x).to_bytes(32, "big")
        y = int(public_key.y).to_bytes(32, "big")
        jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": _b64u(x),
            "y": _b64u(y),
        }
        return private_key, jwk

    def _make_token(self, private_key, payload: dict) -> str:
        payload_b64 = _b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = private_key.sign(payload_b64.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return f"{payload_b64}.{_b64u(signature)}"

    def test_base64url_roundtrip(self):
        raw = b"hello-license-token"
        encoded = codec.b64url_encode(raw)
        decoded = codec.b64url_decode(encoded)
        self.assertEqual(raw, decoded)

    def test_verify_local_token_ok(self):
        private_key, jwk = self._make_keypair()
        now = int(time.time())
        payload = {
            "lic": "ABC-123-XYZ",
            "mid": "MID-TEST-1",
            "plat": "linux",
            "iat": now - 10,
            "exp": now + 3600,
        }
        token = self._make_token(private_key, payload)
        state = {
            "licenseKey": payload["lic"],
            "token": token,
            "exp": payload["exp"],
            "platform": payload["plat"],
            "machineId": payload["mid"],
            "savedAt": now,
        }

        with mock.patch.dict(os.environ, {"BLB_PUBLIC_JWK": json.dumps(jwk)}, clear=False):
            result = validator.verifyLocalToken(
                state=state,
                machine_id=payload["mid"],
                platform=payload["plat"],
                now_unix=now,
            )
        self.assertTrue(result.valid, msg=result.code)

    def test_verify_reason_codes(self):
        private_key, jwk = self._make_keypair()
        now = int(time.time())
        payload = {
            "lic": "KEY-1",
            "mid": "MID-1",
            "plat": "linux",
            "iat": now - 20,
            "exp": now + 30,
        }
        token = self._make_token(private_key, payload)
        state = {
            "licenseKey": payload["lic"],
            "token": token,
            "exp": payload["exp"],
            "platform": payload["plat"],
            "machineId": payload["mid"],
            "savedAt": now,
        }

        with mock.patch.dict(os.environ, {"BLB_PUBLIC_JWK": json.dumps(jwk)}, clear=False):
            self.assertEqual(
                "machine_mismatch",
                validator.verifyLocalToken(state=state, machine_id="MID-OTHER", platform="linux", now_unix=now).code,
            )
            self.assertEqual(
                "platform_mismatch",
                validator.verifyLocalToken(state=state, machine_id="MID-1", platform="win", now_unix=now).code,
            )
            self.assertEqual(
                "token_expired",
                validator.verifyLocalToken(state=state, machine_id="MID-1", platform="linux", now_unix=now + 999).code,
            )

    def test_token_format_invalid(self):
        state = {
            "licenseKey": "L1",
            "token": "not-a-token",
            "exp": 0,
            "platform": "linux",
            "machineId": "MID",
            "savedAt": 0,
        }
        result = validator.verifyLocalToken(state=state, machine_id="MID", platform="linux", now_unix=0)
        self.assertEqual("token_format_invalid", result.code)

    def test_storage_path_windows(self):
        with mock.patch("licensing.storage.sys.platform", "win32"), mock.patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"}, clear=False):
            path = storage.get_license_file_path()
        normalized = str(path).replace("/", "\\")
        self.assertIn("BurningLotus", normalized)
        self.assertTrue(normalized.endswith("\\license.json"))

    def test_require_license_revalidates_online_when_due(self):
        private_key, jwk = self._make_keypair()
        now = int(time.time())
        payload = {
            "lic": "KEY-ONLINE-OK",
            "mid": "MID-TEST-ONLINE",
            "plat": "linux",
            "iat": now - 20,
            "exp": now + 3600,
        }
        token = self._make_token(private_key, payload)
        state = {
            "licenseKey": payload["lic"],
            "token": token,
            "exp": payload["exp"],
            "platform": payload["plat"],
            "machineId": payload["mid"],
            "savedAt": now - 10,
            "lastValidatedAt": now - 10,
        }

        with (
            mock.patch.dict(
                os.environ,
                {
                    "BLB_PUBLIC_JWK": json.dumps(jwk),
                    "BLB_LICENSE_VALIDATE_INTERVAL_SECONDS": "0",
                    "BLB_LICENSE_VALIDATE_GRACE_SECONDS": "0",
                },
                clear=False,
            ),
            mock.patch("licensing.validator.get_device_id", return_value=payload["mid"]),
            mock.patch("licensing.validator._platform_code", return_value=payload["plat"]),
            mock.patch("licensing.validator.loadLicenseState", return_value=state),
            mock.patch("licensing.validator._http_post_json", return_value=(200, {"ok": True, "exp": payload["exp"]}, "{\"ok\":true}")) as http_mock,
            mock.patch("licensing.storage.save_license_state") as save_mock,
        ):
            result = validator.require_license_or_block()

        self.assertTrue(result.valid, msg=result.code)
        self.assertEqual("ok", result.code)
        self.assertEqual(validator.VALIDATE_URL, http_mock.call_args[0][0])
        self.assertTrue(save_mock.called)
        saved_state = save_mock.call_args[0][0]
        self.assertEqual("ok", saved_state.get("lastValidationCode"))
        self.assertGreater(int(saved_state.get("lastValidatedAt", 0)), 0)

    def test_require_license_revalidation_denied_by_server(self):
        private_key, jwk = self._make_keypair()
        now = int(time.time())
        payload = {
            "lic": "KEY-REVOKED",
            "mid": "MID-TEST-REV",
            "plat": "linux",
            "iat": now - 20,
            "exp": now + 3600,
        }
        token = self._make_token(private_key, payload)
        state = {
            "licenseKey": payload["lic"],
            "token": token,
            "exp": payload["exp"],
            "platform": payload["plat"],
            "machineId": payload["mid"],
            "savedAt": now - 10,
            "lastValidatedAt": now - 10,
        }

        with (
            mock.patch.dict(
                os.environ,
                {
                    "BLB_PUBLIC_JWK": json.dumps(jwk),
                    "BLB_LICENSE_VALIDATE_INTERVAL_SECONDS": "0",
                    "BLB_LICENSE_VALIDATE_GRACE_SECONDS": "0",
                },
                clear=False,
            ),
            mock.patch("licensing.validator.get_device_id", return_value=payload["mid"]),
            mock.patch("licensing.validator._platform_code", return_value=payload["plat"]),
            mock.patch("licensing.validator.loadLicenseState", return_value=state),
            mock.patch("licensing.validator._http_post_json", return_value=(403, {"code": "license_revoked"}, "{\"code\":\"license_revoked\"}")),
        ):
            result = validator.require_license_or_block()

        self.assertFalse(result.valid)
        self.assertEqual("license_revoked", result.code)

    def test_require_license_network_error_uses_offline_grace(self):
        private_key, jwk = self._make_keypair()
        now = int(time.time())
        payload = {
            "lic": "KEY-GRACE",
            "mid": "MID-TEST-GRACE",
            "plat": "linux",
            "iat": now - 20,
            "exp": now + 3600,
        }
        token = self._make_token(private_key, payload)
        state = {
            "licenseKey": payload["lic"],
            "token": token,
            "exp": payload["exp"],
            "platform": payload["plat"],
            "machineId": payload["mid"],
            "savedAt": now - 40,
            "lastValidatedAt": now - 40,
        }

        with (
            mock.patch.dict(
                os.environ,
                {
                    "BLB_PUBLIC_JWK": json.dumps(jwk),
                    "BLB_LICENSE_VALIDATE_INTERVAL_SECONDS": "0",
                    "BLB_LICENSE_VALIDATE_GRACE_SECONDS": "300",
                },
                clear=False,
            ),
            mock.patch("licensing.validator.get_device_id", return_value=payload["mid"]),
            mock.patch("licensing.validator._platform_code", return_value=payload["plat"]),
            mock.patch("licensing.validator.loadLicenseState", return_value=state),
            mock.patch("licensing.validator._http_post_json", side_effect=RuntimeError("network down")),
        ):
            result = validator.require_license_or_block()

        self.assertTrue(result.valid)
        self.assertEqual("ok_offline_grace", result.code)


if __name__ == "__main__":
    unittest.main()
