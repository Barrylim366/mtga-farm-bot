from Controller.MTGAController.Controller import Controller
from AI.DummyAI import DummyAI
from Game import Game
import ForgeEngine
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


def _load_runtime_bot_config(cfg_path: pathlib.Path) -> tuple[dict, bool]:
    """Load CLI settings while preserving defaults missing from older files."""
    click_targets: dict = {}
    auto_concede_stalled_matches = True
    try:
        if cfg_path.is_file():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            loaded_targets = cfg.get("click_targets")
            if isinstance(loaded_targets, dict):
                click_targets = loaded_targets
            configured_auto_concede = cfg.get("auto_concede_stalled_matches")
            if isinstance(configured_auto_concede, bool):
                auto_concede_stalled_matches = configured_auto_concede
    except Exception:
        click_targets = {}
    return click_targets, auto_concede_stalled_matches


def _forge_engine_enabled(cfg_path: pathlib.Path | None) -> bool:
    """The "Enable Forge Engine" setting; default to True if installed."""
    try:
        from ForgeEngine import paths
        if not paths.installed():
            return False
    except Exception:
        return False
    if cfg_path is None or not cfg_path.is_file():
        return True
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            val = json.load(f).get("forge_engine_enabled")
            return True if val is None else bool(val)
    except Exception:
        return True


def _load_runtime_deck_preferences(cfg_path: pathlib.Path | None) -> tuple[str, str, bool]:
    """Load preferred deck settings from calibration_config.json."""
    starter = ""
    historic = ""
    only_grind = False
    try:
        if cfg_path and cfg_path.is_file():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            starter = str(cfg.get("preferred_starter_deck", "") or "")
            historic = str(cfg.get("preferred_historic_deck", "") or "")
            only_grind = bool(cfg.get("only_grind_preferred_deck", False))
    except Exception:
        pass
    return starter, historic, only_grind


def _free_forge_enabled(cfg_path: pathlib.Path | None) -> bool:
    """Whether Free Forge autonomous casting is enabled."""
    try:
        from runtime_paths import runtime_file
        n_path = runtime_file("config", "notifications.json")
        if n_path.is_file():
            with open(n_path, "r", encoding="utf-8") as f:
                val = json.load(f).get("free_forge")
                if val is not None:
                    return bool(val)
    except Exception:
        pass
    if cfg_path and cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                val = json.load(f).get("free_forge")
                if val is not None:
                    return bool(val)
        except Exception:
            pass
    env_val = os.environ.get("MTGA_FORGE_FREE", "").strip().lower()
    return env_val in ("1", "true", "yes", "on")


def main():
    print("Starting MTG AI Bot...")

    # Enable Per-Monitor DPI Awareness (v2) on Windows to prevent coordinate drift
    try:
        from vision.window_locator import enable_per_monitor_dpi_v2
        enable_per_monitor_dpi_v2()
    except Exception:
        pass

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
    auto_concede_stalled_matches = True
    cfg_path = None
    try:
        from runtime_paths import runtime_file  # type: ignore
        cfg_path = runtime_file("config", "calibration_config.json")
        click_targets, auto_concede_stalled_matches = _load_runtime_bot_config(cfg_path)
    except Exception:
        click_targets = {}

    pref_starter, pref_historic, only_grind = _load_runtime_deck_preferences(cfg_path)

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
            "mulligan_centre": {"x": 835, "y": 562},
            "library": {"x": 556, "y": 578},
            "mulligan_done": {"x": 956, "y": 868},
            # Hand row only, not the full window width -- see the note at
            # Controller.hand_scan_p1 for why the edges are dangerous.
            "hand_scan_points": {
                "p1": {"x": 220, "y": 1050},
                "p2": {"x": 1700, "y": 1050},
            },
        }

    # MTGA must run windowed at an exact 16:9 size; any OS display scaling is
    # supported (window rects are converted to physical pixels).
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
            auto_concede_stalled_matches=auto_concede_stalled_matches,
            preferred_starter_deck=pref_starter,
            preferred_historic_deck=pref_historic,
            only_grind_preferred_deck=only_grind,
        )
        
        # Setup graceful shutdown handlers early before starting background components
        import signal

        game = None

        def _graceful_exit(signum=None, frame=None):
            print("\nShutting down MTG AI Bot cleanly...")
            if game is not None:
                try:
                    game.stop()
                except Exception:
                    pass
            try:
                controller._stop_requested = True
            except Exception:
                pass
            try:
                ForgeEngine.shutdown()
            except Exception:
                pass
            sys.exit(0)

        try:
            signal.signal(signal.SIGINT, _graceful_exit)
            signal.signal(signal.SIGTERM, _graceful_exit)
        except Exception:
            pass

        if _free_forge_enabled(cfg_path):
            os.environ["MTGA_FORGE_FREE"] = "on"
            print("Free Forge: Autonomous Casting (experimental) enabled.")
        else:
            os.environ["MTGA_FORGE_FREE"] = "off"
        os.environ["MTGA_SELECTIVE_ATTACK"] = "0"

        print("Initializing AI...")
        forge_enabled = _forge_engine_enabled(cfg_path)
        ai = ForgeEngine.make_ai(DummyAI(), lambda: forge_enabled)
        if ForgeEngine.apply(controller, forge_enabled, log_path=log_path):
            print("Forge engine enabled: starting the advisor in the background.")

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
