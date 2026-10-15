"""Counterspell logic: decide when to cast a counter in response to an
opponent's spell on the stack.

A counterspell is described by a *counter profile*:
  {"restrict": "creature"}     -- counters only creature spells (Essence Scatter)
  {"restrict": "noncreature"}  -- counters only noncreature spells
  {"restrict": "any"}          -- counters any spell

Profiles are resolved manual-first (MANUAL_PROFILES), then auto-detected from
the card's oracle text.

Usage (AI side): when we hold priority and the opponent has a spell on the
stack that one of our in-hand counters can counter, cast that counter. The
target (the spell on the stack) is then chosen by the Controller's target
selection in response to MTGA's SelectTargetsReq.
"""

from __future__ import annotations

import re

import AI.Utilities.CardInfo as CardInfo

# Manual overrides: grpId -> {"restrict": "creature"|"noncreature"|"any"}.
MANUAL_PROFILES: dict[int, dict] = {
    # Essence Scatter (Foundations printing) -- "Counter target creature spell."
    # Auto-detected from oracle text; pinned here as an offline safety net.
    93866: {"restrict": "creature"},
}

_RE_COUNTER_CREATURE = re.compile(r"counter target creature spell", re.I)
_RE_COUNTER_NONCREATURE = re.compile(r"counter target noncreature spell", re.I)
# "counter target spell" but not "...creature spell" / "...ability".
_RE_COUNTER_ANY = re.compile(r"counter target spell", re.I)


_counter_profile_memo: dict[int, dict | None] = {}


def _detect_counter_profile(text: str) -> dict | None:
    t = text.replace("\n", " ")
    if _RE_COUNTER_CREATURE.search(t):
        return {"restrict": "creature"}
    if _RE_COUNTER_NONCREATURE.search(t):
        return {"restrict": "noncreature"}
    if _RE_COUNTER_ANY.search(t):
        return {"restrict": "any"}
    return None


def get_counter_profile(grp_id) -> dict | None:
    """Resolve a counter profile for a card, or None if it is not a counter.

    Called per-candidate-card on every AI decision, so the oracle-based result
    is memoized once real oracle text is available (never for a transient
    fetch failure -- see RemovalLogic's identical rationale).
    """
    if grp_id is None:
        return None
    try:
        grp_id = int(grp_id)
    except Exception:
        return None
    if grp_id in MANUAL_PROFILES:
        return dict(MANUAL_PROFILES[grp_id])
    if grp_id in _counter_profile_memo:
        profile = _counter_profile_memo[grp_id]
        return dict(profile) if profile is not None else None
    try:
        text = CardInfo.get_oracle_text(grp_id) or ""
    except Exception:
        text = ""
    if not text:
        return None
    profile = _detect_counter_profile(text)
    _counter_profile_memo[grp_id] = profile
    return dict(profile) if profile is not None else None


def stack_zone_ids(full_state: dict) -> set[int]:
    ids: set[int] = set()
    for zone in (full_state.get("zones", []) or []):
        if zone.get("type") == "ZoneType_Stack" and zone.get("zoneId") is not None:
            ids.add(zone.get("zoneId"))
    return ids


def _stack_order(full_state: dict, stack_ids: set[int]) -> list[int]:
    """Ordered instanceIds on the stack (bottom -> top)."""
    order: list[int] = []
    for zone in (full_state.get("zones", []) or []):
        if zone.get("zoneId") in stack_ids:
            order.extend(zone.get("objectInstanceIds", []) or [])
    return order


def opponent_spells_on_stack(
    game_objects: list[dict], my_seat: int, stack_ids: set[int]
) -> list[dict]:
    out = []
    for obj in game_objects or []:
        if not isinstance(obj, dict):
            continue
        if obj.get("zoneId") not in stack_ids:
            continue
        if obj.get("controllerSeatId") == my_seat:
            continue
        if obj.get("instanceId") is None:
            continue
        # Triggered/activated abilities also live on the stack as objects with
        # type "GameObjectType_Ability" (see Controller.py's stack-selection
        # handling). "Counter target spell" cannot legally target those, so
        # excluding them here keeps the AI from casting a counter at something
        # MTGA will never offer as a legal target.
        if str(obj.get("type") or "") == "GameObjectType_Ability":
            continue
        out.append(obj)
    return out


def can_counter(profile: dict, stack_obj: dict) -> bool:
    types = stack_obj.get("cardTypes") or []
    is_creature = "CardType_Creature" in types
    restrict = profile.get("restrict")
    if restrict == "creature":
        return is_creature
    if restrict == "noncreature":
        return not is_creature
    return True


def score_spell_threat(stack_obj: dict) -> int:
    """Calculate threat score for an opponent spell on the stack.
    Higher score indicates a higher priority target to counter."""
    score = 10  # base score
    types = stack_obj.get("cardTypes") or []

    if "CardType_Planeswalker" in types:
        score += 50
    elif "CardType_Creature" in types:
        score += 30
        p = int((stack_obj.get("power") or {}).get("value", 0)) if isinstance(stack_obj.get("power"), dict) else int(stack_obj.get("power") or 0)
        t = int((stack_obj.get("toughness") or {}).get("value", 0)) if isinstance(stack_obj.get("toughness"), dict) else int(stack_obj.get("toughness") or 0)
        score += max(0, p * 5) + max(0, t * 2)
    elif "CardType_Enchantment" in types or "CardType_Artifact" in types:
        score += 25
    elif "CardType_Sorcery" in types or "CardType_Instant" in types:
        score += 20

    # CMC and rarity weighting
    grp_id = stack_obj.get("grpId")
    if grp_id:
        try:
            info = CardInfo.get_card_info_local(grp_id) or {}
            cmc = CardInfo.calculate_cmc(info.get("manaCost", "")) if info else 0
            score += cmc * 10
            rarity = str(info.get("rarity") or "").lower()
            if rarity in ("mythic", "rare"):
                score += 15
        except Exception:
            pass

    return score


def find_counterable_spell(
    profile: dict,
    game_objects: list[dict],
    my_seat: int,
    stack_ids: set[int],
    full_state: dict | None = None,
) -> int | None:
    """Return the instanceId of the highest-threat counterable opponent spell
    on the stack, breaking ties by topmost (resolves first)."""
    spells = {
        obj["instanceId"]: obj
        for obj in opponent_spells_on_stack(game_objects, my_seat, stack_ids)
    }
    if not spells:
        return None
    order = _stack_order(full_state or {}, stack_ids)
    ordered_ids = [iid for iid in order if iid in spells] or list(spells)

    candidates = [
        iid for iid in reversed(ordered_ids)
        if can_counter(profile, spells[iid])
    ]
    if not candidates:
        return None

    # Pick candidate with highest threat score; stable key maintains order on tie
    return int(max(candidates, key=lambda iid: score_spell_threat(spells[iid])))
