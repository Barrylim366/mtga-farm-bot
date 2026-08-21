"""
Centralized bot logging - logs all parsed player.log data with timestamps
"""
from datetime import datetime
import os
from pathlib import Path
import threading
from runtime_paths import ensure_runtime_subdir, runtime_file

_log_lock = threading.Lock()
_fallback_warning_printed = False
_hover_logging_enabled = False

# Debug-bundle rotation. The per-failure bundle writers (keep-click, hand-select,
# nav, assign-damage, arena-setup, ...) all funnel through ensure_debug_dir and
# had no cap, so runtime/debug grew unbounded (1.2 GB observed). Prune oldest
# sibling bundles opportunistically, throttled so we don't listdir on every call.
_MAX_DEBUG_BUNDLES = 60
_DEBUG_PRUNE_MIN_INTERVAL_SEC = 600
_debug_prune_lock = threading.Lock()
_last_debug_prune_ts = 0.0
# Own their own retention / are not per-failure bundles -> never auto-pruned here.
_DEBUG_PRUNE_EXCLUDE = {"matches", "clicks.jsonl", "clicks.jsonl.1", "clicks"}


def _maybe_prune_debug_bundles(base: Path) -> None:
    global _last_debug_prune_ts
    import time as _time
    now = _time.time()
    with _debug_prune_lock:
        if now - _last_debug_prune_ts < _DEBUG_PRUNE_MIN_INTERVAL_SEC:
            return
        _last_debug_prune_ts = now
    try:
        import shutil
        entries = [
            entry for entry in base.iterdir()
            if entry.is_dir() and entry.name not in _DEBUG_PRUNE_EXCLUDE
        ]
        if len(entries) <= _MAX_DEBUG_BUNDLES:
            return
        entries.sort(key=lambda p: p.stat().st_mtime)
        for old in entries[: len(entries) - _MAX_DEBUG_BUNDLES]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


def _resolve_bot_log_path() -> str:
    """Resolve the runtime bot.log location, honouring MTGA_RUNTIME_DIR.

    Resolved per call rather than frozen into a module constant at import time.
    That constant made the runtime-dir override useless here: bot_logger is
    pulled in transitively by LogReader before anything gets a chance to set the
    variable, so the path was already baked in and the test suite wrote (and
    truncated) the live bot.log no matter what. Path resolution is a dict lookup
    plus a mkdir on an existing directory; _write_lines opens the file on every
    call anyway.
    """
    try:
        return str(runtime_file("logs", "bot.log"))
    except Exception:
        # Last-resort fallback to previous relative behavior.
        return "bot.log"


def get_bot_log_path() -> str:
    """Where bot.log is right now. Call this instead of caching the result --
    the module-level constant this replaces is what made MTGA_RUNTIME_DIR
    unusable for anything importing bot_logger transitively."""
    return _resolve_bot_log_path()


def init_bot_log():
    """Initialize bot.log file at session start"""
    with _log_lock:
        _write_lines('w', [f"[{_timestamp()}] === MTGA Bot Session Started ===\n"])


def get_app_log_dir() -> str:
    """Return the repo-local runtime log directory."""
    try:
        return str(ensure_runtime_subdir("logs").resolve())
    except Exception:
        return str(Path(".").resolve())


def ensure_debug_dir(subdir: str | None = None) -> str:
    """Create and return a debug output directory."""
    debug_root = ensure_runtime_subdir("debug")
    # Opportunistically cap the accumulated per-failure bundles (throttled).
    _maybe_prune_debug_bundles(debug_root)
    base = debug_root
    if subdir:
        base = base / str(subdir)
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return str(base)


def _write_lines(mode: str, lines: list[str]) -> None:
    global _fallback_warning_printed
    target = _resolve_bot_log_path()
    try:
        with open(target, mode, encoding="utf-8") as f:
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
                f"[bot_logger] Warning: failed writing '{target}', using local 'bot.log' fallback."
            )
    except Exception:
        # Intentionally swallow logger errors so gameplay loop stays alive.
        pass


def _timestamp():
    """Get formatted timestamp"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# A GameStateMessage runs several KB, so the old 500-char cap cut every one of
# them off inside the first zone -- which made stack/trigger stalls undiagnosable
# (we could see "stack has 1 object(s)" but never WHICH object). 4000 keeps
# turnInfo + zones + the first game objects while still bounding the log.
_RAW_LINE_MAX_CHARS = 4000


def log_raw_line(pattern: str, line: str):
    """Log raw line matched from player.log"""
    with _log_lock:
        lines = [f"[{_timestamp()}] [RAW] Pattern='{pattern}' matched\n"]
        # Truncate very long lines for readability
        if "SelectTargetsReq" in pattern:
            lines.append(f"[{_timestamp()}] [RAW] Line: {line.strip()}\n")
        elif len(line) > _RAW_LINE_MAX_CHARS:
            lines.append(
                f"[{_timestamp()}] [RAW] Line (truncated): {line[:_RAW_LINE_MAX_CHARS]}...\n"
            )
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
        stack_ids: list = []
        if 'zones' in game_state_dict:
            for zone in game_state_dict['zones']:
                zone_type = zone.get('type', '?')
                owner = zone.get('ownerSeatId', '?')
                zone_obj_ids = zone.get('objectInstanceIds', []) or []
                obj_count = len(zone_obj_ids)
                # Spell out the stack's contents: "Deferring decision: stack has 1
                # object(s)" was undiagnosable without knowing WHICH object it was.
                if zone_type == 'ZoneType_Stack' and zone_obj_ids:
                    stack_ids = list(zone_obj_ids)
                    lines.append(
                        f"[{ts}] [ZONE] type={zone_type}, owner={owner}, "
                        f"objects={obj_count}, ids={stack_ids}\n"
                    )
                else:
                    lines.append(f"[{ts}] [ZONE] type={zone_type}, owner={owner}, objects={obj_count}\n")

        # Log game objects summary
        if 'gameObjects' in game_state_dict:
            objects = game_state_dict['gameObjects']
            lines.append(f"[{ts}] [OBJECTS] count={len(objects)}\n")
            logged_ids = set()
            for obj in objects[:10]:  # Log first 10 objects to avoid spam
                inst_id = obj.get('instanceId', '?')
                grp_id = obj.get('grpId', '?')
                obj_type = obj.get('type', '?')
                zone_id = obj.get('zoneId', '?')
                logged_ids.add(inst_id)
                lines.append(
                    f"[{ts}] [OBJECT] instId={inst_id}, grpId={grp_id}, type={obj_type}, zone={zone_id}\n"
                )
            # Whatever sits on the stack is exactly what we need when a decision is
            # deferred on it, and it is routinely past the first 10 (observed: the
            # trigger that froze turn 16 was object 381). Always log those.
            for obj in objects:
                inst_id = obj.get('instanceId', '?')
                if inst_id in logged_ids or inst_id not in stack_ids:
                    continue
                logged_ids.add(inst_id)
                lines.append(
                    f"[{ts}] [OBJECT|STACK] instId={inst_id}, grpId={obj.get('grpId', '?')}, "
                    f"type={obj.get('type', '?')}, zone={obj.get('zoneId', '?')}, "
                    f"controller={obj.get('controllerSeatId', '?')}, "
                    f"source={obj.get('objectSourceGrpId', '?')}\n"
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


def log_match_end(result: str, turns: int, duration_sec: float):
    """Emit a single structured match-end marker.

    Written to bot.log (and thus to the persistent history.log copied by
    tools/session_watchdog.py) so the watchdog can build one match record per
    game without any cross-process coupling into the gameplay thread. Format is
    deliberately flat and grep-friendly:
        [MATCH_END] result=won turns=8 duration_sec=142.53
    """
    with _log_lock:
        safe_result = str(result or "unknown").strip() or "unknown"
        try:
            turns_val = int(turns)
        except Exception:
            turns_val = -1
        try:
            duration_val = float(duration_sec)
        except Exception:
            duration_val = 0.0
        _write_lines(
            'a',
            [
                f"[{_timestamp()}] [MATCH_END] result={safe_result} "
                f"turns={turns_val} duration_sec={duration_val:.2f}\n"
            ],
        )


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


def click_is_risky(source=None, region_age=None) -> bool:
    """A click is 'risky' when the arena window was lost (absolute desktop
    fallback) or the window fix is stale. Shared by log_click and click_recorder
    so the flag stays consistent."""
    return bool(source and str(source).startswith("absolute")) or (
        region_age is not None and region_age > 10.0
    )


def log_click(x: int, y: int, purpose: str, *, source=None, region_age=None, arena=None):
    """Log mouse click with absolute coordinates and purpose.

    Optional visual-layer context (all keyword-only, backward compatible):
      source      -- the arena-mapping source from _map_abs_point_to_arena, e.g.
                     "arena_relative_1920_direct" or the risky "absolute_no_arena"
                     (window lost -> blind desktop click).
      region_age  -- seconds since the arena window was last located.
      arena       -- the current arena_region tuple (or None if lost).
    When provided, the context is appended to the [CLICK] line and the click is
    also forwarded to click_recorder for the always-on clicks.jsonl."""
    risky = click_is_risky(source, region_age)
    extra = ""
    if source is not None or region_age is not None:
        age_str = f"{region_age:.1f}s" if region_age is not None else "?"
        extra = f" | source={source} arena={arena} age={age_str}{' RISKY' if risky else ''}"
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [CLICK] ({x}, {y}) - {purpose}{extra}\n"])
    # Forward to the structured click log (best-effort, never disturb clicking).
    try:
        import click_recorder
        click_recorder.record(
            x, y, purpose, source=source, region_age=region_age, arena=arena, risky=risky
        )
    except Exception:
        pass


def log_move(x: int, y: int, purpose: str):
    """Log mouse move with absolute coordinates and purpose"""
    with _log_lock:
        _write_lines('a', [f"[{_timestamp()}] [MOVE] ({x}, {y}) - {purpose}\n"])
