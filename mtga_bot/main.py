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
from .log_parser import EventType, LogParser
from .quest_ai import Action, ActionType, QuestAI
from .card_db import CardDatabase
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
    # Allow the poll interval to influence time-based fallbacks.
    keep_hand_fallback_delay = max(4.0, poll_interval * 4)
    wait_after_keep_seconds = float(config.get("wait_after_keep_seconds", 6.0))
    player_seat_id = int(config.get("player_seat_id", 1))
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
    cards_path = Path(config.get("cards_path", "cards.json")).expanduser()
    card_db: Optional[CardDatabase] = None
    if cards_path.exists():
        try:
            card_db = CardDatabase(cards_path)
            logger.info("Loaded card database from %s", cards_path)
        except Exception as exc:
            logger.warning("Could not load card database at %s: %s", cards_path, exc)
    else:
        logger.warning("Card database not found at %s; hand cost checks disabled", cards_path)

    game_state = GameState()
    game_state.player_seat_id = player_seat_id
    game_state.card_db = card_db
    ui_controller = UIController(
        image_dir=config.get("image_dir"),
        dry_run=config.get("dry_run", True),
        confidence=float(config.get("image_confidence", 0.9)),
        click_region=parse_click_region(config),
        user_mouse_pause_seconds=float(config.get("user_mouse_pause_seconds", 4.0)),
        target_overrides=config.get("click_targets"),
        hand_y_ratio=float(config.get("hand_y_ratio", 0.85)),
        hand_x_ratios=config.get("hand_x_ratios"),
        land_y_ratio=float(config.get("land_y_ratio", 0.84)),
        land_x_ratios=config.get("land_x_ratios"),
        use_image_search=bool(config.get("use_image_search", True)),
    )
    default_strategy = config.get("default_strategy") or config.get("deck_strategy") or "play_games"
    quest_ai = QuestAI(default_color=load_deck_color(config), default_strategy=default_strategy)

    deck_strategy_name = config.get("deck_strategy")
    if deck_strategy_name == "mono_red":
        quest_ai.register_strategy(deck_strategy_name, MonoRedAggroStrategy())

    unparsed_logged = 0
    last_queue_action: Optional[float] = None
    last_keep_action: Optional[float] = None
    turn_fallback_done = False
    next_action_not_before: Optional[float] = None
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
            if event.event_type == EventType.HAND_UPDATE:
                summary = game_state.hand_info or {}
                logger.info(
                    "Hand update: %s cards, %s lands, cheapest spell: %s",
                    summary.get("total_cards", "?"),
                    summary.get("lands", "?"),
                    summary.get("cheapest_spell", "unknown"),
                )
            action = quest_ai.get_action(game_state)
            # If we don't have priority (when known), avoid taking board actions.
            if action and action.action_type in {
                ActionType.PLAY_LAND,
                ActionType.CAST_SPELL,
                ActionType.ATTACK_ALL,
                ActionType.END_STEP,
            }:
                if not game_state.has_priority():
                    logger.debug(
                        "Skipping %s until priority is ours (priority=%s mine=%s)",
                        action.action_type,
                        game_state.priority_player,
                        game_state.player_seat_id,
                    )
                    action = Action(ActionType.WAIT, reason="Waiting for priority (log-based)")

            # Guard against acting while opponent still has priority right after keep.
            if next_action_not_before and time.time() < next_action_not_before:
                if action and action.action_type in {
                    ActionType.PLAY_LAND,
                    ActionType.CAST_SPELL,
                    ActionType.ATTACK_ALL,
                    ActionType.END_STEP,
                }:
                    wait_seconds = max(0.0, next_action_not_before - time.time())
                    logger.debug(
                        "Delaying action %s for %.1fs to wait for priority after keep",
                        action.action_type,
                        wait_seconds,
                    )
                    action = Action(ActionType.WAIT, reason="Waiting for priority after keep")
                elif time.time() >= next_action_not_before:
                    next_action_not_before = None
            elif next_action_not_before and time.time() >= next_action_not_before:
                next_action_not_before = None

            if action:
                logger.info("Action: %s (%s)", action.action_type, action.reason)
                if action.action_type == ActionType.KEEP_HAND:
                    logger.debug(
                        "Marking hand_kept=True before perform (phase=%s turn=%s match_id=%s)",
                        game_state.phase,
                        game_state.turn,
                        game_state.match_id,
                    )
                    # Avoid requesting mulligan/keep multiple times per match.
                    game_state.hand_kept = True
                    last_keep_action = time.time()
                    turn_fallback_done = False
                    next_action_not_before = time.time() + wait_after_keep_seconds
                elif action.action_type == ActionType.QUEUE_FOR_MATCH:
                    last_queue_action = time.time()
                    last_keep_action = None
                    turn_fallback_done = False
                    next_action_not_before = None
                elif action.action_type == ActionType.PLAY_LAND:
                    game_state.last_play_land_turn = game_state.turn
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
                last_keep_action = time.time()
                turn_fallback_done = False
                next_action_not_before = time.time() + wait_after_keep_seconds
            # Fallback: some logs never emit TURN_START. If hand is kept and we stayed on turn 0, assume turn 1.
            if (
                game_state.phase == MatchPhase.IN_MATCH
                and game_state.hand_kept
                and game_state.turn == 0
                and last_keep_action
                and not turn_fallback_done
                and time.time() - last_keep_action > keep_hand_fallback_delay
            ):
                game_state.turn = 1
                turn_fallback_done = True
                logger.debug(
                    "Fallback: assuming turn=1 after keep (delay %.1fs since keep_hand); TURN_START not seen",
                    time.time() - last_keep_action,
                )
            # Reset action guard if match ends.
            if event.event_type == EventType.MATCH_END:
                next_action_not_before = None
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
