import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import json
import os
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
            "assign_damage_done"
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
                    text="Installiere 'slurp' für Wayland-Kalibrierung.",
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
                text="Kalibrierung per Live-Tracking nicht verfügbar. Nutze 'Capture (slurp)'.",
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
            self.instruction_label.config(text=f"Kein gespeicherter Punkt für '{button_name}'.", fg="#ffcc66")
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
        self.geometry("350x400")
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
            for button_name, coord in coords.items():
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
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return self._default_config()
        return self._default_config()

    def _default_config(self):
        detected_log = self._detect_player_log_path()
        return {
            "log_path": detected_log or "C:/Users/giaco/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log",
            "screen_bounds": [[0, 0], [2560, 1440]],
            "input_backend": "auto",
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


class MTGBotUI(tk.Tk):
    """Main application window"""

    def __init__(self):
        super().__init__()

        self.title("MTGA Bot")
        self.geometry("400x600")
        self.resizable(False, False)
        self.configure(bg="#1e1e1e")

        self.config_manager = ConfigManager()
        self.bot_running = False
        self.game = None
        self.bot_thread = None
        self.session_games = 0
        self.session_wins = 0
        self.session_info_window = None

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

            # Convert to RGBA and replace white/light background with UI background color
            logo_image = logo_image.convert("RGBA")
            pixels = logo_image.load()
            bg_color = (30, 30, 30, 255)  # #1e1e1e in RGBA

            for y in range(logo_image.height):
                for x in range(logo_image.width):
                    r, g, b, a = pixels[x, y]
                    # Replace light gray/white pixels (the background circle area)
                    if r > 180 and g > 180 and b > 180:
                        pixels[x, y] = bg_color
                    # Also handle the checkered/transparent areas
                    elif r > 150 and g > 150 and b > 150:
                        pixels[x, y] = bg_color

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

        # Session Info Button
        self.session_btn = tk.Button(buttons_frame, text="Session info", command=self._open_session_info,
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

            controller = Controller(log_path=log_path, screen_bounds=screen_bounds,
                                   click_targets=click_targets, input_backend=input_backend)
            ai = DummyAI()
            self.game = Game(controller, ai)
            self.game.start()

            # Wrap match end callback so we can update session stats and still restart games.
            def _on_match_end(won=None):
                self.session_games += 1
                if won is True:
                    self.session_wins += 1
                self.after(0, self._update_session_info_window)
                try:
                    self.game.on_match_end(won)
                except TypeError:
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
                self.game.controller.end_game()
            except:
                pass
            self.game = None

        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.calibrate_btn.config(state=tk.NORMAL)
        self.status_label.config(text="Status: Stopped", fg="#ff6666")

    def _open_calibration(self):
        CalibrationWindow(self, self.config_manager)

    def _open_session_info(self):
        if self.session_info_window and self.session_info_window.winfo_exists():
            self.session_info_window.lift()
            self.session_info_window.focus_force()
            return
        self.session_info_window = SessionInfoWindow(self, self.session_games, self.session_wins)

    def _update_session_info_window(self):
        if self.session_info_window and self.session_info_window.winfo_exists():
            self.session_info_window.update_stats(self.session_games, self.session_wins)


class SessionInfoWindow(tk.Toplevel):
    def __init__(self, parent, games: int, wins: int):
        super().__init__(parent)
        self.title("Session info")
        self.geometry("320x160")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")

        frame = tk.Frame(self, bg="#2b2b2b", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(frame, text="Current session", bg="#2b2b2b", fg="white",
                         font=("Segoe UI", 12, "bold"))
        title.pack(pady=(0, 12))

        self.games_label = tk.Label(frame, text="", bg="#2b2b2b", fg="#00ff00", font=("Consolas", 12))
        self.games_label.pack(pady=4)

        self.wins_label = tk.Label(frame, text="", bg="#2b2b2b", fg="#00ff00", font=("Consolas", 12))
        self.wins_label.pack(pady=4)

        hint = tk.Label(frame, text="Resets when ui.py restarts", bg="#2b2b2b", fg="#aaaaaa",
                        font=("Segoe UI", 9))
        hint.pack(pady=(10, 0))

        self.update_stats(games, wins)

    def update_stats(self, games: int, wins: int):
        self.games_label.config(text=f"Games played: {games}")
        self.wins_label.config(text=f"Win: {wins}")


def main():
    app = MTGBotUI()
    app.mainloop()


if __name__ == "__main__":
    main()
