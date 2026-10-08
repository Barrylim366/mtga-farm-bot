"""Builds small GRE messages in the shape Arena writes to Player.log, for tests."""
from __future__ import annotations

import json

ME, OPP = 1, 2

# real Arena ids from the Foundations starter decks, which the bot's bundled card data covers offline
FOREST = 66531
LLANOWAR_ELVES = 93940          # {G}
NESSIAN_HORNBEETLE = 93942      # {1}{G}
APOTHECARY_STOMPER = 93812      # {4}{G}{G}
INSPIRATION_FROM_BEYOND = 93756  # a card the bot refuses to cast (its prompt cannot be clicked)

NAMES = {
    FOREST: "Forest",
    LLANOWAR_ELVES: "Llanowar Elves",
    NESSIAN_HORNBEETLE: "Nessian Hornbeetle",
    APOTHECARY_STOMPER: "Apothecary Stomper",
    INSPIRATION_FROM_BEYOND: "Inspiration from Beyond",
    1: "Grizzly Bears",
    2: "Hill Giant",
}

GREEN = [{"color": ["ManaColor_Green"], "count": 1}]


def card(instance_id: int, grp_id: int, zone_id: int, seat: int = ME, **fields) -> dict:
    obj = {
        "instanceId": instance_id,
        "grpId": grp_id,
        "type": "GameObjectType_Card",
        "zoneId": zone_id,
        "visibility": "Visibility_Public",
        "ownerSeatId": seat,
        "controllerSeatId": seat,
    }
    obj.update(fields)
    return obj


def creature(instance_id: int, grp_id: int, power: int, toughness: int, seat: int = ME, **fields) -> dict:
    return card(
        instance_id, grp_id, 28, seat,
        cardTypes=["CardType_Creature"], power={"value": power}, toughness={"value": toughness}, **fields,
    )


def land(instance_id: int, seat: int = ME, **fields) -> dict:
    return card(
        instance_id, FOREST, 28, seat,
        cardTypes=["CardType_Land"], subtypes=["SubType_Forest"], superTypes=["SuperType_Basic"], **fields,
    )


def state_message(
    *,
    state_id: int = 10,
    full: bool = True,
    phase: str = "Phase_Main1",
    step: str | None = None,
    active: int = ME,
    turn: int = 5,
    hand: list[dict] | None = None,
    battlefield: list[dict] | None = None,
    stack: list[int] | None = None,
    actions: list[dict] | None = None,
    life: tuple[int, int] = (20, 20),
    extra: dict | None = None,
) -> dict:
    hand = hand or []
    battlefield = battlefield or []
    turn_info = {
        "phase": phase, "turnNumber": turn, "activePlayer": active,
        "priorityPlayer": ME, "decisionPlayer": ME,
    }
    if step:
        turn_info["step"] = step
    gsm = {
        "type": "GameStateType_Full" if full else "GameStateType_Diff",
        "gameStateId": state_id,
        "gameInfo": {"matchID": "test-match"},
        "turnInfo": turn_info,
        "players": [
            {"systemSeatNumber": ME, "lifeTotal": life[0], "startingLifeTotal": 20, "controllerSeatId": ME},
            {"systemSeatNumber": OPP, "lifeTotal": life[1], "startingLifeTotal": 20, "controllerSeatId": OPP},
        ],
        "zones": [
            {"zoneId": 27, "type": "ZoneType_Stack", "objectInstanceIds": stack or []},
            {"zoneId": 28, "type": "ZoneType_Battlefield", "objectInstanceIds": [o["instanceId"] for o in battlefield]},
            {"zoneId": 31, "type": "ZoneType_Hand", "ownerSeatId": ME, "objectInstanceIds": [o["instanceId"] for o in hand]},
            {"zoneId": 32, "type": "ZoneType_Library", "ownerSeatId": ME, "objectInstanceIds": list(range(1000, 1030))},
            {"zoneId": 33, "type": "ZoneType_Graveyard", "ownerSeatId": ME, "objectInstanceIds": []},
            {"zoneId": 35, "type": "ZoneType_Hand", "ownerSeatId": OPP, "objectInstanceIds": [900, 901, 902]},
            {"zoneId": 36, "type": "ZoneType_Library", "ownerSeatId": OPP, "objectInstanceIds": list(range(2000, 2030))},
            {"zoneId": 37, "type": "ZoneType_Graveyard", "ownerSeatId": OPP, "objectInstanceIds": []},
        ],
        "gameObjects": hand + battlefield,
        "annotations": [],
        "timers": [],
        "actions": [{"seatId": ME, "action": a} for a in actions or []],
    }
    if not full:
        gsm["prevGameStateId"] = state_id - 1
    gsm.update(extra or {})
    return {"type": "GREMessageType_GameStateMessage", "systemSeatIds": [ME], "gameStateMessage": gsm}


def connect() -> dict:
    return {"type": "GREMessageType_ConnectResp", "systemSeatIds": [ME]}


def log_line(*messages: dict) -> str:
    """One Player.log line carrying these messages, as the client writes it."""
    return json.dumps({"greToClientEvent": {"greToClientMessages": list(messages)}}) + "\n"


def cast(instance_id: int, mana: int = 1) -> dict:
    return {"actionType": "ActionType_Cast", "instanceId": instance_id, "manaCost": GREEN * mana}


def play(instance_id: int) -> dict:
    return {"actionType": "ActionType_Play", "instanceId": instance_id}


def tap_for_green(instance_id: int) -> dict:
    return {"actionType": "ActionType_Activate_Mana", "instanceId": instance_id, "abilityGrpId": 1005}
