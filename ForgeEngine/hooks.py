"""Forge's steps put around a handful of the controller's own.

The controller asks ``self.decision_hooks`` at five points; with no hooks set
(the engine switched off) every one of them is the bot's own code, unchanged:

* ``all_attack``          attack with the creatures Forge picks instead of everything
* ``review_blocks``       Forge's blocks instead of CombatLogic's
* ``schedule_target``     Forge's target for the spell or ability it chose
* ``handle_group_req``    scry / surveil: drag away what Forge wants gone, then Done
* ``activate_ability``    click the permanent whose ability Forge chose

Every hook falls back to the bot's own step on any problem. Only the choice
changes; the clicking, waiting and submitting are the bot's own primitives.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

import bot_logger
from ForgeEngine.bridge import Bridge, Config, Intent, log, shared
from ForgeEngine import snapshot

DECLARED = ("AttackState_Declared", "AttackState_Attacking")
STATE_WAIT_SEC = 1.5
ABILITY_STARTED_WITHIN_SEC = 3.0
# "No Attacks" sits directly above the attack button: 55 px at 1600x900,
# i.e. 66 px in the bot's 1920x1080 reference frame.
NO_ATTACKS_ABOVE_ATTACK_BUTTON = 66

# Scry and surveil overlays, in the bot's 1920x1080 reference frame, measured
# from one screenshot each (scry at 1600x900, surveil at 1024x576).
OVERLAYS = {
    "scry": {"cards": ((850, 470), (1390, 490)), "single": (1056, 480), "away": (600, 480)},
    "surveil": {"cards": ((800, 530), (1290, 550)), "single": (1022, 544), "away": (300, 544)},
}


def _kind(tracker, instance_id) -> tuple:
    """Creatures Arena treats as interchangeable when one of a stack is clicked."""
    obj = tracker.object(instance_id) or {}
    return (obj.get("grpId"), snapshot.value_of(obj.get("power")), snapshot.value_of(obj.get("toughness")))


def attack_difference(tracker, chosen: list, marked: list) -> tuple[list, list]:
    """(creatures still to mark, creatures to un-mark), counting identical ones by kind."""
    want = [_kind(tracker, i) for i in chosen]
    have = [_kind(tracker, i) for i in marked]
    to_mark = []
    for instance_id in chosen:
        kind = _kind(tracker, instance_id)
        if have.count(kind) < want.count(kind) and instance_id not in marked:
            to_mark.append(instance_id)
            have.append(kind)
    have = [_kind(tracker, i) for i in marked]
    to_unmark = []
    for instance_id in marked:
        kind = _kind(tracker, instance_id)
        if have.count(kind) > want.count(kind):
            to_unmark.append(instance_id)
            have.remove(kind)
    return to_mark, to_unmark


def choose_target(intent: Intent | None, opposing: list, ours: list, face_legal: bool):
    """Forge's target as one of the bot's own target steps, or None to let the bot decide.

    Returns ``("creature", id, friendly)`` or ``("face",)``.
    """
    if intent is None:
        return None
    if len(intent.cards) == 1 and not intent.players:
        target = intent.cards[0]
        if target in ours:
            return ("creature", target, True)
        if target in opposing:
            return ("creature", target, False)
        return None
    if not intent.cards and len(intent.players) == 1 and intent.players[0] == 1 and face_legal:
        return ("face",)
    return None


def group_requests(line: str) -> list[dict]:
    """The scry/surveil requests in one Player.log line."""
    start = line.find("{")
    if start == -1:
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
                "ids": [i for i in request.get("instanceIds") or [] if isinstance(i, int)],
                "seats": [s for s in message.get("systemSeatIds") or [] if isinstance(s, int)],
            })
    return found


class ForgeHooks:
    def __init__(self, bridge: Bridge | None = None, config: Callable[[], Config] = Config.from_env):
        self.bridge = bridge or shared()
        self.config = config
        self._attack_busy = threading.Lock()
        self._ability_busy = threading.Lock()
        self._declined: tuple | None = None
        self._recent_groups: list = []

    # -- attack -----------------------------------------------------------------

    def _marked(self) -> list[int]:
        """Our creatures Arena currently shows as declared attackers."""
        with self.bridge._lock:
            self.bridge.sync()
            tracker = self.bridge.tracker
            result: list[int] = []
            for o in tracker.zone_objects("ZoneType_Battlefield"):
                iid = o.get("instanceId")
                if (
                    isinstance(iid, int)
                    and o.get("controllerSeatId") == tracker.my_seat
                    and o.get("attackState") in DECLARED
                ):
                    result.append(iid)
            return sorted(result)

    def _still_asked(self) -> bool:
        with self.bridge._lock:
            self.bridge.sync()
            return self.bridge.tracker.pending_is_current("GREMessageType_DeclareAttackersReq")

    def _toggle(self, controller, instance_id: int) -> bool:
        """Click one creature and wait for Arena to show the change."""
        before = self._marked()
        if not controller.select_battlefield_permanent(instance_id, 1):
            return False
        deadline = time.monotonic() + STATE_WAIT_SEC
        while time.monotonic() < deadline:
            time.sleep(0.1)
            if self._marked() != before:
                return True
        return False

    def _names(self, ids) -> str:
        return ", ".join(self.bridge.name(i) for i in ids) or "nobody"

    def all_attack(self, controller, own: Callable[[], bool]) -> bool:
        """The bot's all_attack with Forge's choice of attackers in front.

        The bot calls all_attack more than once per attack step (after every
        marked creature Arena sends a fresh request, and from the recovery
        timer). A click on a marked creature un-marks it, so two runs side by
        side would undo each other: only one runs at a time, and every run
        starts from what Arena says is marked and only clicks the difference.
        """
        if not self._attack_busy.acquire(blocking=False):
            log("attack: already being declared by another call; leaving it to that one")
            return True
        try:
            return self._attack(controller, own)
        except Exception as exc:
            log(f"selective attack failed ({exc}); attacking with everything")
            return own()
        finally:
            self._attack_busy.release()

    def _attack(self, controller, own: Callable[[], bool]) -> bool:
        config = self.config()
        if config.off() or not config.acting() or config.attack != "select":
            return own()
        if controller.attack_target_required():
            # A planeswalker to attack: the bot's own recipient flow handles it.
            return own()
        plan = self.bridge.plan_attack(config)
        if plan is None:
            return own()
        turn = plan.key[1]
        held = [i for i in plan.legal if i not in plan.attackers]
        if not held:
            log(f"attack turn={turn}: everything attacks ({plan.choice})")
            return own()
        if not self._still_asked():
            # This turn's attack has already been answered.
            return True

        if not plan.attackers:
            if self._declined is not None and self._declined[0] == plan.key and time.monotonic() - self._declined[1] < 3.0:
                # Just clicked; give Arena time to move on.
                return True
            for instance_id in self._marked():
                # Something was marked earlier; take it back first.
                self._toggle(controller, instance_id)
            base = controller.main_br_button_coordinates
            point = (int(base[0]), int(base[1]) - NO_ATTACKS_ABOVE_ATTACK_BUTTON)
            target, source = controller._map_abs_point_to_arena(
                point, label="NO_ATTACKS", force_reacquire=True, apply_correction=False
            )
            if source == "absolute_no_arena":
                return False
            # One press submits "no attackers". Through the bot's verified
            # press: a click Arena swallows (seen live, 2026-10-11) is pressed
            # again rather than left to the recovery timer 3.6 s later.
            controller.press_combat_button_verified(
                target,
                "FORGE_NO_ATTACKS",
                declared_markers=["SubmitAttackersReq"],
                submitted_markers=["SubmitAttackersReq"],
            )
            controller.note_attack_submitted()
            self._declined = (plan.key, time.monotonic())
            log(f"attack turn={turn}: no attack; holding back {self._names(held)}")
            return True

        # Some, not all: bring what Arena has marked in line with the plan, then submit.
        for _ in range(2):
            to_mark, to_unmark = attack_difference(self.bridge.tracker, plan.attackers, self._marked())
            if not to_mark and not to_unmark:
                break
            for instance_id in to_mark + to_unmark:
                self._toggle(controller, instance_id)
        marked = self._marked()
        if not marked:
            log(f"attack turn={turn}: none of the chosen attackers could be marked; attacking with everything")
            return own()
        if not self._still_asked():
            return True
        target, source = controller._map_abs_point_to_arena(
            controller.main_br_button_coordinates,
            label="ATTACK_SELECTED",
            force_reacquire=True,
            apply_correction=False,
        )
        if source == "absolute_no_arena":
            return False
        # The creatures are already marked, so the first press is the submit.
        controller.press_combat_button_verified(
            target,
            "ATTACK_SELECTED",
            declared_markers=["AttackState_Declared", '"canSubmitAttackers": true'],
            submitted_markers=["SubmitAttackersReq", "AttackState_Attacking"],
        )
        controller.note_attack_submitted()
        staying = [i for i in plan.legal if i not in marked]
        log(f"attack turn={turn}: attacking with [{self._names(marked)}], holding back [{self._names(staying)}]")
        return True

    # -- blocks -----------------------------------------------------------------

    def review_blocks(self, legal_blockers: list, attacking_ids, decision: dict | None) -> dict | None:
        """Forge's blocks in place of the bot's, limited to what Arena allows."""
        config = self.config()
        if config.off() or not config.blocks:
            return decision
        try:
            pairs = self.bridge.choose_blocks(legal_blockers, attacking_ids, config)
        except Exception as exc:
            log(f"block decision failed ({exc}); using the bot's blocks")
            return decision
        if pairs is None or not config.acting():
            return decision
        bot_assignments = list((decision or {}).get("assignments") or [])
        if not pairs and bot_assignments:
            log("Forge returned no legal blocks; retaining the bot's defensive block assignments")
            return decision
        reviewed = dict(decision or {})
        reviewed["bot_assignments"] = bot_assignments
        reviewed["assignments"] = [[b, a] for b, a in pairs]
        reviewed["detail"] = [{"blocker": b, "attacker": a, "outcome": "forge"} for b, a in pairs]
        reviewed["reason"] = "forge"
        return reviewed

    # -- targets ----------------------------------------------------------------

    def schedule_target(self, controller, own: Callable[..., None], source_id, reason,
                        legal_creature_ids=None, face_legal=None, own_creature_ids=None) -> None:
        """The bot's choice of target, narrowed to Forge's when the prompt is for Forge's move."""
        def bots_own():
            own(source_id, reason, legal_creature_ids, face_legal, own_creature_ids)

        try:
            intent = self.bridge.intent_for(source_id)
            pick = choose_target(intent, list(legal_creature_ids or []), list(own_creature_ids or []), bool(face_legal))
        except Exception as exc:
            log(f"target: failed ({exc}); the bot picks the target")
            return bots_own()
        if pick is None:
            if intent is not None:
                log(f"target: Forge's target {intent.names} cannot be clicked this way; the bot picks")
            return bots_own()
        if pick[0] == "face":
            log("target: the opponent, as Forge chose")
            return own(source_id, reason, [], True, [])
        _, creature_id, friendly = pick
        name = intent.names[0] if intent and intent.names else creature_id
        log(f"target: {name} ({'ours' if friendly else 'theirs'}), as Forge chose")
        controller.schedule_creature_target_selection(source_id, creature_id, f"{reason}(Forge)", friendly=friendly)

    # -- scry and surveil -----------------------------------------------------

    def handle_group_req(self, controller, line: str, own: Callable[[str], None]) -> None:
        """Forge's advice in front of the bot's own answer to a grouping prompt.

        Runs on the log reader's thread, so the work (a Forge request and the
        drags, which need the reader to report hovers) goes to its own thread,
        with gameplay held, and the bot's handler (which presses Done) runs
        after it.
        """
        config = self.config()
        if config.off() or config.scry in ("off", "0", "false", "no"):
            return own(line)
        requests = []
        now = time.monotonic()
        self._recent_groups = [r for r in self._recent_groups if now - r[2] < 8.0]
        for request in group_requests(line):
            if any(r[0] == request["mode"] and r[1] == request["ids"] for r in self._recent_groups):
                continue
            self._recent_groups.append((request["mode"], request["ids"], now))
            requests.append(request)
        if not requests:
            return own(line)
        acting = config.acting() and config.scry == "on"
        if not acting:
            def advise_only():
                for request in requests:
                    self._quietly(lambda r=request: self.advise_group(controller, r, config, act=False))

            threading.Thread(target=advise_only, name="forge-scry", daemon=True).start()
            return own(line)

        controller.hold_group_prompt(12.0)

        def work():
            try:
                for request in requests:
                    self._quietly(lambda r=request: self.advise_group(controller, r, config, act=True))
            finally:
                controller.hold_group_prompt(0.0)
                own(line)

        threading.Thread(target=work, name="forge-scry", daemon=True).start()

    @staticmethod
    def _quietly(work) -> None:
        try:
            work()
        except Exception as exc:
            log(f"scry: failed ({exc})")

    def advise_group(self, controller, request: dict, config: Config, act: bool) -> dict | None:
        bridge = self.bridge
        with bridge._lock:
            if not bridge.usable():
                return None
            bridge.sync()
            tracker = bridge.tracker
            asked_of_us = not request["seats"] or tracker.my_seat in request["seats"]
            if not tracker.in_sync or not asked_of_us or bridge.cards is None:
                return None
            position = snapshot.build_state(tracker, bridge.cards)
            if position is None:
                return None
            looked = []
            for instance_id in request["ids"]:
                obj = tracker.object(instance_id) or {}
                name = bridge.cards.name(obj.get("grpId"))
                if not name:
                    # A card we cannot name cannot be judged.
                    return None
                looked.append({"id": instance_id, "name": name})
            if not looked:
                return None
            me = position["players"][0]
            me["library"] = looked
            me["librarySize"] = max(int(me.get("librarySize") or 0), len(looked))
            decision = bridge.decision(
                bridge.decide("scry", position, config, {"mode": request["mode"], "cards": request["ids"]})
            )
            turn = tracker.turn_info.get("turnNumber")
        keep = [str(n) for n in decision.get("topNames") or []]
        away = [str(n) for n in decision.get("awayNames") or []]
        log(f"{request['mode']} turn={turn}: forge would keep [{', '.join(keep)}] away [{', '.join(away)}]")
        if act and away:
            decision["moved"] = self._drag_away(controller, request, decision)
        bridge.record({"kind": request["mode"], "turn": turn, "forge": decision})
        return decision

    def _drag_away(self, controller, request: dict, decision: dict) -> list:
        """Drag every card Forge wants gone out of the pile; returns the ids dragged."""
        layout = OVERLAYS[request["mode"]]

        def on_screen(point):
            mapped, _source = controller._map_abs_point_to_arena(
                point, label="FORGE_SCRY", force_reacquire=False, apply_correction=False
            )
            return mapped

        away_point = on_screen(layout["away"])
        moved = []
        time.sleep(1.0)  # let the overlay settle
        for card_id in [i for i in decision.get("away") or [] if isinstance(i, int)]:
            p1, p2 = on_screen(layout["cards"][0]), on_screen(layout["cards"][1])
            start = None
            if controller.locate_object_in_region(card_id, p1, p2, step=40, label="FORGE_SCRY_CARD", max_scan_sec=3.0):
                pos = controller.input.position()
                start = (pos.x, pos.y)
            elif len(request["ids"]) == 1:
                # A lone card always sits in the same place.
                start = on_screen(layout["single"])
                log(f"scry: card {card_id} not reported under the cursor; using the single-card position")
            if start is None:
                log(f"scry: card {card_id} not found in the overlay; it stays on top")
                continue
            self._drag(controller, start, away_point)
            moved.append(card_id)
        return moved

    @staticmethod
    def _drag(controller, start, end) -> None:
        """Press, carry in small steps (Arena ignores a teleporting drag), release."""
        inp = controller.input
        bot_logger.log_move(start[0], start[1], "FORGE_SCRY_DRAG_START")
        inp.move_abs(start[0], start[1])
        time.sleep(0.15)
        inp.left_down()
        time.sleep(0.15)
        steps = 12
        try:
            for step in range(1, steps + 1):
                x = round(start[0] + (end[0] - start[0]) * step / steps)
                y = round(start[1] + (end[1] - start[1]) * step / steps)
                inp.move_abs(x, y)
                time.sleep(0.03)
            time.sleep(0.15)
        finally:
            inp.left_up()
        bot_logger.log_move(end[0], end[1], "FORGE_SCRY_DRAG_END")
        time.sleep(0.3)

    # -- abilities ----------------------------------------------------------------

    def _situation(self, card_id: int, ability_id: int) -> dict | None:
        """What the game says about a permanent of ours and its ability; None if not ours."""
        bridge = self.bridge
        with bridge._lock:
            bridge.sync()
            tracker = bridge.tracker
            if not tracker.in_sync or tracker.my_seat is None:
                return None
            ours = [o for o in tracker.zone_objects("ZoneType_Battlefield") if o.get("controllerSeatId") == tracker.my_seat]
            permanent = next((o for o in ours if o.get("instanceId") == card_id), None)
            if permanent is None:
                return None
            per_permanent: dict = {}
            for action in tracker.my_actions():
                if isinstance(action.get("instanceId"), int):
                    per_permanent.setdefault(action["instanceId"], []).append(action)

            def only_this_ability(instance_id):
                offers = per_permanent.get(instance_id) or []
                return (len(offers) == 1 and offers[0].get("actionType") == "ActionType_Activate"
                        and offers[0].get("abilityGrpId") == ability_id)

            # Another copy in the same state, offering the same ability, is as good.
            twins = {
                o.get("instanceId") for o in ours
                if o.get("grpId") == permanent.get("grpId")
                and bool(o.get("isTapped")) == bool(permanent.get("isTapped"))
                and only_this_ability(o.get("instanceId"))
            }
            twins.add(card_id)
            return {
                "offered": only_this_ability(card_id),
                "ability_id": ability_id,
                "twins": twins,
                "name": bridge.name(card_id),
                "match_id": tracker.game_info.get("matchID"),
                "turn": tracker.turn_info.get("turnNumber"),
                "pending_before": json.dumps(tracker.pending, sort_keys=True),
                "stack_before": {a.get("instanceId") for a in tracker.abilities_on_stack()},
            }

    def _started(self, situation: dict) -> bool:
        """A new source ability is on the stack, or a fresh local cost/target request arrived."""
        bridge = self.bridge
        with bridge._lock:
            bridge.sync()
            tracker = bridge.tracker
            if tracker.game_info.get("matchID") != situation["match_id"]:
                return False
            evidence = tracker.activation_evidence(situation["observer"])
            if evidence:
                situation["ack_evidence"] = evidence
                return True
            if any(a.get("parentId") in situation["twins"]
                   and a.get("grpId") == situation["ability_id"]
                   and a.get("controllerSeatId") == tracker.my_seat
                   and a.get("instanceId") not in situation["stack_before"]
                   for a in tracker.abilities_on_stack()):
                situation["ack_evidence"] = {"kind": "source_ability_on_stack"}
                return True
            kind = tracker.pending_type
            pending = tracker.pending or {}
            started = (
                kind in ("GREMessageType_PayCostsReq", "GREMessageType_SelectTargetsReq")
                and tracker.pending_is_current(kind)
                and tracker.my_seat in (pending.get("systemSeatIds") or [])
                and json.dumps(pending, sort_keys=True) != situation["pending_before"]
            )
            if started:
                situation["ack_evidence"] = {
                    "kind": kind, "msg_id": pending.get("msgId"),
                    "game_state_id": pending.get("gameStateId"),
                }
            return started

    def activate_ability(self, controller, card_id: int, ability_id: int, own: Callable[[], None]) -> None:
        if not self._ability_busy.acquire(blocking=False):
            log(f"ability: concurrent activation skipped card={card_id} ability={ability_id}")
            return
        try:
            self._activation_watch = None
            self._activate_ability(controller, card_id, ability_id, own)
        finally:
            with self.bridge._lock:
                if self._activation_watch is not None:
                    self.bridge.tracker.end_activation_observation(self._activation_watch)
                self._activation_watch = None
            self._ability_busy.release()

    def _activate_ability(self, controller, card_id: int, ability_id: int, own: Callable[[], None]) -> None:
        """The bot's "activate ability" with our permanents' abilities carried out first.

        The bot's own step only presses the button in the corner (its graveyard
        cases); for a permanent on the battlefield nothing clicked it. Here the
        permanent is clicked, and the stack is checked: if the ability did not
        start, that permanent is left alone for the rest of the turn.
        """
        config = self.config()
        if not config.abilities or config.off():
            return own()
        try:
            situation = self._situation(card_id, ability_id)
        except Exception as exc:
            log(f"ability: failed ({exc}); nothing clicked")
            return None
        if situation is None:
            return own()
        name = situation["name"]
        if not situation["offered"]:
            log(f"ability: {name}: no longer on offer; leaving it")
            self.bridge.ability_result(card_id, False)
            return None
        match_id = situation["match_id"]
        context = f"match_id={match_id} card={card_id} ability={ability_id}"

        def acknowledged(confirmed):
            evidence = json.dumps(situation.get("ack_evidence"), sort_keys=True)
            log(f"ABILITY_ACTIVATION_ACKNOWLEDGED: {context} confirmation_clicked={confirmed} evidence={evidence}")
            self.bridge.ability_result(card_id, True)

        def allowed():
            if not match_id or not controller.can_execute_game_action(match_id):
                return False
            with self.bridge._lock:
                self.bridge.sync()
                tracker = self.bridge.tracker
                return (tracker.game_info.get("matchID") == match_id
                        and tracker.turn_info.get("turnNumber") == situation["turn"])

        if not allowed():
            return None
        with self.bridge._lock:
            # Drain old messages before arming; only subsequent evidence belongs
            # to the source click below. The observer sees every message in a poll.
            self.bridge.sync()
            if (self.bridge.tracker.game_info.get("matchID") != match_id
                    or self.bridge.tracker.turn_info.get("turnNumber") != situation["turn"]):
                return None
            situation["stack_before"] = set(self.bridge.tracker.objects)
            self._activation_watch = self.bridge.tracker.begin_activation_observation(
                situation["twins"], ability_id,
            )
            situation["observer"] = self._activation_watch
        if not controller.select_battlefield_permanent(card_id, 1):
            log(f"ability: {name}: not found on the battlefield; not used this turn")
            self.bridge.ability_result(card_id, False)
            return None
        log(f"ABILITY_ACTIVATION_SELECTED: {context} name={name}")
        confirmed = False
        deadline = time.monotonic() + ABILITY_STARTED_WITHIN_SEC
        while time.monotonic() < deadline:
            if not allowed():
                return None
            if self._started(situation):
                acknowledged(confirmed)
                return None
            if not confirmed and controller.click_ability_control(
                "activate", action_guard=lambda: allowed() and not self._started(situation),
            ):
                confirmed = True
                log(f"ABILITY_ACTIVATION_CLICKED: {context}")
                deadline = time.monotonic() + ABILITY_STARTED_WITHIN_SEC
            time.sleep(0.15)
        if not allowed():
            return None
        if self._started(situation):
            acknowledged(confirmed)
            return None
        log(f"ABILITY_ACTIVATION_TIMEOUT: {context} confirmation_clicked={confirmed}")
        with self.bridge._lock:
            pending_after = json.dumps(self.bridge.tracker.pending, sort_keys=True)
        controller.write_ability_activation_debug({
            "card_id": card_id, "ability_id": ability_id, "match_id": match_id,
            "name": name, "confirmation_clicked": confirmed,
            "pending_before": situation["pending_before"],
            "pending_after": pending_after,
            "activation_observer": {
                "match_id": match_id, "source_ids": sorted(situation["twins"]),
                "ability_id": ability_id,
                "armed_message": situation["observer"]["armed_message"],
                "armed_state": situation["observer"]["armed_state"],
                "evidence": situation.get("ack_evidence"),
            },
        })
        # Cancel only if the same local activation is still unacknowledged.
        if not allowed():
            return None
        if self._started(situation):
            acknowledged(confirmed)
            return None
        cancelled = controller.click_ability_control(
            "cancel", action_guard=lambda: allowed() and not self._started(situation),
        )
        if not allowed():
            return None
        started = self._started(situation)
        log(f"ABILITY_ACTIVATION_RECOVERY: {context} cancel_clicked={cancelled} acknowledged={started}")
        if started:
            acknowledged(confirmed)
        else:
            self.bridge.ability_result(card_id, False)
        return None
