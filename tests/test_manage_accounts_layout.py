"""Exercise actual Tk geometry with dummy accounts; never capture the screen."""
import json
import os
import re
import tempfile
import tkinter as tk
from types import MethodType
import unittest
from unittest import mock

import ui


class _Config:
    def get_managed_accounts(self):
        return [dict(name="W" * 40 + "#12345", screen_name="W" * 40 + "#12345",
                     email="W" * 50 + "@example.test", pw="dummy")]

    def get_account_play_order(self): return []
    def get_account_switch_mode(self): return "quests"
    def get_account_switch_minutes(self): return 30
    def get_account_switch_main_quests(self): return 1
    def get_account_switch_quest_target(self): return "leave"
    def get_account_switch_quests_to_leave(self): return 2
    def get_account_switch_quests_to_complete(self): return 1
    def get_account_switch_daily_wins(self): return 15
    def get_account_switch_750_only(self): return False
    def get_shutdown_pc_when_round_complete(self): return False
    def get_max_play_hours(self): return 3
    def get_daily_reset_utc_hour(self): return 9


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
                        # Fit a short display and keep the footer reachable.
                        # Windows applies geometry() to a withdrawn window only
                        # once it is mapped, and mapping it could steal focus
                        # from a running bot. Check the size that was requested.
                        window.winfo_screenheight = lambda: 680
                        requested = []
                        real_geometry = window.geometry
                        window.geometry = lambda *a: (requested.extend(a), real_geometry(*a))[1]
                        window._apply_content_minsize()
                        window.update_idletasks()
                        geometry = next((g for g in reversed(requested) if "x" in g), real_geometry())
                        height_match = re.match(r"\d+x(\d+)", geometry)
                        self.assertIsNotNone(height_match, geometry)
                        self.assertLessEqual(int(height_match.group(1)), 680, geometry)
                        canvas.yview_moveto(1)
                        footer = canvas.bbox(window._canvas_buttons["close_bottom"]["bg_item"])
                        self.assertLessEqual(footer[3], canvas.canvasy(canvas.winfo_height()))
                    finally:
                        window.destroy()

    def test_the_quest_target_dropdown_swaps_the_modes_number(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        self.addCleanup(root.destroy)
        root._render_button_skin = MethodType(ui.MTGBotUI._render_button_skin, root)
        root._hex_to_rgb = ui.SwitchAccountWindow._hex_to_rgb
        parent = tk.Frame(root)
        parent._ui_scale = 0.5
        window = ui.SwitchAccountWindow(parent, _Config())
        window.withdraw()
        self.addCleanup(window.destroy)
        self.assertEqual(window.switch_quest_target_var.get(), "leave open")
        self.assertEqual(window.switch_main_var.get(), "2")
        window.switch_quest_target_var.set("complete")
        window._sync_quest_target_entry()
        self.assertEqual(window.switch_main_var.get(), "1")
        self.assertEqual(ui.SwitchAccountWindow._quest_target_mode("leave open"), "leave")
        self.assertEqual(ui.SwitchAccountWindow._quest_target_mode("complete"), "complete")

    def test_saving_the_quest_target_keeps_each_modes_number(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        self.addCleanup(root.destroy)
        root._render_button_skin = MethodType(ui.MTGBotUI._render_button_skin, root)
        root._hex_to_rgb = ui.SwitchAccountWindow._hex_to_rgb
        parent = tk.Frame(root)
        parent._ui_scale = 0.5
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "calibration_config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "account_switch_mode": "quests",
                    "account_switch_main_quests": 1,
                    "account_switch_daily_wins": 4,
                },
                handle,
            )
        config = ui.ConfigManager(path)
        window = ui.SwitchAccountWindow(parent, config)
        window.withdraw()
        self.addCleanup(window.destroy)
        window.switch_quest_target_var.set("complete")
        window._sync_quest_target_entry()
        window.switch_main_var.set("1")
        with mock.patch.object(ui.messagebox, "showinfo"):
            window._save_switch_settings()
        self.assertEqual(config.get_account_switch_quest_target(), "complete")
        self.assertEqual(config.get_account_switch_quests_to_complete(), 1)
        # The leave number the install already had is untouched.
        self.assertEqual(config.get_account_switch_quests_to_leave(), 2)


if __name__ == "__main__":
    unittest.main()
