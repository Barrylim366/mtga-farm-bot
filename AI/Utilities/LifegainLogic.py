"""Lifegain-payoff detection for the Orzhov starter deck.

The deck's engine is "gain life -> a creature grows". The payoff creatures all
share one oracle line:

    Whenever you gain life, put a +1/+1 counter on this creature.

(Ajani's Pridemate, Twinblade Paladin, Fiendish Panda.) Their value is almost
entirely a function of how many lifegain triggers they are on the board FOR, so
a turn spent not having one out is a turn of counters lost forever. That is why
the AI casts them ahead of other creatures of the same plan.

Detection is by oracle text rather than a grpId table on purpose: the deck data
holds four printings of Ajani's Pridemate and two of Twinblade Paladin (93447,
93848, 69455, 67690 / 70069, 93652), and a hand-maintained list silently misses
the next reprint. GRPIDS below is only an offline fallback for when Scryfall is
unreachable and the local DB carries no oracle text.
"""

from __future__ import annotations

import re

import AI.Utilities.CardInfo as CardInfo

# Offline safety net only -- detection normally runs off oracle text. Printings
# present in data/starter_deck_cards.json as of 2026-07-20.
PAYOFF_GRPIDS: set[int] = {
    67690, 69455, 93447, 93848,  # Ajani's Pridemate
    70069, 93652,                # Twinblade Paladin
    93833,                       # Fiendish Panda
}

# "Whenever you gain life, put a +1/+1 counter on this creature." Deliberately
# not anchored on the counter clause: cards that do something else on lifegain
# still key off the same engine and still want to be deployed early.
_RE_LIFEGAIN_PAYOFF = re.compile(r"whenever you gain life", re.I)

_payoff_memo: dict[int, bool] = {}


def _payoff_lookup(grp_id: int) -> bool:
    if grp_id in PAYOFF_GRPIDS:
        return True
    if grp_id in _payoff_memo:
        return _payoff_memo[grp_id]
    try:
        text = str(CardInfo.get_oracle_text(grp_id) or "")
    except Exception:
        text = ""
    result = bool(_RE_LIFEGAIN_PAYOFF.search(text))
    if text:
        # Same rationale as RemovalLogic._removal_profile_lookup: only memoize
        # once real oracle text was available, so a transient Scryfall failure
        # does not permanently misclassify the card.
        _payoff_memo[grp_id] = result
    return result


def is_lifegain_payoff(grp_id) -> bool:
    """True if the card grows (or otherwise triggers) whenever we gain life."""
    if grp_id is None:
        return False
    try:
        grp_id = int(grp_id)
    except Exception:
        return False
    return _payoff_lookup(grp_id)


# --- Enablers: cards that FEED the engine (produce lifegain) ---
# Lifelink is the keyword form; the rest read "you gain N life" somewhere in the
# text (Hinterland Sanctifier, Inspiring Overseer, Vengeful Bloodwitch).
_RE_GAIN_LIFE = re.compile(r"you gain \d+ life", re.I)
_RE_LIFELINK = re.compile(r"lifelink", re.I)
# Evasion makes an enabler much better: it keeps connecting, so it keeps feeding
# the engine instead of being walled by a bigger body.
_EVASION_KEYWORDS = {"flying", "menace", "trample", "shadow", "fear", "intimidate"}

_enabler_memo: dict[int, bool] = {}


def _card(grp_id: int) -> dict:
    """Offline-only card lookup. This runs inside target selection while the
    in-game rope is ticking, so it must never make a blocking Scryfall request
    (hence get_card_info_local, not get_card_info)."""
    try:
        return CardInfo.get_card_info_local(grp_id) or {}
    except Exception:
        return {}


def is_lifegain_enabler(grp_id) -> bool:
    """True if the card produces lifegain, i.e. it powers the payoffs."""
    if grp_id is None:
        return False
    try:
        grp_id = int(grp_id)
    except Exception:
        return False
    if grp_id in _enabler_memo:
        return _enabler_memo[grp_id]
    card = _card(grp_id)
    text = str(card.get("oracleText") or "")
    keywords = [str(k).lower() for k in (card.get("keywords") or [])]
    result = "lifelink" in keywords or bool(
        _RE_GAIN_LIFE.search(text) or _RE_LIFELINK.search(text)
    )
    if card:
        _enabler_memo[grp_id] = result
    return result


def _has_evasion(grp_id: int) -> bool:
    keywords = [str(k).lower() for k in (_card(grp_id).get("keywords") or [])]
    return any(k in _EVASION_KEYWORDS for k in keywords)


# Tiers, highest first. The ordering is by ROLE in the deck's engine, never by
# raw stats: Ajani's Pridemate is a 2/2 and still outranks a 2/3 Vampire
# Nighthawk, because it is the thing the whole deck is built to grow. Body size
# only breaks ties WITHIN a tier -- it must never promote a card across one.
TIER_PAYOFF = 3          # grows from lifegain (Pridemate, Twinblade, Panda)
TIER_ENABLER_EVASIVE = 2  # produces lifegain and keeps connecting (Healer's Hawk)
TIER_ENABLER = 1         # produces lifegain (Hinterland Sanctifier, Bloodwitch)
TIER_BODY = 0            # no interaction with the engine


def creature_tier(grp_id) -> int:
    """Role of a creature card in the lifegain engine. See the TIER_* notes."""
    if is_lifegain_payoff(grp_id):
        return TIER_PAYOFF
    if is_lifegain_enabler(grp_id):
        try:
            return TIER_ENABLER_EVASIVE if _has_evasion(int(grp_id)) else TIER_ENABLER
        except Exception:
            return TIER_ENABLER
    return TIER_BODY


def _stat(value) -> int:
    """Read a power/toughness field, which the GRE sends as {"value": N}."""
    if isinstance(value, dict):
        value = value.get("value", 0)
    try:
        return int(value or 0)
    except Exception:
        return 0


def creature_score(obj: dict) -> tuple:
    """Sort key for a creature card; bigger is better.

    (tier, mana value, power + toughness). Mana value sits above the body because
    reanimation cheats cost -- getting the expensive card back is the bigger
    swing -- but both only ever separate cards of the SAME tier.
    """
    grp_id = (obj or {}).get("grpId")
    tier = creature_tier(grp_id)
    try:
        cmc = CardInfo.calculate_cmc(str(_card(int(grp_id)).get("manaCost") or "")) if grp_id is not None else 0
    except Exception:
        cmc = 0
    body = _stat((obj or {}).get("power")) + _stat((obj or {}).get("toughness"))
    return (tier, cmc, body)


def best_creature(objects: list[dict]) -> dict | None:
    """Pick the creature card we most want back from a graveyard/exile prompt.

    Non-creature cards are ignored when any creature is offered; if none of the
    candidates is a creature we return None so the caller keeps its own
    behaviour rather than guessing.
    """
    creatures = [
        o for o in (objects or [])
        if isinstance(o, dict) and "CardType_Creature" in (o.get("cardTypes") or [])
    ]
    if not creatures:
        return None
    return max(creatures, key=creature_score)
