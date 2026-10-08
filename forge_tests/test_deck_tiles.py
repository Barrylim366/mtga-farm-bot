"""Deck selection from the colour icons on Arena's My Decks grid.

    .venv/Scripts/python.exe -m unittest forge_tests.test_deck_tiles -v

Uses two real screenshots of the client (tests/data). Needs OpenCV, so it runs in the bot's
environment and is skipped elsewhere.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

DATA = ROOT / "forge_tests" / "data"


def client_area(name: str):
    """A screenshot of the whole window, cut down to what the bot captures: no title bar, no border."""
    return cv2.imread(str(DATA / name))[32:, 1:1601]


class FakeVision:
    def __init__(self, image):
        self.image = image

    def begin_tick(self):
        pass

    def capture(self, region):
        return self.image


class FakeController:
    """The handful of controller methods the hook touches."""

    def __init__(self, image):
        self._vision = FakeVision(image)
        self.clicks = []
        self.remembered = []

    def _ensure_arena_region(self, force_reacquire=False):
        return (100, 50, 1600, 900)

    def _historic_deck_candidate_accounts(self, planned_account=None):
        return [{"name": "Tester", "folder": "Tester"}]

    def _resolve_account_dir(self, account):
        return "accounts/Tester"

    def _click_abs(self, x, y, tag):
        self.clicks.append((x, y, tag))

    def _remember_selected_deck(self, name, path):
        self.remembered.append((name, path))


@unittest.skipIf(cv2 is None, "OpenCV not installed (run with the bot's Python)")
class DeckTileTests(unittest.TestCase):
    def test_reads_every_deck_on_the_play_screen(self):
        from forge_bridge import deck_tiles

        decks = deck_tiles.find_decks(client_area("play_blade.webp"))
        self.assertEqual([d["colors"] for d in decks], ["WB", "WU", "UR", "G", "B", "R", "W"])

    def test_ignores_the_colour_filter_row_of_the_deck_manager(self):
        from forge_bridge import deck_tiles

        decks = deck_tiles.find_decks(client_area("decks_tab.webp"))
        self.assertEqual([d["colors"] for d in decks], ["WU", "UR", "G", "B", "R", "WB", "W"])

    def test_same_decks_at_other_window_sizes(self):
        from forge_bridge import deck_tiles

        image = client_area("play_blade.webp")
        for width in (1920, 1280):
            resized = cv2.resize(image, (width, int(image.shape[0] * width / 1600)))
            decks = deck_tiles.find_decks(resized)
            self.assertEqual([d["colors"] for d in decks], ["WB", "WU", "UR", "G", "B", "R", "W"], width)
            # click points come back in the resized image's own pixels
            self.assertAlmostEqual(decks[2]["click"][0], 864 * width / 1600, delta=3)

    def test_choice_prefers_exact_colours_and_never_a_stranger(self):
        from forge_bridge import deck_tiles

        decks = deck_tiles.find_decks(client_area("play_blade.webp"))
        self.assertEqual(deck_tiles.choose_deck(decks, "UR")["colors"], "UR")
        self.assertEqual(deck_tiles.choose_deck(decks, "R")["colors"], "R")
        self.assertIn(deck_tiles.choose_deck(decks, "RG")["colors"], ("R", "G"))
        self.assertIsNone(deck_tiles.choose_deck([d for d in decks if d["colors"] == "G"], "UR"))
        self.assertIsNone(deck_tiles.choose_deck(decks, ""))


@unittest.skipIf(cv2 is None, "OpenCV not installed (run with the bot's Python)")
class DeckHookTests(unittest.TestCase):
    def setUp(self):
        from forge_bridge import deck_hook

        self.calls = []

        def original(controller, colors, forced_filename, planned_account=None, label="", click_tag=""):
            self.calls.append((colors, forced_filename))
            return ("thumbnail", "Tester")

        self.select = deck_hook.wrap_select(original)
        self.controller = FakeController(client_area("play_blade.webp"))

    def test_clicks_the_matching_tile(self):
        path, account = self.select(self.controller, "UR", None)
        self.assertEqual((Path(path).name, account), ("UR.png", "Tester"))
        x, y, _ = self.controller.clicks[0]
        # the UR tile's art, offset by where the client sits on the desktop
        self.assertAlmostEqual(x - 100, 864, delta=4)
        self.assertAlmostEqual(y - 50, 469, delta=6)
        self.assertEqual(self.calls, [])

    def test_a_forced_deck_file_goes_to_the_thumbnail_method(self):
        self.assertEqual(self.select(self.controller, "", "C.png"), ("thumbnail", "Tester"))
        self.assertEqual(self.controller.clicks, [])

    def test_no_matching_colour_goes_to_the_thumbnail_method(self):
        # a board with only the green deck readable cannot serve a blue-red quest
        image = client_area("play_blade.webp").copy()
        image[:, :1000] = 0
        image[650:, :] = 0
        controller = FakeController(image)
        self.assertEqual(self.select(controller, "UR", None), ("thumbnail", "Tester"))
        self.assertEqual(controller.clicks, [])

    def test_the_tile_it_selected_is_not_clicked_again_once_its_icons_are_hidden(self):
        self.select(self.controller, "UR", None)
        image = client_area("play_blade.webp").copy()
        image[540:620, 760:980] = 0  # Arena redraws the selected tile; its icons are gone
        self.controller._vision.image = image
        path, _ = self.select(self.controller, "UR", None)
        self.assertEqual(Path(path).name, "UR.png")
        self.assertEqual(len(self.controller.clicks), 1)

    def test_with_no_quest_it_plays_the_first_deck_listed(self):
        path, _ = self.select(self.controller, "", None)
        self.assertEqual(Path(path).name, "WB.png")
        self.assertEqual(len(self.controller.clicks), 1)
        self.assertEqual(self.calls, [])

    def test_with_no_quest_it_plays_the_configured_default_deck(self):
        import os

        os.environ["MTGA_FORGE_DEFAULT_DECK"] = "ur"
        self.addCleanup(os.environ.pop, "MTGA_FORGE_DEFAULT_DECK", None)
        path, _ = self.select(self.controller, "", None)
        self.assertEqual(Path(path).name, "UR.png")

    def test_the_compact_layout_with_a_selected_deck_is_read(self):
        # the screen the bot was stuck on: no description lines, grid drawn higher, the R deck selected
        from forge_bridge import deck_tiles

        image = cv2.imread(str(DATA / "play_blade_compact.png"))
        decks = deck_tiles.find_decks(image)
        self.assertEqual([d["colors"] for d in decks], ["WB", "WU", "G", "UR", "B", "R", "W"])

    def test_the_bots_anchors_fall_inside_the_widened_boxes_on_both_layouts(self):
        from forge_bridge import deck_hook

        class Navigation:
            DECKS_HEADER_ROI = (0, 250, 800, 220)
            DECKS_GRID_ROI = (0, 450, 800, 400)

        import sys as _sys
        import types

        package = types.ModuleType("actions")
        package.navigation_flow = Navigation  # type: ignore[attr-defined]
        saved = {name: _sys.modules.get(name) for name in ("actions", "actions.navigation_flow")}
        _sys.modules["actions"], _sys.modules["actions.navigation_flow"] = package, Navigation  # type: ignore[assignment]
        try:
            deck_hook.widen_deck_screen_boxes()
        finally:
            for name, module in saved.items():
                if module is None:
                    _sys.modules.pop(name, None)
                else:
                    _sys.modules[name] = module

        bot = ROOT
        checks = [
            (bot / "Buttons" / "my_decks_grid_open.png", Navigation.DECKS_GRID_ROI),
            (bot / "assets" / "assert" / "my_decks_anchor.png", Navigation.DECKS_HEADER_ROI),
        ]
        screens = [cv2.imread(str(DATA / "play_blade_compact.png")), client_area("play_blade.webp")]
        for screen in screens:
            reference = cv2.resize(screen, (1920, 1080))
            for template_path, (x, y, w, h) in checks:
                template = cv2.imread(str(template_path))
                region = reference[y:y + h, x:x + w]
                score = cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))[1]
                self.assertGreater(score, 0.80, template_path.name)

    def test_switched_off(self):
        import os

        os.environ["MTGA_FORGE_DECKS"] = "off"
        self.addCleanup(os.environ.pop, "MTGA_FORGE_DECKS", None)
        self.assertEqual(self.select(self.controller, "UR", None), ("thumbnail", "Tester"))


if __name__ == "__main__":
    unittest.main()
