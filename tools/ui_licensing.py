#!/usr/bin/env python
from __future__ import annotations

import base64
import hashlib
import json
import sys
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except Exception:
    serialization = None
    Ed25519PrivateKey = None
try:
    from nacl.signing import SigningKey as NaClSigningKey
except Exception:
    NaClSigningKey = None

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from licensing.codec import b64url_encode, canonical_json_bytes, canonical_json_dumps

PRODUCT_NAME = "BurningLotusBot"


def _decode_base64_loose(value: str) -> bytes:
    text = (value or "").strip()
    if not text:
        raise ValueError("Private key input is empty.")
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except Exception:
        return base64.b64decode(text + padding)


def _normalize_iso(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        datetime.fromisoformat(text[:-1] + "+00:00")
        return text
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _private_key_from_text(value: str):
    text = (value or "").strip()
    if not text:
        raise ValueError("Please paste a private key.")
    if text.startswith("-----BEGIN"):
        if serialization is None or Ed25519PrivateKey is None:
            raise ValueError("PEM private keys require the 'cryptography' package.")
        key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("PEM key is not an Ed25519 private key.")
        return key

    raw = _decode_base64_loose(text)
    if len(raw) == 64:
        raw = raw[:32]
    if len(raw) != 32:
        raise ValueError("Private key must decode to 32 bytes (or 64 bytes seed+pub).")
    if Ed25519PrivateKey is not None:
        return Ed25519PrivateKey.from_private_bytes(raw)
    if NaClSigningKey is not None:
        return NaClSigningKey(raw)
    raise ValueError("No Ed25519 backend available. Install 'cryptography' (recommended) or 'pynacl'.")


def _sign_payload(private_key, payload_bytes: bytes) -> bytes:
    if Ed25519PrivateKey is not None and isinstance(private_key, Ed25519PrivateKey):
        return private_key.sign(payload_bytes)
    if NaClSigningKey is not None and isinstance(private_key, NaClSigningKey):
        return bytes(private_key.sign(payload_bytes).signature)
    if hasattr(private_key, "sign"):
        signed = private_key.sign(payload_bytes)
        if isinstance(signed, bytes):
            return signed
        signature = getattr(signed, "signature", None)
        if isinstance(signature, bytes):
            return signature
    raise ValueError("Unsupported signing backend.")


def _hash_device_id(device_id: str) -> str:
    resolved = (device_id or "").strip()
    if not resolved:
        raise ValueError("Device ID is required.")
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()


def _build_license_json(
    *,
    private_key_text: str,
    customer_id: str,
    seat_index: int,
    device_id: str,
    expires_at: str,
    features_csv: str,
) -> tuple[str, str]:
    private_key = _private_key_from_text(private_key_text)
    customer = (customer_id or "").strip()
    if not customer:
        raise ValueError("Customer ID is required.")
    if seat_index not in (1, 2):
        raise ValueError("Seat index must be 1 or 2.")

    issued_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    expires_norm = _normalize_iso(expires_at)
    device_hash = _hash_device_id(device_id)

    payload: dict[str, object] = {
        "product": PRODUCT_NAME,
        "customer_id": customer,
        "seat_index": seat_index,
        "device_id_hash": device_hash,
        "issued_at": issued_at,
        "expires_at": expires_norm,
    }
    features = [x.strip() for x in (features_csv or "").split(",") if x.strip()]
    if features:
        payload["features"] = features

    payload_bytes = canonical_json_bytes(payload)
    signature = _sign_payload(private_key, payload_bytes)
    license_obj = {
        "payload": b64url_encode(payload_bytes),
        "sig": b64url_encode(signature),
    }
    return canonical_json_dumps(license_obj), device_hash


class LicensingToolUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BurningLotus License Generator")
        self.geometry("860x760")
        self.minsize(860, 760)

        self.customer_var = tk.StringVar()
        self.seat_var = tk.StringVar(value="1")
        self.device_var = tk.StringVar()
        self.device_hash_var = tk.StringVar(value="-")
        self.expires_var = tk.StringVar()
        self.features_var = tk.StringVar(value="full")
        self.output_path_var = tk.StringVar()

        self._build_ui()
        self._show_backend_warning_once()

    def _show_backend_warning_once(self) -> None:
        if Ed25519PrivateKey is not None or NaClSigningKey is not None:
            return
        self.after(
            120,
            lambda: messagebox.showwarning(
                "License Generator",
                "No Ed25519 backend found.\nInstall 'cryptography' (recommended) or 'pynacl'.",
            ),
        )

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(7, weight=1)
        root.rowconfigure(10, weight=1)

        ttk.Label(root, text="Customer ID").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(root, textvariable=self.customer_var).grid(row=0, column=1, sticky="ew", pady=(0, 6))

        ttk.Label(root, text="Seat Index").grid(row=1, column=0, sticky="w", pady=(0, 6))
        seat_combo = ttk.Combobox(root, values=["1", "2"], textvariable=self.seat_var, state="readonly", width=10)
        seat_combo.grid(row=1, column=1, sticky="w", pady=(0, 6))

        ttk.Label(root, text="Device ID").grid(row=2, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(root, textvariable=self.device_var).grid(row=2, column=1, sticky="ew", pady=(0, 6))
        ttk.Button(root, text="Update Hash", command=self._update_device_hash).grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(0, 6))

        ttk.Label(root, text="device_id_hash").grid(row=3, column=0, sticky="w", pady=(0, 6))
        ttk.Label(root, textvariable=self.device_hash_var).grid(row=3, column=1, sticky="w", pady=(0, 6))

        ttk.Label(root, text="Expires At (optional, ISO)").grid(row=4, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(root, textvariable=self.expires_var).grid(row=4, column=1, sticky="ew", pady=(0, 6))

        ttk.Label(root, text="Features (optional CSV)").grid(row=5, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(root, textvariable=self.features_var).grid(row=5, column=1, sticky="ew", pady=(0, 6))

        ttk.Label(root, text="Private Key (Base64 or PEM)").grid(row=6, column=0, sticky="nw", pady=(8, 6))
        self.private_key_text = tk.Text(root, height=12, wrap="word")
        self.private_key_text.grid(row=6, column=1, columnspan=2, sticky="nsew", pady=(8, 6))

        buttons = ttk.Frame(root)
        buttons.grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 6))
        ttk.Button(buttons, text="Generate License", command=self._generate).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Copy License String", command=self._copy_license).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(buttons, text="Save .bllic File", command=self._save_license).pack(side=tk.LEFT, padx=(8, 0))

        path_row = ttk.Frame(root)
        path_row.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        path_row.columnconfigure(1, weight=1)
        ttk.Label(path_row, text="Last File").grid(row=0, column=0, sticky="w")
        ttk.Entry(path_row, textvariable=self.output_path_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(root, text="License JSON (compact)").grid(row=10, column=0, sticky="nw", pady=(8, 6))
        self.output_text = tk.Text(root, height=10, wrap="word")
        self.output_text.grid(row=10, column=1, columnspan=2, sticky="nsew", pady=(8, 6))

    def _update_device_hash(self) -> None:
        try:
            device_hash = _hash_device_id(self.device_var.get())
        except Exception as exc:
            self.device_hash_var.set(f"error: {exc}")
            return
        self.device_hash_var.set(device_hash)

    def _generate(self) -> None:
        try:
            seat = int((self.seat_var.get() or "1").strip())
        except Exception:
            messagebox.showerror("License Generator", "Seat index must be 1 or 2.")
            return
        try:
            license_json, device_hash = _build_license_json(
                private_key_text=self.private_key_text.get("1.0", tk.END),
                customer_id=self.customer_var.get(),
                seat_index=seat,
                device_id=self.device_var.get(),
                expires_at=self.expires_var.get(),
                features_csv=self.features_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("License Generator", str(exc))
            return

        self.device_hash_var.set(device_hash)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert("1.0", license_json)
        messagebox.showinfo("License Generator", "License string generated.")

    def _copy_license(self) -> None:
        raw = self.output_text.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showerror("License Generator", "Generate a license first.")
            return
        self.clipboard_clear()
        self.clipboard_append(raw)
        self.update()
        messagebox.showinfo("License Generator", "License string copied to clipboard.")

    def _save_license(self) -> None:
        raw = self.output_text.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showerror("License Generator", "Generate a license first.")
            return
        try:
            json.loads(raw)
        except Exception:
            messagebox.showerror("License Generator", "Output is not valid JSON.")
            return

        customer = (self.customer_var.get() or "customer").strip() or "customer"
        seat = (self.seat_var.get() or "1").strip() or "1"
        initial_name = f"{customer}_seat{seat}.bllic"
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".bllic",
            initialfile=initial_name,
            filetypes=[
                ("Burning Lotus License", "*.bllic"),
                ("JSON", "*.json"),
                ("All files", "*.*"),
            ],
            title="Save license file",
        )
        if not path:
            return
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw + "\n", encoding="utf-8")
        self.output_path_var.set(str(target))
        messagebox.showinfo("License Generator", f"Saved:\n{target}")


def main() -> int:
    app = LicensingToolUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
