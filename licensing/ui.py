from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .fingerprint import get_device_id
from .validator import (
    LicenseValidationResult,
    activate_license_text,
    format_license_details,
    validate_installed_license,
)


class LicenseDialog(tk.Toplevel):
    def __init__(self, parent, on_license_change=None):
        super().__init__(parent)
        self.title("License")
        self.resizable(False, False)
        self._on_license_change = on_license_change
        self._status_result: LicenseValidationResult | None = None

        parent.update_idletasks()
        width, height = 700, 560
        x = parent.winfo_x() + 20
        y = parent.winfo_y() + 20
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        root = ttk.Frame(self, padding=14)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(7, weight=1)

        ttk.Label(root, text="License Status", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.status_var = tk.StringVar(value="Checking...")
        ttk.Label(root, textvariable=self.status_var).grid(row=0, column=1, sticky="w", pady=(0, 6))

        ttk.Label(root, text="Details").grid(row=1, column=0, sticky="nw", pady=(0, 6))
        self.details_var = tk.StringVar(value="-")
        self.details_label = ttk.Label(root, textvariable=self.details_var, justify="left")
        self.details_label.grid(row=1, column=1, sticky="w", pady=(0, 6))

        ttk.Label(root, text="Device ID").grid(row=2, column=0, sticky="w", pady=(8, 4))
        self.device_var = tk.StringVar(value="")
        self.device_entry = ttk.Entry(root, textvariable=self.device_var, state="readonly")
        self.device_entry.grid(row=2, column=1, sticky="ew", pady=(8, 4))
        ttk.Button(root, text="Copy", command=self._copy_device_id).grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(8, 4))

        ttk.Label(root, text="License (string or file)").grid(row=3, column=0, sticky="w", pady=(10, 4))
        ttk.Button(root, text="Import license file", command=self._import_license_file).grid(
            row=3, column=1, sticky="w", pady=(10, 4)
        )

        self.license_text = tk.Text(root, height=14, wrap="word")
        self.license_text.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(0, 10))

        btn_row = ttk.Frame(root)
        btn_row.grid(row=8, column=0, columnspan=3, sticky="e")
        ttk.Button(btn_row, text="Activate", command=self._activate_license).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side="left")

        self._load_device_id()
        self._refresh_status()
        self.focus_force()

    def _load_device_id(self) -> None:
        try:
            self.device_var.set(get_device_id())
        except Exception as exc:
            self.device_var.set(f"<unavailable: {exc}>")

    def _copy_device_id(self) -> None:
        value = (self.device_var.get() or "").strip()
        if not value or value.startswith("<unavailable"):
            messagebox.showerror("License", "Device ID is not available.")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()

    def _import_license_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select license file",
            filetypes=[
                ("Burning Lotus License", "*.bllic"),
                ("JSON", "*.json"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as exc:
            messagebox.showerror("License", f"Could not read file:\n{exc}")
            return
        self.license_text.delete("1.0", tk.END)
        self.license_text.insert("1.0", content.strip())
        self.title(f"License - {os.path.basename(path)}")

    def _refresh_status(self) -> None:
        self._status_result = validate_installed_license()
        result = self._status_result
        if result.valid:
            self.status_var.set("Activated")
            self.details_var.set(format_license_details(result.payload))
            return
        self.status_var.set("Not activated")
        self.details_var.set(result.message)

    def _notify_change(self, result: LicenseValidationResult) -> None:
        if self._on_license_change is None:
            return
        try:
            self._on_license_change(result)
        except Exception:
            pass

    def _activate_license(self) -> None:
        raw = self.license_text.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showerror("License", "Please paste a license string or import a file.")
            return

        result = activate_license_text(raw)
        self._notify_change(result)
        self._refresh_status()
        if result.valid:
            messagebox.showinfo("License", "License activated successfully.")
            return
        messagebox.showerror("License", result.message)


def open_license_dialog(parent, on_license_change=None) -> LicenseDialog:
    return LicenseDialog(parent, on_license_change=on_license_change)
