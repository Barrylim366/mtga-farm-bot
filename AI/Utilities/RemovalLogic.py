"""Targeted-removal logic shared by the AI (cast decision) and the Controller
(target selection).

A spell/ability that can target an enemy creature (or, for burn, the face) is
described by a *removal profile*. Profiles are resolved manual-first:

  1. `MANUAL_PROFILES[grpId]`  -- hand-authored override, always wins.
  2. auto-detection from the card's oracle text  -- covers the common patterns.

`choose_removal_target(profile, board, my_seat, opponent_life)` returns:
  *  -1 (`FACE_TARGET`)     -> aim at the opponent's face (lethal burn),
  *  a positive instanceId  -> aim at that enemy creature,
  *  None                   -> no good target; the caller must NOT cast it.

Policy (agreed in design):
  * creature target = the killable enemy creature with the highest *effective*
    toughness (current toughness minus marked damage), power as a tiebreak.
  * "killable" is evaluated with safeguards: indestructible survives `destroy`
    and `damage` (but not `-X/-X` reducing toughness to 0, nor `exile`);
    a deathtouch damage source kills with any amount.
  * dual-mode burn (can hit face or creature): if it is lethal to the face,
    go face; otherwise kill the best creature it can; otherwise don't cast.
"""

from __future__ import annotations

import re

import AI.Utilities.CardInfo as CardInfo

# Sentinel target: the opponent's face/avatar. Matches the Controller's existing
# convention where select_target(-1) clicks the opponent avatar.
FACE_TARGET = -1


# --- Layer 1a: manual overrides (grpId -> profile). Always take precedence. ---
# kinds:
#   {"kind": "damage", "amount": N, "can_hit_creature": bool, "can_hit_face": bool}
#   {"kind": "minus_toughness", "amount": N}   target creature gets -x/-N
#   {"kind": "destroy"}                          destroy target creature
#   {"kind": "exile"}                            exile target creature
# optional flag: "source_deathtouch": True       any damage is lethal
MANUAL_PROFILES: dict[int, dict] = {
    # Fill grpIds as we review cards one by one, e.g.:
    # 12345: {"kind": "minus_toughness", "amount": 2},   # Moment of Craving
    #
    # Bake into a Pie -- "Destroy target creature. Create a Food token."
    # Auto-detection now resolves this from oracle text (the real bug was the
    # missing Accept header on Scryfall requests, see CardInfo._SCRYFALL_HEADERS).
    # Kept as an offline safety net: the local card DB has no oracle text, so with
    # no network only these manual profiles work.
    93882: {"kind": "destroy"},
}


_removal_profile_memo: dict[int, dict | None] = {}


def _removal_profile_lookup(grp_id: int) -> dict | None:
    if grp_id in MANUAL_PROFILES:
        return MANUAL_PROFILES[grp_id]
    if grp_id in _removal_profile_memo:
        return _removal_profile_memo[grp_id]
    try:
        text = CardInfo.get_oracle_text(grp_id) or ""
    except Exception:
        text = ""
    profile = detect_profile_from_oracle(grp_id) if text else None
    if text:
        # Only memoize once we actually got oracle text. An empty result can be a
        # transient Scryfall failure (see CardInfo's own negative-cache note), and
        # caching that here would make the miss permanent even after the network
        # recovers -- defeating the whole point of that cache being time-limited.
        _removal_profile_memo[grp_id] = profile
    return profile


def get_removal_profile(grp_id: int | None) -> dict | None:
    """Resolve a removal profile for a card: manual table first, else oracle.

    A card's profile never changes within a process once real oracle text is
    available, and this is called per-candidate-card on every AI decision and
    every enemy-creature evaluation, so the oracle-based lookup is memoized; a
    fresh dict is returned each call so callers can't mutate the cached entry.
    """
    if grp_id is None:
        return None
    try:
        grp_id = int(grp_id)
    except Exception:
        return None
    profile = _removal_profile_lookup(grp_id)
    return dict(profile) if profile is not None else None


# --- Self-target pump/protect tricks ---
# These must be cast on one of OUR creatures, never an enemy's (that would buff
# the opponent) and never the avatar. "target creature" spells also list enemy
# creatures as legal, so without this the generic path would hand the buff to the
# opponent or stall on the illegal avatar. Fake Your Own Death across all its
# printings, plus an oracle heuristic for other "+N/+M" pump tricks.
SELF_BUFF_GRPIDS: set[int] = {
    80230, 90433, 92991, 93887,
    94150, 94489,  # Unflinching Courage (both printings) -- +2/+2 trample/lifelink
                   # Aura. Pin it so it is always cast on OUR strongest creature
                   # (never an enemy's, never with no creature), regardless of
                   # whether its oracle text is available offline.
    93793, 102792,  # Bulk Up (both printings) -- "Double target creature's power".
                    # Phrased with no "+N/M", so the _RE_SELF_BUFF pattern missed it
                    # and the generic path handed it to an ENEMY creature: MTGA then
                    # asked "Do you want to target Arahbo, the First Fang with Bulk
                    # Up?" -- i.e. we were about to double the OPPONENT's power.
}

_RE_SELF_BUFF = re.compile(r"target creature gets \+\d+/", re.I)
# Beneficial Auras (e.g. Angelic Destiny, Ethereal Armor) read "Enchant creature"
# + "Enchanted creature gets +N/+M". They must land on OUR creature, not an
# enemy's -- otherwise MTGA raises an "Are You Sure?" confirm that the bot cannot
# answer and the match stalls. Detrimental auras (Pacifism "-X/-X", "can't attack")
# are excluded by the removal-profile guard in _is_self_buff_lookup below.
_RE_BUFF_AURA = re.compile(r"enchanted creature gets \+\d+/\+?\d+", re.I)
# Buffs phrased without a "+N/M" at all, which the two patterns above miss --
# e.g. Bulk Up's "Double target creature's power until end of turn". Handing one
# of these to an enemy creature is strictly helping the opponent.
_RE_POWER_BUFF = re.compile(r"double target creature's power", re.I)


_self_buff_memo: dict[int, bool] = {}


def _is_self_buff_lookup(grp_id: int) -> bool:
    if grp_id in SELF_BUFF_GRPIDS:
        return True
    if grp_id in _self_buff_memo:
        return _self_buff_memo[grp_id]
    try:
        text = str(CardInfo.get_oracle_text(grp_id) or "")
    except Exception:
        text = ""
    # A card that is removal is not a self-buff, even if its text mentions a buff.
    if text and get_removal_profile(grp_id):
        result = False
    else:
        result = bool(
            _RE_SELF_BUFF.search(text)
            or _RE_BUFF_AURA.search(text)
            or _RE_POWER_BUFF.search(text)
        )
    if text:
        # Same rationale as _removal_profile_lookup: only memoize once real
        # oracle text was available, so a transient fetch failure doesn't
        # permanently misclassify the card.
        _self_buff_memo[grp_id] = result
    return result


def is_self_buff(grp_id) -> bool:
    """True if the card is a pump/protect trick to cast on our own creature."""
    if grp_id is None:
        return False
    try:
        grp_id = int(grp_id)
    except Exception:
        return False
    return _is_self_buff_lookup(grp_id)


# --- Layer 1b: auto-detection from oracle text ---
_CREATURE_TARGET = r"target (?:attacking |blocking |tapped |untapped )?creature"

_RE_DESTROY = re.compile(r"destroy " + _CREATURE_TARGET, re.I)
# Conditional destroy that only hits big creatures, e.g. Valorous Stance:
# "Destroy target creature with toughness 4 or greater." The min stat is a
# targeting restriction -- the bot must only aim it at creatures that satisfy it.
_RE_DESTROY_MIN = re.compile(
    r"destroy target creature with (toughness|power) (\d+) or greater", re.I
)
_RE_EXILE = re.compile(r"exile " + _CREATURE_TARGET, re.I)
# "target creature ... gets -X/-Y"
_RE_MINUS = re.compile(_CREATURE_TARGET + r"[^.]*?gets\s*[+-]?\d+/(-\d+)", re.I)
# "deals N damage to <target phrase>"
_RE_DAMAGE = re.compile(r"deals?\s+(\d+)\s+damage\s+to\s+([^.;]+)", re.I)


def _analyze_damage_targets(phrase: str) -> tuple[bool, bool]:
    """(can_hit_creature, can_hit_face) for a damage target phrase.

    "any target" covers creature, player, and planeswalker. Otherwise we look
    for the words themselves; "planeswalker" is not the face.
    """
    p = phrase.lower()
    if "any target" in p:
        return True, True
    hits_creature = "creature" in p
    hits_face = ("player" in p) or ("opponent" in p)
    return hits_creature, hits_face


def detect_profile_from_oracle(grp_id: int) -> dict | None:
    text = ""
    try:
        text = CardInfo.get_oracle_text(grp_id) or ""
    except Exception:
        text = ""
    if not text:
        return None
    t = text.replace("\n", " ")

    # Prefer the unconditional kill if a card does several things.
    if _RE_EXILE.search(t):
        return {"kind": "exile"}
    # Conditional destroy (e.g. Valorous Stance) must be checked before the plain
    # destroy so its targeting restriction is captured.
    m_min = _RE_DESTROY_MIN.search(t)
    if m_min:
        stat = m_min.group(1).lower()
        amount = int(m_min.group(2))
        key = "min_toughness" if stat == "toughness" else "min_power"
        return {"kind": "destroy", key: amount}
    if _RE_DESTROY.search(t):
        return {"kind": "destroy"}

    m = _RE_MINUS.search(t)
    if m:
        amount = abs(int(m.group(1)))
        if amount > 0:
            return {"kind": "minus_toughness", "amount": amount}

    m = _RE_DAMAGE.search(t)
    if m:
        hits_creature, hits_face = _analyze_damage_targets(m.group(2))
        if hits_creature or hits_face:
            profile = {
                "kind": "damage",
                "amount": int(m.group(1)),
                "can_hit_creature": hits_creature,
                "can_hit_face": hits_face,
            }
            if _source_has_deathtouch(grp_id, text):
                profile["source_deathtouch"] = True
            return profile

    return None


def _source_has_deathtouch(grp_id: int, oracle_text: str) -> bool:
    try:
        info = CardInfo.get_card_info(grp_id) or {}
        kws = [str(k).lower() for k in (info.get("keywords") or [])]
        if "deathtouch" in kws:
            return True
    except Exception:
        pass
    return "deathtouch" in (oracle_text or "").lower()


# --- Layer 2: board evaluation ---
def _stat(value) -> int:
    if isinstance(value, dict):
        try:
            return int(value.get("value", 0) or 0)
        except Exception:
            return 0
    try:
        return int(value or 0)
    except Exception:
        return 0


def effective_toughness(creature: dict) -> int:
    """Current toughness minus marked damage."""
    return _stat(creature.get("toughness")) - _stat(creature.get("damage"))


# Creatures whose death is beneficial or who recur from the graveyard -- ideal
# to feed to a sacrifice cost (e.g. Eaten Alive). Manual pins plus an oracle
# heuristic so it generalizes.
_SAC_FODDER_GRPIDS = {
    93777,                # Infestation Sage (dies -> token)
    93776,                # Infernal Vessel (dies -> returns upgraded)
    67912, 93032, 93895,  # Reassembling Skeleton (graveyard recursion)
}


def is_sacrifice_fodder(grp_id) -> bool:
    """True if sacrificing this creature is (near) free -- a beneficial death
    trigger or graveyard recursion."""
    if grp_id is None:
        return False
    try:
        grp_id = int(grp_id)
    except Exception:
        return False
    if grp_id in _SAC_FODDER_GRPIDS:
        return True
    try:
        info = CardInfo.get_card_info(grp_id) or {}
    except Exception:
        return False
    text = str(info.get("oracleText") or "").lower()
    return "this creature dies" in text or "from your graveyard" in text


# High-value creatures we must NOT feed to a sacrifice cost/effect (e.g. Vampire
# Gourmand's "sacrifice another creature" ability) unless there is literally no
# other legal choice. Perforating Artist is a repeatable damage engine (its Raid
# ability drains the opponent every combat), so sacrificing it for a few counters
# is a bad trade. Keyed by grpId because these cards have no oracle text in the
# offline DB.
_SAC_PROTECTED_GRPIDS = {
    93837,  # Perforating Artist
}


def is_protected_from_sacrifice(grp_id) -> bool:
    """True if this creature should be kept off the sacrifice-cost chopping block
    whenever any other creature is available to sacrifice instead."""
    if grp_id is None:
        return False
    try:
        grp_id = int(grp_id)
    except Exception:
        return False
    return grp_id in _SAC_PROTECTED_GRPIDS


def _creature_keywords(creature: dict) -> set[str]:
    """Printed keywords for the creature's card (best-effort; keywords granted
    by other effects are not visible here)."""
    grp_id = creature.get("grpId")
    if grp_id is None:
        return set()
    try:
        info = CardInfo.get_card_info(grp_id) or {}
    except Exception:
        return set()
    kws = {str(k).lower() for k in (info.get("keywords") or [])}
    oracle = str(info.get("oracleText") or "").lower()
    for kw in ("indestructible", "protection", "hexproof", "shroud"):
        if kw in oracle:
            kws.add(kw)
    return kws


# --- Ward ---------------------------------------------------------------------
# Ward is invisible in the GRE stream: MTGA raises a *client-side* "Are You Sure?
# This target has Ward" confirm that emits no message at all. Answering No cancels
# the spell and hands the same board back to the decision loop, which re-picks the
# same target -- observed 2026-07-30 17:31-17:37, a Mortify aimed at Tolarian
# Terror (Ward {2}) looped for six minutes across four turns.
#
# So the ward cost has to be read from the card and priced BEFORE we target:
# affordable -> target it and pay, unaffordable -> pick something else.
#
# "Ward {2}" / "Ward {1}{U}" are payable with mana. "Ward--Pay 3 life" and
# friends are not something this bot can drive, so they are priced as unpayable.
# The reminder text ("...counter it unless that player pays {2}.") also contains a
# mana symbol, which is why the pattern anchors on the word `ward` itself.
_WARD_MANA_RE = re.compile(r"\bward\b\s*((?:\{[^}]+\}\s*)+)", re.IGNORECASE)
_WARD_ANY_RE = re.compile(r"\bward\b", re.IGNORECASE)


def ward_cost(grp_id) -> dict | None:
    """Ward on this card, or None if it has none.

    Returns `{"mana": int | None, "text": str}` where `mana` is the mana needed
    to pay the ward, or None for a ward whose cost we cannot pay through the UI
    (pay life, discard a card, ...). `{"mana": None}` therefore means "warded and
    we must not target it", not "free".
    """
    if grp_id is None:
        return None
    try:
        info = CardInfo.get_card_info(grp_id) or {}
    except Exception:
        return None
    keywords = {str(k).lower() for k in (info.get("keywords") or [])}
    oracle = str(info.get("oracleText") or "")
    if "ward" not in keywords and not _WARD_ANY_RE.search(oracle):
        return None
    match = _WARD_MANA_RE.search(oracle)
    if not match:
        # Keyword says ward but we cannot read a cost (no oracle text offline, or
        # a non-mana ward). Unknown cost is treated as unpayable on purpose: a
        # countered removal spell is strictly worse than not casting it.
        return {"mana": None, "text": oracle.strip()}
    try:
        mana = CardInfo.calculate_cmc(match.group(1).replace(" ", ""))
    except Exception:
        return {"mana": None, "text": oracle.strip()}
    return {"mana": int(mana), "text": match.group(0).strip()}


def creature_ward_cost(creature: dict) -> dict | None:
    """`ward_cost` for a board object."""
    if not isinstance(creature, dict):
        return None
    return ward_cost(creature.get("grpId"))


def ward_is_affordable(creature: dict, ward_budget: int | None) -> bool:
    """Can we pay this creature's ward with `ward_budget` mana left over?

    `ward_budget is None` means the caller does not know how much mana will be
    free after the spell is paid for; an unknown budget never *excludes* a
    target (see `choose_removal_target`), it only deprioritizes it.
    """
    cost = creature_ward_cost(creature)
    if cost is None:
        return True
    if ward_budget is None:
        return True
    if cost.get("mana") is None:
        return False
    return int(cost["mana"]) <= int(ward_budget)


# Targets we pointed a spell at and then backed out of, keyed by
# (spell grpId, target instanceId). Without this the decision loop is memoryless:
# it re-derives the same "best" target from the same board every time, which is
# exactly what turned one declined ward confirm into an endless cast loop.
# Module-level because the AI (cast decision) and the Controller (target
# selection) are separate objects that already share this module. Reset per match.
_declined_targets: set[tuple[int, int]] = set()


def note_declined_target(source_grp_id, target_id) -> None:
    """Remember that targeting `target_id` with this spell was abandoned."""
    if source_grp_id is None or target_id is None:
        return
    try:
        _declined_targets.add((int(source_grp_id), int(target_id)))
    except (TypeError, ValueError):
        pass


def is_declined_target(source_grp_id, target_id) -> bool:
    if source_grp_id is None or target_id is None:
        return False
    try:
        return (int(source_grp_id), int(target_id)) in _declined_targets
    except (TypeError, ValueError):
        return False


def reset_declined_targets() -> None:
    """Clear the decline list. Call at match end -- instanceIds are per-game."""
    _declined_targets.clear()


def can_kill(profile: dict, creature: dict) -> bool:
    kind = profile.get("kind")
    kws = _creature_keywords(creature)

    if kind == "exile":
        # Exile ignores indestructible; MTGA would not offer an illegal target.
        return True

    if kind == "destroy":
        if "indestructible" in kws:
            return False
        # Conditional destroy (e.g. Valorous Stance: toughness 4+) is only a legal
        # target when the creature meets the minimum stat, using current stats.
        # A missing/not-yet-populated stat (e.g. a creature instance whose first
        # GRE diff hasn't reported toughness/power yet) is treated as unknown,
        # not 0 -- otherwise a brand-new creature would wrongly look unkillable.
        min_t = int(profile.get("min_toughness", 0) or 0)
        if min_t > 0 and creature.get("toughness") is not None and _stat(creature.get("toughness")) < min_t:
            return False
        min_p = int(profile.get("min_power", 0) or 0)
        if min_p > 0 and creature.get("power") is not None and _stat(creature.get("power")) < min_p:
            return False
        return True

    if kind == "minus_toughness":
        # Toughness <= 0 is a state-based kill; indestructible does NOT save it.
        return effective_toughness(creature) <= int(profile.get("amount", 0))

    if kind == "damage":
        if "indestructible" in kws:
            return False
        if profile.get("source_deathtouch"):
            return True
        return effective_toughness(creature) <= int(profile.get("amount", 0))

    return False


def battlefield_zone_ids(full_state: dict) -> set[int]:
    """zoneIds of every ZoneType_Battlefield zone (both players' battlefields).

    Was copy-pasted inline at four call sites (Controller.py's pay-costs,
    removal-target and chooser-target handlers, and DummyAI.generate_move),
    each wrapped in its own try/except-reset-to-empty-set; centralized here
    next to CounterLogic.stack_zone_ids, which is the same pattern for the
    stack zone.
    """
    ids: set[int] = set()
    try:
        for zone in (full_state or {}).get("zones", []) or []:
            if zone.get("type") == "ZoneType_Battlefield" and zone.get("zoneId") is not None:
                ids.add(zone.get("zoneId"))
    except Exception:
        return set()
    return ids


def battlefield_instance_ids(
    full_state: dict,
    known_zone_ids: set[int] | None = None,
) -> set[int] | None:
    """instanceIds that are ON a battlefield right now, read from the zone's own
    objectInstanceIds list. None means "could not read any battlefield zone",
    which callers must treat as "no data" rather than "the board is empty".

    Why this exists: GameState.update MERGES gameObjects across diffs and only
    drops one on an explicit diffDeletedInstanceIds. A creature that left the
    battlefield without such a diff therefore keeps its stale `zoneId` forever,
    so filtering on obj["zoneId"] in battlefield_zone_ids() happily returns
    creatures that died many turns (or matches) ago. Observed 2026-07-20 09:26:
    the AI cast Mortify at instanceId 378, an opponent creature last seen alive
    at 09:09 -- MTGA offered only OUR two creatures as legal targets, the bot
    hunted a card that was not on screen, and burned the rope.

    Zone entries do not have that problem: `zones` merges by zoneId, and a zone's
    objectInstanceIds is a list, which __update_dict replaces wholesale on every
    diff that mentions the zone. So the zone list is authoritative for membership.
    """
    ids: set[int] = set()
    seen_zone = False
    try:
        for zone in (full_state or {}).get("zones", []) or []:
            if not isinstance(zone, dict):
                continue
            is_bf = zone.get("type") == "ZoneType_Battlefield" or (
                known_zone_ids is not None and zone.get("zoneId") in known_zone_ids
            )
            if not is_bf:
                continue
            seen_zone = True
            for inst_id in zone.get("objectInstanceIds", []) or []:
                if isinstance(inst_id, int):
                    ids.add(inst_id)
    except Exception:
        return None
    return ids if seen_zone else None


def opponent_creatures(
    game_objects: list[dict],
    my_seat: int,
    battlefield_zone_ids: set[int] | None = None,
    live_instance_ids: set[int] | None = None,
) -> list[dict]:
    out = []
    for obj in game_objects or []:
        if not isinstance(obj, dict):
            continue
        if "CardType_Creature" not in (obj.get("cardTypes") or []):
            continue
        if obj.get("controllerSeatId") == my_seat:
            continue
        if battlefield_zone_ids is not None and obj.get("zoneId") not in battlefield_zone_ids:
            continue
        # The zoneId above can be stale (see battlefield_instance_ids); the zone's
        # own membership list cannot. When we have it, it wins.
        if live_instance_ids is not None and obj.get("instanceId") not in live_instance_ids:
            continue
        if obj.get("instanceId") is None:
            continue
        out.append(obj)
    return out


def opponent_life_from_players(players: list[dict], my_seat: int) -> int | None:
    """Lowest life total among opponents (seats other than my_seat)."""
    best = None
    for pl in players or []:
        if not isinstance(pl, dict):
            continue
        seat = pl.get("systemSeatNumber")
        if seat is None or seat == my_seat:
            continue
        life = pl.get("lifeTotal", pl.get("life"))
        if life is None:
            continue
        try:
            life = int(life)
        except Exception:
            continue
        best = life if best is None else min(best, life)
    return best


def choose_removal_target(
    profile: dict,
    game_objects: list[dict],
    my_seat: int,
    opponent_life: int | None = None,
    battlefield_zone_ids: set[int] | None = None,
    live_instance_ids: set[int] | None = None,
    ward_budget: int | None = None,
    source_grp_id: int | None = None,
) -> int | None:
    """FACE_TARGET (-1) for lethal burn, a creature instanceId, or None.

    `ward_budget` is the mana that will still be untapped *after* this spell is
    paid for. With a number, a warded creature we cannot pay for is dropped from
    the candidates entirely -- targeting it would only get the spell countered.
    With None (budget unknown) warded creatures are merely ranked last, and the
    Controller's ward confirm makes the final call with a live mana reading.

    `source_grp_id` enables the decline list: a target we already backed out of
    with this spell is skipped, so a refused ward confirm cannot loop.
    """
    if not profile:
        return None
    kind = profile.get("kind")

    # Dual-mode burn: prefer lethal to the face.
    if kind == "damage" and profile.get("can_hit_face") and opponent_life is not None:
        try:
            if int(opponent_life) <= int(profile.get("amount", 0)):
                return FACE_TARGET
        except Exception:
            pass

    # Burn that cannot hit creatures has no creature fallback.
    if kind == "damage" and not profile.get("can_hit_creature", True):
        return None

    enemies = opponent_creatures(
        game_objects, my_seat, battlefield_zone_ids, live_instance_ids
    )
    killable = [c for c in enemies if can_kill(profile, c)]
    if source_grp_id is not None:
        killable = [
            c for c in killable
            if not is_declined_target(source_grp_id, c.get("instanceId"))
        ]
    if ward_budget is not None:
        killable = [c for c in killable if ward_is_affordable(c, ward_budget)]
    if not killable:
        return None
    # Biggest threat still wins -- paying a ward to kill the 5/5 beats killing a
    # 2/2 for free. Being unwarded is only a tiebreak between equal bodies, and
    # it is the tiebreak that matters when the budget is unknown, because then
    # the warded one may still cost us the whole spell.
    best = max(
        killable,
        key=lambda c: (
            effective_toughness(c),
            _stat(c.get("power")),
            creature_ward_cost(c) is None,
        ),
    )
    return int(best["instanceId"])
