"""Account Manager geometry: a clicked header must not collide with its neighbour.

The drawing itself is checked by looking at it, but this collision is a
measurement rather than taste: clicking a header marks the sort column, and the
first version appended "  ▼" to the header text -- wider than the 22 px column
gap -- so a right-aligned header grew leftwards into the column before it
(reported live on 2026-11-05 for Draft Tokens / Mythic WC / Rare WC).

The window is built at the 50% UI scale this machine actually uses, which also
pins the readability floor.
"""
import os
import tempfile
import tkinter as tk
from types import MethodType
import unittest

import ui

SORT_KEYS = ("account", "gems", "gold", "draft_tokens", "mythic_wildcards", "rare_wildcards")


SIGNED_IN_LOG = (
    "[Accounts - Login] Logged in successfully. Display Name: alpha#11111\n"
    '{ "InventoryInfo": { "SeqId": 1, "Gems": 5000, "Gold": 44950 } }\n'
)
AT_LOGIN_SCREEN_LOG = (
    "[Accounts - Login] Logged in successfully. Display Name: alpha#11111\n"
    "ALT_Prefab.LoginPanelPrefab\n"
    '{ "InventoryInfo": { "SeqId": 1, "Gems": 5000, "Gold": 44950 } }\n'
)


class _Config:
    def __init__(self, root_dir, log_text=SIGNED_IN_LOG):
        self.log_path = os.path.join(root_dir, "Player.log")
        with open(self.log_path, "w", encoding="utf-8") as handle:
            handle.write(log_text)

    def get_managed_accounts(self):
        return [
            {"name": name, "screen_name": name, "email": f"{name}@example.test",
             "pw": "x", "folder": name}
            for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
        ]

    def get_log_path(self):
        return self.log_path

    def detect_player_log_path(self):
        return self.log_path


class AccountManagerHeaderLayoutTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.root._ui_scale = 0.5        # this machine's setting
        self.root._render_button_skin = MethodType(ui.MTGBotUI._render_button_skin, self.root)
        self.root._hex_to_rgb = ui.MTGBotUI._hex_to_rgb
        self.root.bot_running = False
        self.root._manual_login_running = False
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self._windows = 0
        self.window = self.make_window(SIGNED_IN_LOG)

    def make_window(self, log_text):
        self._windows += 1
        folder = os.path.join(self.directory.name, f"w{self._windows}")
        os.makedirs(folder, exist_ok=True)
        window = ui.AccountManagerWindow(self.root, _Config(folder, log_text), spawn_xy=(40, 40))
        window.withdraw()
        self.addCleanup(window.destroy)
        window._refresh()
        return window

    def header_boxes(self):
        """Every visible text item in the header band, as (text, bbox)."""
        canvas = self.window._canvas
        top = self.window._s(90)
        bottom = self.window._s(135)
        boxes = []
        for item in canvas.find_all():
            if canvas.type(item) != "text":
                continue
            if canvas.itemcget(item, "state") == "hidden":
                continue
            text = canvas.itemcget(item, "text")
            box = canvas.bbox(item)
            if not text or not box:
                continue
            if top <= box[1] and box[3] <= bottom:
                boxes.append((text, box))
        return boxes

    @staticmethod
    def _overlaps(a, b):
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    def test_the_readability_floor_is_ninety_percent(self):
        self.assertEqual(self.window._ui_scale, 0.9)

    def test_the_window_fits_a_small_screen(self):
        self.assertLessEqual(self.window._win_width, 1024)
        self.assertLessEqual(self.window.winfo_reqheight(), 768)

    def test_no_header_collides_when_sorted_by_any_column(self):
        for key in SORT_KEYS:
            with self.subTest(sort=key):
                self.window._on_header_click(key)
                self.window.update_idletasks()
                boxes = self.header_boxes()
                # Six data headers, the Log In label, and this column's arrow.
                self.assertEqual(len(boxes), 8, [text for text, _box in boxes])
                arrows = [text for text, _box in boxes if text in ("\u25b2", "\u25bc")]
                self.assertEqual(len(arrows), 1)
                for i in range(len(boxes)):
                    for j in range(i + 1, len(boxes)):
                        first, a = boxes[i]
                        second, b = boxes[j]
                        self.assertFalse(
                            self._overlaps(a, b),
                            f"{key}: {first!r} {a} collides with {second!r} {b}",
                        )


class SignedInRowTest(unittest.TestCase):
    """The account the log says is in is bold, and its button is not clickable."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.root._ui_scale = 0.5
        self.root._render_button_skin = MethodType(ui.MTGBotUI._render_button_skin, self.root)
        self.root._hex_to_rgb = ui.MTGBotUI._hex_to_rgb
        self.root.bot_running = False
        self.root._manual_login_running = False
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self._windows = 0

    def make_window(self, log_text):
        self._windows += 1
        folder = os.path.join(self.directory.name, f"w{self._windows}")
        os.makedirs(folder, exist_ok=True)
        window = ui.AccountManagerWindow(self.root, _Config(folder, log_text), spawn_xy=(40, 40))
        window.withdraw()
        self.addCleanup(window.destroy)
        window._refresh()
        return window

    def test_the_signed_in_row_is_bold_and_its_button_is_disabled(self):
        window = self.make_window(SIGNED_IN_LOG)
        rows = {row["account"]["name"]: row for row in window._table_rows}
        buttons = {button["account"]["name"]: button for button in window._row_buttons}
        self.assertTrue(rows["alpha"]["signed_in"])
        self.assertIn("bold", window._canvas.itemcget(rows["alpha"]["name"], "font"))
        self.assertNotIn("bold", window._canvas.itemcget(rows["bravo"]["name"], "font"))
        self.assertFalse(buttons["alpha"]["enabled"])
        self.assertTrue(buttons["bravo"]["enabled"])
        self.assertTrue(buttons["alpha"]["is_current"])

    def test_clicking_the_signed_in_row_does_nothing(self):
        window = self.make_window(SIGNED_IN_LOG)
        button = {b["account"]["name"]: b for b in window._row_buttons}["alpha"]
        button["pressed"] = True
        button["hover"] = True
        window._on_login_release(button)
        self.assertFalse(window._login_running)
        self.assertIsNone(window._login_thread)

    def test_a_client_on_the_login_screen_has_no_signed_in_row(self):
        window = self.make_window(AT_LOGIN_SCREEN_LOG)
        self.assertTrue(window._table_rows)
        self.assertFalse(any(row["signed_in"] for row in window._table_rows))
        self.assertTrue(all(button["enabled"] for button in window._row_buttons))

    def test_reenabling_rows_keeps_the_signed_in_row_disabled(self):
        window = self.make_window(SIGNED_IN_LOG)
        window._set_row_buttons_enabled(True)
        buttons = {b["account"]["name"]: b for b in window._row_buttons}
        self.assertFalse(buttons["alpha"]["enabled"])
        self.assertTrue(buttons["bravo"]["enabled"])


if __name__ == "__main__":
    unittest.main()
