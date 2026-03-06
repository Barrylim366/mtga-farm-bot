"""
Centralized bot logging - logs all parsed player.log data with timestamps
"""
from datetime import datetime
import os
from pathlib import Path
import sys
import threading

_log_lock = threading.Lock()
_fallback_warning_printed = False
_hover_logging_enabled = False


def _resolve_bot_log_path() -> str:
    """Resolve a writable per-user bot.log location."""
    try:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA")
            if base:
                target_dir = Path(base) / "BurningLotusBot"
            else:
                target_dir = Path.home() / "AppData" / "Local" / "BurningLotusBot"
        elif sys.platform == "darwin":
            target_dir = Path.home() / "Library" / "Application Support" / "BurningLotusBot"
        else:
            target_dir = Path.home() / ".config" / "burninglotusbot"

        target_dir.mkdir(parents=True, exist_ok=True)
        return str(target_dir / "bot.log")
    except Exception:
        # Last-resort fallback to previous relative behavior.
        return "bot.log"


BOT_LOG_FILE = _resolve_bot_log_path()


def init_bot_log():
    """Initialize bot.log file at session start"""
    with _log_lock:
        _write_lines('w', [f"[{_timestamp()}] === MTGA Bot Session Started ===\n"])


def get_app_log_dir() -> str:
    """Return the preferred per-user app log directory."""
    try:
        return str(Path(BOT_LOG_FILE).resolve().parent)
    except Exception:
        return str(Path(".").resolve())


def ensure_debug_dir(subdir: str | None = None) -> str:
    """Create and return a debug output directory."""
    base = Path(get_app_log_dir()) / "debug"
    if subdir:
        base = base / str(subdir)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(base)


def _write_lines(mode: str, lines: list[str]) -> None:
    global _fallback_warning_printed
    try:
        with open(BOT_LOG_FILE, mode, encoding="utf-8") as f:
            for line in lines:
                f.write(line)
        return
    except Exception:
        pass

    # Fallback: avoid crashing the bot if writing to preferred path fails.
    try:
        with open("bot.log", mode, encoding="utf-8") as f:
            for line in lines:
                f.write(line)
        if not _fallback_warning_printed:
            _fallback_warning_printed = True
            print(
                f"[bot_logger] Warning: failed writing '{BOT_LOG_FILE}', using local 'bot.log' fallback."
            )
    except Exception:
        # Intentionally swallow logger errors so gameplay loop stays alive.
        pass


def _timestamp():
    """Get formatted timestamp"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log_raw_line(pattern: str, line: str):
    """Log raw line matched from player.log"""
    with _log_lock:
        lines = [f"[{_timestamp()}] [RAW] Pattern='{pattern}' matched\n"]
        # Truncate very long lines for readability
        if "SelectTargetsReq" in pattern:
            lines.append(f"[{_timestamp()}] [RAW] Line: {line.strip()}\n")
        elif len(line) > 500:
            lines.append(f"[{_timestamp()}] [RAW] Line (truncated): {line[:500]}...\n")
        else:
            lines.append(f"[{_timestamp()}] [RAW] Line: {line.strip()}\n")
        _write_lines('a', lines)


def log_game_state_update(game_state_dict: dict):
    """Log parsed game state data"""
    with _log_lock:
        ts = _timestamp()
        lines = [f"[{ts}] [GAME_STATE] Update received\n"]

        # Log turnInfo
        if 'turnInfo' in game_state_dict:
            ti = game_state_dict['turnInfo']
            lines.append(
                f"[{ts}] [TURN_INFO] turn={ti.get('turnNumber')}, "
                f"phase={ti.get('phase')}, step={ti.get('step')}, "
                f"activePlayer={ti.get('activePlayer')}, "
                f"priorityPlayer={ti.get('priorityPlayer')}, "
                f"decisionPlayer={ti.get('decisionPlayer')}\n"
            )

        # Log players
        if 'players' in game_state_dict:
            for player in game_state_dict['players']:
                seat = player.get('systemSeatNumber', player.get('seatId', '?'))
                life = player.get('lifeTotal', '?')
                lines.append(f"[{ts}] [PLAYER] seat={seat}, life={life}\n")

        # Log zones summary
        if 'zones' in game_state_dict:
            for zone in game_state_dict['zones']:
                zone_type = zone.get('type', '?')
                owner = zone.get('ownerSeatId', '?')
                obj_count = len(zone.get('objectInstanceIds', []))
                lines.append(f"[{ts}] [ZONE] type={zone_type}, owner={owner}, objects={obj_count}\n")

        # Log game objects summary
        if 'gameObjects' in game_state_dict:
            objects = game_state_dict['gameObjects']
            lines.append(f"[{ts}] [OBJECTS] count={len(objects)}\n")
            for obj in objects[:10]:  # Log first 10 objects to avoid spam
                inst_id = obj.get('instanceId', '?')
                grp_id = obj.get('grpId', '?')
                obj_type = obj.get('type', '?')
                zone_id = obj.get('zoneId', '?')
                lines.append(
                    f"[{ts}] [OBJECT] instId={inst_id}, grpId={grp_id}, type={obj_type}, zone={zone_id}\n"
                )
            if len(objects) > 10:
                lines.append(f"[{ts}] [OBJECTS] ... and {len(objects) - 10} more objects\n")

        # Log actions
        if 'actions' in game_state_dict:
            actions = game_state_dict['actions']
            lines.append(f"[{ts}] [ACTIONS] count={len(actions)}\n")
            for i, action_wrapper in enumerate(actions[:5]):  # First 5 actions
                action = action_wrapper.get('action', action_wrapper)
                action_type = action.get('actionType', '?')
                inst_id = action.get('instanceId', '?')
                lines.append(f"[{ts}] [ACTION] {i}: type={action_type}, instId={inst_id}\n")
            if len(actions) > 5:
                lines.append(f"[{ts}] [ACTIONS] ... and {len(actions) - 5} more actions\n")

        # Log annotations summary
        if 'annotations' in game_state_dict:
            annots = game_state_dict['annotations']
            lines.append(f"[{ts}] [ANNOTATIONS] count={len(annots)}\n")

        _write_lines('a', lines)


def log_actions_available(actions: list):
    """Log actions available from ActionsAvailableReq"""
    with _log_lock:
        ts = _timestamp()
        lines = [f"[{ts}] [ACTIONS_REQ] {len(actions)} actions available\n"]
        for i, action in enumerate(actions[:10]):
            action_type = action.get('actionType', '?')
            inst_id = action.get('instanceId', '?')
            mana_cost = action.get('manaCost', [])
            ability_grp_id = action.get('abilityGrpId', '?')
            # Log abilityGrpId for mana actions to help verify the mapping
            if action_type == 'ActionType_Activate_Mana':
                lines.append(
                    f"[{ts}] [ACTIONS_REQ] {i}: type={action_type}, instId={inst_id}, abilityGrpId={ability_grp_id}\n"
                )
            else:
                lines.append(
                    f"[{ts}] [ACTIONS_REQ] {i}: type={action_type}, instId={inst_id}, manaCost={mana_cost}\n"
                )
        if len(actions) > 10:
            lines.append(f"[{ts}] [ACTIONS_REQ] ... and {len(actions) - 10} more\n")
        _write_lines('a', lines)


def log_mulligan_decision(keep: bool, card_count: int):
    """Log mulligan decision"""
    with _log_lock:
        decision = "KEEP" if keep else "MULLIGAN"
        _write_lines('a', [f"[{_timestamp()}] [MULLIGAN] Decision: {decision} ({card_count} cards)\n"])


def log_decision(move_name: str, move_data):
    """Log AI decision/move"""
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [DECISION] move={move_name}, data={move_data}\n"])


def log_controller_event(event: str, details: str = ""):
    """Log controller events"""
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [CTRL] {event} {details}\n"])


def log_hover(object_id: int):
    """Log hover detection"""
    if not _hover_logging_enabled:
        return
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [HOVER] objectId={object_id}\n"])


def set_hover_logging(enabled: bool) -> None:
    """Enable or disable hover logging to reduce noise."""
    global _hover_logging_enabled
    _hover_logging_enabled = bool(enabled)


def log_info(message: str):
    """General info logging"""
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [INFO] {message}\n"])


def log_ai(message: str):
    """AI debug logging."""
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [AI] {message}\n"])


def log_error(message: str):
    """Error logging"""
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [ERROR] {message}\n"])


def log_click(x: int, y: int, purpose: str):
    """Log mouse click with absolute coordinates and purpose"""
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [CLICK] ({x}, {y}) - {purpose}\n"])


def log_move(x: int, y: int, purpose: str):
    """Log mouse move with absolute coordinates and purpose"""
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [MOVE] ({x}, {y}) - {purpose}\n"])
