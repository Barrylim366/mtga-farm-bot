"""Intelligent London Mulligan Engine for MTGA Farm Bot.

Analyzes opening hands using land-to-spell ratios, mana color alignment,
and early-curve castability (Turns 1-3).
"""
from __future__ import annotations

import re
import bot_logger
import AI.Utilities.CardInfo as CardInfo


def extract_mana_colors(mana_cost: str, colors_list: list[str] | None = None) -> set[str]:
    """Extract required colored mana from mana cost or card metadata."""
    symbol_to_color = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red', 'G': 'green'}
    colors = set()
    if mana_cost:
        for symbol in re.findall(r"\{([WUBRG])\}", mana_cost.upper()):
            if symbol in symbol_to_color:
                colors.add(symbol_to_color[symbol])
    if colors_list:
        for c in colors_list:
            if isinstance(c, str):
                c_low = c.lower()
                if c_low in symbol_to_color.values():
                    colors.add(c_low)
    return colors


def _get_card_details(card_entry: dict | int) -> dict:
    """Resolve card metadata from an object dict or grpId using local card data."""
    if isinstance(card_entry, int):
        grp_id = card_entry
        inst_id = None
    elif isinstance(card_entry, dict):
        grp_id = card_entry.get("grpId")
        inst_id = card_entry.get("instanceId")
    else:
        return {"name": "Unknown", "is_land": False, "cmc": 999, "produced_colors": set(), "required_colors": set()}

    card_info = CardInfo.get_card_info_local(grp_id) if grp_id else {}
    types = (card_info.get("types") or []) if card_info else []
    is_land = "Land" in types or "Basic Land" in types
    cmc = CardInfo.calculate_cmc(card_info.get("manaCost", "")) if card_info else 999
    name = card_info.get("name", "Unknown") if card_info else "Unknown"
    
    produced_colors = set()
    if is_land and card_info:
        title_id = card_info.get('titleId')
        if title_id in CardInfo.BASIC_LAND_MANA_MAP:
            produced_colors = {CardInfo.BASIC_LAND_MANA_MAP[title_id]}
        elif card_info.get('name') in CardInfo.BASIC_LAND_NAME_MAP:
            produced_colors = {CardInfo.BASIC_LAND_NAME_MAP[card_info['name']]}
        elif grp_id:
            produced_colors = CardInfo.get_land_produced_colors(grp_id)
    elif is_land and grp_id:
        produced_colors = CardInfo.get_land_produced_colors(grp_id)
    mana_cost = card_info.get("manaCost", "") if card_info else ""
    required_colors = extract_mana_colors(mana_cost, card_info.get("colors") if card_info else None) if not is_land else set()

    return {
        "instanceId": inst_id,
        "grpId": grp_id,
        "name": name,
        "is_land": is_land,
        "cmc": cmc,
        "types": types,
        "produced_colors": produced_colors,
        "required_colors": required_colors,
        "manaCost": mana_cost,
    }


def evaluate_opening_hand(hand_cards: list, mulligan_count: int = 0) -> tuple[bool, str]:
    """Evaluate whether to keep or mulligan the given opening hand.
    
    Returns:
        (keep: bool, reason: str)
    """
    if not hand_cards:
        # Fallback if hand cannot be read: keep safely
        return True, "Hand data unavailable; default keep"

    resolved = [_get_card_details(c) for c in hand_cards]
    hand_size = len(resolved)
    lands = [c for c in resolved if c["is_land"]]
    spells = [c for c in resolved if not c["is_land"]]
    land_count = len(lands)
    spell_count = len(spells)

    bot_logger.log_info(
        f"[MULLIGAN_EVAL] Hand size={hand_size}, Lands={land_count}, Spells={spell_count}, MullCount={mulligan_count}: "
        f"{[c['name'] for c in resolved]}"
    )

    # 4 or fewer cards: never mulligan further to preserve game presence
    if hand_size <= 4:
        return True, f"Hand size {hand_size} is at or below minimum threshold; keeping."

    # 0 Lands is an auto-loss
    if land_count == 0:
        return False, "0 lands in hand; automatic mulligan."

    # All Lands is an auto-loss
    if spell_count == 0:
        return False, f"All {land_count} cards are lands (no spells); automatic mulligan."

    # Color alignment check: ensure lands produce colors needed by our spells
    if 2 <= land_count <= 4 and spell_count > 0:
        produced_colors = set()
        for land in lands:
            produced_colors.update(land.get("produced_colors") or set())

        if produced_colors:
            all_spell_colors = set()
            for s in spells:
                all_spell_colors.update(s.get("required_colors") or set())

            # If spells require colored mana but lands produce NONE of those colors
            if all_spell_colors and not (produced_colors & all_spell_colors):
                if hand_size >= 6 and mulligan_count < 2:
                    return False, f"Color mismatch: lands produce {produced_colors} but spells require {all_spell_colors}; mulliganing."

    # --- 7-Card Hand Evaluation (First draw) ---
    if hand_size == 7:
        if land_count == 1:
            # Keep 1-land only if we have at least two cheap 1-mana plays
            one_drops = [s for s in spells if s["cmc"] <= 1]
            if len(one_drops) >= 2:
                return True, "1 land with multiple 1-drops; risky keep."
            return False, "1 land with insufficient 1-drops; mulliganing."

        if land_count == 6:
            # 6 lands: only keep if the sole spell is a low-curve threat
            if spells[0]["cmc"] <= 2:
                return True, "6 lands with 1-2 CMC threat; keep."
            return False, "6 lands with high CMC spell; mulliganing."

        if land_count == 5:
            # 5 lands: check if we have early plays (CMC <= 3)
            early_plays = [s for s in spells if s["cmc"] <= 3]
            if early_plays:
                return True, f"5 lands with {len(early_plays)} early play(s); keeping."
            return False, "5 lands with all high-CMC spells; mulliganing."

        if 2 <= land_count <= 4:
            # Ideal land count (2-4 lands in 7 cards)
            early_plays = [s for s in spells if s["cmc"] <= 3]
            if early_plays or land_count == 3:
                return True, f"Ideal land count ({land_count}) with solid curve; keeping."
            # High curve check: all spells are CMC 4+
            if all(s["cmc"] >= 4 for s in spells):
                return False, f"{land_count} lands but all spells are 4+ CMC; mulliganing."
            return True, f"Balanced hand ({land_count} lands); keeping."

    # --- 6-Card Hand Evaluation ---
    if hand_size == 6:
        if land_count == 1:
            early_plays = [s for s in spells if s["cmc"] <= 2]
            if len(early_plays) >= 2:
                return True, "6-card hand: 1 land with early plays; keeping."
            return False, "6-card hand: 1 land without early curve; mulliganing to 5."

        if land_count in (2, 3, 4):
            return True, f"6-card hand: good land count ({land_count}); keeping."

        if land_count >= 5:
            return False, f"6-card hand: land flooded ({land_count} lands); mulliganing to 5."

    # --- 5-Card Hand Evaluation ---
    if hand_size == 5:
        if 1 <= land_count <= 3:
            return True, f"5-card hand: {land_count} lands; keeping."
        if land_count >= 4:
            return False, "5-card hand: 4+ lands; mulliganing to 4."

    return True, "Default keep fallback."


def select_cards_to_tuck(hand_cards: list, count_to_tuck: int) -> list[int]:
    """London Mulligan: Select which cards to put on the bottom of the library.
    
    Returns list of instanceIds (or fallback tokens) to tuck.
    """
    if count_to_tuck <= 0 or not hand_cards:
        return []

    resolved = [_get_card_details(c) for c in hand_cards]
    lands = [c for c in resolved if c["is_land"]]
    spells = [c for c in resolved if not c["is_land"]]

    tuck_candidates: list[int] = []

    def get_token(card: dict) -> int | None:
        iid = card.get("instanceId")
        if iid is not None:
            return int(iid)
        gid = card.get("grpId")
        if gid is not None:
            bot_logger.log_info(
                f"[MULLIGAN_TUCK] Missing instanceId for card {card.get('name')}; falling back to grpId={gid}"
            )
            return int(gid)
        return None

    # 1. If we have excess lands (> 3 lands), tuck excess lands first
    if len(lands) > 3:
        excess_lands = lands[3:]
        for land in excess_lands:
            tok = get_token(land)
            if tok is not None and tok not in tuck_candidates:
                tuck_candidates.append(tok)
                if len(tuck_candidates) == count_to_tuck:
                    return tuck_candidates

    # 2. Tuck highest CMC spells that we won't be able to cast anytime soon
    spells_by_cmc_desc = sorted(spells, key=lambda s: -s["cmc"])
    for spell in spells_by_cmc_desc:
        tok = get_token(spell)
        if tok is not None and tok not in tuck_candidates:
            tuck_candidates.append(tok)
            if len(tuck_candidates) == count_to_tuck:
                return tuck_candidates

    # 3. Fallback: tuck whatever is available to satisfy count
    for c in resolved:
        tok = get_token(c)
        if tok is not None and tok not in tuck_candidates:
            tuck_candidates.append(tok)
            if len(tuck_candidates) == count_to_tuck:
                return tuck_candidates

    # If any card lacked both instanceId and grpId, add synthetic index tokens
    for idx, c in enumerate(resolved):
        if len(tuck_candidates) == count_to_tuck:
            break
        if idx not in tuck_candidates:
            tuck_candidates.append(idx)

    return tuck_candidates
