import json

CARD_DATA_PATH = "cards.json"
_card_data = []

# Basic Land titleId to mana color mapping
# All verified from player.log mana activation data
BASIC_LAND_MANA_MAP = {
    647: "green",    # Forest (verified: grpId 95200 -> ManaColor_Green)
    648: "white",    # Plains (verified: grpId 95192 -> ManaColor_White)
    652: "blue",     # Island (verified)
    653: "black",    # Swamp (verified)
    1250: "red",     # Mountain (verified)
}

try:
    with open(CARD_DATA_PATH, 'r', encoding='utf-8') as f:
        _card_data = json.load(f)
except FileNotFoundError:
    print(f"Error: {CARD_DATA_PATH} not found. Please ensure the cards.json file is in the root directory.")
except json.JSONDecodeError:
    print(f"Error: Could not decode JSON from {CARD_DATA_PATH}. Please check file integrity.")


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
