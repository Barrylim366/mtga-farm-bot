from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .fingerprint import get_device_id
from .validator import LicenseValidationResult, activateOnline, format_license_details, verifyLocalToken


class LicenseDialog(tk.Toplevel):
    def __init__(self, parent, on_license_change=None):
        super().__init__(parent)
        self.title("License")
        self.resizable(False, False)
        self._on_license_change = on_license_change
        self._status_result: LicenseValidationResult | None = None

        self.columnconfigure(0, weight=1)
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="License Status").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.status_var = tk.StringVar(value="Checking...")
        ttk.Label(root, textvariable=self.status_var).grid(row=0, column=1, sticky="w", pady=(0, 8))

        ttk.Label(root, text="Details").grid(row=1, column=0, sticky="nw", pady=(0, 8))
        self.details_var = tk.StringVar(value="-")
        ttk.Label(root, textvariable=self.details_var, justify="left", wraplength=420).grid(row=1, column=1, sticky="w", pady=(0, 8))

        ttk.Label(root, text="Machine ID").grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.device_var = tk.StringVar(value="")
        ttk.Entry(root, textvariable=self.device_var, state="readonly").grid(row=2, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(root, text="License Key").grid(row=3, column=0, sticky="w", pady=(6, 6))
        self.license_key_var = tk.StringVar(value="")
        ttk.Entry(root, textvariable=self.license_key_var).grid(row=3, column=1, sticky="ew", pady=(6, 6))

        btn_row = ttk.Frame(root)
        btn_row.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btn_row, text="Activate", command=self._activate_license).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Refresh", command=self._refresh_status).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side="left")

        self._load_device_id()
        self._refresh_status()
        self.focus_force()

    def _load_device_id(self) -> None:
        try:
            self.device_var.set(get_device_id())
        except Exception as exc:
            self.device_var.set(f"<unavailable: {exc}>")

    def _refresh_status(self) -> None:
        result = verifyLocalToken()
        self._status_result = result
        if result.valid:
            self.status_var.set("Activated")
            self.details_var.set(format_license_details(result.payload))
        else:
            self.status_var.set("Not activated")
            self.details_var.set(f"{result.code}: {result.message}")

    def _notify_change(self, result: LicenseValidationResult) -> None:
        if self._on_license_change is None:
            return
        try:
            self._on_license_change(result)
        except Exception:
            pass

    def _activate_license(self) -> None:
        key = (self.license_key_var.get() or "").strip()
        if not key:
            messagebox.showerror("License", "Please enter a license key.")
            return

        result = activateOnline(key)
        self._notify_change(result)
        self._refresh_status()
        if result.valid:
            messagebox.showinfo("License", "License activated successfully.")
            self.license_key_var.set("")
            return
        messagebox.showerror("License", f"{result.code}: {result.message}")


def open_license_dialog(parent, on_license_change=None) -> LicenseDialog:
    return LicenseDialog(parent, on_license_change=on_license_change)
