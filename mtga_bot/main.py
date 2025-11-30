from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .game_model import GameState, MatchPhase
from .log_parser import LogParser
from .quest_ai import Action, ActionType, QuestAI
from .strategies import MonoRedAggroStrategy
from .ui_controller import UIController


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_deck_color(config: Dict[str, Any]) -> str:
    decks_path = Path(config.get("decks_path", "decks.example.json")).expanduser()
    default_deck = config.get("default_deck", "mono_red")
    if not decks_path.exists():
        return config.get("default_color", "red")

    try:
        decks = load_json(decks_path)
        deck = decks.get(default_deck, {})
        return deck.get("color", deck.get("colours", "red"))
    except Exception:
        return config.get("default_color", "red")


def resolve_log_path(config: Dict[str, Any], logger: logging.Logger) -> Path:
    """
    Determine the Player.log location, preferring a user-provided path and
    auto-detecting common Windows 11 and Proton locations otherwise.
    """
    custom_path = config.get("log_path")
    if custom_path:
        path = Path(custom_path).expanduser()
        logger.info("Using configured log path: %s", path)
        return path

    candidates = []
    system = platform.system().lower()
    if system == "windows":
        home = Path.home()
        local_appdata = os.environ.get("LOCALAPPDATA")
        candidates.append(home / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log")
        if local_appdata:
            candidates.append(
                Path(local_appdata).parent / "LocalLow" / "Wizards Of The Coast" / "MTGA" / "Player.log"
            )
    else:
        candidates.append(
            Path.home()
            / ".local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"
        )

    for candidate in candidates:
        if candidate.exists():
            logger.info("Auto-detected Player.log at %s", candidate)
            return candidate

    fallback = candidates[0] if candidates else Path("Player.log")
    logger.warning("Player.log not found; using fallback path %s. Set `log_path` in config if different.", fallback)
    return fallback


def parse_click_region(config: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
    """
    Optional override for windowed mode on ultra-wide setups.
    Expects dict with x, y, width, height (absolute screen pixels).
    """
    region = config.get("click_region")
    if not isinstance(region, dict):
        return None
    try:
        x = int(region["x"])
        y = int(region["y"])
        w = int(region["width"])
        h = int(region["height"])
        return (x, y, w, h)
    except Exception:
        return None


def run_bot(config_path: Path) -> None:
    config = load_json(config_path)

    poll_interval = float(config.get("poll_interval", 1.0))
    logging.basicConfig(
        level=getattr(logging, str(config.get("log_level", "INFO")).upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("mtga_bot")

    logger.info("Starting MTGA bot with config at %s", config_path)

    log_path = resolve_log_path(config, logger)
    if not log_path.exists():
        logger.warning("Log file does not exist yet: %s (will wait for MTGA to create it)", log_path)

    log_parser = LogParser(str(log_path))
    game_state = GameState()
    ui_controller = UIController(
        image_dir=config.get("image_dir"),
        dry_run=config.get("dry_run", True),
        confidence=float(config.get("image_confidence", 0.9)),
        click_region=parse_click_region(config),
    )
    default_strategy = config.get("default_strategy") or config.get("deck_strategy") or "play_games"
    quest_ai = QuestAI(default_color=load_deck_color(config), default_strategy=default_strategy)

    deck_strategy_name = config.get("deck_strategy")
    if deck_strategy_name == "mono_red":
        quest_ai.register_strategy(deck_strategy_name, MonoRedAggroStrategy())

    unparsed_logged = 0
    last_queue_action: Optional[float] = None
    try:
        for event in log_parser.follow(poll_interval=poll_interval, yield_unparsed=True):
            if event.payload.get("unparsed"):
                # During idle, log a few unparsed lines so the user sees activity.
                if game_state.is_idle():
                    if unparsed_logged < 5:
                        logger.debug("Unparsed log line (idle): %s", event.payload.get("message"))
                        unparsed_logged += 1
                    continue
                logger.debug("Unparsed log line (in match/queue): %s", event.payload.get("message"))
            else:
                logger.debug("Event: %s", event)

            game_state.apply_event(event)
            action = quest_ai.get_action(game_state)
            if action:
                logger.info("Action: %s (%s)", action.action_type, action.reason)
                if action.action_type == ActionType.KEEP_HAND:
                    # Avoid requesting mulligan/keep multiple times per match.
                    game_state.hand_kept = True
                elif action.action_type == ActionType.QUEUE_FOR_MATCH:
                    last_queue_action = time.time()
                ui_controller.perform(action)
            # Fallback: if we queued recently and never saw a mulligan keep, force it after a delay.
            if (
                last_queue_action
                and not game_state.hand_kept
                and game_state.phase in (MatchPhase.QUEUED, MatchPhase.IN_MATCH)
                and time.time() - last_queue_action > 6
            ):
                logger.info("Action: KEEP_HAND (timer fallback after queue)")
                game_state.hand_kept = True
                ui_controller.perform(Action(ActionType.KEEP_HAND, reason="Timer fallback keep hand"))
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple MTGA daily quest bot")
    parser.add_argument(
        "--config",
        default="config.json",
        type=str,
        help="Path to config JSON file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_bot(Path(args.config).expanduser())


if __name__ == "__main__":
    main()
