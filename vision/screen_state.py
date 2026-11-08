"""Exclusive screen-state detection for the Arena client.

The bot's old "am I on Home?" check was the Home tab in the top navigation.
That tab is visible on every page, so it matched on Mastery, Store and Profile
too; combined with a stale Player.log scene name, the bot pressed ESC and
clicked the blind Log Out point on the Mastery reward track (2026-11-09).

This module answers the question from the frame alone, with evidence that is
exclusive to the page:

* the selected top-nav tab, detected by its long orange underline (the
  notification diamonds are orange too but only a few pixels wide), and
* for Home specifically, the large orange Play button, which is present on the
  Home page and not on the other nav pages.

A near-black frame is reported as a transition ("Waiting for the Server...");
callers must not click during one. Everything else is ``unknown`` and must not
be acted on either. The module is pure: no screen capture, no input. The caller
normalizes the client area to 1920x1080 and passes an RGB frame.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080

# Tab centres in the 1920x1080 client frame, measured from live captures on
# 2026-11-09. The tab strip is stable across pages; only the selected state
# changes.
NAV_TABS: tuple[tuple[str, int], ...] = (
    ("Home", 104),
    ("Profile", 208),
    ("Decks", 311),
    ("Packs", 415),
    ("Store", 519),
    ("Mastery", 623),
    ("Achievements", 727),
    ("MTGA+", 831),
)

_TAB_HALF_WIDTH = 55
_ICON_TOP, _ICON_BOTTOM = 8, 72
_UNDERLINE_TOP, _UNDERLINE_BOTTOM = 76, 112
# The selected tab's underline measured 98-110 px wide across Home, Store and
# Mastery; the orange notification diamonds elsewhere measured <= 10 px.
_MIN_UNDERLINE_RUN = 50
# The selected tab's icon measured 136-155 mean grey; unselected tabs <= 109.
_MIN_TAB_BRIGHTNESS = 120.0
# play_btn.png scored 0.944 on Home and 0.575-0.606 on Store/Mastery.
_HOME_PLAY_THRESHOLD = 0.80
# The "Waiting for the Server..." transition is a near-black frame.
_TRANSIENT_MEAN = 15.0

_BUTTONS_DIR = Path(__file__).resolve().parent.parent / "Buttons"


@dataclass(frozen=True)
class ScreenGuess:
    """What the frame shows, with enough detail to debug a bad call."""

    kind: str  # "home" | "nav" | "transient" | "unknown"
    tab: str | None = None
    play_btn_score: float = 0.0
    underline_run: int = 0
    brightness: float = 0.0


def _normalize(frame: np.ndarray, bgr: bool) -> np.ndarray:
    if frame is None:
        raise ValueError("frame is None")
    arr = np.asarray(frame)
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    elif arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise ValueError(f"unexpected frame shape {arr.shape}")
    if arr.shape[2] == 3 and bgr:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    elif arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB if bgr else cv2.COLOR_RGBA2RGB)
    if arr.shape[1] != FRAME_WIDTH or arr.shape[0] != FRAME_HEIGHT:
        arr = cv2.resize(arr, (FRAME_WIDTH, FRAME_HEIGHT), interpolation=cv2.INTER_AREA)
    return arr


def _max_orange_run(strip: np.ndarray) -> int:
    r = strip[:, :, 0].astype(int)
    g = strip[:, :, 1].astype(int)
    b = strip[:, :, 2].astype(int)
    mask = (r > 170) & (g > 70) & (b < 130) & (r > g + 40)
    best = 0
    for row in mask:
        run = 0
        for value in row:
            run = run + 1 if value else 0
            if run > best:
                best = run
    return best


def _tab_scores(rgb: np.ndarray, gray: np.ndarray) -> dict[str, tuple[int, float]]:
    scores: dict[str, tuple[int, float]] = {}
    for tab, x in NAV_TABS:
        left, right = x - _TAB_HALF_WIDTH, x + _TAB_HALF_WIDTH
        underline = rgb[_UNDERLINE_TOP:_UNDERLINE_BOTTOM, left:right]
        run = _max_orange_run(underline)
        brightness = float(
            gray[_ICON_TOP:_ICON_BOTTOM, x - 28:x + 28].mean()
        )
        scores[tab] = (run, brightness)
    return scores


@functools.lru_cache(maxsize=1)
def _play_button_template() -> np.ndarray | None:
    path = _BUTTONS_DIR / "play_btn.png"
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return template


def home_play_button_score(frame: np.ndarray, *, bgr: bool = False) -> float:
    """Best ``play_btn.png`` match in the frame (0.0 when unavailable)."""
    template = _play_button_template()
    if template is None:
        return 0.0
    rgb = _normalize(frame, bgr)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    return float(result.max())


def selected_nav_tab(frame: np.ndarray, *, bgr: bool = False) -> str | None:
    """The top-nav tab whose orange underline is selected, or None."""
    rgb = _normalize(frame, bgr)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    scores = _tab_scores(rgb, gray)
    tab, (run, brightness) = max(
        scores.items(), key=lambda item: (item[1][0], item[1][1])
    )
    if run >= _MIN_UNDERLINE_RUN and brightness >= _MIN_TAB_BRIGHTNESS:
        return tab
    return None


def is_transient(frame: np.ndarray, *, bgr: bool = False) -> bool:
    """True for the near-black transition frames (e.g. Waiting for Server)."""
    rgb = _normalize(frame, bgr)
    return float(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).mean()) < _TRANSIENT_MEAN


def is_home(frame: np.ndarray, *, bgr: bool = False) -> bool:
    """True only when the frame is the Home page with its Play button."""
    if is_transient(frame, bgr=bgr):
        return False
    if selected_nav_tab(frame, bgr=bgr) != "Home":
        return False
    return home_play_button_score(frame, bgr=bgr) >= _HOME_PLAY_THRESHOLD


def classify_screen(frame: np.ndarray, *, bgr: bool = False) -> ScreenGuess:
    """Classify a frame as home, a nav page, a transition, or unknown.

    ``unknown`` includes the in-game board: it has no nav strip, and its
    identity belongs to the log/game state, not this classifier.
    """
    rgb = _normalize(frame, bgr)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if float(gray.mean()) < _TRANSIENT_MEAN:
        return ScreenGuess(kind="transient")

    scores = _tab_scores(rgb, gray)
    tab, (run, brightness) = max(
        scores.items(), key=lambda item: (item[1][0], item[1][1])
    )
    if run < _MIN_UNDERLINE_RUN or brightness < _MIN_TAB_BRIGHTNESS:
        return ScreenGuess(kind="unknown")

    play_score = home_play_button_score(frame, bgr=bgr)
    kind = (
        "home"
        if tab == "Home" and play_score >= _HOME_PLAY_THRESHOLD
        else "nav"
    )
    return ScreenGuess(
        kind=kind,
        tab=tab,
        play_btn_score=play_score,
        underline_run=run,
        brightness=brightness,
    )
