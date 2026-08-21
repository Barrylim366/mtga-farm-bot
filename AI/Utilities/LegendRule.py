"""The legend rule, applied BEFORE the cast instead of after it.

Casting a second copy of a legendary permanent we already control is a legal
action, so MTGA offers it and the AI happily picks it. What follows was
observed live: the client raises its own "Are You Sure?" confirm, which emits
no GRE message at all, the cast never lands, and after three sweeps
`cast()` gives up -- ~20s of rope per decision, and the card is then marked
"unreachable" although it sat in hand the whole time.
`_dismiss_are_you_sure_if_present` answering No is correct (never confirm a
question we did not understand), so the loop cannot resolve itself.

Confirming instead would not help: the copy resolves, CR 704.5j then makes us
put one of the two into the graveyard, and we are left with the same board and
one card fewer. There is no line where this cast is good, so the fix is to
never pick it.

No card database needed. The game objects carry both halves of the answer:
  * `superTypes` -- ["SuperType_Legendary"]. Present on battlefield objects and
    on our own hand objects (verified against a live Player.log: 64 of the
    legendary objects were in the battlefield zone, 4 in hand).
  * `name` -- a title id, not a display string. It is shared by every printing
    of the same card, so it matches an old Alchemy/set reprint of a legend
    against the copy in hand, which a grpId comparison would miss.
"""

from __future__ import annotations

LEGENDARY_SUPERTYPE = "SuperType_Legendary"


def is_legendary(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    return LEGENDARY_SUPERTYPE in (obj.get("superTypes") or [])


def title_id(obj):
    """The card's title id ("name" in GRE terms), or None.

    Deliberately not the display name: the GRE sends an integer id here, and
    two printings of one legend share it while their grpIds differ.
    """
    if not isinstance(obj, dict):
        return None
    return obj.get("name")


def find_object(game_objects, instance_id):
    if instance_id is None:
        return None
    for obj in game_objects or []:
        if isinstance(obj, dict) and obj.get("instanceId") == instance_id:
            return obj
    return None


def duplicate_legend_in_play(
    *,
    cast_instance_id,
    game_objects,
    my_seat,
    battlefield_zone_ids,
    live_instance_ids=None,
):
    """instanceId of a legendary permanent we already control that carries the
    same title id as the card about to be cast, or None if the cast is fine.

    Fails OPEN on purpose -- an unknown answer means "cast it". Two of those
    unknowns matter:

    * `battlefield_zone_ids` empty. The zone filter is REQUIRED and must never
      be bypassed, for the same reason RemovalLogic documents at length: this
      object list spans every zone, so without it a SECOND COPY IN HAND would
      match the first copy in hand and we would refuse a perfectly good cast.
    * Neither object reports superTypes. Then we cannot know it is legendary,
      and refusing a non-legendary duplicate (a second Llanowar Elves) would
      be far worse than the Are-You-Sure loop this guard exists to avoid.

    `live_instance_ids`, when given, is the authority on what is actually on
    the battlefield: gameObjects is merged across diffs and a dead creature
    keeps its battlefield zoneId indefinitely.
    """
    if not battlefield_zone_ids:
        return None
    cast_obj = find_object(game_objects, cast_instance_id)
    cast_title = title_id(cast_obj)
    if cast_title is None:
        return None

    for obj in game_objects or []:
        if not isinstance(obj, dict):
            continue
        instance_id = obj.get("instanceId")
        if instance_id == cast_instance_id:
            continue
        if obj.get("controllerSeatId") != my_seat:
            continue
        if obj.get("zoneId") not in battlefield_zone_ids:
            continue
        if live_instance_ids is not None and instance_id not in live_instance_ids:
            continue
        if title_id(obj) != cast_title:
            continue
        # Same card, ours, on the board. One report of Legendary is enough:
        # a shared title id means these are the same card, so whichever of the
        # two objects carries superTypes answers for both.
        if is_legendary(obj) or is_legendary(cast_obj):
            return instance_id
    return None
