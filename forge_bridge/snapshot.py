"""Turns the tracked Arena state into the position the Forge advisor loads."""
from __future__ import annotations

from typing import Optional

from forge_bridge.carddb import CardDb
from forge_bridge.tracker import ArenaTracker

# Arena ability ids for the evergreen keywords, as Forge spells them. Read from the live object, so a
# keyword granted by another permanent is included and one that was removed is not.
KEYWORDS = {
    1: "Deathtouch",
    2: "Defender",
    3: "Double Strike",
    6: "First Strike",
    7: "Flash",
    8: "Flying",
    9: "Haste",
    10: "Hexproof",
    12: "Lifelink",
    13: "Reach",
    14: "Trample",
    15: "Vigilance",
    104: "Indestructible",
    142: "Menace",
}

_COLORS = {
    "CardColor_White": "W",
    "CardColor_Blue": "U",
    "CardColor_Black": "B",
    "CardColor_Red": "R",
    "CardColor_Green": "G",
}

_PHASES = {
    "Phase_Main1": "MAIN1",
    "Phase_Main2": "MAIN2",
}

# Arena step -> Forge phase, for the moments outside the two main phases
_STEPS = {
    "Step_Upkeep": "UPKEEP",
    "Step_Draw": "DRAW",
    "Step_BeginCombat": "COMBAT_BEGIN",
    "Step_DeclareAttack": "COMBAT_DECLARE_ATTACKERS",
    "Step_DeclareBlock": "COMBAT_DECLARE_BLOCKERS",
    "Step_FirstStrikeDamage": "COMBAT_FIRST_STRIKE_DAMAGE",
    "Step_CombatDamage": "COMBAT_DAMAGE",
    "Step_EndCombat": "COMBAT_END",
    "Step_End": "END_OF_TURN",
    "Step_Cleanup": "CLEANUP",
}

_ATTACKING = ("AttackState_Attacking", "AttackState_Declared")
_BLOCKING = ("BlockState_Blocking", "BlockState_Declared")


def forge_phase(turn_info: dict) -> Optional[str]:
    """Forge's name for the phase or step Arena is in, or None when it has no counterpart."""
    phase = _PHASES.get(turn_info.get("phase", ""))
    if phase:
        return phase
    return _STEPS.get(turn_info.get("step", ""))


def combat_request(tracker: ArenaTracker) -> dict:
    """The combat as Arena shows it right now: who is attacking what, and who blocks whom."""
    attackers, blocks = [], []
    for obj in tracker.zone_objects("ZoneType_Battlefield"):
        if obj.get("attackState") in _ATTACKING:
            entry: dict = {"id": obj["instanceId"]}
            target = (obj.get("attackInfo") or {}).get("targetId")
            if target is not None and target not in tracker.players:
                entry["target"] = target
            attackers.append(entry)
        if obj.get("blockState") in _BLOCKING:
            for attacker in (obj.get("blockInfo") or {}).get("attackerIds") or []:
                blocks.append({"blocker": obj["instanceId"], "attacker": attacker})
    return {"attackers": attackers, "blocks": blocks}


def _strip(values: Optional[list], prefix: str) -> list[str]:
    return [str(v)[len(prefix):].replace("_", " ") for v in values or [] if str(v).startswith(prefix)]


def _value(field) -> Optional[int]:
    if isinstance(field, dict):
        field = field.get("value", 0)
    return field if isinstance(field, int) else None


def card_entry(tracker: ArenaTracker, cards: CardDb, obj: dict, on_battlefield: bool, seat_index: dict) -> dict:
    info = cards.get(obj.get("grpId")) or {}
    entry: dict = {"id": obj["instanceId"], "name": info.get("name")}
    if obj.get("type") == "GameObjectType_Token" or info.get("token"):
        entry["token"] = True
    if not on_battlefield:
        return entry

    instance_id = obj["instanceId"]
    if obj.get("isTapped"):
        entry["tapped"] = True
    if obj.get("hasSummoningSickness"):
        entry["sick"] = True
    if obj.get("isFacedown"):
        entry["faceDown"] = True
    if obj.get("damage"):
        entry["damage"] = obj["damage"]
    owner, controller = obj.get("ownerSeatId"), obj.get("controllerSeatId")
    if owner != controller and owner in seat_index:
        entry["owner"] = seat_index[owner]

    types = _strip(obj.get("cardTypes"), "CardType_")
    if types:
        entry["types"] = types
    subtypes = _strip(obj.get("subtypes"), "SubType_")
    if subtypes:
        entry["subtypes"] = subtypes
    supertypes = _strip(obj.get("superTypes"), "SuperType_")
    if supertypes:
        entry["supertypes"] = supertypes
    colors = "".join(_COLORS.get(c, "") for c in obj.get("color") or [])
    if colors:
        entry["colors"] = colors

    power, toughness = _value(obj.get("power")), _value(obj.get("toughness"))
    if "CardType_Creature" in (obj.get("cardTypes") or []) and power is not None and toughness is not None:
        entry["power"] = power
        entry["toughness"] = toughness
    if "power" in info:
        entry["basePower"] = info["power"]
    if "toughness" in info:
        entry["baseToughness"] = info["toughness"]

    keywords = [KEYWORDS[a.get("grpId")] for a in obj.get("uniqueAbilities") or [] if a.get("grpId") in KEYWORDS]
    if keywords:
        entry["keywords"] = sorted(set(keywords))

    counters: dict[str, int] = {}
    for annotation in tracker.annotations_on(instance_id, "AnnotationType_Counter"):
        details = {d.get("key"): (d.get("valueInt32") or [None])[0] for d in annotation.get("details") or []}
        name = cards.counter_name(details.get("counter_type") or 0)
        if name and details.get("count"):
            counters[name] = counters.get(name, 0) + int(details["count"])
    if counters:
        entry["counters"] = counters

    for annotation in tracker.persistent.values():
        # an aura or equipment is the affector, the permanent it is on is the affected object
        if "AnnotationType_Attachment" in (annotation.get("type") or []) and annotation.get("affectorId") == instance_id:
            affected = annotation.get("affectedIds") or []
            if affected:
                entry["attachedTo"] = affected[0]
    return entry


def build_state(tracker: ArenaTracker, cards: CardDb) -> Optional[dict]:
    """The board from our point of view (player 0 is us), or None before a game state is known."""
    me, opp = tracker.my_seat, tracker.opp_seat
    if me is None or opp is None or not tracker.in_sync or me not in tracker.players:
        return None
    seat_index = {me: 0, opp: 1}
    turn_info = tracker.turn_info or {}

    battlefield = tracker.zone_objects("ZoneType_Battlefield")
    exile = tracker.zone_objects("ZoneType_Exile")
    players = []
    for seat in (me, opp):
        info = tracker.players.get(seat) or {}
        hand = tracker.zone_objects("ZoneType_Hand", seat) if seat == me else []
        player = {
            "life": info.get("lifeTotal", 0),
            "landsPlayed": 0,
            "librarySize": tracker.zone_size("ZoneType_Library", seat),
            "handSize": tracker.zone_size("ZoneType_Hand", seat),
            "hand": [card_entry(tracker, cards, o, False, seat_index) for o in hand],
            "battlefield": [
                card_entry(tracker, cards, o, True, seat_index)
                for o in battlefield if o.get("controllerSeatId") == seat
            ],
            "graveyard": [
                card_entry(tracker, cards, o, False, seat_index)
                for o in tracker.zone_objects("ZoneType_Graveyard", seat)
            ],
            "exile": [
                card_entry(tracker, cards, o, False, seat_index)
                for o in exile if o.get("ownerSeatId") == seat and o.get("grpId")
            ],
        }
        players.append(player)

    actions = tracker.my_actions()
    if turn_info.get("activePlayer") == me and not any(a.get("actionType") == "ActionType_Play" for a in actions):
        # Arena offers a land play whenever one is still allowed and we hold a land; not offering one
        # means the drop is used (or there is no land to play, where the value does not matter)
        players[0]["landsPlayed"] = 1

    return {
        "turn": turn_info.get("turnNumber", 1),
        "phase": forge_phase(turn_info) or "MAIN1",
        "activePlayer": seat_index.get(turn_info.get("activePlayer"), 0),
        "players": players,
    }


def main_request(tracker: ArenaTracker) -> dict:
    actions = tracker.my_actions()
    return {
        "castable": sorted({a["instanceId"] for a in actions if a.get("actionType") == "ActionType_Cast" and "instanceId" in a}),
        "playable": sorted({a["instanceId"] for a in actions if a.get("actionType") == "ActionType_Play" and "instanceId" in a}),
    }


def attack_request(tracker: ArenaTracker) -> dict:
    """Arena's own list of creatures that may attack, from the pending DeclareAttackersReq."""
    request = ((tracker.pending or {}).get("declareAttackersReq") or {}) if (
        tracker.pending_type == "GREMessageType_DeclareAttackersReq"
    ) else {}
    listed = request.get("qualifiedAttackers") or request.get("attackers") or []
    return {"legalAttackers": [a["attackerInstanceId"] for a in listed if "attackerInstanceId" in a]}


def block_request(tracker: ArenaTracker) -> dict:
    """Who is attacking us and Arena's legal blocker -> attackers graph, from the pending DeclareBlockersReq."""
    me = tracker.my_seat
    attackers = []
    for obj in tracker.zone_objects("ZoneType_Battlefield"):
        if obj.get("controllerSeatId") == me or obj.get("attackState") not in _ATTACKING:
            continue
        entry: dict = {"id": obj["instanceId"]}
        target = (obj.get("attackInfo") or {}).get("targetId")
        # a seat number means the player; anything else is the planeswalker or battle being attacked
        if target is not None and target not in tracker.players:
            entry["target"] = target
        attackers.append(entry)
    request = ((tracker.pending or {}).get("declareBlockersReq") or {}) if (
        tracker.pending_type == "GREMessageType_DeclareBlockersReq"
    ) else {}
    legal = {
        str(b["blockerInstanceId"]): list(b.get("attackerInstanceIds") or [])
        for b in request.get("blockers") or [] if "blockerInstanceId" in b
    }
    return {"attackers": attackers, "legalBlocks": legal}
