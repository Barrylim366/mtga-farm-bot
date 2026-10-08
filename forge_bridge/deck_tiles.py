"""Reads the decks on Arena's "My Decks" grid from the mana-colour icons each tile shows.

Arena draws a deck's colours as small round icons at the bottom of its tile art, the same five icons
for every deck and every account. Finding them tells us which tile is the red-blue deck without a
screenshot of that deck: nothing has to be captured per deck, and new art or a new sleeve changes
nothing.

Needs OpenCV and NumPy, which the bot's own environment has.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

PIPS_DIR = Path(__file__).resolve().parent / "assets" / "pips"
COLORS = "WUBRG"
# the icon templates were cut from a 1600-pixel-wide client; any other size is scaled to it first
REFERENCE_WIDTH = 1600
MATCH_THRESHOLD = 0.80
_HEADER_HEIGHT = 150   # nothing above this is a deck tile
_ICON_SPACING = 40      # icons of one deck sit about 27 px apart; other decks' are over 200 px away
_ART_ABOVE_ICONS = 75   # from the icon row up to the middle of the tile art, the part that selects the deck

_templates: Optional[dict[str, np.ndarray]] = None


def _load_templates() -> dict[str, np.ndarray]:
    global _templates
    if _templates is None:
        loaded = {}
        for color in COLORS:
            image = cv2.imread(str(PIPS_DIR / f"{color}.png"))
            if image is None:
                raise FileNotFoundError(f"missing colour icon template {color}.png in {PIPS_DIR}")
            loaded[color] = image
        _templates = loaded
    return _templates


def find_icons(image: np.ndarray) -> list[tuple[str, int, int, float]]:
    """(colour, x, y, score) for every colour icon in a client-area image at reference scale."""
    hits: list[tuple[str, int, int, float]] = []
    for color, template in _load_templates().items():
        scores = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        half_h, half_w = template.shape[0] // 2, template.shape[1] // 2
        ys, xs = np.where(scores >= MATCH_THRESHOLD)
        for y, x in zip(ys.tolist(), xs.tolist()):
            hits.append((color, x + half_w, y + half_h, float(scores[y, x])))
    # one icon per spot: the best-scoring colour wins, weaker hits within an icon's width are dropped
    hits.sort(key=lambda h: -h[3])
    kept: list[tuple[str, int, int, float]] = []
    for hit in hits:
        if all(abs(hit[1] - k[1]) > 12 or abs(hit[2] - k[2]) > 12 for k in kept):
            kept.append(hit)
    return kept


def find_decks(image: np.ndarray) -> list[dict]:
    """The deck tiles visible in a client-area image, each with its colours and where to click.

    Coordinates are in the image that was passed in, whatever its size.
    """
    height, width = image.shape[:2]
    scale = REFERENCE_WIDTH / float(width)
    if abs(scale - 1.0) > 0.01:
        image = cv2.resize(
            image, (REFERENCE_WIDTH, int(round(height * scale))),
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
        )
    # the top strip holds the navigation bar and, on the deck manager, a row of colour filter icons
    icons = [icon for icon in find_icons(image) if icon[2] > _HEADER_HEIGHT]
    icons.sort(key=lambda h: (h[2] // 20, h[1]))
    groups: list[list[tuple[str, int, int, float]]] = []
    for icon in icons:
        last = groups[-1] if groups else None
        if last and abs(icon[2] - last[-1][2]) <= 10 and 0 < icon[1] - last[-1][1] <= _ICON_SPACING:
            last.append(icon)
        else:
            groups.append([icon])
    decks = []
    for group in groups:
        found = {icon[0] for icon in group}
        x = sum(icon[1] for icon in group) / len(group)
        y = sum(icon[2] for icon in group) / len(group) - _ART_ABOVE_ICONS
        decks.append({
            "colors": "".join(c for c in COLORS if c in found),
            "click": (int(round(x / scale)), int(round(y / scale))),
            "score": round(min(icon[3] for icon in group), 3),
        })
    return decks


def choose_deck(decks: list[dict], target: str) -> Optional[dict]:
    """The deck for a quest's colours: exactly those colours if there is one, else the closest.

    A deck sharing none of the colours is never returned -- it would make no progress on the quest.
    Among decks sharing the same number of colours, the one with fewest other colours wins.
    """
    wanted = {c for c in (target or "").upper() if c in COLORS}
    if not wanted:
        return None
    best, best_key = None, None
    for deck in decks:
        have = set(deck["colors"])
        shared = len(have & wanted)
        if shared == 0:
            continue
        key = (shared, -len(have - wanted), deck["score"])
        if best_key is None or key > best_key:
            best, best_key = deck, key
    return best
