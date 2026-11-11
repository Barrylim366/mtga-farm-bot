"""Wrap the gold status/phase line instead of letting it run off the card.

At a small UI scale the title-look text has a fixed minimum font size while the
window keeps shrinking, so long phase labels ("Looking for Starter Deck Duel in
All Events") were wider than the whole card and overhung both edges. The
renderer now takes a maximum width and wraps onto as many lines as it needs.
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import ImageFont

import ui

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\seguibl.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
)


def _font(px=24):
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    raise unittest.SkipTest("no TrueType font available for the title renderer")


class TitleTextLinesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font = _font(24)

    def lines(self, text, width):
        return ui.MTGBotUI._title_text_lines(text, self.font, width)

    def test_a_line_that_fits_is_left_alone(self):
        self.assertEqual(self.lines("Status: Running", 600), ["Status: Running"])

    def test_no_width_means_a_single_line(self):
        text = "Status: " + "x" * 60
        self.assertEqual(self.lines(text, None), [text])

    def test_a_long_line_wraps_within_the_width(self):
        text = "Looking for Starter Deck Duel in All Events"
        lines = self.lines(text, 150)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(self.font.getlength(line), 150, line)
        self.assertEqual(" ".join(lines), text)

    def test_a_word_too_wide_is_not_broken(self):
        self.assertEqual(
            self.lines("supercalifragilisticexpialidocious", 40),
            ["supercalifragilisticexpialidocious"],
        )

    def test_the_card_width_at_50_percent_wraps_a_phase_label(self):
        # _MAIN_BASE_W = 460 -> 230 px at 50%, minus the 20 px side margins.
        lines = self.lines("Looking for Starter Deck Duel in All Events", 210)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(self.font.getlength(line), 210, line)


class _Photo:
    def __init__(self, width, height):
        self._width = width
        self._height = height

    def width(self):
        return self._width

    def height(self):
        return self._height


class _Canvas:
    def __init__(self):
        self.items = {}
        self._next = 1

    def create_image(self, *args, **kwargs):
        item = self._next
        self._next += 1
        self.items[item] = {"type": "image", "image": kwargs.get("image")}
        return item

    def type(self, item):
        return self.items[item]["type"]

    def itemconfigure(self, item, **kwargs):
        self.items[item].update(kwargs)


class GoldTextFitTest(unittest.TestCase):
    def setUp(self):
        self.app = type("App", (), {})()
        self.app._card_canvas = _Canvas()
        self.photo_calls = []
        self.layout_calls = []
        self.app._refresh_card_layout = lambda: self.layout_calls.append(True)
        self.app._gold_text_photo = self._photo
        for name in ("_apply_gold_text", "_set_gold_text", "_fit_gold_text_width"):
            setattr(self.app, name, types.MethodType(getattr(ui.MTGBotUI, name), self.app))
        self.item = self.app._card_canvas.create_image(0, 0)

    def _photo(self, text, font_px, max_width=None):
        self.photo_calls.append((text, font_px, max_width))
        # Wrapped text is taller; plain text is one line.
        return _Photo(100, 40 if max_width else 20)

    def test_setting_text_renders_unwrapped_first(self):
        self.app._set_gold_text(self.item, "Status: Running", 24)
        self.assertEqual(self.photo_calls[-1], ("Status: Running", 24, None))

    def test_fitting_rewraps_once_per_width(self):
        self.app._set_gold_text(self.item, "Status: Running", 24)
        self.app._fit_gold_text_width(self.item, 300)
        self.assertEqual(self.photo_calls[-1], ("Status: Running", 24, 300))
        count = len(self.photo_calls)
        self.app._fit_gold_text_width(self.item, 300)
        self.assertEqual(len(self.photo_calls), count, "same width must not re-render")

    def test_fitting_again_after_a_resize_rewraps(self):
        self.app._set_gold_text(self.item, "Status: Running", 24)
        self.app._fit_gold_text_width(self.item, 300)
        self.app._fit_gold_text_width(self.item, 200)
        self.assertEqual(self.photo_calls[-1], ("Status: Running", 24, 200))

    def test_a_relabelled_line_keeps_the_fitted_width(self):
        self.app._set_gold_text(self.item, "Status: Running", 24)
        self.app._fit_gold_text_width(self.item, 200)
        self.app._set_gold_text(self.item, "Looking for Starter Deck Duel in All Events", 24)
        self.assertEqual(
            self.photo_calls[-1],
            ("Looking for Starter Deck Duel in All Events", 24, 200),
        )

    def test_a_taller_wrapped_image_asks_for_a_new_layout(self):
        self.app._set_gold_text(self.item, "Status: Running", 24)
        self.layout_calls.clear()
        self.app._fit_gold_text_width(self.item, 300)
        self.assertEqual(self.layout_calls, [True])


class _FakeTextCanvas:
    """Stand-in for the card canvas holding a plain-text fallback item."""

    def __init__(self):
        self.items = {}
        self.calls = []

    def type(self, item):
        return "text"

    def itemconfigure(self, item, **kwargs):
        self.items.setdefault(item, {}).update(kwargs)
        self.calls.append(kwargs)

    def update_idletasks(self):
        pass

    def bbox(self, item):
        return (0, 0, self.items.get(item, {}).get("width", 100), 60)


class TextFallbackWrapTest(unittest.TestCase):
    """The gold image renderer falls back to plain canvas text on some Pillow
    builds; that fallback is what the user sees, so it must wrap too."""

    def setUp(self):
        self.app = type("App", (), {})()
        self.app._card_canvas = _FakeTextCanvas()
        self.app._refresh_card_layout = lambda: None
        for name in ("_apply_gold_text", "_set_gold_text", "_fit_gold_text_width", "_gold_text_height"):
            setattr(self.app, name, types.MethodType(getattr(ui.MTGBotUI, name), self.app))

    def test_the_wrapped_text_reports_its_real_height_to_the_layout(self):
        self.app._set_gold_text(1, "Status: " + "x" * 40, 24)
        self.assertEqual(self.app._gold_text_height(1, 10), 60)

    def test_fitting_sets_the_wrap_width_and_centres(self):
        self.app._set_gold_text(1, "Status: " + "x" * 40, 24)
        self.app._fit_gold_text_width(1, 210)
        self.assertEqual(self.app._card_canvas.items[1]["width"], 210)
        self.assertEqual(self.app._card_canvas.items[1]["justify"], "center")

    def test_the_phase_line_keeps_its_text_when_rewrapped(self):
        self.app._set_gold_text(1, "Looking for Starter Deck Duel in All Events", 14)
        self.app._fit_gold_text_width(1, 210)
        self.assertEqual(
            self.app._card_canvas.items[1]["text"],
            "Looking for Starter Deck Duel in All Events",
        )


if __name__ == "__main__":
    unittest.main()
