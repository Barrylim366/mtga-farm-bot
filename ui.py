import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from datetime import datetime
import os
import sys
import time
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk
import json
import threading
from Controller.Utilities.input_controller import InputControllerError, create_input_controller

# Import bot components
from Controller.MTGAController.Controller import Controller
from AI.DummyAI import DummyAI
from Game import Game


def _default_player_log_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(
        home,
        ".local",
        "share",
        "Steam",
        "steamapps",
        "compatdata",
        "2141910",
        "pfx",
        "drive_c",
        "users",
        "steamuser",
        "AppData",
        "LocalLow",
        "Wizards Of The Coast",
        "MTGA",
        "Player.log",
    )


def _app_root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    return os.path.abspath(os.path.dirname(__file__))


def _app_path(*parts: str) -> str:
    return os.path.join(_app_root_dir(), *parts)


def _submenu_palette():
    return {
        "bg": "#0F1115",
        "surface": "#151A21",
        "surface_alt": "#1B2230",
        "surface_hover": "#253041",
        "border": "#242B36",
        "text": "#E7EAF0",
        "text_muted": "#9AA3B2",
        "success": "#8FE0B0",
        "danger_bg": "#3A2025",
        "danger_hover": "#4A262C",
    }


def _apply_dark_combobox_style(window):
    c = _submenu_palette()
    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "Dark.TCombobox",
        fieldbackground=c["surface_alt"],
        background=c["surface_alt"],
        foreground=c["text"],
        bordercolor=c["border"],
        lightcolor=c["border"],
        darkcolor=c["border"],
        arrowcolor=c["text"],
    )
    style.map(
        "Dark.TCombobox",
        fieldbackground=[("readonly", c["surface_alt"])],
        background=[("readonly", c["surface_alt"])],
        foreground=[("readonly", c["text"])],
    )


def _apply_submenu_theme(window):
    c = _submenu_palette()
    _apply_dark_combobox_style(window)
    style = ttk.Style(window)
    style.configure(
        "Submenu.TButton",
        font=("Segoe UI", 10),
        padding=(12, 4),
        foreground=c["text"],
        background=c["surface_alt"],
        borderwidth=0,
        relief="flat",
    )
    style.map(
        "Submenu.TButton",
        background=[("pressed", "#323232"), ("active", "#444444"), ("disabled", "#26364F")],
        foreground=[("disabled", c["text_muted"])],
    )
    style.configure(
        "SubmenuDanger.TButton",
        font=("Segoe UI", 10),
        padding=(12, 4),
        foreground=c["text"],
        background=c["danger_bg"],
        borderwidth=0,
        relief="flat",
    )
    style.map(
        "SubmenuDanger.TButton",
        background=[("pressed", "#311B20"), ("active", c["danger_hover"]), ("disabled", "#2A1E20")],
        foreground=[("disabled", c["text_muted"])],
    )
    try:
        window.configure(bg=c["bg"])
    except Exception:
        pass

    bg_map = {
        "#2b2b2b": c["surface"],
        "#3b3b3b": c["surface_alt"],
        "#3a3a3a": c["surface_alt"],
        "#444444": c["surface_hover"],
        "#4a4a4a": c["border"],
        "#1e1e1e": c["bg"],
        "#111111": c["bg"],
    }
    fg_map = {
        "white": c["text"],
        "#ffffff": c["text"],
        "#aaaaaa": c["text_muted"],
        "#dddddd": c["text"],
        "#00ff00": c["success"],
        "#1e1e1e": c["bg"],
    }

    stack = [window]
    while stack:
        widget = stack.pop()
        try:
            stack.extend(widget.winfo_children())
        except Exception:
            pass

        if isinstance(widget, ttk.Combobox):
            try:
                widget.configure(style="Dark.TCombobox")
            except Exception:
                pass
            continue

        if isinstance(widget, ttk.Button):
            try:
                label = str(widget.cget("text") or "").lower()
                current_style = str(widget.cget("style") or "").strip()
                if "delete" in label or "stop" in label:
                    if not current_style or current_style == "TButton":
                        widget.configure(style="SubmenuDanger.TButton")
                else:
                    if not current_style or current_style == "TButton":
                        widget.configure(style="Submenu.TButton")
            except Exception:
                pass
            continue

        if isinstance(widget, tk.Button):
            try:
                label = str(widget.cget("text") or "").lower()
                if "delete" in label or "stop" in label:
                    widget.configure(
                        bg=c["danger_bg"],
                        fg=c["text"],
                        activebackground=c["danger_hover"],
                        activeforeground=c["text"],
                        relief=tk.FLAT,
                    )
                else:
                    widget.configure(
                        bg=c["surface_alt"],
                        fg=c["text"],
                        activebackground=c["surface_hover"],
                        activeforeground=c["text"],
                        relief=tk.FLAT,
                    )
            except Exception:
                pass

        if isinstance(widget, tk.Entry):
            try:
                widget.configure(
                    bg=c["surface_alt"],
                    fg=c["text"],
                    insertbackground=c["text"],
                    relief=tk.FLAT,
                )
            except Exception:
                pass

        if isinstance(widget, tk.Text):
            try:
                widget.configure(
                    bg=c["bg"],
                    fg=c["text"],
                    insertbackground=c["text"],
                )
            except Exception:
                pass

        if isinstance(widget, tk.Checkbutton):
            try:
                widget.configure(
                    bg=c["surface"],
                    fg=c["text"],
                    activebackground=c["surface"],
                    activeforeground=c["text"],
                    selectcolor=c["surface"],
                )
            except Exception:
                pass

        for opt in ("bg", "background", "activebackground", "highlightbackground"):
            try:
                cur = str(widget.cget(opt)).lower()
            except Exception:
                continue
            new_val = bg_map.get(cur)
            if new_val:
                try:
                    widget.configure(**{opt: new_val})
                except Exception:
                    pass

        for opt in ("fg", "foreground", "activeforeground", "insertbackground", "highlightcolor"):
            try:
                cur = str(widget.cget(opt)).lower()
            except Exception:
                continue
            new_val = fg_map.get(cur)
            if new_val:
                try:
                    widget.configure(**{opt: new_val})
                except Exception:
                    pass

class CalibrationWindow(tk.Toplevel):
    """Calibration submenu window"""

    def __init__(self, parent, config_manager):
        super().__init__(parent)
        self.parent = parent
        self.config_manager = config_manager
        self.title("Calibration")
        # Increased height to fit calibration + test controls
        self.geometry("500x420")
        self.resizable(True, False)
        self.configure(bg="#2b2b2b")

        self.is_calibrating = False
        self.mouse_listener = None
        self.keyboard_listener = None
        self._pynput = None
        self.current_x = 0
        self.current_y = 0

        self._setup_ui()
        _apply_submenu_theme(self)
        self._update_calibration_capabilities()

    def _setup_ui(self):
        # Main frame with padding
        main_frame = tk.Frame(self, bg="#2b2b2b", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Row 1: Dropdown and Calibrate button
        row1 = tk.Frame(main_frame, bg="#2b2b2b")
        row1.pack(fill=tk.X, pady=(0, 20))

        # Label
        label = tk.Label(row1, text="Select Button:", bg="#2b2b2b", fg="white", font=("Segoe UI", 10))
        label.pack(side=tk.LEFT, padx=(0, 10))

        # Dropdown for button selection
        self.button_options = [
            "keep_hand",
            "queue_button",
            "next",
            "concede",
            "attack_all",
            "opponent_avatar",
            "hand_scan_p1",
            "hand_scan_p2",
            "assign_damage_done",
            "log_out_btn",
            "log_out_ok_btn"
        ]

        self.selected_button = tk.StringVar(value=self.button_options[0])
        self.dropdown = ttk.Combobox(row1, textvariable=self.selected_button,
                                      values=self.button_options, state="readonly", width=20)
        self.dropdown.pack(side=tk.LEFT, padx=(0, 10))

        # Calibrate button
        self.calibrate_btn = ttk.Button(row1, text="Calibrate", command=self._start_calibration)
        self.calibrate_btn.pack(side=tk.LEFT)

        # Row 2: Coordinate display
        coord_frame = tk.Frame(main_frame, bg="#3b3b3b", padx=15, pady=15)
        coord_frame.pack(fill=tk.X, pady=(0, 20))

        # X coordinate
        x_frame = tk.Frame(coord_frame, bg="#3b3b3b")
        x_frame.pack(fill=tk.X, pady=5)

        x_label = tk.Label(x_frame, text="X:", bg="#3b3b3b", fg="white",
                          font=("Segoe UI", 12, "bold"), width=3, anchor="e")
        x_label.pack(side=tk.LEFT)

        self.x_value = tk.Label(x_frame, text="0", bg="#3b3b3b", fg="#00ff00",
                                font=("Consolas", 14), width=10, anchor="w")
        self.x_value.pack(side=tk.LEFT, padx=(10, 0))

        # Y coordinate
        y_frame = tk.Frame(coord_frame, bg="#3b3b3b")
        y_frame.pack(fill=tk.X, pady=5)

        y_label = tk.Label(y_frame, text="Y:", bg="#3b3b3b", fg="white",
                          font=("Segoe UI", 12, "bold"), width=3, anchor="e")
        y_label.pack(side=tk.LEFT)

        self.y_value = tk.Label(y_frame, text="0", bg="#3b3b3b", fg="#00ff00",
                                font=("Consolas", 14), width=10, anchor="w")
        self.y_value.pack(side=tk.LEFT, padx=(10, 0))

        # Row 3: Instructions
        self.instruction_label = tk.Label(main_frame, text="Select a button and click 'Calibrate'",
                                          bg="#2b2b2b", fg="#aaaaaa", font=("Segoe UI", 9))
        self.instruction_label.pack(pady=(0, 15))

        # Row 4: Saved Buttons button
        self.saved_btn = ttk.Button(main_frame, text="Saved Buttons", command=self._show_saved_buttons)
        self.saved_btn.pack()

        back_btn = ttk.Button(
            main_frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.pack(pady=(10, 0))

        # Row 5: Test saved coordinate click
        test_frame = tk.Frame(main_frame, bg="#2b2b2b")
        test_frame.pack(fill=tk.X, pady=(20, 0))

        test_label = tk.Label(test_frame, text="Test Button:", bg="#2b2b2b", fg="white", font=("Segoe UI", 10))
        test_label.pack(side=tk.LEFT, padx=(0, 10))

        self.test_button_var = tk.StringVar(value=self.button_options[0])
        self.test_dropdown = ttk.Combobox(
            test_frame, textvariable=self.test_button_var, values=self.button_options, state="readonly", width=20
        )
        self.test_dropdown.pack(side=tk.LEFT, padx=(0, 10))

        self.test_btn = ttk.Button(
            test_frame,
            text="Test Click",
            command=self._test_saved_click,
        )
        self.test_btn.pack(side=tk.LEFT)

    def _update_calibration_capabilities(self):
        # Global calibration requires pynput.
        can_use_pynput = True
        try:
            import pynput  # noqa: F401
        except Exception:
            can_use_pynput = False

        if not can_use_pynput:
            self.calibrate_btn.config(state=tk.DISABLED)
            self.instruction_label.config(
                text="Kalibrierung braucht 'pynput'. Bitte installieren.",
                fg="#ff6666",
            )

        # Enable test click only when the configured backend can be initialized.
        try:
            backend = self.config_manager.get_input_backend()
            screen_bounds = self.config_manager.get_screen_bounds()
            input_controller = create_input_controller(backend)
            input_controller.configure_screen_bounds(screen_bounds)
            self.test_btn.config(state=tk.NORMAL)
        except Exception:
            self.test_btn.config(state=tk.DISABLED)

    def _start_calibration(self):
        if self.is_calibrating:
            self._stop_calibration()
            return

        self.is_calibrating = True
        self.calibrate_btn.config(text="Stop", style="SubmenuDanger.TButton")
        self.instruction_label.config(text="Move mouse to target. Press ENTER to save.", fg="#ffff00")

        try:
            if self._pynput is None:
                from pynput import mouse, keyboard
                self._pynput = (mouse, keyboard)

            mouse, keyboard = self._pynput
            self.mouse_listener = mouse.Listener(on_move=self._on_mouse_move)
            self.mouse_listener.start()
            self.keyboard_listener = keyboard.Listener(on_press=self._on_key_press)
            self.keyboard_listener.start()
        except Exception as e:
            self._stop_calibration()
            self.instruction_label.config(
                text="Kalibrierung per Live-Tracking nicht verf++gbar.",
                fg="#ffcc66",
            )

    def _stop_calibration(self):
        self.is_calibrating = False
        self.calibrate_btn.config(text="Calibrate", style="Submenu.TButton")
        self.instruction_label.config(text="Select a button and click 'Calibrate'", fg="#aaaaaa")

        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None

    def _on_mouse_move(self, x, y):
        self.current_x = x
        self.current_y = y
        # Update UI in main thread
        self.after(0, self._update_coordinates)

    def _update_coordinates(self):
        self.x_value.config(text=str(self.current_x))
        self.y_value.config(text=str(self.current_y))

    def _on_key_press(self, key):
        if self._pynput is None:
            return
        _, keyboard = self._pynput
        if key == keyboard.Key.enter and self.is_calibrating:
            self._save_coordinates()

    def _save_coordinates(self):
        button_name = self.selected_button.get()
        self.config_manager.save_coordinate(button_name, self.current_x, self.current_y)
        self._stop_calibration()
        self.instruction_label.config(text=f"Saved {button_name}: ({self.current_x}, {self.current_y})", fg="#00ff00")

    def _test_saved_click(self):
        button_name = self.test_button_var.get()
        coords = self.config_manager.get_all_coordinates()
        coord = coords.get(button_name)
        if not isinstance(coord, dict) or "x" not in coord or "y" not in coord:
            self.instruction_label.config(text=f"Kein gespeicherter Punkt f++r '{button_name}'.", fg="#ffcc66")
            return

        x, y = int(coord["x"]), int(coord["y"])

        try:
            backend = self.config_manager.get_input_backend()
            screen_bounds = self.config_manager.get_screen_bounds()
            input_controller = create_input_controller(backend)
            input_controller.configure_screen_bounds(screen_bounds)
            input_controller.move_abs(x, y)
            input_controller.left_click(1)
            self.instruction_label.config(text=f"Test-Klick: {button_name} ({x}, {y})", fg="#00ff00")
        except InputControllerError as e:
            self.instruction_label.config(text=f"Test fehlgeschlagen: {e}", fg="#ff6666")
        except Exception as e:
            self.instruction_label.config(text=f"Test fehlgeschlagen: {e}", fg="#ff6666")

    def _show_saved_buttons(self):
        SavedButtonsWindow(self, self.config_manager)

    def destroy(self):
        self._stop_calibration()
        super().destroy()


class SavedButtonsWindow(tk.Toplevel):
    """Window showing all saved button coordinates"""

    def __init__(self, parent, config_manager):
        super().__init__(parent)
        self.config_manager = config_manager
        self.title("Saved Buttons")
        self.geometry("380x500")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")

        self._setup_ui()
        _apply_submenu_theme(self)

    def _setup_ui(self):
        # Main frame
        main_frame = tk.Frame(self, bg="#2b2b2b", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        title = tk.Label(main_frame, text="Calibrated Buttons", bg="#2b2b2b", fg="white",
                        font=("Segoe UI", 12, "bold"))
        title.pack(pady=(0, 15))

        # Scrollable list frame
        list_frame = tk.Frame(main_frame, bg="#3b3b3b")
        list_frame.pack(fill=tk.BOTH, expand=True)

        # Canvas with scrollbar
        canvas = tk.Canvas(list_frame, bg="#3b3b3b", highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#3b3b3b")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Get saved coordinates
        coords = self.config_manager.get_all_coordinates()

        if not coords:
            no_data = tk.Label(scrollable_frame, text="No buttons calibrated yet",
                              bg="#3b3b3b", fg="#aaaaaa", font=("Segoe UI", 10))
            no_data.pack(pady=20)
        else:
            for button_name in sorted(coords.keys()):
                coord = coords[button_name]
                item_frame = tk.Frame(scrollable_frame, bg="#3b3b3b", padx=10, pady=8)
                item_frame.pack(fill=tk.X)

                # Button name
                name_label = tk.Label(item_frame, text=button_name, bg="#3b3b3b", fg="white",
                                     font=("Segoe UI", 10, "bold"), anchor="w", width=15)
                name_label.pack(side=tk.LEFT)

                # Coordinates
                if isinstance(coord, dict):
                    coord_text = f"({coord.get('x', 0)}, {coord.get('y', 0)})"
                else:
                    coord_text = str(coord)
                coord_label = tk.Label(item_frame, text=coord_text, bg="#3b3b3b", fg="#00ff00",
                                       font=("Consolas", 10), anchor="e")
                coord_label.pack(side=tk.RIGHT)

                # Separator
                sep = tk.Frame(scrollable_frame, bg="#4a4a4a", height=1)
                sep.pack(fill=tk.X, padx=5)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        back_btn = ttk.Button(
            main_frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.pack(pady=(10, 0))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        back_btn = ttk.Button(
            main_frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.pack(pady=(10, 0))


class ConfigManager:
    """Manages loading and saving of calibration configuration"""

    def __init__(self, config_path="calibration_config.json"):
        self.config_path = config_path if os.path.isabs(config_path) else _app_path(config_path)
        self.config = self._load_config()

    def _detect_player_log_path(self) -> str:
        candidates: list[str] = []
        home = os.path.expanduser("~")

        steam_bases = [
            os.path.join(home, ".local", "share", "Steam"),
            os.path.join(home, ".steam", "steam"),
            os.path.join(home, ".steam", "root"),
            os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
        ]

        for base in steam_bases:
            compat = os.path.join(base, "steamapps", "compatdata")
            if not os.path.isdir(compat):
                continue
            for root, _dirs, files in os.walk(compat):
                if "Player.log" not in files:
                    continue
                full = os.path.join(root, "Player.log")
                if "Wizards Of The Coast/MTGA" in full:
                    candidates.append(full)

        if not candidates:
            return ""

        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return candidates[0]

    def _load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    loaded = json.load(f)
                return self._ensure_defaults(loaded)
            except (json.JSONDecodeError, IOError):
                return self._default_config()
        return self._default_config()

    def _default_config(self):
        detected_log = self._detect_player_log_path()
        return {
            "log_path": detected_log or _default_player_log_path(),
            "screen_bounds": [[0, 0], [2560, 1440]],
            "input_backend": "pynput",
            "account_switch_minutes": 0,
            "managed_accounts": [],
            "account_cycle_index": 0,
            "account_play_order": [],
            "click_targets": {
                "keep_hand": {"x": 1876, "y": 1060},
                "queue_button": {"x": 2485, "y": 1194},
                "next": {"x": 2546, "y": 1137},
                "concede": {"x": 1714, "y": 814},
                "attack_all": {"x": 2529, "y": 1131},
                "opponent_avatar": {"x": 1720, "y": 295},
                "assign_damage_done": {"x": 1280, "y": 720},
                "hand_scan_points": {
                    "p1": {"x": 994, "y": 1255},
                    "p2": {"x": 2421, "y": 1253}
                }
            }
        }

    def _ensure_defaults(self, config):
        defaults = self._default_config()

        def _merge(target, source):
            for key, value in source.items():
                if key not in target:
                    target[key] = value
                elif isinstance(value, dict) and isinstance(target.get(key), dict):
                    _merge(target[key], value)

        _merge(config, defaults)
        # Remove deprecated click targets if present
        try:
            click_targets = config.get("click_targets", {})
            if isinstance(click_targets, dict) and "options_btn" in click_targets:
                click_targets.pop("options_btn", None)
            if isinstance(click_targets, dict) and "log_in_btn" in click_targets:
                click_targets.pop("log_in_btn", None)
        except Exception:
            pass
        return config

    def _save_config(self):
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=4)

    def save_coordinate(self, button_name, x, y):
        if button_name in ["hand_scan_p1", "hand_scan_p2"]:
            # Handle hand scan points specially
            if "hand_scan_points" not in self.config["click_targets"]:
                self.config["click_targets"]["hand_scan_points"] = {}
            key = "p1" if button_name == "hand_scan_p1" else "p2"
            self.config["click_targets"]["hand_scan_points"][key] = {"x": x, "y": y}
        else:
            self.config["click_targets"][button_name] = {"x": x, "y": y}
        self._save_config()

    def get_all_coordinates(self):
        coords = {}
        for key, value in self.config.get("click_targets", {}).items():
            if key == "hand_scan_points":
                if "p1" in value:
                    coords["hand_scan_p1"] = value["p1"]
                if "p2" in value:
                    coords["hand_scan_p2"] = value["p2"]
            else:
                coords[key] = value
        return coords

    def get_click_targets(self):
        return self.config.get("click_targets", {})

    def get_log_path(self):
        return self.config.get("log_path", "")

    def get_screen_bounds(self):
        bounds = self.config.get("screen_bounds", [[0, 0], [2560, 1440]])
        return tuple(tuple(b) for b in bounds)

    def get_input_backend(self):
        return self.config.get("input_backend", "auto")

    def set_input_backend(self, backend: str):
        self.config["input_backend"] = backend
        self._save_config()

    def get_account_switch_minutes(self) -> int:
        try:
            return int(self.config.get("account_switch_minutes", 0))
        except (TypeError, ValueError):
            return 0

    def set_account_switch_minutes(self, minutes: int) -> None:
        try:
            minutes_i = int(minutes)
        except (TypeError, ValueError):
            return
        if minutes_i < 0:
            minutes_i = 0
        self.config["account_switch_minutes"] = minutes_i
        self._save_config()

    def _repo_root(self) -> str:
        return _app_root_dir()

    def _accounts_root(self) -> str:
        root = os.path.join(self._repo_root(), "Accounts")
        os.makedirs(root, exist_ok=True)
        return root

    def _sanitize_folder_name(self, name: str) -> str:
        cleaned = []
        for ch in (name or "").strip():
            if ch.isalnum() or ch in ("_", "-"):
                cleaned.append(ch)
            elif ch == " ":
                cleaned.append(" ")
            else:
                cleaned.append("_")
        candidate = "".join(cleaned).strip("._-")
        return candidate or "account"

    def _next_unique_folder_name(self, desired: str, used: set[str]) -> str:
        if desired not in used:
            return desired
        i = 2
        while True:
            trial = f"{desired}_{i}"
            if trial not in used:
                return trial
            i += 1

    def get_managed_accounts(self) -> list[dict]:
        raw = self.config.get("managed_accounts", [])
        if not isinstance(raw, list):
            return []
        cleaned = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            email = str(item.get("email", "")).strip()
            pw = str(item.get("pw", "")).strip()
            folder = str(item.get("folder", "")).strip()
            if not name:
                continue
            cleaned.append({
                "name": name,
                "email": email,
                "pw": pw,
                "folder": folder,
            })
        return cleaned[:10]

    def save_managed_accounts(self, accounts: list[dict]) -> list[dict]:
        if not isinstance(accounts, list):
            return self.get_managed_accounts()
        normalized = []
        seen_names = set()
        existing_by_name = {
            str(acc.get("name", "")).casefold(): str(acc.get("folder", "")).strip()
            for acc in self.get_managed_accounts()
            if isinstance(acc, dict) and str(acc.get("name", "")).strip()
        }
        used_folders = {str(acc.get("folder", "")).strip() for acc in self.get_managed_accounts()}
        used_folders = {name for name in used_folders if name}

        for item in accounts[:10]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            email = str(item.get("email", "")).strip()
            pw = str(item.get("pw", "")).strip()
            if not name:
                continue
            if not email or not pw:
                continue
            key = name.casefold()
            if key in seen_names:
                continue
            seen_names.add(key)

            folder = str(item.get("folder", "")).strip()
            if not folder:
                folder = existing_by_name.get(key, "")
            if not folder:
                desired = self._sanitize_folder_name(name)
                folder = self._next_unique_folder_name(desired, used_folders)
            used_folders.add(folder)

            folder_path = os.path.join(self._accounts_root(), folder)
            os.makedirs(folder_path, exist_ok=True)
            creds_path = os.path.join(folder_path, "credentials.json")
            with open(creds_path, "w", encoding="utf-8") as f:
                json.dump({name: {"email": email, "pw": pw}}, f, indent=2)

            normalized.append({
                "name": name,
                "email": email,
                "pw": pw,
                "folder": folder,
            })

        self.config["managed_accounts"] = normalized
        valid = {acc["name"].casefold() for acc in normalized}
        order = [x for x in self.get_account_play_order() if x.casefold() in valid]
        self.config["account_play_order"] = order
        if len(normalized) <= 1:
            self.config["account_cycle_index"] = 0
        elif self.get_account_cycle_index() >= len(normalized):
            self.config["account_cycle_index"] = 0
        self._save_config()
        return normalized

    def get_account_cycle_index(self) -> int:
        try:
            return int(self.config.get("account_cycle_index", 0))
        except (TypeError, ValueError):
            return 0

    def set_account_cycle_index(self, index: int) -> None:
        try:
            index_i = int(index)
        except (TypeError, ValueError):
            return
        if index_i < 0:
            index_i = 0
        self.config["account_cycle_index"] = index_i
        self._save_config()

    def get_account_play_order(self) -> list[str]:
        order = self.config.get("account_play_order", [])
        if isinstance(order, list):
            return [str(item) for item in order if item]
        return []

    def set_account_play_order(self, order: list[str]) -> None:
        if not isinstance(order, list):
            return
        valid_names = {acc["name"].casefold() for acc in self.get_managed_accounts() if acc.get("name")}
        cleaned = []
        seen = set()
        for item in order:
            name = str(item).strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            if key not in valid_names:
                continue
            seen.add(key)
            cleaned.append(name)
        self.config["account_play_order"] = cleaned
        self._save_config()


class MTGBotUI(tk.Tk):
    """Main application window"""

    def __init__(self):
        super().__init__()

        self.title("Burning Lotus")
        self._suppress_tk_default_icon()
        width, height = 460, 780
        x, y = 18, 24
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)

        self.config_manager = ConfigManager()
        self.bot_running = False
        self.game = None
        self.bot_thread = None
        self.session_games = 0
        self.session_wins = 0
        self.settings_window = None
        self.current_session_window = None
        self._controller = None
        self._switch_eta_text = self._get_configured_switch_eta_text()

        self.ui_theme = self._build_ui_theme()
        self.configure(bg=self.ui_theme["colors"]["bg"])
        self._style = ttk.Style(self)
        self._bg_source_image = None
        self._bg_photo = None
        self._bg_canvas_item = None
        self._bg_cache_size = None
        self._load_main_background_image()
        self._setup_theme_styles()
        self._setup_ui()
        self._setup_stop_hotkey()

    def _suppress_tk_default_icon(self):
        try:
            self._blank_icon = tk.PhotoImage(width=1, height=1)
            self.iconphoto(True, self._blank_icon)
        except Exception:
            pass

    def _setup_stop_hotkey(self):
        # Global hotkey via pynput (mouse wheel down).
        try:
            from pynput import mouse
        except Exception:
            mouse = None

        if mouse:
            try:
                def _on_scroll(_x, _y, _dx, dy):
                    if dy < 0:
                        self.after(0, self._stop_bot)
                self._stop_mouse_listener = mouse.Listener(on_scroll=_on_scroll)
                self._stop_mouse_listener.daemon = True
                self._stop_mouse_listener.start()
            except Exception:
                pass

    def _pick_font_family(self):
        preferred = ["Segoe UI Variable", "Segoe UI", "Inter", "Arial"]
        available = {name.lower(): name for name in tkfont.families(self)}
        for candidate in preferred:
            resolved = available.get(candidate.lower())
            if resolved:
                return resolved
        return "TkDefaultFont"

    def _build_ui_theme(self):
        base_font = self._pick_font_family()
        return {
            "colors": {
                "bg": "#0F1115",
                "surface": "#151A21",
                "surface_2": "#1B2230",
                "text": "#E7EAF0",
                "text_muted": "#9AA3B2",
                "accent": "#C8141E",
                "accent_primary": "#1F3A2D",
                "accent_hover": "#274837",
                "accent_pressed": "#1A3026",
                "accent_primary_border": "#2E5A45",
                "subtitle_green": "#8FB9A3",
                "border": "#242B36",
                "disabled_bg": "#1A202B",
                "disabled_text": "#6E7686",
                "shadow": "#0B0E13",
                "pill_bg": "#1B2230",
                "pill_border": "#30394A",
                "pill_running_bg": "#12301F",
                "pill_running_text": "#8FE0B0",
                "status_stopped_text": "#B07A80",
            },
            "spacing": {"xs": 8, "sm": 12, "md": 14, "lg": 18, "xl": 28, "card_pad": 28, "outer_margin": 20},
            "size": {"logo": 210, "button_width": 30, "card_width": 392},
            "font": {
                "family": base_font,
                "title": (base_font, 26, "bold"),
                "subtitle": (base_font, 10),
                "body": (base_font, 11),
                "button": (base_font, 11, "bold"),
            },
            "radius": {"card": 18, "button": 13},
        }

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        color = color.lstrip("#")
        if len(color) != 6:
            return (0, 0, 0)
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def _mix_rgb(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
        t = max(0.0, min(1.0, t))
        return (
            int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t),
        )

    def _render_button_skin(
        self,
        width: int,
        height: int,
        radius: int,
        top_hex: str,
        bottom_hex: str,
        border_hex: str,
        glow_hex: str,
    ) -> ImageTk.PhotoImage:
        glow_pad = 6
        img_w = width + glow_pad * 2
        img_h = height + glow_pad * 2
        base = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))

        # Soft drop shadow to separate the button from busy backgrounds.
        shadow = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle(
            (glow_pad, glow_pad + 1, glow_pad + width - 1, glow_pad + height),
            radius=radius,
            fill=(0, 0, 0, 115),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(4))
        base = Image.alpha_composite(base, shadow)

        glow = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        gr, gg, gb = self._hex_to_rgb(glow_hex)
        glow_draw.rounded_rectangle(
            (glow_pad, glow_pad, glow_pad + width - 1, glow_pad + height - 1),
            radius=radius,
            outline=(gr, gg, gb, 205),
            width=2,
        )
        glow = glow.filter(ImageFilter.GaussianBlur(4))
        base = Image.alpha_composite(base, glow)

        shape_mask = Image.new("L", (width, height), 0)
        shape_mask_draw = ImageDraw.Draw(shape_mask)
        shape_mask_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)

        # Fixed button body color requested by user: #3D130E slightly more transparent.
        body_fill = Image.new("RGBA", (width, height), (61, 19, 14, 210))
        base.paste(body_fill, (glow_pad, glow_pad), shape_mask)

        rim = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        rim_draw = ImageDraw.Draw(rim)
        br, bg, bb = self._hex_to_rgb(border_hex)
        rim_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, outline=(br, bg, bb, 245), width=2)
        rim_draw.rounded_rectangle(
            (2, 2, width - 3, height - 3),
            radius=max(2, radius - 2),
            outline=(255, 255, 255, 92),
            width=1,
        )
        rim_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        rim_layer.paste(rim, (glow_pad, glow_pad), rim)
        base = Image.alpha_composite(base, rim_layer)

        sheen = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        sheen_draw = ImageDraw.Draw(sheen)
        half = max(1, height // 2)
        for y in range(half):
            alpha = int(96 * (1.0 - (y / half)))
            sheen_draw.line((2, y + 2, width - 3, y + 2), fill=(255, 255, 255, alpha))
        for y in range(half, height):
            t = (y - half) / max(1, (height - half))
            alpha = int(78 * t)
            sheen_draw.line((2, y, width - 3, y), fill=(0, 0, 0, alpha))
        sheen_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        sheen_layer.paste(sheen, (glow_pad, glow_pad), sheen)
        base = Image.alpha_composite(base, sheen_layer)

        inner = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        inner_draw = ImageDraw.Draw(inner)
        inner_draw.rounded_rectangle(
            (1, 1, width - 2, height - 2),
            radius=max(2, radius - 1),
            outline=(0, 0, 0, 45),
            width=1,
        )
        inner_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        inner_layer.paste(inner, (glow_pad, glow_pad), inner)
        base = Image.alpha_composite(base, inner_layer)

        return ImageTk.PhotoImage(base)

    def _install_button_skin_style(self, style_name: str, element_name: str, skins: dict[str, ImageTk.PhotoImage]):
        try:
            self._style.element_create(
                element_name,
                "image",
                skins["normal"],
                ("disabled", skins["disabled"]),
                ("pressed", skins["pressed"]),
                ("active", skins["hover"]),
                border=(22, 16),
                sticky="nsew",
            )
        except tk.TclError:
            pass

        self._style.layout(
            style_name,
            [
                (
                    element_name,
                    {
                        "sticky": "nsew",
                        "children": [
                            (
                                "Button.padding",
                                {
                                    "sticky": "nsew",
                                    "children": [("Button.label", {"sticky": "nsew"})],
                                },
                            )
                        ],
                    },
                )
            ],
        )
        self._style.configure(
            style_name,
            padding=(0, 0),
            borderwidth=0,
            relief="flat",
            anchor="center",
            foreground="#F2F6FF",
            font=self.ui_theme["font"]["button"],
        )
        self._style.map(
            style_name,
            foreground=[
                ("disabled", "#A5AFBF"),
                ("pressed", "#FFFFFF"),
                ("active", "#FFFFFF"),
            ],
        )

    def _setup_main_menu_button_skins(self):
        width = 336
        height = 48
        radius = 14
        specs = {
            "Primary.TButton": {
                "element": "MainPrimaryGlow.button",
                "normal": ("#2FC07B", "#1F7F4F", "#6AE5A8", "#4DDC98"),
                "hover": ("#3AD58A", "#23975A", "#86EDBC", "#64E3AA"),
                "pressed": ("#1A6E43", "#145938", "#4FC087", "#2EA86D"),
                "disabled": ("#3C4B47", "#2C3835", "#55655F", "#3F504A"),
            },
            "Secondary.TButton": {
                "element": "MainSecondaryGlow.button",
                "normal": ("#3B4D74", "#24324D", "#6078A6", "#5E77A8"),
                "hover": ("#47608D", "#2B3C5C", "#7E98C6", "#728EBE"),
                "pressed": ("#253753", "#1C2940", "#4B628A", "#425A84"),
                "disabled": ("#39414F", "#2C3442", "#546078", "#46526A"),
            },
            "Destructive.TButton": {
                "element": "MainDangerGlow.button",
                "normal": ("#7D3F4A", "#5A2B33", "#A96673", "#985A66"),
                "hover": ("#92505C", "#6A343E", "#C07E89", "#AF707B"),
                "pressed": ("#5F2E37", "#4A232A", "#8F5560", "#7E474F"),
                "disabled": ("#4A3E42", "#3A2F34", "#66575D", "#564A50"),
            },
        }

        self._button_skins = {}
        for style_name, spec in specs.items():
            states = {}
            for state_name in ("normal", "hover", "pressed", "disabled"):
                top, bottom, border, glow = spec[state_name]
                states[state_name] = self._render_button_skin(width, height, radius, top, bottom, border, glow)
            self._button_skins[style_name] = states
            self._install_button_skin_style(style_name, spec["element"], states)

    def _create_canvas_menu_button(
        self,
        name: str,
        text: str,
        style_name: str,
        command,
        enabled: bool = True,
    ) -> None:
        skins = self._button_skins[style_name]
        tag = f"main_btn_{name}"
        bg_item = self._card_canvas.create_image(0, 0, anchor="n", image=skins["normal"], tags=(tag,))
        text_item = self._card_canvas.create_text(
            0,
            0,
            text=text,
            fill="#F2F6FF",
            font=self.ui_theme["font"]["button"],
            anchor="center",
            tags=(tag,),
        )
        self._menu_buttons[name] = {
            "style": style_name,
            "command": command,
            "enabled": bool(enabled),
            "hover": False,
            "pressed": False,
            "skins": skins,
            "bg_item": bg_item,
            "text_item": text_item,
            "width": int(skins["normal"].width()),
            "height": int(skins["normal"].height()),
        }
        self._menu_button_order.append(name)
        self._card_canvas.tag_bind(tag, "<Enter>", lambda _e, n=name: self._on_canvas_menu_button_enter(n))
        self._card_canvas.tag_bind(tag, "<Leave>", lambda _e, n=name: self._on_canvas_menu_button_leave(n))
        self._card_canvas.tag_bind(tag, "<ButtonPress-1>", lambda _e, n=name: self._on_canvas_menu_button_press(n))
        self._card_canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, n=name: self._on_canvas_menu_button_release(n))
        self._refresh_canvas_menu_button_state(name)

    def _refresh_canvas_menu_button_state(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn:
            return
        if not btn["enabled"]:
            state_key = "disabled"
            text_color = "#A5AFBF"
        elif btn["pressed"]:
            state_key = "pressed"
            text_color = "#FFFFFF"
        elif btn["hover"]:
            state_key = "hover"
            text_color = "#FFFFFF"
        else:
            state_key = "normal"
            text_color = "#F2F6FF"

        self._card_canvas.itemconfigure(btn["bg_item"], image=btn["skins"][state_key])
        self._card_canvas.itemconfigure(btn["text_item"], fill=text_color)

    def _set_canvas_menu_button_enabled(self, name: str, enabled: bool) -> None:
        btn = self._menu_buttons.get(name)
        if not btn:
            return
        btn["enabled"] = bool(enabled)
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_canvas_menu_button_state(name)

    def _on_canvas_menu_button_enter(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        self._card_canvas.configure(cursor="hand2")
        btn["hover"] = True
        self._refresh_canvas_menu_button_state(name)

    def _on_canvas_menu_button_leave(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn:
            return
        self._card_canvas.configure(cursor="")
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_canvas_menu_button_state(name)

    def _on_canvas_menu_button_press(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        btn["pressed"] = True
        self._refresh_canvas_menu_button_state(name)

    def _on_canvas_menu_button_release(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn:
            return
        should_fire = bool(btn["enabled"] and btn["pressed"] and btn["hover"])
        btn["pressed"] = False
        self._refresh_canvas_menu_button_state(name)
        if should_fire:
            try:
                btn["command"]()
            except Exception:
                pass

    def _setup_theme_styles(self):
        c = self.ui_theme["colors"]
        f = self.ui_theme["font"]
        style = self._style
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("App.TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["surface"])
        style.configure("Body.TLabel", background=c["surface"], foreground=c["text"], font=f["body"])
        style.configure("Muted.TLabel", background=c["surface"], foreground=c["text_muted"], font=f["body"])
        style.configure("Title.TLabel", background=c["surface"], foreground=c["text"], font=f["title"])
        style.configure("Subtitle.TLabel", background=c["surface"], foreground=c["subtitle_green"], font=f["subtitle"])
        style.configure("Status.TLabel", background=c["surface"], foreground=c["text_muted"], font=f["body"])
        style.configure("ETA.TLabel", background=c["surface"], foreground=c["text_muted"], font=f["body"])
        style.configure(
            "Card.Horizontal.TProgressbar",
            troughcolor=c["surface_2"],
            background=c["accent_primary"],
            bordercolor=c["border"],
            lightcolor=c["accent_hover"],
            darkcolor=c["accent_pressed"],
        )

        common_btn = {
            "font": f["button"],
            "padding": (18, 11),
            "borderwidth": 1,
            "relief": "flat",
            "focuscolor": c["surface_2"],
        }

        style.configure(
            "Primary.TButton",
            **common_btn,
            foreground=c["text"],
            background=c["accent_primary"],
            bordercolor=c["accent_primary_border"],
        )
        style.map(
            "Primary.TButton",
            background=[("pressed", c["accent_pressed"]), ("active", c["accent_hover"]), ("disabled", c["disabled_bg"])],
            foreground=[("disabled", c["disabled_text"])],
            bordercolor=[("pressed", c["accent_primary_border"]), ("active", c["accent_primary_border"]), ("disabled", c["border"])],
        )

        style.configure(
            "Secondary.TButton",
            **common_btn,
            foreground=c["text"],
            background=c["surface_2"],
            bordercolor=c["pill_border"],
        )
        style.map(
            "Secondary.TButton",
            background=[("pressed", "#202838"), ("active", "#253041"), ("disabled", c["disabled_bg"])],
            foreground=[("disabled", c["disabled_text"])],
            bordercolor=[("active", c["pill_border"]), ("pressed", c["pill_border"]), ("disabled", c["border"])],
        )

        style.configure(
            "Destructive.TButton",
            **common_btn,
            foreground=c["text"],
            background="#3A2025",
            bordercolor="#5A2A31",
        )
        style.map(
            "Destructive.TButton",
            background=[("pressed", "#311B20"), ("active", "#4A262C"), ("disabled", c["disabled_bg"])],
            foreground=[("disabled", c["disabled_text"])],
            bordercolor=[("pressed", "#5A2A31"), ("active", "#5A2A31"), ("disabled", c["border"])],
        )
        style.configure("Primary.TButton", font=f["button"])
        style.configure("Secondary.TButton", font=f["button"])
        style.configure("Destructive.TButton", font=f["button"])
        self._setup_main_menu_button_skins()

    def _load_main_background_image(self):
        self._bg_source_image = None
        bg_candidates = [
            "/home/barrylim/Dokumente/mtga_bot/background",
            _app_path("background"),
            _app_path("background.png"),
        ]
        for bg_path in bg_candidates:
            if not os.path.exists(bg_path):
                continue
            try:
                with Image.open(bg_path) as bg_image:
                    self._bg_source_image = bg_image.convert("RGB")
                return
            except Exception:
                continue

    def _refresh_canvas_background(self, canvas_w: int, canvas_h: int):
        if self._bg_source_image is None:
            return
        if canvas_w <= 1 or canvas_h <= 1:
            return

        target_size = (canvas_w, canvas_h)
        if self._bg_cache_size != target_size or self._bg_photo is None:
            fitted_bg = ImageOps.fit(
                self._bg_source_image,
                target_size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            self._bg_photo = ImageTk.PhotoImage(fitted_bg)
            self._bg_cache_size = target_size

        if self._bg_canvas_item is None:
            self._bg_canvas_item = self._card_canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
        else:
            self._card_canvas.coords(self._bg_canvas_item, 0, 0)
            self._card_canvas.itemconfigure(self._bg_canvas_item, image=self._bg_photo)
        self._card_canvas.tag_lower(self._bg_canvas_item)

    def _setup_ui(self):
        c = self.ui_theme["colors"]
        sp = self.ui_theme["spacing"]
        size = self.ui_theme["size"]

        # Start page content sits directly on the background canvas (no inner card panel).
        self._card_canvas = tk.Canvas(self, bg=c["surface"], highlightthickness=0, bd=0)
        self._card_canvas.pack(fill=tk.BOTH, expand=True)

        try:
            logo_path = _app_path("ui_symbol.png")
            logo_image = Image.open(logo_path).convert("RGBA")
            target_size = (size["logo"], size["logo"])
            fitted_logo = ImageOps.contain(logo_image, target_size, Image.Resampling.LANCZOS)
            composed_logo = Image.new("RGBA", target_size, (0, 0, 0, 0))
            x = (target_size[0] - fitted_logo.width) // 2
            y = (target_size[1] - fitted_logo.height) // 2
            composed_logo.alpha_composite(fitted_logo, dest=(x, y))
            self.logo_photo = ImageTk.PhotoImage(composed_logo)
            self._logo_item = self._card_canvas.create_image(0, 0, image=self.logo_photo, anchor="n")
            self._logo_fallback_item = None
        except Exception:
            self._logo_item = None
            self._logo_fallback_item = self._card_canvas.create_text(
                0,
                0,
                text="MTG",
                fill=c["text"],
                font=self.ui_theme["font"]["title"],
                anchor="n",
            )

        self._title_item = self._card_canvas.create_text(
            0,
            0,
            text="Burning Lotus",
            fill=c["text"],
            font=self.ui_theme["font"]["title"],
            anchor="n",
        )

        self._menu_buttons: dict[str, dict] = {}
        self._menu_button_order: list[str] = []
        self._create_canvas_menu_button("start", "Start Bot", "Primary.TButton", self._start_bot, enabled=True)
        self._create_canvas_menu_button("stop", "Stop Bot [Wheel Down]", "Destructive.TButton", self._stop_bot, enabled=False)
        self._create_canvas_menu_button("calibrate", "Calibrate", "Secondary.TButton", self._open_calibration, enabled=True)
        self._create_canvas_menu_button("current_session", "Current Session", "Secondary.TButton", self._open_current_session, enabled=True)
        self._create_canvas_menu_button("settings", "Settings", "Secondary.TButton", self._open_settings, enabled=True)

        self._loading_text_item = self._card_canvas.create_text(
            0,
            0,
            text="Loading Carddata",
            fill=c["text_muted"],
            font=self.ui_theme["font"]["body"],
            anchor="n",
        )
        self.loading_bar = ttk.Progressbar(
            self._card_canvas,
            mode="indeterminate",
            style="Card.Horizontal.TProgressbar",
        )
        self._loading_bar_window = self._card_canvas.create_window(0, 0, anchor="n", window=self.loading_bar)
        self._loading_visible = False

        self._status_text_item = self._card_canvas.create_text(
            0,
            0,
            text="Status: Stopped",
            fill=c["status_stopped_text"],
            font=self.ui_theme["font"]["body"],
            anchor="n",
        )

        self._card_canvas.bind("<Configure>", lambda _e: self._refresh_card_layout())
        self.after(0, self._refresh_card_layout)
        self._set_startup_loading(False)
        self._set_running_state(False)

    def _refresh_card_layout(self):
        if not hasattr(self, "_card_canvas"):
            return
        sp = self.ui_theme["spacing"]
        size = self.ui_theme["size"]

        canvas_w = self._card_canvas.winfo_width()
        canvas_h = self._card_canvas.winfo_height()
        if canvas_w <= 1 or canvas_h <= 1:
            return
        self._refresh_canvas_background(canvas_w, canvas_h)

        self.update_idletasks()

        center_x = canvas_w // 2
        button_gap = 13

        menu_buttons = [self._menu_buttons[name] for name in self._menu_button_order if name in self._menu_buttons]
        btn_h = max((btn["height"] for btn in menu_buttons), default=52)
        btn_w = max((btn["width"] for btn in menu_buttons), default=336)
        self._card_canvas.itemconfigure(self._loading_bar_window, width=max(240, btn_w - 14))

        title_font = tkfont.Font(font=self.ui_theme["font"]["title"])
        body_font = tkfont.Font(font=self.ui_theme["font"]["body"])
        title_h = title_font.metrics("linespace")
        body_h = body_font.metrics("linespace")
        logo_h = size["logo"] if self._logo_item is not None else title_h
        loading_bar_h = self.loading_bar.winfo_reqheight()

        total_h = logo_h + 6 + title_h + sp["lg"]
        total_h += (btn_h * len(menu_buttons)) + (button_gap * max(0, len(menu_buttons) - 1))
        if self._loading_visible:
            total_h += body_h + sp["xs"] + loading_bar_h + sp["md"]
        total_h += sp["lg"] + body_h

        y = max(sp["outer_margin"], (canvas_h - total_h) // 2)

        if self._logo_item is not None:
            self._card_canvas.coords(self._logo_item, center_x, y)
            y += logo_h + 6
            if self._logo_fallback_item is not None:
                self._card_canvas.itemconfigure(self._logo_fallback_item, state="hidden")
        else:
            if self._logo_fallback_item is not None:
                self._card_canvas.itemconfigure(self._logo_fallback_item, state="normal")
                self._card_canvas.coords(self._logo_fallback_item, center_x, y)
            y += logo_h + 6

        self._card_canvas.coords(self._title_item, center_x, y)
        y += title_h + sp["lg"]

        for idx, name in enumerate(self._menu_button_order):
            btn = self._menu_buttons.get(name)
            if not btn:
                continue
            self._card_canvas.coords(btn["bg_item"], center_x, y)
            self._card_canvas.coords(btn["text_item"], center_x, y + (btn["height"] // 2))
            is_last = idx == (len(self._menu_button_order) - 1)
            y += btn_h + (sp["lg"] if is_last else button_gap)

        if self._loading_visible:
            self._card_canvas.itemconfigure(self._loading_text_item, state="normal")
            self._card_canvas.itemconfigure(self._loading_bar_window, state="normal")
            self._card_canvas.coords(self._loading_text_item, center_x, y)
            y += body_h + sp["xs"]
            self._card_canvas.coords(self._loading_bar_window, center_x, y)
            y += loading_bar_h + sp["md"]
        else:
            self._card_canvas.itemconfigure(self._loading_text_item, state="hidden")
            self._card_canvas.itemconfigure(self._loading_bar_window, state="hidden")

        self._card_canvas.coords(self._status_text_item, center_x, y)

    def _set_running_state(self, running: bool):
        c = self.ui_theme["colors"]
        if running:
            self._set_canvas_menu_button_enabled("start", False)
            self._set_canvas_menu_button_enabled("stop", True)
            self._set_canvas_menu_button_enabled("calibrate", False)
            self._card_canvas.itemconfigure(
                self._status_text_item,
                text="Status: Running",
                fill=c["pill_running_text"],
            )
            return

        self._set_canvas_menu_button_enabled("start", True)
        self._set_canvas_menu_button_enabled("stop", False)
        self._set_canvas_menu_button_enabled("calibrate", True)
        self._card_canvas.itemconfigure(
            self._status_text_item,
            text="Status: Stopped",
            fill=c["status_stopped_text"],
        )
        self._switch_eta_text = self._get_configured_switch_eta_text()

    def _get_configured_switch_eta_text(self) -> str:
        minutes = 0
        try:
            minutes = int(self.config_manager.get_account_switch_minutes())
        except Exception:
            minutes = 0
        if minutes <= 0:
            return "Account switch: off"
        return f"{minutes} Min till Account Switch"

    def _set_startup_loading(self, loading: bool):
        if not hasattr(self, "loading_bar"):
            return
        if loading:
            self._loading_visible = True
            self.loading_bar.start(12)
            self._refresh_card_layout()
            return
        self._loading_visible = False
        self.loading_bar.stop()
        self._refresh_card_layout()

    def _start_bot(self):
        if self.bot_running:
            return

        self.bot_running = True
        self._set_running_state(True)
        self._set_startup_loading(True)

        # Start bot in separate thread
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()
        self._update_switch_eta()

    def _run_bot(self):
        try:
            log_path = self.config_manager.get_log_path()
            click_targets = self.config_manager.get_click_targets()
            screen_bounds = self.config_manager.get_screen_bounds()
            input_backend = self.config_manager.get_input_backend()
            account_switch_minutes = self.config_manager.get_account_switch_minutes()
            account_cycle_index = self.config_manager.get_account_cycle_index()
            account_play_order = self.config_manager.get_account_play_order()
            controller = Controller(log_path=log_path, screen_bounds=screen_bounds,
                                   click_targets=click_targets, input_backend=input_backend,
                                   account_switch_minutes=account_switch_minutes,
                                   account_cycle_index=account_cycle_index,
                                   account_play_order=account_play_order)
            self._controller = controller
            ai = DummyAI()
            self.game = Game(controller, ai)
            self.game.start()
            self.after(0, lambda: self._set_startup_loading(False))

            # Wrap match end callback so we can update session stats and still restart games.
            def _on_match_end(won=None):
                self.session_games += 1
                if won is True:
                    self.session_wins += 1
                self.after(0, self._update_current_session_window)
                try:
                    if self.game:
                        self.game.on_match_end(won)
                except TypeError:
                    if self.game:
                        self.game.on_match_end()

            controller.set_match_end_callback(_on_match_end)

            # Keep running while bot is active
            while self.bot_running:
                import time
                time.sleep(1)

        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda msg=err_msg: self._handle_bot_error(msg))

    def _handle_bot_error(self, error_msg):
        self._stop_bot()
        messagebox.showerror("Bot Error", f"An error occurred:\n{error_msg}")

    def _stop_bot(self):
        self.bot_running = False

        if self.game:
            try:
                self.game.stop()
            except:
                pass
            self.game = None

        self._set_running_state(False)
        self._set_startup_loading(False)
        self._controller = None

    def _open_calibration(self):
        CalibrationWindow(self, self.config_manager)

    def _open_current_session(self):
        if self.current_session_window and self.current_session_window.winfo_exists():
            self.current_session_window.lift()
            self.current_session_window.focus_force()
            return
        self.current_session_window = CurrentSessionWindow(self, self.session_games, self.session_wins)
        self.current_session_window.update_stats(self.session_games, self.session_wins, self._switch_eta_text)

    def _update_current_session_window(self):
        if self.current_session_window and self.current_session_window.winfo_exists():
            self.current_session_window.update_stats(self.session_games, self.session_wins, self._switch_eta_text)

    def _open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        self.settings_window = SettingsWindow(self, self.config_manager)

    def _update_switch_eta(self):
        if not self.bot_running:
            return
        minutes = 0
        interval_minutes = self.config_manager.get_account_switch_minutes()
        try:
            if getattr(self, "_controller", None):
                remaining_sec = self._controller.get_account_switch_remaining_sec()
                minutes = int((remaining_sec + 59) / 60) if remaining_sec > 0 else 0
                interval_minutes = self._controller.get_account_switch_interval_minutes()
        except Exception:
            minutes = 0
        if interval_minutes <= 0:
            self._switch_eta_text = "Account switch: off"
        else:
            self._switch_eta_text = f"{minutes} Min till Account Switch"
        self._update_current_session_window()
        self.after(10000, self._update_switch_eta)


class CurrentSessionWindow(tk.Toplevel):
    def __init__(self, parent, games: int, wins: int):
        super().__init__(parent)
        self.title("Current Session")
        width, height = 460, 220
        gap_px = int(parent.winfo_fpixels("5m"))  # ~5 mm
        parent.update_idletasks()
        x = parent.winfo_x()
        y = parent.winfo_rooty() + parent.winfo_height() + gap_px
        max_y = max(0, self.winfo_screenheight() - height)
        y = min(y, max_y)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")

        frame = tk.Frame(self, bg="#2b2b2b", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(frame, text="Current session", bg="#2b2b2b", fg="white",
                         font=("Segoe UI", 12, "bold"))
        title.pack(pady=(0, 12))

        stats_frame = tk.Frame(frame, bg="#2b2b2b")
        stats_frame.pack(fill=tk.X)

        self.switch_eta_label = tk.Label(stats_frame, text="", bg="#2b2b2b", fg="#00ff00", font=("Consolas", 12))
        self.switch_eta_label.pack(anchor="w", pady=4)

        self.games_label = tk.Label(stats_frame, text="", bg="#2b2b2b", fg="#00ff00", font=("Consolas", 12))
        self.games_label.pack(anchor="w", pady=4)

        self.wins_label = tk.Label(stats_frame, text="", bg="#2b2b2b", fg="#00ff00", font=("Consolas", 12))
        self.wins_label.pack(anchor="w", pady=4)

        back_btn = ttk.Button(
            frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.pack(anchor="w", pady=(12, 0))

        self.update_stats(games, wins, "Account switch: off")
        _apply_submenu_theme(self)

    def update_stats(self, games: int, wins: int, switch_eta_text: str | None = None):
        if switch_eta_text is not None:
            self.switch_eta_label.config(text=switch_eta_text)
        self.games_label.config(text=f"Games played: {games}")
        self.wins_label.config(text=f"Win: {wins}")


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, config_manager: ConfigManager):
        super().__init__(parent)
        self.title("Settings")
        width, height = 460, 230
        gap_px = int(parent.winfo_fpixels("5m"))  # ~5 mm
        parent.update_idletasks()
        x = parent.winfo_x()
        y = parent.winfo_rooty() + parent.winfo_height() + gap_px
        max_y = max(0, self.winfo_screenheight() - height)
        y = min(y, max_y)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")
        self._config_manager = config_manager
        self._recording = False
        self._record_ignore_first = False
        self._mouse_listener = None
        self._keyboard_listener = None
        self._playback_thread = None
        self._playback_keyboard_listener = None
        self._playback_stop_event = threading.Event()
        self._current_record_events = []
        self._records_path = _app_path("recorded_actions_records.json")
        self._switch_save_job = None
        self.record_btn = None
        self.show_records_btn = None

        frame = tk.Frame(self, bg="#2b2b2b", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Dark.TCombobox",
            fieldbackground="#1e1e1e",
            background="#3a3a3a",
            foreground="white",
            bordercolor="#2b2b2b",
            lightcolor="#2b2b2b",
            darkcolor="#2b2b2b",
            arrowcolor="white",
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", "#1e1e1e")],
            background=[("readonly", "#3a3a3a")],
            foreground=[("readonly", "white")],
        )
        style.configure(
            "Manage.TButton",
            font=("Segoe UI", 10),
            padding=(12, 4),
            foreground="white",
            background="#3a3a3a",
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Manage.TButton",
            background=[("pressed", "#323232"), ("active", "#444444")],
        )
        style.configure(
            "ManagePrimary.TButton",
            font=("Segoe UI", 10),
            padding=(12, 4),
            foreground="white",
            background="#3a3a3a",
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "ManagePrimary.TButton",
            background=[("pressed", "#323232"), ("active", "#444444")],
        )

        manage_row = tk.Frame(frame, bg="#2b2b2b")
        manage_row.pack(fill=tk.X)

        manage_btn = ttk.Button(
            manage_row,
            text="Manage Accounts",
            command=self._open_switch_account_window,
        )
        manage_btn.pack(side=tk.LEFT)

        record_row = tk.Frame(frame, bg="#2b2b2b")
        record_row.pack(fill=tk.X, pady=(12, 0))

        record_btn = ttk.Button(
            record_row,
            text="Record Action",
            command=self._open_record_actions_window,
        )
        record_btn.pack(side=tk.LEFT)

        back_btn = ttk.Button(
            frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.pack(anchor="w", pady=(12, 0))

        _apply_submenu_theme(self)

    def _record_actions_prompt(self):
        if self._recording:
            self._stop_recording()
            return
        prompt = tk.Toplevel(self)
        prompt.title("Record")
        prompt.geometry("280x80")
        prompt.resizable(False, False)
        prompt.configure(bg="#2b2b2b")
        label = tk.Label(
            prompt,
            text="record action press enter",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        label.pack(expand=True)

        def _start_and_close(_event=None):
            try:
                prompt.destroy()
            finally:
                self._start_recording()

        prompt.bind("<Return>", _start_and_close)
        prompt.focus_force()
        _apply_submenu_theme(prompt)

    def _start_recording(self):
        if self._recording:
            return
        try:
            from pynput import mouse, keyboard
        except Exception as e:
            messagebox.showerror("Record", f"pynput not available: {e}")
            return
        self._recording = True
        self._record_ignore_first = True
        if self.record_btn:
            self.record_btn.config(text="Stop")
        if self.show_records_btn:
            self.show_records_btn.config(state=tk.DISABLED)
        self._current_record_events = []

        def _append_event(event: dict) -> None:
            self._current_record_events.append(event)

        def _on_click(x, y, button, pressed):
            if not pressed or not self._recording:
                return
            if self._record_ignore_first:
                self._record_ignore_first = False
                return
            _append_event({
                "type": "click",
                "ts": datetime.now(),
                "x": int(x),
                "y": int(y),
                "button": str(button).split(".")[-1],
            })

        def _on_key_press(key):
            if not self._recording:
                return False
            if key == keyboard.Key.f8:
                self.after(0, self._stop_recording)
                return False
            try:
                key_name = key.char
            except AttributeError:
                key_name = key.name if hasattr(key, "name") else str(key)
            _append_event({
                "type": "key",
                "ts": datetime.now(),
                "key": key_name,
            })

        self._mouse_listener = mouse.Listener(on_click=_on_click)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()
        self._keyboard_listener = keyboard.Listener(on_press=_on_key_press)
        self._keyboard_listener.daemon = True
        self._keyboard_listener.start()

    def _stop_recording(self):
        if not self._recording:
            return
        self._recording = False
        if self.record_btn:
            self.record_btn.config(text="Record")
        if self.show_records_btn:
            self.show_records_btn.config(state=tk.NORMAL)
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
        self._mouse_listener = None
        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
            except Exception:
                pass
        self._keyboard_listener = None
        self._prompt_record_name_and_save()

    def _show_records(self):
        RecordsWindow(self, self._records_path, self._play_record_actions)

    def _play_record_actions(self, actions: list[dict]) -> None:
        if self._recording:
            return
        if self._playback_thread and self._playback_thread.is_alive():
            return
        if not actions:
            messagebox.showinfo("Test Action", "No actions to play.")
            return

        try:
            from pynput import mouse, keyboard
        except Exception as e:
            messagebox.showerror("Test Action", f"pynput not available: {e}")
            return

        self.show_records_btn.config(state=tk.DISABLED)
        self._playback_stop_event.clear()

        def _on_playback_key(key):
            if key == keyboard.Key.f8:
                self._playback_stop_event.set()
                return False
            return True

        self._playback_keyboard_listener = keyboard.Listener(on_press=_on_playback_key)
        self._playback_keyboard_listener.daemon = True
        self._playback_keyboard_listener.start()

        def _run():
            m = mouse.Controller()
            k = keyboard.Controller()
            prev_delay = 0.0
            for ev in actions:
                if self._playback_stop_event.is_set():
                    break
                delay = float(ev.get("delay", 0.0))
                if delay > 0:
                    end_time = time.time() + delay
                    while time.time() < end_time:
                        if self._playback_stop_event.is_set():
                            break
                        time.sleep(0.05)
                    if self._playback_stop_event.is_set():
                        break
                if ev.get("type") == "click":
                    try:
                        x = int(float(ev.get("x", 0)))
                        y = int(float(ev.get("y", 0)))
                    except Exception:
                        x, y = 0, 0
                    btn_raw = (ev.get("button") or "").split(".")[-1]
                    btn = mouse.Button.left
                    if btn_raw == "right":
                        btn = mouse.Button.right
                    elif btn_raw == "middle":
                        btn = mouse.Button.middle
                    m.position = (x, y)
                    time.sleep(0.05)
                    m.press(btn)
                    time.sleep(0.05)
                    m.release(btn)
                elif ev.get("type") == "key":
                    key_name = ev.get("key", "")
                    key_obj = None
                    if len(key_name) == 1:
                        key_obj = key_name
                    else:
                        if hasattr(keyboard.Key, key_name):
                            key_obj = getattr(keyboard.Key, key_name)
                    if key_obj is not None:
                        k.press(key_obj)
                        k.release(key_obj)
                prev_delay = delay
            self.after(0, self._finish_playback)

        self._playback_thread = threading.Thread(target=_run, daemon=True)
        self._playback_thread.start()

    def _prompt_record_name_and_save(self):
        prompt = tk.Toplevel(self)
        prompt.title("Save Record")
        prompt.geometry("300x120")
        prompt.resizable(False, False)
        prompt.configure(bg="#2b2b2b")

        label = tk.Label(
            prompt,
            text="Record name",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        label.pack(pady=(10, 4))

        name_var = tk.StringVar(value="Account Switch")
        entry = tk.Entry(
            prompt,
            textvariable=name_var,
            bg="#1e1e1e",
            fg="white",
            insertbackground="white",
            relief=tk.FLAT,
        )
        entry.pack(padx=10, fill=tk.X)
        entry.focus_set()

        def _save_and_close(_event=None):
            name = (name_var.get() or "Account Switch").strip() or "Account Switch"
            prompt.destroy()
            self._save_record_snapshot(name)

        ok_btn = ttk.Button(
            prompt,
            text="Save",
            command=_save_and_close,
        )
        ok_btn.pack(pady=10)
        prompt.bind("<Return>", _save_and_close)
        _apply_submenu_theme(prompt)

    def _save_record_snapshot(self, name: str):
        events = list(self._current_record_events)
        if not events:
            return
        events.sort(key=lambda e: e["ts"])
        actions = []
        prev_ts = events[0]["ts"]
        for ev in events:
            delay = (ev["ts"] - prev_ts).total_seconds()
            item = {"type": ev["type"], "delay": delay, "ts": ev["ts"].isoformat()}
            if ev["type"] == "click":
                item["x"] = ev.get("x", 0)
                item["y"] = ev.get("y", 0)
                item["button"] = ev.get("button", "left")
            elif ev["type"] == "key":
                item["key"] = ev.get("key", "")
            actions.append(item)
            prev_ts = ev["ts"]

        record = {
            "name": name,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "actions": actions,
        }

        data = {"records": []}
        try:
            if os.path.exists(self._records_path):
                with open(self._records_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            data = {"records": []}

        data.setdefault("records", []).append(record)
        try:
            with open(self._records_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _finish_playback(self):
        if self._playback_keyboard_listener:
            try:
                self._playback_keyboard_listener.stop()
            except Exception:
                pass
        self._playback_keyboard_listener = None
        if hasattr(self, "test_action_btn") and self.test_action_btn:
            self.test_action_btn.config(state=tk.NORMAL)

    def _open_switch_account_window(self):
        SwitchAccountWindow(self, self._config_manager)

    def _open_record_actions_window(self):
        RecordActionsWindow(self)

    def destroy(self):
        try:
            if self._recording:
                self._stop_recording()
        finally:
            super().destroy()


class SwitchAccountWindow(tk.Toplevel):
    def __init__(self, parent: SettingsWindow, config_manager: ConfigManager):
        super().__init__(parent)
        self._parent = parent
        self._config_manager = config_manager
        self.title("Manage Accounts")
        self.geometry("920x760")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")
        self._order_combos = []
        self._order_vars = []
        self._account_rows = []

        frame = tk.Frame(self, bg="#2b2b2b", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Dark.TCombobox",
            fieldbackground="#1e1e1e",
            background="#3a3a3a",
            foreground="white",
            bordercolor="#2b2b2b",
            lightcolor="#2b2b2b",
            darkcolor="#2b2b2b",
            arrowcolor="white",
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", "#1e1e1e")],
            background=[("readonly", "#3a3a3a")],
            foreground=[("readonly", "white")],
        )

        title = tk.Label(
            frame,
            text="Manage Accounts",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 12, "bold"),
        )
        title.pack(anchor="w", pady=(0, 10))

        switch_row = tk.Frame(frame, bg="#2b2b2b")
        switch_row.pack(fill=tk.X, pady=(0, 12))

        switch_label = tk.Label(
            switch_row,
            text="Switch account (min)",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        switch_label.pack(side=tk.LEFT)

        self.switch_minutes_var = tk.StringVar(value=str(self._config_manager.get_account_switch_minutes()))
        switch_entry = tk.Entry(
            switch_row,
            textvariable=self.switch_minutes_var,
            width=6,
            bg="#1e1e1e",
            fg="white",
            insertbackground="white",
            relief=tk.FLAT,
        )
        switch_entry.pack(side=tk.LEFT, padx=(10, 6))
        switch_entry.bind("<Return>", lambda _e: self._save_switch_minutes())

        switch_hint = tk.Label(
            switch_row,
            text="0 = off",
            bg="#2b2b2b",
            fg="#aaaaaa",
            font=("Segoe UI", 9),
        )
        switch_hint.pack(side=tk.LEFT, padx=(6, 0))

        switch_save = ttk.Button(
            switch_row,
            text="Save",
            command=self._save_switch_minutes,
            style="ManagePrimary.TButton",
        )
        switch_save.pack(side=tk.LEFT, padx=(10, 0))

        accounts_title = tk.Label(
            frame,
            text="Accounts (max 10)",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10, "bold"),
        )
        accounts_title.pack(anchor="w")

        header = tk.Frame(frame, bg="#2b2b2b")
        header.pack(fill=tk.X, pady=(6, 0))
        tk.Label(header, text="#", width=3, bg="#2b2b2b", fg="#aaaaaa", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        tk.Label(header, text="Name", width=18, anchor="w", bg="#2b2b2b", fg="#aaaaaa", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(6, 6))
        tk.Label(header, text="Email", width=35, anchor="w", bg="#2b2b2b", fg="#aaaaaa", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(header, text="Password", width=24, anchor="w", bg="#2b2b2b", fg="#aaaaaa", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))

        rows_wrap = tk.Frame(frame, bg="#2b2b2b")
        rows_wrap.pack(fill=tk.X, pady=(2, 0))

        existing_accounts = self._config_manager.get_managed_accounts()
        existing_accounts = existing_accounts[:10]
        for idx in range(10):
            row = tk.Frame(rows_wrap, bg="#2b2b2b")
            row.pack(fill=tk.X, pady=2)
            tk.Label(
                row,
                text=str(idx + 1),
                width=3,
                bg="#2b2b2b",
                fg="#aaaaaa",
                font=("Segoe UI", 9),
            ).pack(side=tk.LEFT)

            account = existing_accounts[idx] if idx < len(existing_accounts) else {}
            name_var = tk.StringVar(value=str(account.get("name", "")))
            email_var = tk.StringVar(value=str(account.get("email", "")))
            pw_var = tk.StringVar(value=str(account.get("pw", "")))

            name_entry = tk.Entry(
                row,
                textvariable=name_var,
                width=20,
                bg="#1e1e1e",
                fg="white",
                insertbackground="white",
                relief=tk.FLAT,
            )
            name_entry.pack(side=tk.LEFT, padx=(6, 6))

            email_entry = tk.Entry(
                row,
                textvariable=email_var,
                width=38,
                bg="#1e1e1e",
                fg="white",
                insertbackground="white",
                relief=tk.FLAT,
            )
            email_entry.pack(side=tk.LEFT, padx=(0, 6))

            pw_entry = tk.Entry(
                row,
                textvariable=pw_var,
                show="*",
                width=26,
                bg="#1e1e1e",
                fg="white",
                insertbackground="white",
                relief=tk.FLAT,
            )
            pw_entry.pack(side=tk.LEFT, padx=(0, 6))

            self._account_rows.append({
                "name_var": name_var,
                "email_var": email_var,
                "pw_var": pw_var,
                "folder": str(account.get("folder", "")),
            })

        save_accounts_btn = ttk.Button(
            frame,
            text="Save Accounts",
            command=self._save_accounts,
            style="Manage.TButton",
        )
        save_accounts_btn.pack(anchor="w", pady=(10, 0))

        sep = tk.Frame(frame, bg="#4a4a4a", height=1)
        sep.pack(fill=tk.X, pady=(12, 10))

        order_row = tk.Frame(frame, bg="#2b2b2b")
        order_row.pack(fill=tk.X)

        order_label = tk.Label(
            order_row,
            text="Account Play Order",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        order_label.pack(side=tk.LEFT)

        order_inner = tk.Frame(order_row, bg="#2b2b2b")
        order_inner.pack(side=tk.LEFT, padx=(10, 0))

        order_choices = [""] + [acc.get("name", "") for acc in existing_accounts if acc.get("name")]
        current_order = self._config_manager.get_account_play_order()
        current_order = current_order[:10] + [""] * 10

        for idx in range(10):
            num_label = tk.Label(
                order_inner,
                text=str(idx + 1),
                bg="#2b2b2b",
                fg="#aaaaaa",
                font=("Segoe UI", 9),
                width=2,
            )
            row_i = idx % 5
            col_i = idx // 5
            num_label.grid(row=row_i, column=col_i * 2, sticky="w", padx=(0, 2), pady=2)

            var = tk.StringVar(value=current_order[idx] if idx < len(current_order) else "")
            combo = ttk.Combobox(
                order_inner,
                textvariable=var,
                values=order_choices,
                state="readonly",
                width=18,
            )
            combo.configure(style="Dark.TCombobox")
            combo.grid(row=row_i, column=col_i * 2 + 1, sticky="w", padx=(0, 12), pady=2)
            self._order_vars.append(var)
            self._order_combos.append(combo)

        save_order_btn = ttk.Button(
            frame,
            text="Save Order",
            command=self._save_account_play_order,
            style="Manage.TButton",
        )
        save_order_btn.pack(anchor="w", pady=(12, 0))

        close_btn = ttk.Button(
            frame,
            text="Close",
            command=self.destroy,
            style="Manage.TButton",
        )
        close_btn.pack(anchor="w", pady=(10, 0))
        _apply_submenu_theme(self)
        self._refresh_order_choices()

    def _save_switch_minutes(self):
        raw = (self.switch_minutes_var.get() or "").strip()
        if raw == "":
            return
        try:
            minutes = int(raw)
        except ValueError:
            return
        if minutes < 0:
            minutes = 0
        self.switch_minutes_var.set(str(minutes))
        self._config_manager.set_account_switch_minutes(minutes)
        messagebox.showinfo("Saved", "Switch account minutes saved.")

    def _refresh_order_choices(self):
        names = []
        for row in self._account_rows:
            name = (row["name_var"].get() or "").strip()
            if name and name not in names:
                names.append(name)
        choices = [""] + names
        for combo in self._order_combos:
            combo.configure(values=choices)
        for var in self._order_vars:
            if var.get() and var.get() not in names:
                var.set("")

    def _save_accounts(self):
        accounts = []
        seen = set()
        for idx, row in enumerate(self._account_rows, start=1):
            name = (row["name_var"].get() or "").strip()
            email = (row["email_var"].get() or "").strip()
            pw = (row["pw_var"].get() or "").strip()
            if not name and not email and not pw:
                continue
            if not name or not email or not pw:
                messagebox.showerror("Save Accounts", f"Row {idx}: Name, Email and Password are required.")
                return
            key = name.casefold()
            if key in seen:
                messagebox.showerror("Save Accounts", f"Duplicate account name: {name}")
                return
            seen.add(key)
            accounts.append({
                "name": name,
                "email": email,
                "pw": pw,
                "folder": row.get("folder", ""),
            })

        try:
            saved_accounts = self._config_manager.save_managed_accounts(accounts)
        except Exception as e:
            messagebox.showerror("Save Accounts", f"Failed to save accounts: {e}")
            return
        for idx, item in enumerate(saved_accounts):
            if idx >= len(self._account_rows):
                break
            self._account_rows[idx]["folder"] = str(item.get("folder", ""))
        for idx in range(len(saved_accounts), len(self._account_rows)):
            self._account_rows[idx]["folder"] = ""
        self._refresh_order_choices()
        messagebox.showinfo("Saved", f"Saved {len(saved_accounts)} account(s).")

    def _save_account_play_order(self):
        self._refresh_order_choices()
        order = [var.get().strip() for var in getattr(self, "_order_vars", [])]
        order = [item for item in order if item]
        self._config_manager.set_account_play_order(order)
        # When the order changes, start from the first entry next time.
        self._config_manager.set_account_cycle_index(0)
        parent = getattr(self._parent, "master", None)
        if parent and getattr(parent, "bot_running", False) and getattr(parent, "_controller", None):
            try:
                parent._controller.set_account_play_order(order)
                parent._controller.set_account_cycle_index(0)
            except Exception:
                pass
        messagebox.showinfo("Saved", "Account play order saved.")


class RecordActionsWindow(tk.Toplevel):
    def __init__(self, parent: SettingsWindow):
        super().__init__(parent)
        self._parent = parent
        self.title("Record Actions")
        self.geometry("320x160")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")

        frame = tk.Frame(self, bg="#2b2b2b", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        record_btn = ttk.Button(
            frame,
            text="Record",
            command=self._parent._record_actions_prompt,
        )
        record_btn.pack(anchor="w")

        show_btn = ttk.Button(
            frame,
            text="Show Records",
            command=self._parent._show_records,
        )
        show_btn.pack(anchor="w", pady=(8, 0))

        close_btn = ttk.Button(
            frame,
            text="Close",
            command=self.destroy,
        )
        close_btn.pack(anchor="w", pady=(10, 0))

        self._parent.record_btn = record_btn
        self._parent.show_records_btn = show_btn
        _apply_submenu_theme(self)

    def destroy(self):
        if getattr(self._parent, "record_btn", None) is self._parent.record_btn:
            self._parent.record_btn = None
        if getattr(self._parent, "show_records_btn", None) is self._parent.show_records_btn:
            self._parent.show_records_btn = None
        super().destroy()


class LogWindow(tk.Toplevel):
    def __init__(self, parent, log_path: str):
        super().__init__(parent)
        self.title(log_path)
        self.geometry("800x500")
        self.resizable(True, True)
        self.configure(bg="#1e1e1e")
        self._log_path = log_path
        self._stopped = False

        frame = tk.Frame(self, bg="#1e1e1e", padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(frame, wrap=tk.NONE, bg="#111111", fg="#dddddd", insertbackground="#dddddd")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.text.config(state=tk.DISABLED)
        self._refresh()

        back_btn = ttk.Button(
            frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.grid(row=2, column=0, sticky="w", pady=(8, 0))
        _apply_submenu_theme(self)

    def _refresh(self):
        if self._stopped:
            return
        try:
            with open(self._log_path, "r") as f:
                content = f.read()
        except Exception as e:
            content = f"(unable to read {self._log_path}: {e})"

        at_bottom = self.text.yview()[1] >= 0.999
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        self.text.config(state=tk.DISABLED)
        if at_bottom:
            self.text.see(tk.END)

        self.after(1000, self._refresh)

    def destroy(self):
        self._stopped = True
        super().destroy()


class RecordsWindow(tk.Toplevel):
    def __init__(self, parent, records_path: str, play_callback):
        super().__init__(parent)
        self.title("Show Records")
        self.geometry("560x420")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")
        self._records_path = records_path
        self._play_callback = play_callback
        self._setup_ui()
        _apply_submenu_theme(self)

    def _setup_ui(self):
        main_frame = tk.Frame(self, bg="#2b2b2b", padx=16, pady=16)
        main_frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            main_frame,
            text="Recorded Actions",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 12, "bold"),
        )
        title.pack(pady=(0, 10))

        list_frame = tk.Frame(main_frame, bg="#3b3b3b")
        list_frame.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(list_frame, bg="#3b3b3b", highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#3b3b3b")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        records = self._load_records()
        if not records:
            if self._migrate_from_text():
                records = self._load_records()

        if not records:
            no_data = tk.Label(
                scrollable_frame,
                text="No records saved yet",
                bg="#3b3b3b",
                fg="#aaaaaa",
                font=("Segoe UI", 10),
            )
            no_data.pack(pady=20)
        else:
            for idx, rec in enumerate(records):
                item = tk.Frame(scrollable_frame, bg="#3b3b3b", padx=10, pady=8)
                item.pack(fill=tk.X)

                name = rec.get("name", "Unnamed")
                created = rec.get("created_at", "")
                name_label = tk.Label(
                    item,
                    text=name,
                    bg="#3b3b3b",
                    fg="white",
                    font=("Segoe UI", 10, "bold"),
                    anchor="w",
                    width=18,
                )
                name_label.pack(side=tk.LEFT)

                ts_label = tk.Label(
                    item,
                    text=created,
                    bg="#3b3b3b",
                    fg="#aaaaaa",
                    font=("Consolas", 9),
                    anchor="w",
                )
                ts_label.pack(side=tk.LEFT, padx=(6, 0))

                test_btn = ttk.Button(
                    item,
                    text="Test Action",
                    command=lambda a=rec.get("actions", []): self._play_callback(a),
                )
                test_btn.pack(side=tk.RIGHT)

                del_btn = ttk.Button(
                    item,
                    text="Delete",
                    command=lambda i=idx: self._delete_record(i),
                )
                del_btn.pack(side=tk.RIGHT, padx=(0, 8))

                sep = tk.Frame(scrollable_frame, bg="#4a4a4a", height=1)
                sep.pack(fill=tk.X, padx=5)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _load_records(self) -> list[dict]:
        try:
            if os.path.exists(self._records_path):
                with open(self._records_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("records", [])
        except Exception:
            return []
        return []

    def _migrate_from_text(self) -> bool:
        record_path = _app_path("recorded_actions.txt")
        if not os.path.exists(record_path):
            return False
        try:
            with open(record_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
        except Exception:
            return False

        if not lines:
            return False

        events = []
        for line in lines:
            if not line.startswith("[") or "]" not in line:
                continue
            ts_raw = line[1: line.index("]")]
            try:
                ts = datetime.fromisoformat(ts_raw)
            except Exception:
                continue
            rest = line[line.index("]") + 1 :].strip()
            if rest.startswith("click"):
                data = {"type": "click", "ts": ts}
                for part in rest.split():
                    if part.startswith("x="):
                        data["x"] = part.split("=", 1)[1]
                    elif part.startswith("y="):
                        data["y"] = part.split("=", 1)[1]
                    elif part.startswith("button="):
                        data["button"] = part.split("=", 1)[1]
                events.append(data)
            elif rest.startswith("key="):
                key = None
                for part in rest.split():
                    if part.startswith("key="):
                        key = part.split("=", 1)[1]
                        break
                events.append({"type": "key", "key": key, "ts": ts})

        if not events:
            return False

        events.sort(key=lambda e: e["ts"])
        actions = []
        prev_ts = events[0]["ts"]
        for ev in events:
            delay = (ev["ts"] - prev_ts).total_seconds()
            item = {"type": ev["type"], "delay": delay}
            if ev["type"] == "click":
                item["x"] = ev.get("x", 0)
                item["y"] = ev.get("y", 0)
                item["button"] = ev.get("button", "left")
            elif ev["type"] == "key":
                item["key"] = ev.get("key", "")
            actions.append(item)
            prev_ts = ev["ts"]

        record = {
            "name": "Account Switch",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "actions": actions,
        }

        data = {"records": []}
        try:
            if os.path.exists(self._records_path):
                with open(self._records_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            data = {"records": []}
        data.setdefault("records", []).append(record)
        try:
            with open(self._records_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            return False
        return True

    def _delete_record(self, index: int) -> None:
        records = self._load_records()
        if index < 0 or index >= len(records):
            return
        records.pop(index)
        try:
            with open(self._records_path, "w", encoding="utf-8") as f:
                json.dump({"records": records}, f, indent=2)
        except Exception:
            pass
        for widget in self.winfo_children():
            widget.destroy()
        self._setup_ui()

def main():
    app = MTGBotUI()
    app.mainloop()


if __name__ == "__main__":
    main()
