"""Lets the bot pick its Historic deck from the colour icons on screen instead of saved thumbnails.

Wraps the bot's `_select_historic_deck_for_quest`. When a quest names colours, the open My Decks grid
is captured, each tile's colours are read (see deck_tiles), and the matching tile is clicked. If that
finds nothing usable, the bot's own thumbnail matching runs exactly as before.

With no quest left to serve (all dailies done) the bot has no colours to look for. It then plays
the deck named by MTGA_FORGE_DEFAULT_DECK (colour letters, e.g. WB), or the first deck on the grid
when that is not set, so it keeps queueing instead of waiting at Home.

The bot's own search boxes for this screen are also widened here: Arena draws the deck grid higher
when the format's description lines are not shown, which put the "+" tile and the "My Decks" header
partly outside the boxes the bot looks in, so its navigation reported failure on the very screen it
had reached.

Switch off with MTGA_FORGE_DECKS=off.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional


def _enabled() -> bool:
    return (os.environ.get("MTGA_FORGE_DECKS") or "on").strip().lower() not in ("off", "0", "false", "no")


def _log(message: str) -> None:
    try:
        import bot_logger  # type: ignore

        bot_logger.log_info(f"FORGE decks: {message}")
    except Exception:
        print(f"[forge] decks: {message}")


def select_by_icons(
    controller: Any,
    colors: str,
    planned_account: Optional[dict],
    click_tag: str,
    memory: dict,
) -> Optional[tuple[str, str]]:
    """Click the tile whose colour icons fit `colors`. Returns what the bot's own selector returns, or None."""
    from forge_bridge import deck_tiles

    arena = controller._ensure_arena_region(force_reacquire=True)
    if arena is None:
        return None
    controller._vision.begin_tick()  # drop the cached frame: the grid has only just opened
    image = controller._vision.capture(arena)
    if image is None:
        return None
    decks = deck_tiles.find_decks(image)
    if not colors:
        # no quest to serve: the configured default if it is on the grid, else whatever is listed first
        wanted = "".join(c for c in (os.environ.get("MTGA_FORGE_DEFAULT_DECK") or "").upper() if c in deck_tiles.COLORS)
        exact = [d for d in decks if set(d["colors"]) == set(wanted)] if wanted else []
        if exact:
            colors = exact[0]["colors"]
        elif decks:
            colors = decks[0]["colors"]
        else:
            return None

    candidates = controller._historic_deck_candidate_accounts(planned_account)
    account = candidates[0] if candidates else {}
    account_name = str(account.get("name", "")).strip() or str(account.get("folder", "")).strip()

    # Arena redraws the selected tile (lifted, cards fanned over the art), which can hide its icons.
    # The tile we clicked last for this account, if it is no longer readable where it was, is that one.
    remembered = memory.get(account_name)
    if remembered and not any(
        d["colors"] == remembered["colors"]
        and abs(d["click"][0] - remembered["click"][0]) < 60
        and abs(d["click"][1] - remembered["click"][1]) < 60
        for d in decks
    ):
        decks = decks + [dict(remembered, selected=True, score=1.0)]

    _log("on screen: {}".format(", ".join(
        d["colors"] + ("(selected)" if d.get("selected") else "") for d in decks
    ) or "no deck tiles read"))
    deck = deck_tiles.choose_deck(decks, colors)
    if deck is None:
        _log(f"no deck shares a colour with quest {colors}; leaving it to the thumbnail method")
        return None

    # the bot checks its pick by the colour letters in a file name, so answer in that form
    folder = controller._resolve_account_dir(account) or ""
    stand_in = os.path.join(folder, deck["colors"] + ".png")
    if deck.get("selected"):
        _log(f"quest {colors}: deck {deck['colors']} is already selected")
        return stand_in, account_name
    x, y = arena[0] + deck["click"][0], arena[1] + deck["click"][1]
    controller._click_abs(x, y, click_tag)
    time.sleep(0.6)
    memory[account_name] = {"colors": deck["colors"], "click": deck["click"]}
    try:
        controller._remember_selected_deck(account_name, stand_in)
    except Exception:
        pass
    _log(f"quest {colors}: clicked deck {deck['colors']} at {deck['click']} (match {deck['score']})")
    return stand_in, account_name


def wrap_select(original: Callable[..., Any]) -> Callable[..., Any]:
    memory: dict = {}

    def _select_historic_deck_for_quest(
        self, colors, forced_filename, planned_account=None, label="Historic", click_tag="HISTORIC_DECK"
    ):
        # a quest that forces one particular deck file (creature-type quests) is not a colour question
        if _enabled() and not forced_filename:
            try:
                picked = select_by_icons(self, colors, planned_account, click_tag, memory)
            except Exception as exc:
                _log(f"failed ({exc}); using the thumbnail method")
                picked = None
            if picked is not None:
                return picked
        return original(
            self, colors, forced_filename, planned_account=planned_account, label=label, click_tag=click_tag
        )

    return _select_historic_deck_for_quest


def widen_deck_screen_boxes() -> None:
    """Make the bot look for the My Decks header and the "+" tile over the whole left column.

    Measured on a 1600x900 client (in the bot's 1920x1080 frame): with the description lines shown the
    header is at y~360 and the "+" tile at y~516; without them they sit at y~190 and y~377, above the
    bot's boxes, which start at 250 and 450.
    """
    try:
        import actions.navigation_flow as navigation  # type: ignore

        navigation.DECKS_HEADER_ROI = (0, 150, 800, 400)
        navigation.DECKS_GRID_ROI = (0, 330, 800, 560)
    except Exception as exc:
        _log(f"deck screen boxes not widened ({exc})")


def install() -> bool:
    """Needs the bot's full environment (its controller imports OpenCV); a no-op without it."""
    try:
        from Controller.MTGAController.Controller import Controller  # type: ignore
    except Exception as exc:
        _log(f"not installed ({exc})")
        return False
    if getattr(Controller, "_forge_decks_installed", False):
        return True
    Controller._select_historic_deck_for_quest = wrap_select(Controller._select_historic_deck_for_quest)
    widen_deck_screen_boxes()
    Controller._forge_decks_installed = True
    return True
