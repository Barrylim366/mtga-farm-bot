"""Exercise actual Tk geometry with dummy accounts; never capture the screen."""
import tkinter as tk
from types import MethodType
import unittest

import ui


class _Config:
    def get_managed_accounts(self):
        return [dict(name="W" * 40 + "#12345", screen_name="W" * 40 + "#12345",
                     email="W" * 50 + "@example.test", pw="dummy")]

    def get_account_play_order(self): return []
    def get_account_switch_mode(self): return "quests"
    def get_account_switch_minutes(self): return 30
    def get_account_switch_main_quests(self): return 3
    def get_account_switch_daily_wins(self): return 15
    def get_account_switch_750_only(self): return False
    def get_shutdown_pc_when_round_complete(self): return False
    def get_max_play_hours(self): return 3


class ManageAccountsLayoutTest(unittest.TestCase):
    def test_controls_and_table_fit_at_multiple_dpi_and_ui_scales(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        self.addCleanup(root.destroy)
        root._render_button_skin = MethodType(ui.MTGBotUI._render_button_skin, root)
        root._hex_to_rgb = ui.SwitchAccountWindow._hex_to_rgb
        parent = tk.Frame(root)
        for dpi in (1.333, 2.0, 2.667):
            root.tk.call("tk", "scaling", dpi)
            for scale in (0.5, 0.78, 1.0, 1.2):
                with self.subTest(dpi=dpi, scale=scale):
                    parent._ui_scale = scale
                    window = ui.SwitchAccountWindow(parent, _Config())
                    window.withdraw()
                    try:
                        window.update_idletasks()
                        canvas = window._canvas
                        for row in window._table_rows:
                            bg = canvas.bbox(row["bg"])
                            for key in ("idx", "name", "email"):
                                box = canvas.bbox(row[key])
                                if box:
                                    self.assertLessEqual(box[3], bg[3])
                                    self.assertLessEqual(box[2], bg[2], (key, canvas.itemcget(row[key], "text"), canvas.itemcget(row[key], "font")))
                            name = canvas.bbox(row["name"])
                            email = canvas.bbox(row["email"])
                            if name and email:
                                self.assertLess(name[2], email[0])
                        for item in canvas.find_all():
                            if canvas.type(item) in ("text", "window"):
                                box = canvas.bbox(item)
                                if box:
                                    self.assertLessEqual(box[2], window._s(438), (canvas.type(item), canvas.itemcget(item, "text") if canvas.type(item) == "text" else canvas.itemcget(item, "window")))
                        for button in window._canvas_buttons.values():
                            text = canvas.bbox(button["text_item"])
                            bg = canvas.bbox(button["bg_item"])
                            self.assertGreaterEqual(text[0], bg[0])
                            self.assertLessEqual(text[2], bg[2])
                        # A short display must keep the footer reachable.
                        window.winfo_screenheight = lambda: 680
                        window._apply_content_minsize()
                        window.update_idletasks()
                        canvas.yview_moveto(1)
                        footer = canvas.bbox(window._canvas_buttons["close_bottom"]["bg_item"])
                        self.assertLessEqual(footer[3], canvas.canvasy(canvas.winfo_height()))
                    finally:
                        window.destroy()


if __name__ == "__main__":
    unittest.main()
