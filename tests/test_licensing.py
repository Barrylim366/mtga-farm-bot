import hashlib
import os
import re
import unittest
from datetime import datetime, timezone
from unittest import mock

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    CRYPTO_AVAILABLE = True
except Exception:
    serialization = None
    Ed25519PrivateKey = None
    CRYPTO_AVAILABLE = False

from licensing import codec, fingerprint, storage, validator


class LicensingTests(unittest.TestCase):
    @unittest.skipUnless(CRYPTO_AVAILABLE, "cryptography is not installed")
    def test_canonical_payload_signature_verify(self):
        device_id = "A" * 32
        private_key = Ed25519PrivateKey.generate()
        public_key_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

        payload = {
            "product": "BurningLotusBot",
            "customer_id": "cust-123",
            "seat_index": 1,
            "device_id_hash": hashlib.sha256(device_id.encode("utf-8")).hexdigest(),
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2027-01-01T00:00:00Z",
            "features": ["full"],
        }
        payload_bytes = codec.canonical_json_bytes(payload)
        license_obj = {
            "payload": codec.b64url_encode(payload_bytes),
            "sig": codec.b64url_encode(private_key.sign(payload_bytes)),
        }

        result = validator.validate_license_object(
            license_obj,
            current_device_id=device_id,
            public_key_bytes=public_key_raw,
            now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        self.assertTrue(result.valid, result.message)

    def test_fingerprint_format(self):
        with mock.patch("licensing.fingerprint.get_device_fingerprint_raw", return_value="example-raw-fingerprint"):
            device_id = fingerprint.get_device_id(length=32)
        self.assertEqual(32, len(device_id))
        self.assertRegex(device_id, r"^[A-Z2-7]{32}$")

    def test_storage_path_windows(self):
        with mock.patch("licensing.storage.os.name", "nt"), mock.patch("licensing.storage.sys.platform", "win32"), mock.patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"}, clear=False):
            path = storage.get_license_file_path()
        normalized = str(path).replace("/", "\\")
        self.assertIn("BurningLotusBot", normalized)
        self.assertTrue(normalized.endswith("\\license.bllic"))

if __name__ == "__main__":
    unittest.main()
