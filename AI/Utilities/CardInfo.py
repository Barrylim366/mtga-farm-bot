import json

CARD_DATA_PATH = "cards.json"
_card_data = []

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
