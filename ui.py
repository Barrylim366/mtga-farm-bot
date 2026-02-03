import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime
import os
import time
from PIL import Image, ImageTk
import json
import re
import shutil
import subprocess
import threading
from Controller.Utilities.input_controller import InputControllerError, create_input_controller

# Import bot components
from Controller.MTGAController.Controller import Controller
from AI.DummyAI import DummyAI
from Game import Game


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
        self.calibrate_btn = tk.Button(row1, text="Calibrate", command=self._start_calibration,
                                        bg="#4a4a4a", fg="white", font=("Segoe UI", 10),
                                        activebackground="#5a5a5a", activeforeground="white",
                                        relief=tk.FLAT, padx=15, pady=5)
        self.calibrate_btn.pack(side=tk.LEFT)

        # Wayland-friendly capture (slurp)
        self.slurp_btn = tk.Button(row1, text="Capture (slurp)", command=self._capture_with_slurp,
                                   bg="#4a4a4a", fg="white", font=("Segoe UI", 10),
                                   activebackground="#5a5a5a", activeforeground="white",
                                   relief=tk.FLAT, padx=15, pady=5)
        self.slurp_btn.pack(side=tk.LEFT, padx=(10, 0))

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
        self.saved_btn = tk.Button(main_frame, text="Saved Buttons", command=self._show_saved_buttons,
                                   bg="#4a4a4a", fg="white", font=("Segoe UI", 10),
                                   activebackground="#5a5a5a", activeforeground="white",
                                   relief=tk.FLAT, padx=15, pady=5)
        self.saved_btn.pack()

        back_btn = tk.Button(
            main_frame,
            text="Back",
            command=self.destroy,
            bg="#3a3a3a",
            fg="white",
            font=("Segoe UI", 10),
            activebackground="#444444",
            activeforeground="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
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

        self.test_btn = tk.Button(
            test_frame,
            text="Test Click",
            command=self._test_saved_click,
            bg="#4a4a4a",
            fg="white",
            font=("Segoe UI", 10),
            activebackground="#5a5a5a",
            activeforeground="white",
            relief=tk.FLAT,
            padx=15,
            pady=5,
        )
        self.test_btn.pack(side=tk.LEFT)

    def _update_calibration_capabilities(self):
        # Under Wayland, pynput global capture is often unavailable; also it may not be installed.
        has_slurp = shutil.which("slurp") is not None
        self.slurp_btn.config(state=(tk.NORMAL if has_slurp else tk.DISABLED))

        can_use_pynput = True
        try:
            import pynput  # noqa: F401
        except Exception:
            can_use_pynput = False

        if not can_use_pynput:
            self.calibrate_btn.config(state=tk.DISABLED)
            if has_slurp:
                self.instruction_label.config(
                    text="Wayland: nutze 'Capture (slurp)' (kein Live-Tracking ohne pynput).",
                    fg="#aaaaaa",
                )
            else:
                self.instruction_label.config(
                    text="Installiere 'slurp' f++r Wayland-Kalibrierung.",
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
        self.calibrate_btn.config(text="Stop", bg="#aa4444")
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
            # No modal error on Wayland; guide user to slurp-based capture instead.
            self.instruction_label.config(
                text="Kalibrierung per Live-Tracking nicht verf++gbar. Nutze 'Capture (slurp)'.",
                fg="#ffcc66",
            )

    def _stop_calibration(self):
        self.is_calibrating = False
        self.calibrate_btn.config(text="Calibrate", bg="#4a4a4a")
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

    def _capture_with_slurp(self):
        if shutil.which("slurp") is None:
            self.instruction_label.config(text="`slurp` nicht gefunden. Installiere es zuerst.", fg="#ff6666")
            return

        def _worker():
            try:
                # Most slurp builds support point selection with `-p`.
                # We parse the first two integers we see to be resilient across formats.
                out = subprocess.check_output(["slurp", "-p"], text=True).strip()
                nums = [int(n) for n in re.findall(r"-?\\d+", out)]
                if len(nums) < 2:
                    raise ValueError(f"Unexpected slurp output: {out!r}")
                x, y = nums[0], nums[1]
                self.current_x, self.current_y = x, y
                self.after(0, self._update_coordinates)
                self.after(0, self._save_coordinates)
            except Exception as e:
                self.after(0, lambda: self.instruction_label.config(text=f"slurp fehlgeschlagen: {e}", fg="#ff6666"))

        threading.Thread(target=_worker, daemon=True).start()

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

        back_btn = tk.Button(
            main_frame,
            text="Back",
            command=self.destroy,
            bg="#3a3a3a",
            fg="white",
            font=("Segoe UI", 10),
            activebackground="#444444",
            activeforeground="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
        )
        back_btn.pack(pady=(10, 0))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        back_btn = tk.Button(
            main_frame,
            text="Back",
            command=self.destroy,
            bg="#3a3a3a",
            fg="white",
            font=("Segoe UI", 10),
            activebackground="#444444",
            activeforeground="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
        )
        back_btn.pack(pady=(10, 0))


class ConfigManager:
    """Manages loading and saving of calibration configuration"""

    def __init__(self, config_path="calibration_config.json"):
        self.config_path = config_path
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
            "log_path": detected_log or "C:/Users/giaco/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log",
            "screen_bounds": [[0, 0], [2560, 1440]],
            "input_backend": "auto",
            "account_switch_minutes": 0,
            "credentials_path": "C:/Users/giaco/source/repos/MTG_AI_Bot-master/MTG_AI_Bot-master/credentials.txt",
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

    def get_credentials_path(self) -> str:
        return self.config.get("credentials_path", "")

    def set_credentials_path(self, path: str) -> None:
        self.config["credentials_path"] = path or ""
        self._save_config()

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
        cleaned = [str(item) for item in order if item]
        self.config["account_play_order"] = cleaned
        self._save_config()


class MTGBotUI(tk.Tk):
    """Main application window"""

    def __init__(self):
        super().__init__()

        self.title("MTGA Bot")
        # Slightly taller window so all controls fit on typical font/DPI setups
        self.geometry("400x680")
        self.resizable(False, True)
        self.configure(bg="#1e1e1e")

        self.config_manager = ConfigManager()
        self.bot_running = False
        self.game = None
        self.bot_thread = None
        self.session_games = 0
        self.session_wins = 0
        self.settings_window = None

        self._setup_ui()

    def _setup_ui(self):
        # Main container with grid
        main_frame = tk.Frame(self, bg="#1e1e1e")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)

        # Logo section
        logo_frame = tk.Frame(main_frame, bg="#1e1e1e")
        logo_frame.pack(fill=tk.X, pady=(0, 30))

        # Load and display logo
        try:
            logo_path = os.path.join(os.path.dirname(__file__), "ui_symbol.jpg")
            logo_image = Image.open(logo_path)
            logo_image = logo_image.resize((120, 120), Image.Resampling.LANCZOS)

            # Convert to RGBA and mask everything outside the circle to the UI background.
            # This removes the checkerboard/square border in the source image while keeping the round gray badge.
            logo_image = logo_image.convert("RGBA")
            bg_color = (30, 30, 30, 255)  # #1e1e1e in RGBA
            bg_image = Image.new("RGBA", logo_image.size, bg_color)
            # Build an anti-aliased circular mask by drawing at higher res and downsampling.
            from PIL import ImageDraw
            aa = 4
            pad = 6
            mask_big = Image.new("L", (logo_image.width * aa, logo_image.height * aa), 0)
            draw = ImageDraw.Draw(mask_big)
            pad_big = pad * aa
            draw.ellipse(
                (pad_big, pad_big, logo_image.width * aa - pad_big - 1, logo_image.height * aa - pad_big - 1),
                fill=255,
            )
            mask = mask_big.resize(logo_image.size, Image.Resampling.LANCZOS)
            logo_image = Image.composite(logo_image, bg_image, mask)

            self.logo_photo = ImageTk.PhotoImage(logo_image)
            logo_label = tk.Label(logo_frame, image=self.logo_photo, bg="#1e1e1e")
            logo_label.pack()
        except Exception as e:
            # Fallback if image can't be loaded
            logo_label = tk.Label(logo_frame, text="MTG", bg="#1e1e1e", fg="white",
                                 font=("Segoe UI", 36, "bold"))
            logo_label.pack()

        # Title
        title_label = tk.Label(main_frame, text="MTGA Bot", bg="#1e1e1e", fg="white",
                              font=("Segoe UI", 24, "bold"))
        title_label.pack(pady=(0, 40))

        # Buttons frame
        buttons_frame = tk.Frame(main_frame, bg="#1e1e1e")
        buttons_frame.pack(fill=tk.X)

        # Button style settings
        btn_width = 20
        btn_height = 2
        btn_font = ("Segoe UI", 11)
        btn_pady = 8

        # Start Button
        self.start_btn = tk.Button(buttons_frame, text="Start Bot", command=self._start_bot,
                                   bg="#2d5a2d", fg="white", font=btn_font,
                                   activebackground="#3d6a3d", activeforeground="white",
                                   relief=tk.FLAT, width=btn_width, height=btn_height,
                                   cursor="hand2")
        self.start_btn.pack(pady=btn_pady)

        # Stop Button
        self.stop_btn = tk.Button(buttons_frame, text="Stop Bot", command=self._stop_bot,
                                  bg="#5a2d2d", fg="white", font=btn_font,
                                  activebackground="#6a3d3d", activeforeground="white",
                                  relief=tk.FLAT, width=btn_width, height=btn_height,
                                  state=tk.DISABLED, cursor="hand2")
        self.stop_btn.pack(pady=btn_pady)

        # Calibrate Button
        self.calibrate_btn = tk.Button(buttons_frame, text="Calibrate", command=self._open_calibration,
                                       bg="#4a4a4a", fg="white", font=btn_font,
                                       activebackground="#5a5a5a", activeforeground="white",
                                       relief=tk.FLAT, width=btn_width, height=btn_height,
                                       cursor="hand2")
        self.calibrate_btn.pack(pady=btn_pady)

        # Settings Button
        self.session_btn = tk.Button(buttons_frame, text="Settings", command=self._open_settings,
                                     bg="#2d4f8a", fg="white", font=btn_font,
                                     activebackground="#3a63aa", activeforeground="white",
                                     relief=tk.FLAT, width=btn_width, height=btn_height,
                                     cursor="hand2")
        self.session_btn.pack(pady=btn_pady)

        # Status section
        status_frame = tk.Frame(main_frame, bg="#1e1e1e")
        status_frame.pack(fill=tk.X, pady=(30, 0))

        self.status_label = tk.Label(status_frame, text="Status: Stopped", bg="#1e1e1e",
                                    fg="#ff6666", font=("Segoe UI", 10))
        self.status_label.pack()

    def _start_bot(self):
        if self.bot_running:
            return

        self.bot_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.calibrate_btn.config(state=tk.DISABLED)
        self.status_label.config(text="Status: Running", fg="#66ff66")

        # Start bot in separate thread
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()

    def _run_bot(self):
        try:
            log_path = self.config_manager.get_log_path()
            click_targets = self.config_manager.get_click_targets()
            screen_bounds = self.config_manager.get_screen_bounds()
            input_backend = self.config_manager.get_input_backend()
            account_switch_minutes = self.config_manager.get_account_switch_minutes()
            credentials_path = self.config_manager.get_credentials_path()
            account_cycle_index = self.config_manager.get_account_cycle_index()
            account_play_order = self.config_manager.get_account_play_order()

            controller = Controller(log_path=log_path, screen_bounds=screen_bounds,
                                   click_targets=click_targets, input_backend=input_backend,
                                   account_switch_minutes=account_switch_minutes,
                                   credentials_path=credentials_path,
                                   account_cycle_index=account_cycle_index,
                                   account_play_order=account_play_order)
            ai = DummyAI()
            self.game = Game(controller, ai)
            self.game.start()

            # Wrap match end callback so we can update session stats and still restart games.
            def _on_match_end(won=None):
                self.session_games += 1
                if won is True:
                    self.session_wins += 1
                self.after(0, self._update_settings_window)
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
            self.after(0, lambda: self._handle_bot_error(str(e)))

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

        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.calibrate_btn.config(state=tk.NORMAL)
        self.status_label.config(text="Status: Stopped", fg="#ff6666")

    def _open_calibration(self):
        CalibrationWindow(self, self.config_manager)

    def _open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        self.settings_window = SettingsWindow(self, self.config_manager, self.session_games, self.session_wins)

    def _update_settings_window(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.update_stats(self.session_games, self.session_wins)


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, config_manager: ConfigManager, games: int, wins: int):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("620x360")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")
        self._log_window = None
        self._config_manager = config_manager
        self._recording = False
        self._record_ignore_first = False
        self._mouse_listener = None
        self._keyboard_listener = None
        self._playback_thread = None
        self._playback_keyboard_listener = None
        self._playback_stop_event = threading.Event()
        self._current_record_events = []
        self._records_path = os.path.join(os.path.dirname(__file__), "recorded_actions_records.json")
        self._switch_save_job = None

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

        title = tk.Label(frame, text="Current session", bg="#2b2b2b", fg="white",
                         font=("Segoe UI", 12, "bold"))
        title.pack(pady=(0, 12))

        stats_frame = tk.Frame(frame, bg="#2b2b2b")
        stats_frame.pack(fill=tk.X)

        self.games_label = tk.Label(stats_frame, text="", bg="#2b2b2b", fg="#00ff00", font=("Consolas", 12))
        self.games_label.pack(anchor="w", pady=4)

        self.wins_label = tk.Label(stats_frame, text="", bg="#2b2b2b", fg="#00ff00", font=("Consolas", 12))
        self.wins_label.pack(anchor="w", pady=4)

        sep = tk.Frame(frame, bg="#4a4a4a", height=1)
        sep.pack(fill=tk.X, pady=(0, 12))

        log_row = tk.Frame(frame, bg="#2b2b2b")
        log_row.pack(fill=tk.X)

        log_label = tk.Label(log_row, text="Show Log", bg="#2b2b2b", fg="white", font=("Segoe UI", 10))
        log_label.pack(side=tk.LEFT)

        self.show_log_var = tk.BooleanVar(value=False)
        show_log_cb = tk.Checkbutton(
            log_row,
            variable=self.show_log_var,
            command=self._toggle_log_window,
            bg="#2b2b2b",
            fg="white",
            activebackground="#2b2b2b",
            activeforeground="white",
            selectcolor="#2b2b2b",
        )
        show_log_cb.pack(side=tk.LEFT, padx=(10, 0))

        switch_row = tk.Frame(frame, bg="#2b2b2b")
        switch_row.pack(fill=tk.X, pady=(12, 0))

        switch_label = tk.Label(
            switch_row,
            text="Switch Account",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        switch_label.pack(side=tk.LEFT)

        switch_inner = tk.Frame(switch_row, bg="#2b2b2b")
        switch_inner.pack(side=tk.LEFT, padx=(10, 0))

        switch_inner_label = tk.Label(
            switch_inner,
            text="Switch account (min)",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        switch_inner_label.pack(side=tk.LEFT)

        self.switch_minutes_var = tk.StringVar(value=str(self._config_manager.get_account_switch_minutes()))
        switch_entry = tk.Entry(
            switch_inner,
            textvariable=self.switch_minutes_var,
            width=6,
            bg="#1e1e1e",
            fg="white",
            insertbackground="white",
            relief=tk.FLAT,
        )
        switch_entry.pack(side=tk.LEFT, padx=(10, 6))
        switch_entry.bind("<Return>", lambda _e: self._save_switch_minutes())
        switch_entry.bind("<FocusOut>", lambda _e: self._save_switch_minutes())
        switch_entry.bind("<KeyRelease>", self._debounced_save_switch_minutes)

        switch_hint = tk.Label(
            switch_inner,
            text="0 = off",
            bg="#2b2b2b",
            fg="#aaaaaa",
            font=("Segoe UI", 9),
        )
        switch_hint.pack(side=tk.LEFT)

        order_row = tk.Frame(frame, bg="#2b2b2b")
        order_row.pack(fill=tk.X, pady=(10, 0))

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

        order_choices = ["", "Acc_1", "Acc_2", "Acc_3"]
        current_order = self._config_manager.get_account_play_order()
        current_order = current_order[:3] + ["", "", ""]

        self._order_vars = []
        self._order_boxes = []
        for idx in range(3):
            num_label = tk.Label(
                order_inner,
                text=str(idx + 1),
                bg="#2b2b2b",
                fg="#aaaaaa",
                font=("Segoe UI", 9),
                width=2,
            )
            num_label.pack(side=tk.LEFT, padx=(0, 2))

            var = tk.StringVar(value=current_order[idx] if idx < len(current_order) else "")
            combo = ttk.Combobox(
                order_inner,
                textvariable=var,
                values=order_choices,
                state="readonly",
                width=7,
            )
            combo.configure(style="Dark.TCombobox")
            combo.pack(side=tk.LEFT, padx=(0, 6))
            combo.bind("<<ComboboxSelected>>", lambda _e: self._save_account_play_order())
            self._order_vars.append(var)
            self._order_boxes.append(combo)

        record_row = tk.Frame(frame, bg="#2b2b2b")
        record_row.pack(fill=tk.X, pady=(12, 0))

        record_label = tk.Label(
            record_row,
            text="Record Action",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        record_label.pack(side=tk.LEFT)

        self.record_btn = tk.Button(
            record_row,
            text="Record",
            command=self._record_actions_prompt,
            bg="#3a3a3a",
            fg="white",
            activebackground="#444444",
            activeforeground="white",
            relief=tk.FLAT,
            padx=10,
            pady=2,
        )
        self.record_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.show_records_btn = tk.Button(
            record_row,
            text="Show Records",
            command=self._show_records,
            bg="#3a3a3a",
            fg="white",
            activebackground="#444444",
            activeforeground="white",
            relief=tk.FLAT,
            padx=10,
            pady=2,
        )
        self.show_records_btn.pack(side=tk.LEFT, padx=(8, 0))

        back_btn = tk.Button(
            frame,
            text="Back",
            command=self.destroy,
            bg="#3a3a3a",
            fg="white",
            font=("Segoe UI", 10),
            activebackground="#444444",
            activeforeground="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
        )
        back_btn.pack(anchor="w", pady=(12, 0))

        self.update_stats(games, wins)

    def update_stats(self, games: int, wins: int):
        self.games_label.config(text=f"Games played: {games}")
        self.wins_label.config(text=f"Win: {wins}")

    def _toggle_log_window(self):
        if self.show_log_var.get():
            if self._log_window and self._log_window.winfo_exists():
                self._log_window.lift()
                return
            self._log_window = LogWindow(self, "human.log")
        else:
            if self._log_window and self._log_window.winfo_exists():
                self._log_window.destroy()
            self._log_window = None

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

    def _save_account_play_order(self):
        order = [var.get().strip() for var in getattr(self, "_order_vars", [])]
        order = [item for item in order if item]
        self._config_manager.set_account_play_order(order)

    def _debounced_save_switch_minutes(self, _event=None):
        if self._switch_save_job:
            try:
                self.after_cancel(self._switch_save_job)
            except Exception:
                pass
        self._switch_save_job = self.after(500, self._save_switch_minutes)

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
        self.record_btn.config(text="Stop")
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
        self.record_btn.config(text="Record")
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

        ok_btn = tk.Button(
            prompt,
            text="Save",
            command=_save_and_close,
            bg="#3a3a3a",
            fg="white",
            activebackground="#444444",
            activeforeground="white",
            relief=tk.FLAT,
            padx=10,
            pady=2,
        )
        ok_btn.pack(pady=10)
        prompt.bind("<Return>", _save_and_close)

    def _save_record_snapshot(self, name: str):
        events = list(self._current_record_events)
        if not events:
            return
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
        self.test_action_btn.config(state=tk.NORMAL)

    def destroy(self):
        try:
            if self._log_window and self._log_window.winfo_exists():
                self._log_window.destroy()
            if self._recording:
                self._stop_recording()
        finally:
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

        back_btn = tk.Button(
            frame,
            text="Back",
            command=self.destroy,
            bg="#3a3a3a",
            fg="white",
            font=("Segoe UI", 10),
            activebackground="#444444",
            activeforeground="white",
            relief=tk.FLAT,
            padx=12,
            pady=4,
        )
        back_btn.grid(row=2, column=0, sticky="w", pady=(8, 0))

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

                test_btn = tk.Button(
                    item,
                    text="Test Action",
                    command=lambda a=rec.get("actions", []): self._play_callback(a),
                    bg="#00ff00",
                    fg="#1e1e1e",
                    activebackground="#33ff33",
                    activeforeground="#1e1e1e",
                    relief=tk.FLAT,
                    padx=8,
                    pady=2,
                )
                test_btn.pack(side=tk.RIGHT)

                del_btn = tk.Button(
                    item,
                    text="Delete",
                    command=lambda i=idx: self._delete_record(i),
                    bg="#5a2a2a",
                    fg="white",
                    activebackground="#6a3333",
                    activeforeground="white",
                    relief=tk.FLAT,
                    padx=8,
                    pady=2,
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
        record_path = os.path.join(os.path.dirname(__file__), "recorded_actions.txt")
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
