"""A steadier way for the bot to find and click a permanent on the battlefield.

The bot finds a card by moving the cursor across a strip of the screen and watching the client's
log for "now hovering object N". Its own sweep goes left to right, waits 50 ms per point and clicks
wherever the cursor is when the right id shows up. Two things go wrong with that:

  * the log line can arrive after the cursor has already moved on, so the click lands one step past
    the card -- on the next card, or on nothing;
  * creatures sit around the middle of the board, so starting at the far left spends most of the
    sweep on empty table.

This replacement starts in the middle and works outwards, waits longer at each point, and before
clicking goes away and comes back to the spot to check the client really reports the wanted card
there. Only battlefield sweeps are replaced; the hand and the stack keep the bot's own code.

MTGA_FORGE_POINTER=off restores the bot's sweep. MTGA_FORGE_HOVER_DWELL sets the wait per point in
seconds (default 0.09).
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional


def _enabled() -> bool:
    return (os.environ.get("MTGA_FORGE_POINTER") or "on").strip().lower() not in ("off", "0", "false", "no")


def _dwell() -> float:
    try:
        return min(0.5, max(0.03, float(os.environ.get("MTGA_FORGE_HOVER_DWELL") or 0.09)))
    except ValueError:
        return 0.09


def centre_out(low: int, high: int, step: int) -> list[int]:
    """Positions from `low` to `high`, `step` apart, ordered from the middle outwards."""
    points = list(range(low, high + 1, step))
    middle = (low + high) / 2.0
    return sorted(points, key=lambda x: (abs(x - middle), x))


class _Hover:
    """Reads the hovered object id from the client's log through the bot's own reader and parser."""

    def __init__(self, controller: Any) -> None:
        self.reader = controller.log_reader
        self.pattern = controller.patterns["hover_id"]
        self.parse = controller._Controller__parse_hover_id_line

    def clear(self) -> None:
        self.reader.clear_new_line_flag(self.pattern)

    def read(self) -> Optional[int]:
        """The newest hovered id reported since the last clear, or None."""
        latest = None
        while self.reader.has_new_line(self.pattern):
            parsed = self.parse(self.reader.get_latest_line_containing_pattern(self.pattern))
            if parsed is not None:
                latest = parsed
        return latest


def find_and_click(
    controller: Any,
    card_id: int,
    p1: tuple[int, int],
    p2: tuple[int, int],
    step: int,
    clicks: int,
    label: str,
    max_scan_sec: Optional[float],
    log: Callable[[str], None],
    action: Optional[Callable[[int, int], None]] = None,
) -> bool:
    """Find the card under the cursor sweep and click it -- or, given `action`, hand it the spot instead."""
    match_id = controller._Controller__live_match_id
    if not controller.can_execute_game_action(match_id):
        return False
    hover = _Hover(controller)
    dwell = _dwell()
    x_min, x_max = sorted((p1[0], p2[0]))
    y_min, y_max = sorted((p1[1], p2[1]))
    step = max(10, int(step))
    # a slower sweep needs proportionally more time than the bot budgeted for its own
    deadline = None if max_scan_sec is None else time.time() + max_scan_sec * max(1.0, dwell / 0.05) + 1.5
    away = (x_min, max(controller.screen_bounds[0][1], y_min - 80))

    def rest() -> None:
        controller.input.move_abs(*away)
        time.sleep(dwell)
        hover.clear()

    def confirmed_at(x: int, y: int) -> bool:
        """Leave, come straight back, and see whether the client reports the wanted card here."""
        rest()
        controller.input.move_abs(x, y)
        time.sleep(dwell * 2)
        return hover.read() == card_id

    rest()
    for y in range(y_min, y_max + 1, step):
        visited: list[int] = []
        for x in centre_out(x_min, x_max, step):
            if not controller.can_execute_game_action(match_id):
                return False
            if deadline is not None and time.time() > deadline:
                log(f"{label}: card {card_id} not found in time")
                return False
            hover.clear()
            controller.input.move_abs(x, y)
            time.sleep(dwell)
            visited.append(x)
            if hover.read() != card_id:
                continue
            # The report may belong to this point or, if it was slow, to one we have just left -- which,
            # working outwards from the middle, is on the other side of the board.
            candidates: list[int] = []
            for seen in reversed(visited[-3:]):
                for cx in (seen, seen - step // 2, seen + step // 2):
                    if x_min <= cx <= x_max and cx not in candidates:
                        candidates.append(cx)
            for cx in candidates:
                if confirmed_at(cx, y):
                    if not controller.can_execute_game_action(match_id):
                        return False
                    if action is not None:
                        log(f"{label}: card {card_id} confirmed at ({cx}, {y})")
                        action(cx, y)
                        return True
                    log(f"{label}: card {card_id} confirmed at ({cx}, {y}); clicking")
                    for _ in range(max(1, int(clicks))):
                        controller.input.left_click(1)
                        time.sleep(0.1)
                    return True
            log(f"{label}: card {card_id} seen near ({x}, {y}) but not confirmed; searching on")
            rest()
    log(f"{label}: card {card_id} not found in the scan region")
    return False


def wrap(original: Callable[..., bool]) -> Callable[..., bool]:
    def select_object_in_region(self, card_id, p1, p2, step, clicks, label, max_scan_sec=None):
        if not _enabled() or "BATTLEFIELD" not in str(label):
            return original(self, card_id=card_id, p1=p1, p2=p2, step=step, clicks=clicks, label=label,
                            max_scan_sec=max_scan_sec)
        try:
            import bot_logger  # type: ignore

            log = lambda message: bot_logger.log_info(f"FORGE pointer: {message}")  # noqa: E731
        except Exception:
            log = lambda message: print(f"[forge] pointer: {message}")  # noqa: E731
        try:
            return find_and_click(self, card_id, p1, p2, step, clicks, label, max_scan_sec, log)
        except Exception as exc:
            log(f"failed ({exc}); using the bot's own sweep")
            return original(self, card_id=card_id, p1=p1, p2=p2, step=step, clicks=clicks, label=label,
                            max_scan_sec=max_scan_sec)

    return select_object_in_region


def install() -> bool:
    try:
        from Controller.MTGAController.Controller import Controller  # type: ignore
    except Exception:
        return False
    if getattr(Controller, "_forge_pointer_installed", False):
        return True
    # a double-underscore method: Python stores it under the class-prefixed name
    name = "_Controller__select_object_in_region"
    setattr(Controller, name, wrap(getattr(Controller, name)))
    Controller._forge_pointer_installed = True
    return True
