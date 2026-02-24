from Controller.MTGAController.Controller import Controller
from AI.DummyAI import DummyAI
from Game import Game
import time
import os
import pathlib
import sys
from licensing.validator import require_license_or_block


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
    license_result = require_license_or_block()
    if not license_result.valid:
        print(f"License check failed: {license_result.message}")
        print("Open the UI and activate a valid license first.")
        return

    # User configuration
    log_path = os.environ.get("MTGA_BOT_LOG_PATH", "")
    if not log_path:
        log_path = _detect_player_log_path()
    
    click_targets = {
        "keep_hand": {
            "x": 1876,
            "y": 1060
        },
        "queue_button": {
            "x": 2485,
            "y": 1194
        },
        "next": {
            "x": 2546,
            "y": 1137
        },
        "concede": {
            "x": 1714,
            "y": 814
        },
        "attack_all": {
            "x": 2529,
            "y": 1131
        },
        "opponent_avatar": {
            "x": 1720,
            "y": 295
        },
        "hand_scan_points": {
            "p1": {
                "x": 994,
                "y": 1255
            },
            "p2": {
                "x": 2421,
                "y": 1253
            }
        }
    }

    # Estimated screen bounds based on coordinates (assuming 2560x1440 or similar)
    # This is important for card casting relative positions
    screen_bounds = ((0, 0), (2560, 1440))

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
