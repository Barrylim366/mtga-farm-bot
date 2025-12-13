"""
Centralized bot logging - logs all parsed player.log data with timestamps
"""
from datetime import datetime
import json
import threading

_log_lock = threading.Lock()
BOT_LOG_FILE = "bot.log"


def init_bot_log():
    """Initialize bot.log file at session start"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'w') as f:
            f.write(f"[{_timestamp()}] === MTGA Bot Session Started ===\n")


def _timestamp():
    """Get formatted timestamp"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log_raw_line(pattern: str, line: str):
    """Log raw line matched from player.log"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            f.write(f"[{_timestamp()}] [RAW] Pattern='{pattern}' matched\n")
            # Truncate very long lines for readability
            if len(line) > 500:
                f.write(f"[{_timestamp()}] [RAW] Line (truncated): {line[:500]}...\n")
            else:
                f.write(f"[{_timestamp()}] [RAW] Line: {line.strip()}\n")


def log_game_state_update(game_state_dict: dict):
    """Log parsed game state data"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            ts = _timestamp()
            f.write(f"[{ts}] [GAME_STATE] Update received\n")

            # Log turnInfo
            if 'turnInfo' in game_state_dict:
                ti = game_state_dict['turnInfo']
                f.write(f"[{ts}] [TURN_INFO] turn={ti.get('turnNumber')}, "
                       f"phase={ti.get('phase')}, step={ti.get('step')}, "
                       f"activePlayer={ti.get('activePlayer')}, "
                       f"priorityPlayer={ti.get('priorityPlayer')}, "
                       f"decisionPlayer={ti.get('decisionPlayer')}\n")

            # Log players
            if 'players' in game_state_dict:
                for player in game_state_dict['players']:
                    seat = player.get('systemSeatNumber', player.get('seatId', '?'))
                    life = player.get('lifeTotal', '?')
                    f.write(f"[{ts}] [PLAYER] seat={seat}, life={life}\n")

            # Log zones summary
            if 'zones' in game_state_dict:
                for zone in game_state_dict['zones']:
                    zone_type = zone.get('type', '?')
                    owner = zone.get('ownerSeatId', '?')
                    obj_count = len(zone.get('objectInstanceIds', []))
                    f.write(f"[{ts}] [ZONE] type={zone_type}, owner={owner}, objects={obj_count}\n")

            # Log game objects summary
            if 'gameObjects' in game_state_dict:
                objects = game_state_dict['gameObjects']
                f.write(f"[{ts}] [OBJECTS] count={len(objects)}\n")
                for obj in objects[:10]:  # Log first 10 objects to avoid spam
                    inst_id = obj.get('instanceId', '?')
                    grp_id = obj.get('grpId', '?')
                    obj_type = obj.get('type', '?')
                    zone_id = obj.get('zoneId', '?')
                    f.write(f"[{ts}] [OBJECT] instId={inst_id}, grpId={grp_id}, type={obj_type}, zone={zone_id}\n")
                if len(objects) > 10:
                    f.write(f"[{ts}] [OBJECTS] ... and {len(objects) - 10} more objects\n")

            # Log actions
            if 'actions' in game_state_dict:
                actions = game_state_dict['actions']
                f.write(f"[{ts}] [ACTIONS] count={len(actions)}\n")
                for i, action_wrapper in enumerate(actions[:5]):  # First 5 actions
                    action = action_wrapper.get('action', action_wrapper)
                    action_type = action.get('actionType', '?')
                    inst_id = action.get('instanceId', '?')
                    f.write(f"[{ts}] [ACTION] {i}: type={action_type}, instId={inst_id}\n")
                if len(actions) > 5:
                    f.write(f"[{ts}] [ACTIONS] ... and {len(actions) - 5} more actions\n")

            # Log annotations summary
            if 'annotations' in game_state_dict:
                annots = game_state_dict['annotations']
                f.write(f"[{ts}] [ANNOTATIONS] count={len(annots)}\n")


def log_actions_available(actions: list):
    """Log actions available from ActionsAvailableReq"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            ts = _timestamp()
            f.write(f"[{ts}] [ACTIONS_REQ] {len(actions)} actions available\n")
            for i, action in enumerate(actions[:10]):
                action_type = action.get('actionType', '?')
                inst_id = action.get('instanceId', '?')
                mana_cost = action.get('manaCost', [])
                ability_grp_id = action.get('abilityGrpId', '?')
                # Log abilityGrpId for mana actions to help verify the mapping
                if action_type == 'ActionType_Activate_Mana':
                    f.write(f"[{ts}] [ACTIONS_REQ] {i}: type={action_type}, instId={inst_id}, abilityGrpId={ability_grp_id}\n")
                else:
                    f.write(f"[{ts}] [ACTIONS_REQ] {i}: type={action_type}, instId={inst_id}, manaCost={mana_cost}\n")
            if len(actions) > 10:
                f.write(f"[{ts}] [ACTIONS_REQ] ... and {len(actions) - 10} more\n")


def log_mulligan_decision(keep: bool, card_count: int):
    """Log mulligan decision"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            decision = "KEEP" if keep else "MULLIGAN"
            f.write(f"[{_timestamp()}] [MULLIGAN] Decision: {decision} ({card_count} cards)\n")


def log_decision(move_name: str, move_data):
    """Log AI decision/move"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            f.write(f"[{_timestamp()}] [DECISION] move={move_name}, data={move_data}\n")


def log_controller_event(event: str, details: str = ""):
    """Log controller events"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            f.write(f"[{_timestamp()}] [CTRL] {event} {details}\n")


def log_hover(object_id: int):
    """Log hover detection"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            f.write(f"[{_timestamp()}] [HOVER] objectId={object_id}\n")


def log_info(message: str):
    """General info logging"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            f.write(f"[{_timestamp()}] [INFO] {message}\n")


def log_error(message: str):
    """Error logging"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            f.write(f"[{_timestamp()}] [ERROR] {message}\n")


def log_click(x: int, y: int, purpose: str):
    """Log mouse click with absolute coordinates and purpose"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            f.write(f"[{_timestamp()}] [CLICK] ({x}, {y}) - {purpose}\n")


def log_move(x: int, y: int, purpose: str):
    """Log mouse move with absolute coordinates and purpose"""
    with _log_lock:
        with open(BOT_LOG_FILE, 'a') as f:
            f.write(f"[{_timestamp()}] [MOVE] ({x}, {y}) - {purpose}\n")
