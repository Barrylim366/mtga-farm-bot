"""Forge's opinion on scry and surveil prompts.

Arena asks with a GroupReq listing the cards being looked at. Forge is asked which to keep on top and
which to send away (bottom of the library for scry, graveyard for surveil), and the answer goes to
the log and the moves window.

The bot's own handler only presses Done, which keeps every card on top. Before it runs, the cards
Forge wants gone are dragged out: to the left of the library pile for a scry (the bottom), into the
Graveyard box for a surveil. Then the bot presses Done as it always did. A card that cannot be found
or dragged simply stays on top, which is what happened before this existed.

Positions were measured from one screenshot of each overlay (scry at 1600x900, surveil at 1024x576)
and are kept in the bot's 1920x1080 reference frame.

MTGA_FORGE_SCRY: on (default: act) | advise (log and show only) | off
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Optional

from forge_bridge import forge_ai, snapshot


def _mode() -> str:
    value = (os.environ.get("MTGA_FORGE_SCRY") or "on").strip().lower()
    if value in ("off", "0", "false", "no"):
        return "off"
    return "advise" if value in ("advise", "shadow") else "on"


def _enabled() -> bool:
    return _mode() != "off"


# Where things are in each overlay, in the bot's 1920x1080 reference frame.
#   cards:  the strip the looked-at cards are laid out in (swept with the cursor to find one)
#   single: where the card sits when there is only one
#   away:   where to drop a card that should not stay on top
LAYOUT = {
    "scry": {"cards": ((850, 470), (1390, 490)), "single": (1056, 480), "away": (600, 480)},
    "surveil": {"cards": ((800, 530), (1290, 550)), "single": (1022, 544), "away": (300, 544)},
}
_OVERLAY_SETTLE_SEC = 1.0


def drag(controller: Any, start: tuple[int, int], end: tuple[int, int]) -> None:
    """Press on `start`, carry the card to `end` in small steps (Arena ignores a teleporting drag), release."""
    controller.input.move_abs(*start)
    time.sleep(0.15)
    controller.input.left_down()
    time.sleep(0.15)
    steps = 12
    for i in range(1, steps + 1):
        x = int(round(start[0] + (end[0] - start[0]) * i / steps))
        y = int(round(start[1] + (end[1] - start[1]) * i / steps))
        controller.input.move_abs(x, y)
        time.sleep(0.03)
    time.sleep(0.15)
    controller.input.left_up()
    time.sleep(0.3)


def act(controller: Any, request: dict, decision: dict, log: Callable[[str], None]) -> list[int]:
    """Drag every card Forge wants gone out of the pile. Returns the ids that were dragged."""
    from forge_bridge import pointer

    layout = LAYOUT[request["mode"]]

    def on_screen(point: tuple[int, int]) -> tuple[int, int]:
        target, _source = controller._map_abs_point_to_arena(point, label="FORGE_SCRY", force_reacquire=False)
        return int(target[0]), int(target[1])

    away_point = on_screen(layout["away"])
    moved: list[int] = []
    time.sleep(_OVERLAY_SETTLE_SEC)
    for card_id in decision.get("away") or []:
        found: list[tuple[int, int]] = []
        try:
            p1, p2 = on_screen(layout["cards"][0]), on_screen(layout["cards"][1])
            pointer.find_and_click(
                controller, card_id, p1, p2, 40, 1, "FORGE_SCRY_CARD", 3.0, log,
                action=lambda x, y: found.append((x, y)),
            )
        except Exception as exc:
            log(f"could not look for card {card_id} ({exc})")
        if not found and len(request["ids"]) == 1:
            # a lone card always sits in the same place
            found.append(on_screen(layout["single"]))
            log(f"card {card_id} not reported under the cursor; using the measured single-card position")
        if not found:
            log(f"card {card_id} not found in the overlay; it stays on top")
            continue
        drag(controller, found[0], away_point)
        moved.append(card_id)
    return moved


def group_requests(line: str) -> list[dict]:
    """The scry/surveil requests in one Player.log line."""
    start = line.find("{")
    if start < 0:
        return []
    try:
        payload = json.loads(line[start:])
    except ValueError:
        return []
    found = []
    for message in (payload.get("greToClientEvent") or {}).get("greToClientMessages") or []:
        if message.get("type") != "GREMessageType_GroupReq":
            continue
        request = message.get("groupReq") or {}
        context = str(request.get("context") or "")
        if "Scry" in context or "Surveil" in context:
            found.append({
                "mode": "surveil" if "Surveil" in context else "scry",
                "ids": list(request.get("instanceIds") or []),
                "seats": message.get("systemSeatIds") or [],
            })
    return found


def advise(
    bridge: "forge_ai.Bridge", request: dict, config: "forge_ai.Config", controller: Any = None
) -> Optional[dict]:
    """Ask Forge about one scry/surveil prompt, act on it when given the bot's controller, and publish it."""
    with bridge.lock:
        if not bridge._usable():
            return None
        bridge.sync()
        tracker = bridge.tracker
        if not tracker.in_sync or tracker.my_seat not in (request["seats"] or [tracker.my_seat]):
            return None
        state = snapshot.build_state(tracker, bridge.cards)  # type: ignore[arg-type]
        if state is None:
            return None
        looked = []
        for instance_id in request["ids"]:
            obj = tracker.objects.get(instance_id) or {}
            name = bridge.cards.name(obj.get("grpId")) if bridge.cards else None
            if not name:
                return None  # a card we cannot name cannot be judged
            looked.append({"id": instance_id, "name": name})
        if not looked:
            return None
        me = state["players"][0]
        me["library"] = looked
        me["librarySize"] = max(me.get("librarySize", 0), len(looked))
        decision = bridge._decide("scry", state, config, mode=request["mode"], cards=request["ids"])["decision"]

    away_to = "the graveyard" if request["mode"] == "surveil" else "the bottom"
    keep, away = decision.get("topNames") or [], decision.get("awayNames") or []
    parts = []
    if keep:
        parts.append("keep " + ", ".join(keep) + " on top")
    if away:
        parts.append("send " + ", ".join(away) + " to " + away_to)
    turn = (bridge.tracker.turn_info or {}).get("turnNumber")
    forge_ai._log(f"{request['mode']} turn={turn}: forge would " + "; ".join(parts))
    acting = controller is not None and config.acting and _mode() == "on"
    explanation = ["Forge's AI would " + " and ".join(parts) + "."]
    if not away:
        explanation.append("Nothing to move: pressing Done keeps the card on top.")
    elif not acting:
        explanation.append("Advice only: the bot pressed Done, which keeps every card on top.")
    else:
        moved = act(controller, request, decision, lambda message: forge_ai._log("scry: " + message))
        decision["moved"] = moved
        if len(moved) == len(decision.get("away") or []):
            explanation.append("Dragged out before pressing Done.")
        else:
            explanation.append(
                f"Dragged {len(moved)} of {len(decision.get('away') or [])}; a card that could not be found stays on top."
            )
    forge_ai.publish(
        "Turn {} · {} — {}".format(
            turn, "Surveil" if request["mode"] == "surveil" else "Scry",
            ", ".join(c["name"] for c in looked),
        ),
        "\n".join(explanation),
    )
    bridge._record({"kind": request["mode"], "turn": turn, "forge": decision})
    return decision


def wrap(original: Callable[..., Any]) -> Callable[..., Any]:
    recent: dict = {}

    def handle_group_req(self, line):
        if _enabled():
            config = forge_ai.Config()
            if not config.off:
                for request in group_requests(line):
                    # the client can log the same prompt twice; drag for it once
                    key = (request["mode"], tuple(request["ids"]))
                    if time.time() - recent.get(key, 0.0) < 8.0:
                        continue
                    recent[key] = time.time()
                    if config.acting and _mode() == "on":
                        # Before the bot's handler: it presses Done, which would close the overlay under
                        # the drag. Its own "scry in progress" gate keeps other clicks away meanwhile.
                        try:
                            self._Controller__group_req_active_until = time.time() + 10.0
                        except Exception:
                            pass
                        forge_ai._quietly(advise, forge_ai.Bridge.instance(), request, config, self)
                    else:
                        threading.Thread(
                            target=forge_ai._quietly, args=(advise, forge_ai.Bridge.instance(), request, config),
                            name="forge-scry", daemon=True,
                        ).start()
        return original(self, line)

    return handle_group_req


def install() -> bool:
    try:
        from Controller.MTGAController.Controller import Controller  # type: ignore
    except Exception:
        return False
    if getattr(Controller, "_forge_scry_installed", False):
        return True
    name = "_Controller__handle_group_req"
    setattr(Controller, name, wrap(getattr(Controller, name)))
    Controller._forge_scry_installed = True
    return True
