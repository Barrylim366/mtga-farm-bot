"""Simple Tkinter launcher to start/stop the MTGA bot."""
from __future__ import annotations

import subprocess
import sys
from argparse import ArgumentParser
import os
import json
import tkinter as tk
from pathlib import Path
from typing import Optional
mouse = None
try:
    from pynput import mouse as _pynput_mouse
    mouse = _pynput_mouse
except ModuleNotFoundError:
    pass

# Path to the config used to start the bot; adjust if needed.
DEFAULT_CONFIG = "config.json"
LOG_PATH = "bot_gui_subprocess.log"


class BotController:
    def __init__(self, base_dir: Path, status_var: tk.StringVar) -> None:
        self.base_dir = base_dir
        self.status_var = status_var
        self.process: Optional[subprocess.Popen] = None
        self._log_handle = None
        self.config_path = self.base_dir / DEFAULT_CONFIG

    def start_bot(self) -> None:
        if self.process and self.process.poll() is None:
            self.status_var.set("Bot already running.")
            return

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        if getattr(sys, "frozen", False):
            # When packaged (e.g., PyInstaller), reuse the same executable with a bot-only flag.
            cmd = [sys.executable, "--run-bot", "--config", DEFAULT_CONFIG]
        else:
            # Prefer the repo's virtualenv python if present.
            venv_python = self._find_venv_python()
            interpreter = venv_python or sys.executable
            cmd = [
                interpreter,
                "-m",
                "mtga_bot.main",
                "--config",
                DEFAULT_CONFIG,
            ]
        try:
            self._log_handle = Path(self.base_dir / LOG_PATH).open("a", encoding="utf-8")
            log_path = Path(self.base_dir / LOG_PATH).resolve()
            self.process = subprocess.Popen(
                cmd,
                cwd=self.base_dir,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                env=env,
            )
            self.status_var.set(
                f"Bot started. Using {cmd[0]} | Logs -> {log_path}"
            )
            # If the process exits immediately, surface the return code.
            self.process.poll()
            if self.process.returncode is not None:
                self.status_var.set(
                    f"Bot exited immediately (code {self.process.returncode}). Check logs -> {log_path}"
                )
        except Exception as exc:
            self.process = None
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None
            self.status_var.set(f"Failed to start bot: {exc}")

    def stop_bot(self) -> None:
        if not self.process:
            self.status_var.set("Bot is not running.")
            return
        if self.process.poll() is not None:
            self.status_var.set("Bot already stopped.")
            self.process = None
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.status_var.set("Bot stopped.")
        except Exception as exc:
            self.status_var.set(f"Failed to stop bot: {exc}")
        finally:
            self.process = None
            if self._log_handle:
                try:
                    self._log_handle.close()
                except Exception:
                    pass
                self._log_handle = None

    def _find_venv_python(self) -> Optional[str]:
        posix_path = self.base_dir / ".venv" / "bin" / "python"
        windows_path = self.base_dir / ".venv" / "Scripts" / "python.exe"
        if posix_path.exists():
            return str(posix_path)
        if windows_path.exists():
            return str(windows_path)
        return None

    # --- Calibration helpers ---
    def load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        try:
            return json.loads(self.config_path.read_text())
        except Exception:
            return {}

    def save_config(self, data: dict) -> None:
        try:
            self.config_path.write_text(json.dumps(data, indent=2))
            self.status_var.set(f"Config updated at {self.config_path}")
        except Exception as exc:
            self.status_var.set(f"Failed to save config: {exc}")


def create_window(base_dir: Path) -> tk.Tk:
    window = tk.Tk()
    window.title("MTGA Bot Launcher")

    status_var = tk.StringVar(value="Idle.")
    controller = BotController(base_dir, status_var)
    hand_points: list[tuple[float, float]] = []

    tk.Label(window, text="MTGA Bot Launcher").pack(pady=(10, 5))

    button_frame = tk.Frame(window)
    button_frame.pack(pady=5)

    tk.Button(button_frame, text="Start Bot", width=12, command=controller.start_bot).pack(
        side=tk.LEFT, padx=5
    )
    tk.Button(button_frame, text="Stop Bot", width=12, command=controller.stop_bot).pack(
        side=tk.LEFT, padx=5
    )

    tk.Label(window, textvariable=status_var, fg="blue").pack(pady=(5, 10))

    # Calibration UI
    calib_frame = tk.LabelFrame(window, text="Calibration", labelanchor="n")
    calib_visible = tk.BooleanVar(value=False)

    def toggle_calib() -> None:
        if calib_visible.get():
            calib_frame.pack_forget()
            calib_visible.set(False)
        else:
            calib_frame.pack(fill="x", padx=10, pady=5)
            calib_visible.set(True)

    tk.Button(window, text="Calibrate", command=toggle_calib).pack(pady=(0, 5))

    def get_pyautogui():
        try:
            import pyautogui  # type: ignore
            return pyautogui
        except ModuleNotFoundError:
            # Try to load from the repo's venv if present.
            venv_site = list((controller.base_dir / ".venv").glob("lib/python*/site-packages"))
            for sp in venv_site:
                if str(sp) not in sys.path:
                    sys.path.append(str(sp))
                    try:
                        import pyautogui  # type: ignore
                        return pyautogui
                    except ModuleNotFoundError:
                        continue
            status_var.set("pyautogui missing. Activate .venv or install dependencies.")
            return None

    capture_in_progress = {"target": False, "hand": False}
    tk.Label(calib_frame, text="1) Select target").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(2, 0)
    )
    tk.Label(calib_frame, text="2) Click 'Arm capture', window minimizes, click the target").grid(
        row=1, column=0, columnspan=3, sticky="w"
    )
    tk.Label(calib_frame, text="Note: choose hand_7 and click 7 cards left->right").grid(
        row=2, column=0, columnspan=3, sticky="w"
    )

    COMMON_TARGETS = [
        "queue_button",
        "keep_hand",
        "mulligan",
        "next_button",
        "end_turn_button",
        "submit_button",
        "concede",
        "all_attack_btn",
        "no_attack_btn",
        "no_blocks_btn",
        "hand_7",
    ]
    target_var = tk.StringVar(value=COMMON_TARGETS[0])
    tk.Label(calib_frame, text="Button name:").grid(row=3, column=0, sticky="e", pady=4)
    tk.OptionMenu(calib_frame, target_var, *COMMON_TARGETS).grid(row=3, column=1, sticky="w", pady=4, padx=4)
    custom_entry = tk.Entry(calib_frame, width=20)
    custom_entry.grid(row=3, column=2, sticky="w", pady=4, padx=4)
    custom_entry.insert(0, "custom_name")


    def get_mouse():
        try:
            from pynput import mouse as m  # type: ignore
            return m
        except ModuleNotFoundError:
            venv_site = list((controller.base_dir / ".venv").glob("lib/python*/site-packages"))
            for sp in venv_site:
                if str(sp) not in sys.path:
                    sys.path.append(str(sp))
                try:
                    from pynput import mouse as m  # type: ignore
                    return m
                except ModuleNotFoundError:
                    continue
        status_var.set("pynput missing. Activate .venv or install dependencies.")
        return None

    def _resolve_target_name() -> str:
        name = target_var.get().strip()
        if name == "custom_name" or not name:
            name = custom_entry.get().strip()
        return name

    def _arm_listener(kind: str, on_click_cb) -> None:
        mouse_mod = get_mouse()
        if mouse_mod is None:
            return
        if capture_in_progress[kind]:
            status_var.set(f"{kind.capitalize()} capture already armed. Click once.")
            return
        capture_in_progress[kind] = True

        def restore(result_msg: str) -> None:
            capture_in_progress[kind] = False
            try:
                window.deiconify()
            except Exception:
                pass
            status_var.set(result_msg)

        def listener_thread():
            try:
                with mouse_mod.Listener(on_click=on_click_cb(restore)) as listener:
                    listener.join()
            finally:
                if capture_in_progress[kind]:
                    # Timeout or error; restore anyway.
                    window.after(0, restore, f"{kind.capitalize()} capture stopped.")

        status_var.set("Armed. Window minimized. Click once.")
        try:
            window.iconify()
        except Exception:
            pass
        # Run listener in a thread so Tk does not block
        import threading

        threading.Thread(target=listener_thread, daemon=True).start()

    def capture_target() -> None:
        pyautogui = get_pyautogui()
        if not pyautogui:
            return

        name = _resolve_target_name()
        if not name:
            status_var.set("Choose or enter a button name first.")
            return

        width, height = pyautogui.size()
        hand_points.clear()

        def make_on_click(restore_cb):
            def _inner(x, y, button, pressed):
                if not pressed:
                    return
                rel = (x / width, y / height)
                # Special handling for hand_7: collect 7 clicks, then save hand/land ratios.
                if name == "hand_7":
                    hand_points.append(rel)
                    if len(hand_points) < 7:
                        status_msg = f"Hand slot {len(hand_points)}/7 saved ({rel[0]:.4f},{rel[1]:.4f})"
                        window.after(0, status_var.set, status_msg)
                        return  # continue listening
                    xs = [p[0] for p in hand_points]
                    ys = [p[1] for p in hand_points]
                    hand_y = sum(ys) / len(ys)
                    cfg = controller.load_config()
                    cfg["hand_x_ratios"] = xs
                    cfg["land_x_ratios"] = xs
                    cfg["hand_y_ratio"] = hand_y
                    cfg["land_y_ratio"] = hand_y
                    controller.save_config(cfg)
                    window.after(0, restore_cb, "Saved hand_7 (7 slots).")
                    return False
                else:
                    cfg = controller.load_config()
                    targets = cfg.get("click_targets") or {}
                    targets[name] = rel
                    cfg["click_targets"] = targets
                    controller.save_config(cfg)
                    window.after(0, restore_cb, f"Saved {name} -> ({rel[0]:.4f}, {rel[1]:.4f})")
                    return False

            return _inner

        _arm_listener("target", make_on_click)

    tk.Button(calib_frame, text="Arm capture", command=capture_target).grid(
        row=4, column=2, padx=4, pady=4
    )

    def show_targets() -> None:
        cfg = controller.load_config()
        targets = cfg.get("click_targets") or {}
        parts = []
        if targets:
            items = [f"{k}: ({v[0]:.4f}, {v[1]:.4f})" for k, v in targets.items()]
            parts.append("Targets -> " + " | ".join(items))
        hand_x = cfg.get("hand_x_ratios")
        hand_y = cfg.get("hand_y_ratio")
        if hand_x and hand_y is not None:
            hx = ", ".join(f"{x:.4f}" for x in hand_x)
            parts.append(f"Hand_7 -> y={hand_y:.4f}, x=[{hx}]")
        if not parts:
            status_var.set("No targets captured yet.")
            return
        status_var.set(" | ".join(parts))

    tk.Button(calib_frame, text="Show captured targets", command=show_targets).grid(
        row=5, column=0, padx=4, pady=6, sticky="w"
    )

    def on_close() -> None:
        controller.stop_bot()
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", on_close)
    return window


def parse_args() -> ArgumentParser:
    parser = ArgumentParser(description="MTGA bot GUI launcher")
    parser.add_argument(
        "--run-bot",
        action="store_true",
        help="Internal flag: run the bot CLI instead of the GUI.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Path to config JSON (used with --run-bot).",
    )
    return parser


def main() -> None:
    parser = parse_args()
    args = parser.parse_args()

    if args.run_bot:
        # Run the bot directly (no GUI) when invoked with --run-bot.
        from mtga_bot.main import run_bot  # Local import to keep GUI light.

        run_bot(Path(args.config).expanduser())
        return

    # If not in the venv, re-exec using .venv/python to ensure deps (pyautogui/pynput) are available.
    if not getattr(sys, "frozen", False):
        base_dir = Path(__file__).resolve().parent
        venv_python = base_dir / ".venv" / "bin" / "python"
        if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
            os.execve(str(venv_python), [str(venv_python), __file__], os.environ)
    else:
        base_dir = Path(sys.executable).resolve().parent

    window = create_window(base_dir)
    window.mainloop()


if __name__ == "__main__":
    main()
