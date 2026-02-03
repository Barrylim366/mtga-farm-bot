import json
import urllib.request
import urllib.error
import os

CARD_DATA_PATH = "cards.json"
SCRYFALL_CACHE_PATH = "scryfall_cache.json"
MISSING_CARDS_PATH = "missing_cards.json"
_card_data = []
_scryfall_cache = {}

# Load scryfall cache if it exists
try:
    with open(SCRYFALL_CACHE_PATH, 'r', encoding='utf-8') as f:
        _scryfall_cache = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    _scryfall_cache = {}


def _save_scryfall_cache():
    """Save the scryfall cache to disk"""
    try:
        with open(SCRYFALL_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(_scryfall_cache, f)
    except Exception:
        pass


def _load_missing_cards() -> list[int]:
    if not os.path.exists(MISSING_CARDS_PATH):
        return []
    try:
        with open(MISSING_CARDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [int(x) for x in data if isinstance(x, int) or str(x).isdigit()]
    except Exception:
        return []
    return []


def _save_missing_cards(ids: list[int]) -> None:
    try:
        with open(MISSING_CARDS_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(set(ids)), f, indent=2)
    except Exception:
        pass


def _fetch_card_info_from_scryfall(arena_id: int) -> dict | None:
    try:
        url = f"https://api.scryfall.com/cards/arena/{arena_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'MTGABot/1.0'})
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
        # Normalize to the fields the bot uses from cards.json
        card = {
            "grpId": arena_id,
            "titleId": data.get("oracle_id"),
            "manaCost": data.get("mana_cost", ""),
            "colors": data.get("colors", []),
            "types": data.get("type_line", "").replace("—", "-").split(),
            "setCode": data.get("set", "").upper(),
            "rarity": data.get("rarity", ""),
            "name": data.get("name", f"Card#{arena_id}"),
        }
        return card
    except Exception:
        return None


def refresh_missing_cards() -> None:
    """
    Try to resolve any previously missing Arena IDs from Scryfall.
    This keeps cards.json up to date across sessions without a full bulk download.
    """
    ids = _load_missing_cards()
    if not ids:
        return
    updated = False
    remaining = []
    for arena_id in ids:
        card = _fetch_card_info_from_scryfall(arena_id)
        if card:
            _card_data.append(card)
            updated = True
        else:
            remaining.append(arena_id)
    if updated:
        try:
            with open(CARD_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(_card_data, f, indent=2)
        except Exception:
            pass
    _save_missing_cards(remaining)


def get_produced_mana_from_scryfall(arena_id: int):
    """
    Fetch the produced_mana colors for a card from Scryfall API.
    Results are cached to avoid repeated API calls.

    Parameters:
        arena_id: The MTGA arena ID (grpId)
    Returns:
        List of color codes like ['B', 'G'] or None if not found
    """
    cache_key = str(arena_id)

    # Check cache first
    if cache_key in _scryfall_cache:
        return _scryfall_cache[cache_key]

    # Fetch from Scryfall
    try:
        url = f"https://api.scryfall.com/cards/arena/{arena_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'MTGABot/1.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            produced_mana = data.get('produced_mana', [])

            # Cache the result
            _scryfall_cache[cache_key] = produced_mana
            _save_scryfall_cache()

            return produced_mana
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, Exception):
        # Cache None to avoid repeated failed requests
        _scryfall_cache[cache_key] = None
        _save_scryfall_cache()
        return None


def get_land_produced_colors(arena_id: int):
    """
    Get the mana colors a land can produce.

    Parameters:
        arena_id: The MTGA arena ID (grpId)
    Returns:
        Set of color strings like {'black', 'green'} or empty set if unknown
    """
    color_map = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red', 'G': 'green'}

    # First, try to get from local card data using titleId for basic lands
    card_info = get_card_info(arena_id)
    if card_info:
        title_id = card_info.get('titleId')
        if title_id in BASIC_LAND_MANA_MAP:
            return {BASIC_LAND_MANA_MAP[title_id]}

    # For non-basic lands, try Scryfall
    produced_mana = get_produced_mana_from_scryfall(arena_id)
    if produced_mana:
        return {color_map.get(c, c) for c in produced_mana if c in color_map}

    return set()


# Basic Land titleId to mana color mapping
# All verified from player.log mana activation data
BASIC_LAND_MANA_MAP = {
    647: "green",    # Forest (verified: grpId 95200 -> ManaColor_Green)
    648: "white",    # Plains (verified: grpId 95192 -> ManaColor_White)
    652: "blue",     # Island (verified)
    653: "black",    # Swamp (verified)
    1250: "red",     # Mountain (verified)
}

# abilityGrpId to mana color mapping for ActionType_Activate_Mana
# These are the standard tap-for-mana ability IDs in MTGA
# Verified from bot.log correlations:
#   grpId=58449 (Mountain, titleId=1250) -> abilityGrpId=1004 = RED
#   grpId=58453 (Forest, titleId=647) -> abilityGrpId=1005 = GREEN (was labeled as Swamp/Black)
MANA_ABILITY_MAP = {
    # TODO: Need to verify all these mappings from actual game data
    # For now, leaving empty - we'll use Scryfall for all lands
}


def get_mana_color_from_ability(ability_grp_id: int):
    """
    Returns the mana color for a given abilityGrpId from ActionType_Activate_Mana.

    Parameters:
        ability_grp_id: The abilityGrpId from the action
    Returns:
        The mana color as a string ('white', 'blue', 'black', 'red', 'green')
        or None if not a recognized mana ability
    """
    return MANA_ABILITY_MAP.get(ability_grp_id)

try:
    with open(CARD_DATA_PATH, 'r', encoding='utf-8') as f:
        _card_data = json.load(f)
except FileNotFoundError:
    print(f"Error: {CARD_DATA_PATH} not found. Please ensure the cards.json file is in the root directory.")
except json.JSONDecodeError:
    print(f"Error: Could not decode JSON from {CARD_DATA_PATH}. Please check file integrity.")


def reload_cards_from_disk() -> None:
    """Reload cards.json into memory after an export/update."""
    global _card_data
    try:
        with open(CARD_DATA_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            _card_data = data
    except Exception:
        pass


def get_card_info(mtga_id: int):
    """
    Parameters
        mtga_id: Must be a valid mtg arena id
    Returns
        A dictionary object containing full info of the card that has the specified MTGA id
    """
    for card in _card_data:
        if card.get("grpId") == mtga_id:
            return card
    # Not found in local data: try Scryfall once and cache.
    card = _fetch_card_info_from_scryfall(mtga_id)
    if card:
        _card_data.append(card)
        try:
            with open(CARD_DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(_card_data, f, indent=2)
        except Exception:
            pass
        return card
    # Track missing IDs for refresh on next start.
    ids = _load_missing_cards()
    if mtga_id not in ids:
        ids.append(mtga_id)
        _save_missing_cards(ids)
    return None # Return None if card not found


def calculate_cmc(mana_cost: str) -> int:
    """
    Convert manaCost like "{2}{W}{W}" to a simple integer mana value.
    """
    if not mana_cost:
        return 0
    symbols = mana_cost.replace("}{", " ").replace("{", "").replace("}", "").split()
    total = 0
    for sym in symbols:
        if not sym:
            continue
        if sym.lower() == "x":
            continue
        if sym.isdigit():
            total += int(sym)
        else:
            total += 1
    return total


def get_land_mana_color(mtga_id: int):
    """
    Returns the mana color produced by a land card.

    Parameters:
        mtga_id: The MTGA grpId of the land card
    Returns:
        The mana color as a string ('white', 'blue', 'black', 'red', 'green')
        or None if not a recognized basic land
    """
    card_info = get_card_info(mtga_id)
    if card_info is None:
        return None

    # Check if it's a land
    if 'Land' not in card_info.get('types', []):
        return None

    # Get mana color from titleId mapping for basic lands
    title_id = card_info.get('titleId')
    if title_id in BASIC_LAND_MANA_MAP:
        return BASIC_LAND_MANA_MAP[title_id]

    # For non-basic lands with colors field, use that
    colors = card_info.get('colors', [])
    if colors:
        color_map = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red', 'G': 'green'}
        return color_map.get(colors[0])

    return None
