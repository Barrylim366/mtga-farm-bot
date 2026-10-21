"""Turns the tracked Arena state into the position the Forge advisor loads.

Player 0 is always us. The request shapes match what
``forge_advisor/.../Position.java`` and ``Decisions.java`` read.
"""
from __future__ import annotations

from ForgeEngine.carddb import CardDb
from ForgeEngine.tracker import ArenaTracker

# Arena ability ids of the evergreen keywords, as Forge spells them. Read from
# the live object, so a keyword granted by another permanent is included and
# one that was removed is not.
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

COLOR_LETTER = {
    "CardColor_White": "W",
    "CardColor_Blue": "U",
    "CardColor_Black": "B",
    "CardColor_Red": "R",
    "CardColor_Green": "G",
}

MANA_LETTER = {
    "ManaColor_White": "W",
    "ManaColor_Blue": "U",
    "ManaColor_Black": "B",
    "ManaColor_Red": "R",
    "ManaColor_Green": "G",
    "ManaColor_Generic": "",
    "ManaColor_Colorless": "",
}

ATTACKING = ("AttackState_Attacking", "AttackState_Declared")
BLOCKING = ("BlockState_Blocking", "BlockState_Declared")

_STEP_PHASE = {
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


def forge_phase(turn_info: dict) -> str | None:
    """Forge's name for the phase or step Arena is in, or None."""
    phase = (turn_info or {}).get("phase")
    if phase == "Phase_Main1":
        return "MAIN1"
    if phase == "Phase_Main2":
        return "MAIN2"
    return _STEP_PHASE.get((turn_info or {}).get("step") or "")


def value_of(field):
    """A number the GRE sends plain or wrapped as ``{"value": n}``."""
    if isinstance(field, dict):
        inner = field.get("value", 0)
        return inner if isinstance(inner, int) else None
    return field if isinstance(field, int) and not isinstance(field, bool) else None


def _strip(values, prefix: str) -> list[str]:
    return [v[len(prefix):].replace("_", " ") for v in (values or []) if isinstance(v, str) and v.startswith(prefix)]


def _attack_target(tracker: ArenaTracker, obj: dict):
    """What an attacker attacks when it is not a player (a planeswalker or battle)."""
    target = (obj.get("attackInfo") or {}).get("targetId")
    if target is None or target in tracker.players:
        return None
    return target


def combat_request(tracker: ArenaTracker) -> dict:
    """The combat as Arena shows it right now: who attacks what, who blocks whom."""
    attackers, blocks = [], []
    for obj in tracker.zone_objects("ZoneType_Battlefield"):
        if obj.get("attackState") in ATTACKING:
            entry = {"id": obj.get("instanceId")}
            target = _attack_target(tracker, obj)
            if target is not None:
                entry["target"] = target
            attackers.append(entry)
        if obj.get("blockState") in BLOCKING:
            for attacker in (obj.get("blockInfo") or {}).get("attackerIds") or []:
                blocks.append({"blocker": obj.get("instanceId"), "attacker": attacker})
    return {"attackers": attackers, "blocks": blocks}


def card_entry(tracker: ArenaTracker, cards: CardDb, obj: dict, on_battlefield: bool, seat_index: dict) -> dict:
    """One card as the advisor is told about it."""
    info = cards.get(obj.get("grpId")) or {}
    entry: dict = {"id": obj.get("instanceId"), "name": info.get("name")}
    if obj.get("type") == "GameObjectType_Token" or info.get("token"):
        entry["token"] = True
    if not on_battlefield:
        return entry

    instance_id = obj.get("instanceId")
    for flag, key in (("isTapped", "tapped"), ("hasSummoningSickness", "sick"), ("isFacedown", "faceDown")):
        if obj.get(flag):
            entry[key] = True
    if obj.get("damage"):
        entry["damage"] = obj.get("damage")
    owner, controller = obj.get("ownerSeatId"), obj.get("controllerSeatId")
    if owner != controller and owner in seat_index:
        entry["owner"] = seat_index[owner]

    for source, prefix, key in (
        ("cardTypes", "CardType_", "types"),
        ("subtypes", "SubType_", "subtypes"),
        ("superTypes", "SuperType_", "supertypes"),
    ):
        names = _strip(obj.get(source), prefix)
        if names:
            entry[key] = names
    colors = "".join(COLOR_LETTER.get(c, "") for c in obj.get("color") or [] if isinstance(c, str))
    if colors:
        entry["colors"] = colors

    is_creature = "CardType_Creature" in (obj.get("cardTypes") or [])
    power, toughness = value_of(obj.get("power")), value_of(obj.get("toughness"))
    if is_creature and power is not None and toughness is not None:
        entry["power"] = power
        entry["toughness"] = toughness
    if "power" in info:
        entry["basePower"] = info["power"]
    if "toughness" in info:
        entry["baseToughness"] = info["toughness"]

    keywords = sorted(
        {KEYWORDS[a.get("grpId")] for a in obj.get("uniqueAbilities") or [] if a.get("grpId") in KEYWORDS}
    )
    if keywords:
        entry["keywords"] = keywords

    if isinstance(instance_id, int):
        counters: dict = {}
        for annotation in tracker.annotations_on(instance_id, "AnnotationType_Counter"):
            def detail(key, _annotation=annotation):
                for item in reversed(_annotation.get("details") or []):
                    if item.get("key") == key:
                        values = item.get("valueInt32") or []
                        return values[0] if values else None
                return None

            count = detail("count") or 0
            name = cards.counter_name(detail("counter_type") or 0)
            if name and count:
                counters[name] = counters.get(name, 0) + count
        if counters:
            entry["counters"] = counters
        for annotation in tracker.persistent.values():
            # An aura or equipment is the affector, what it is on the affected.
            if "AnnotationType_Attachment" in (annotation.get("type") or []) and annotation.get("affectorId") == instance_id:
                affected = annotation.get("affectedIds") or []
                if affected:
                    entry["attachedTo"] = affected[0]
    return entry


def build_state(tracker: ArenaTracker, cards: CardDb) -> dict | None:
    """The board from our point of view, or None before a game state is known."""
    me, opp = tracker.my_seat, tracker.opp_seat()
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

        def entries(objects, on_battlefield):
            return [card_entry(tracker, cards, o, on_battlefield, seat_index) for o in objects]

        ours = [o for o in battlefield if o.get("controllerSeatId") == seat]
        exiled = [o for o in exile if o.get("ownerSeatId") == seat and o.get("grpId")]
        players.append(
            {
                "life": info.get("lifeTotal", 0),
                "landsPlayed": 0,
                "librarySize": tracker.zone_size("ZoneType_Library", seat),
                "handSize": tracker.zone_size("ZoneType_Hand", seat),
                "hand": entries(hand, False),
                "battlefield": entries(ours, True),
                "graveyard": entries(tracker.zone_objects("ZoneType_Graveyard", seat), False),
                "exile": entries(exiled, False),
            }
        )
    offered_a_land = any(a.get("actionType") == "ActionType_Play" for a in tracker.my_actions())
    if turn_info.get("activePlayer") == me and not offered_a_land:
        # Arena offers a land play whenever one is still allowed and we hold a
        # land; not offering one means the drop is used (or there is no land
        # to play, where the value does not matter).
        players[0]["landsPlayed"] = 1
    active_seat = turn_info.get("activePlayer")
    active_idx = seat_index.get(active_seat, 0) if isinstance(active_seat, int) else 0
    return {
        "turn": turn_info.get("turnNumber", 1),
        "phase": forge_phase(turn_info) or "MAIN1",
        "activePlayer": active_idx,
        "players": players,
    }


def ability_cost(action: dict) -> dict:
    """An offered ability's mana as ``{"cmc": 2, "colors": "B"}``.

    Empty when it is not a plain amount (X, hybrid, Phyrexian), which leaves the
    cost out of the comparison.
    """
    total, colors = 0, set()
    for part in action.get("manaCost") or []:
        kinds = part.get("color") or []
        letter = MANA_LETTER.get(kinds[0]) if len(kinds) == 1 else None
        if letter is None:
            return {}
        count = part.get("count")
        total += count if isinstance(count, int) else 0
        if letter:
            colors.add(letter)
    return {"cmc": total, "colors": "".join(c for c in "WUBRG" if c in colors)}


def activatable(tracker: ArenaTracker) -> list[dict]:
    """The abilities of our permanents that Arena offers now and that one click starts.

    One click starts an ability when it is the only thing Arena offers on that
    permanent; with a second ability, or a mana ability beside it, the click
    opens a chooser instead, and those are left out. So are abilities of cards
    in hand or the graveyard, and of an aura or equipment already attached.
    """
    actions = [a for a in tracker.my_actions() if "instanceId" in a]
    offers: dict = {}
    for action in actions:
        offers[action.get("instanceId")] = offers.get(action.get("instanceId"), 0) + 1
    ours = {
        o.get("instanceId")
        for o in tracker.zone_objects("ZoneType_Battlefield")
        if tracker.my_seat is not None and o.get("controllerSeatId") == tracker.my_seat
    }
    attached = tracker.attached()
    out = []
    for action in actions:
        instance_id = action.get("instanceId")
        if (
            action.get("actionType") != "ActionType_Activate"
            or instance_id not in ours
            or offers.get(instance_id) != 1
            or instance_id in attached
        ):
            continue
        entry = {"id": instance_id, "abilityGrpId": action.get("abilityGrpId")}
        entry.update(ability_cost(action))
        out.append(entry)
    out.sort(key=lambda e: e["id"])
    return out


def _offered_ids(actions: list, action_type: str) -> list[int]:
    return sorted(
        {a.get("instanceId") for a in actions if a.get("actionType") == action_type and isinstance(a.get("instanceId"), int)}
    )


def main_request(tracker: ArenaTracker) -> dict:
    """What Arena offers in a main phase."""
    actions = tracker.my_actions()
    return {
        "castable": _offered_ids(actions, "ActionType_Cast"),
        "playable": _offered_ids(actions, "ActionType_Play"),
        "activatable": activatable(tracker),
    }


def attack_request(tracker: ArenaTracker) -> list[int]:
    """Arena's list of creatures that may attack, from the pending request."""
    if tracker.pending is None or tracker.pending_type != "GREMessageType_DeclareAttackersReq":
        return []
    request = tracker.pending.get("declareAttackersReq") or {}
    listed = request.get("qualifiedAttackers") or request.get("attackers") or []
    return [a.get("attackerInstanceId") for a in listed if isinstance(a.get("attackerInstanceId"), int)]


def block_request(tracker: ArenaTracker) -> dict:
    """Who attacks us and Arena's legal blocker -> attackers graph."""
    me = tracker.my_seat
    attackers = []
    for obj in tracker.zone_objects("ZoneType_Battlefield"):
        if (me is not None and obj.get("controllerSeatId") == me) or obj.get("attackState") not in ATTACKING:
            continue
        entry = {"id": obj.get("instanceId")}
        target = _attack_target(tracker, obj)
        if target is not None:
            entry["target"] = target
        attackers.append(entry)
    legal = {}
    if tracker.pending is not None and tracker.pending_type == "GREMessageType_DeclareBlockersReq":
        for blocker in (tracker.pending.get("declareBlockersReq") or {}).get("blockers") or []:
            blocker_id = blocker.get("blockerInstanceId")
            if isinstance(blocker_id, int):
                legal[str(blocker_id)] = list(blocker.get("attackerInstanceIds") or [])
    return {"attackers": attackers, "legalBlocks": legal}
