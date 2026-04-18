from Controller.MTGAController.Controller import Controller
from AI.DummyAI import DummyAI
from Game import Game
import json
import time
import os
import pathlib
import sys


def _default_player_log_path() -> str:
    home = pathlib.Path.home()
    if os.name == "nt":
        return str(home / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log")
    if sys.platform == "darwin":
        return str(home / "Library/Logs/Wizards Of The Coast/MTGA/Player.log")
    return str(
        home
        / ".local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"
    )


def _detect_player_log_path() -> str:
    if os.name == "nt":
        candidates = []
        roots = []
        user_profile = os.environ.get("USERPROFILE", "")
        if user_profile:
            roots.append(pathlib.Path(user_profile))
        home = pathlib.Path.home()
        if home not in roots:
            roots.append(home)
        for root in roots:
            candidate = root / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"
            if candidate.is_file():
                candidates.append(candidate)
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(candidates[0])
        return _default_player_log_path()
    if sys.platform == "darwin":
        mac_candidates = [
            pathlib.Path.home() / "Library/Logs/Wizards Of The Coast/MTGA/Player.log",
            pathlib.Path.home() / "Library/Logs/Wizards Of The Coast/MTGA/Player-prev.log",
        ]
        found = [p for p in mac_candidates if p.is_file()]
        if found:
            found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(found[0])
        return _default_player_log_path()

    home = pathlib.Path.home()
    steam_bases = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    found: list[pathlib.Path] = []
    for base in steam_bases:
        compat = base / "steamapps/compatdata"
        if not compat.is_dir():
            continue
        for p in compat.rglob("Player.log"):
            s = str(p)
            if "Wizards Of The Coast/MTGA/Player.log" in s:
                found.append(p)
    if found:
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return str(found[0])
    return _default_player_log_path()


def main():
    print("Starting MTG AI Bot...")

    # User configuration
    log_path = os.environ.get("MTGA_BOT_LOG_PATH", "")
    if not log_path:
        log_path = _detect_player_log_path()
    if not os.path.isfile(log_path):
        print(f"Player.log not found: {log_path}")
        print(
            "Start MTGA once and verify detailed logs are enabled, "
            "or set MTGA_BOT_LOG_PATH to the correct Player.log path."
        )
        return
    
    # Prefer a calibrated config written by the UI (runtime/config/calibration_config.json).
    # Fall back to 1920-relative defaults that match ui.ConfigManager._default_config()
    # so run_bot.py works out of the box on a fresh checkout.
    click_targets: dict = {}
    try:
        from runtime_paths import runtime_file  # type: ignore
        cfg_path = runtime_file("config", "calibration_config.json")
        if cfg_path.is_file():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            loaded_targets = cfg.get("click_targets")
            if isinstance(loaded_targets, dict):
                click_targets = loaded_targets
    except Exception:
        click_targets = {}

    if not click_targets:
        # Match Controller.py internal fallbacks (1920-relative).
        click_targets = {
            "keep_hand": {"x": 1101, "y": 870},
            "queue_button": {"x": 1699, "y": 996},
            "next": {"x": 1755, "y": 944},
            "concede": {"x": 962, "y": 631},
            "attack_all": {"x": 1755, "y": 944},
            "opponent_avatar": {"x": 1286, "y": 216},
            "assign_damage_done": {"x": 1280, "y": 720},
            "hand_scan_points": {
                "p1": {"x": 0, "y": 1050},
                "p2": {"x": 1920, "y": 1050},
            },
        }

    # MTGA must run windowed at 1920x1080 with 100% display scaling.
    # Monitor resolution can be anything; the bot maps into the MTGA window.
    screen_bounds = ((0, 0), (1920, 1080))

    try:
        # Initialize components
        print(f"Initializing Controller with log path: {log_path}")
        input_backend = os.environ.get("MTGA_BOT_INPUT_BACKEND", "auto")  # "ydotool" / "pynput" / "pyautogui" / "auto"
        controller = Controller(
            log_path=log_path,
            screen_bounds=screen_bounds,
            click_targets=click_targets,
            input_backend=input_backend,
        )
        
        print("Initializing AI...")
        ai = DummyAI()
        
        print("Initializing Game...")
        game = Game(controller, ai)
        
        print("Starting Game loop...")
        game.start()
        
        # Keep the script running
        while True:
            time.sleep(1)

    except Exception as e:
        print(f"An error occurred: {e}")
        input("Press Enter to exit...")

if __name__ == "__main__":
    main()
