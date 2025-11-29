from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .game_model import GameState
from .log_parser import LogParser
from .quest_ai import QuestAI
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


def run_bot(config_path: Path) -> None:
    config = load_json(config_path)

    poll_interval = float(config.get("poll_interval", 1.0))
    logging.basicConfig(
        level=getattr(logging, str(config.get("log_level", "INFO")).upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("mtga_bot")

    logger.info("Starting MTGA bot with config at %s", config_path)

    log_path = Path(config["log_path"])
    if not log_path.exists():
        logger.warning("Log file does not exist yet: %s (will wait for MTGA to create it)", log_path)

    log_parser = LogParser(str(log_path))
    game_state = GameState()
    ui_controller = UIController(
        image_dir=config.get("image_dir"),
        dry_run=config.get("dry_run", True),
        confidence=float(config.get("image_confidence", 0.9)),
    )
    default_strategy = config.get("default_strategy") or config.get("deck_strategy") or "play_games"
    quest_ai = QuestAI(default_color=load_deck_color(config), default_strategy=default_strategy)

    deck_strategy_name = config.get("deck_strategy")
    if deck_strategy_name == "mono_red":
        quest_ai.register_strategy(deck_strategy_name, MonoRedAggroStrategy())

    unparsed_logged = 0
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
                ui_controller.perform(action)
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
