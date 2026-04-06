import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from datetime import datetime
import os
import sys
import time
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat, ImageTk
import json
import threading
from Controller.Utilities.input_controller import InputControllerError, create_input_controller
from runtime_paths import runtime_file
from vision.window_locator import ArenaDetectionResult, run_arena_setup_check

# Import bot components
from Controller.MTGAController.Controller import Controller
from AI.DummyAI import DummyAI
from Game import Game


def _default_player_log_path() -> str:
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(
            home,
            "AppData",
            "LocalLow",
            "Wizards Of The Coast",
            "MTGA",
            "Player.log",
        )
    if sys.platform == "darwin":
        return os.path.join(
            home,
            "Library",
            "Logs",
            "Wizards Of The Coast",
            "MTGA",
            "Player.log",
        )
    return os.path.join(
        home,
        ".local",
        "share",
        "Steam",
        "steamapps",
        "compatdata",
        "2141910",
        "pfx",
        "drive_c",
        "users",
        "steamuser",
        "AppData",
        "LocalLow",
        "Wizards Of The Coast",
        "MTGA",
        "Player.log",
    )


def _app_root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    return os.path.abspath(os.path.dirname(__file__))


def _app_path(*parts: str) -> str:
    return os.path.join(_app_root_dir(), *parts)


def _resource_root_dir() -> str:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if isinstance(meipass, str) and meipass and os.path.isdir(meipass):
            return os.path.abspath(meipass)
    return os.path.abspath(os.path.dirname(__file__))


def _resource_path(*parts: str) -> str:
    return os.path.join(_resource_root_dir(), *parts)


def _image_path(filename: str) -> str:
    candidates = [
        _resource_path("images", filename),
        _app_path("images", filename),
        _resource_path(filename),
        _app_path(filename),
    ]
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def _get_ui_topmost_setting_from_widget(widget) -> bool:
    cur = widget
    while cur is not None:
        cfg = getattr(cur, "config_manager", None)
        if cfg is not None and hasattr(cfg, "get_ui_windows_topmost"):
            try:
                return bool(cfg.get_ui_windows_topmost())
            except Exception:
                return False
        cur = getattr(cur, "master", None)
    return False


def _apply_window_topmost(window, enabled: bool) -> None:
    try:
        window.attributes("-topmost", bool(enabled))
    except Exception:
        pass


def _fit_window_to_canvas_content(
    window,
    canvas: tk.Canvas,
    *,
    exclude_items: set[int] | None = None,
    pad_x: int = 20,
    pad_y: int = 20,
    floor_w: int = 240,
    floor_h: int = 180,
) -> None:
    try:
        window.update_idletasks()
        ids = canvas.find_all()
        if not ids:
            return
        excluded = exclude_items or set()
        x1 = y1 = x2 = y2 = None
        for item_id in ids:
            if item_id in excluded:
                continue
            bbox = canvas.bbox(item_id)
            if not bbox:
                continue
            bx1, by1, bx2, by2 = bbox
            x1 = bx1 if x1 is None else min(x1, bx1)
            y1 = by1 if y1 is None else min(y1, by1)
            x2 = bx2 if x2 is None else max(x2, bx2)
            y2 = by2 if y2 is None else max(y2, by2)
        if x1 is None or y1 is None or x2 is None or y2 is None:
            return
        req_w = max(int(floor_w), int(x2 + pad_x))
        req_h = max(int(floor_h), int(y2 + pad_y))
        window.minsize(req_w, req_h)
        cur_w = int(window.winfo_width())
        cur_h = int(window.winfo_height())
        if cur_w < req_w or cur_h < req_h:
            x = int(window.winfo_x())
            y = int(window.winfo_y())
            new_w = max(cur_w, req_w)
            new_h = max(cur_h, req_h)
            max_x = max(0, int(window.winfo_screenwidth()) - new_w)
            max_y = max(0, int(window.winfo_screenheight()) - new_h)
            x = min(max(0, x), max_x)
            y = min(max(0, y), max_y)
            window.geometry(f"{new_w}x{new_h}+{x}+{y}")
    except Exception:
        pass


def _get_ui_scale_from_widget(widget) -> float:
    cur = widget
    while cur is not None:
        scale = getattr(cur, "_ui_scale", None)
        if isinstance(scale, (int, float)) and scale > 0:
            # Keep subwindows aligned with global UI scaling.
            return max(0.50, float(scale))
        cur = getattr(cur, "master", None)
    return 1.0


def _submenu_palette():
    return {
        "bg": "#0F1115",
        "surface": "#151A21",
        "surface_alt": "#1B2230",
        "surface_hover": "#253041",
        "border": "#242B36",
        "text": "#E7EAF0",
        "text_muted": "#9AA3B2",
        "success": "#8FE0B0",
        "danger_bg": "#3A2025",
        "danger_hover": "#4A262C",
    }


def _apply_dark_combobox_style(window):
    c = _submenu_palette()
    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "Dark.TCombobox",
        fieldbackground=c["surface_alt"],
        background=c["surface_alt"],
        foreground=c["text"],
        bordercolor=c["border"],
        lightcolor=c["border"],
        darkcolor=c["border"],
        arrowcolor=c["text"],
    )
    style.map(
        "Dark.TCombobox",
        fieldbackground=[("readonly", c["surface_alt"])],
        background=[("readonly", c["surface_alt"])],
        foreground=[("readonly", c["text"])],
    )


def _apply_submenu_theme(window):
    c = _submenu_palette()
    _apply_dark_combobox_style(window)
    style = ttk.Style(window)
    style.configure(
        "Submenu.TButton",
        font=("Segoe UI", 10),
        padding=(12, 4),
        foreground=c["text"],
        background=c["surface_alt"],
        borderwidth=0,
        relief="flat",
    )
    style.map(
        "Submenu.TButton",
        background=[("pressed", "#323232"), ("active", "#444444"), ("disabled", "#26364F")],
        foreground=[("disabled", c["text_muted"])],
    )
    style.configure(
        "SubmenuDanger.TButton",
        font=("Segoe UI", 10),
        padding=(12, 4),
        foreground=c["text"],
        background=c["danger_bg"],
        borderwidth=0,
        relief="flat",
    )
    style.map(
        "SubmenuDanger.TButton",
        background=[("pressed", "#311B20"), ("active", c["danger_hover"]), ("disabled", "#2A1E20")],
        foreground=[("disabled", c["text_muted"])],
    )
    try:
        window.configure(bg=c["bg"])
    except Exception:
        pass

    bg_map = {
        "#2b2b2b": c["surface"],
        "#3b3b3b": c["surface_alt"],
        "#3a3a3a": c["surface_alt"],
        "#444444": c["surface_hover"],
        "#4a4a4a": c["border"],
        "#1e1e1e": c["bg"],
        "#111111": c["bg"],
    }
    fg_map = {
        "white": c["text"],
        "#ffffff": c["text"],
        "#aaaaaa": c["text_muted"],
        "#dddddd": c["text"],
        "#00ff00": c["success"],
        "#1e1e1e": c["bg"],
    }

    stack = [window]
    while stack:
        widget = stack.pop()
        try:
            stack.extend(widget.winfo_children())
        except Exception:
            pass

        if isinstance(widget, ttk.Combobox):
            try:
                widget.configure(style="Dark.TCombobox")
            except Exception:
                pass
            continue

        if isinstance(widget, ttk.Button):
            try:
                label = str(widget.cget("text") or "").lower()
                current_style = str(widget.cget("style") or "").strip()
                if "delete" in label or "stop" in label:
                    if not current_style or current_style == "TButton":
                        widget.configure(style="SubmenuDanger.TButton")
                else:
                    if not current_style or current_style == "TButton":
                        widget.configure(style="Submenu.TButton")
            except Exception:
                pass
            continue

        if isinstance(widget, tk.Button):
            try:
                label = str(widget.cget("text") or "").lower()
                if "delete" in label or "stop" in label:
                    widget.configure(
                        bg=c["danger_bg"],
                        fg=c["text"],
                        activebackground=c["danger_hover"],
                        activeforeground=c["text"],
                        relief=tk.FLAT,
                    )
                else:
                    widget.configure(
                        bg=c["surface_alt"],
                        fg=c["text"],
                        activebackground=c["surface_hover"],
                        activeforeground=c["text"],
                        relief=tk.FLAT,
                    )
            except Exception:
                pass

        if isinstance(widget, tk.Entry):
            try:
                widget.configure(
                    bg=c["surface_alt"],
                    fg=c["text"],
                    insertbackground=c["text"],
                    relief=tk.FLAT,
                )
            except Exception:
                pass

        if isinstance(widget, tk.Text):
            try:
                widget.configure(
                    bg=c["bg"],
                    fg=c["text"],
                    insertbackground=c["text"],
                )
            except Exception:
                pass

        if isinstance(widget, tk.Checkbutton):
            try:
                widget.configure(
                    bg=c["surface"],
                    fg=c["text"],
                    activebackground=c["surface"],
                    activeforeground=c["text"],
                    selectcolor=c["surface"],
                )
            except Exception:
                pass

        for opt in ("bg", "background", "activebackground", "highlightbackground"):
            try:
                cur = str(widget.cget(opt)).lower()
            except Exception:
                continue
            new_val = bg_map.get(cur)
            if new_val:
                try:
                    widget.configure(**{opt: new_val})
                except Exception:
                    pass

        for opt in ("fg", "foreground", "activeforeground", "insertbackground", "highlightcolor"):
            try:
                cur = str(widget.cget(opt)).lower()
            except Exception:
                continue
            new_val = fg_map.get(cur)
            if new_val:
                try:
                    widget.configure(**{opt: new_val})
                except Exception:
                    pass

class CalibrationWindow(tk.Toplevel):
    """Calibration submenu window"""

    def __init__(self, parent, config_manager, spawn_xy: tuple[int, int] | None = None, on_close=None):
        super().__init__(parent)
        self.parent = parent
        self._on_close_callback = on_close
        self.config_manager = config_manager
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self.title("Calibrate")
        # Increased width from 640 to 760 for more breathing room
        width, height = self._s(760), self._s(680)
        parent.update_idletasks()
        if spawn_xy is not None:
            x, y = int(spawn_xy[0]), int(spawn_xy[1])
        else:
            gap_px = int(parent.winfo_fpixels("4m"))  # ~0.4 cm
            x = parent.winfo_x() + parent.winfo_width() + gap_px
            y = parent.winfo_y()
        max_x = max(0, self.winfo_screenwidth() - width)
        max_y = max(0, self.winfo_screenheight() - height)
        x = min(max(0, x), max_x)
        y = min(max(0, y), max_y)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)
        self.minsize(width, height)
        self.maxsize(width, height)
        self.configure(bg="#0F1115")
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))

        self.is_calibrating = False
        self.mouse_listener = None
        self.keyboard_listener = None
        self._pynput = None
        self._calibration_mode = "none"  # "pynput" | "poll"
        self._calibration_poll_job = None
        self.current_x = 0
        self.current_y = 0
        self._theme = {
            "bg": "#0F1115",
            "panel_alt": "#341616",
            "border": "#5D2E34",
            "text": "#E7EAF0",
            "text_muted": "#B8A9AE",
            "value": "#F7E5B1",
            "ok": "#8FE0B0",
            "warn": "#F5D07A",
            "error": "#E38790",
        }
        self._bg_source_image = None
        self._bg_photo = None
        self._bg_canvas_item = None

        self._canvas = tk.Canvas(self, bg=self._theme["bg"], highlightthickness=0, bd=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", self._on_canvas_resize_background)
        self._title_item = self._canvas.create_text(
            0,
            0,
            text="Calibrate",
            fill=self._theme["text"],
            font=("Segoe UI", 24, "bold"),
            anchor="n",
        )
        self._divider_item = None
        self._divider_glow_item = None
        self._capture_title_item = None
        self._capture_underline_item = None
        self._verify_title_item = None
        self._verify_underline_item = None
        self._select_label_item = None
        self._dropdown_window = None
        self._capture_panel_item = None
        self._capture_panel_title_item = None
        self._x_label_item = None
        self._instruction_item = None
        self._x_value_item = None
        self._y_label_item = None
        self._y_value_item = None
        self._test_label_item = None
        self._test_dropdown_window = None
        self._status_panel_item = None
        self._status_panel_title_item = None
        self._footer_item = None
        self._calibrate_button_name = "calibrate"
        self._test_button_name = "test_click"
        self._canvas_buttons = {}
        self._canvas_button_order = []
        self._button_skin_cache = {}

        self._setup_ui()
        self._load_background_image()
        self.bind("<Configure>", self._on_resize_background)
        self.after(30, self._refresh_scene)
        self.after(120, self._refresh_scene)
        self._update_calibration_capabilities()

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _load_background_image(self):
        self._bg_source_image = None
        for path in (_image_path("background"), _image_path("background.png")):
            if not os.path.exists(path):
                continue
            try:
                with Image.open(path) as image:
                    self._bg_source_image = image.convert("RGB")
                    return
            except Exception:
                continue

    def _on_resize_background(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._refresh_scene()

    def _on_canvas_resize_background(self, event=None):
        if event is not None and event.widget is not self._canvas:
            return
        self._refresh_scene()

    def _refresh_scene(self):
        self._refresh_background()
        self._layout_scene()

    def _apply_content_minsize(self):
        # Calibration window uses fixed geometry to avoid feedback loops
        # between dynamic content fitting and configure-driven relayout.
        return

    def _refresh_background(self):
        if self._bg_source_image is None:
            return
        width = max(2, self._canvas.winfo_width())
        height = max(2, self._canvas.winfo_height())
        try:
            fitted = ImageOps.fit(
                self._bg_source_image,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            self._bg_photo = ImageTk.PhotoImage(fitted)
            if self._bg_canvas_item is None:
                self._bg_canvas_item = self._canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
            else:
                self._canvas.coords(self._bg_canvas_item, 0, 0)
                self._canvas.itemconfigure(self._bg_canvas_item, image=self._bg_photo)
            self._canvas.tag_lower(self._bg_canvas_item)
        except Exception:
            pass

    def _setup_calibrate_combobox_style(self):
        c = self._theme
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        try:
            style.configure(
                "CalibrateFire.TCombobox",
                fieldbackground=c["panel_alt"],
                background=c["panel_alt"],
                foreground=c["text"],
                bordercolor=c["border"],
                lightcolor=c["border"],
                darkcolor=c["border"],
                arrowcolor=c["text"],
                borderwidth=1,
                padding=4,
            )
            style.map(
                "CalibrateFire.TCombobox",
                fieldbackground=[("readonly", "#341616")],
                background=[("readonly", "#341616")],
                foreground=[("readonly", c["text"])],
            )
        except Exception:
            # Fallback for Tk builds that do not accept extended combobox style keys.
            try:
                style.configure(
                    "CalibrateFire.TCombobox",
                    fieldbackground=c["panel_alt"],
                    background=c["panel_alt"],
                    foreground=c["text"],
                )
                style.map(
                    "CalibrateFire.TCombobox",
                    fieldbackground=[("readonly", "#341616")],
                    background=[("readonly", "#341616")],
                    foreground=[("readonly", c["text"])],
                )
            except Exception:
                pass

    def _resolve_button_skins(self, style_name: str, body_width: int | None = None, body_height: int | None = None):
        parent_ui = getattr(self, "master", None)
        if parent_ui is not None and body_width and body_height:
            key = (style_name, int(body_width), int(body_height))
            cached = self._button_skin_cache.get(key)
            if cached:
                return cached
            render_skin = getattr(parent_ui, "_render_button_skin", None)
            if callable(render_skin):
                specs = {
                    "Primary.TButton": {
                        "normal": ("#2FC07B", "#1F7F4F", "#6AE5A8", "#4DDC98"),
                        "hover": ("#3AD58A", "#23975A", "#86EDBC", "#64E3AA"),
                        "pressed": ("#1A6E43", "#145938", "#4FC087", "#2EA86D"),
                        "disabled": ("#3C4B47", "#2C3835", "#55655F", "#3F504A"),
                    },
                    "Secondary.TButton": {
                        "normal": ("#3B4D74", "#24324D", "#6078A6", "#5E77A8"),
                        "hover": ("#47608D", "#2B3C5C", "#7E98C6", "#728EBE"),
                        "pressed": ("#253753", "#1C2940", "#4B628A", "#425A84"),
                        "disabled": ("#39414F", "#2C3442", "#546078", "#46526A"),
                    },
                    "Destructive.TButton": {
                        "normal": ("#7D3F4A", "#5A2B33", "#A96673", "#985A66"),
                        "hover": ("#92505C", "#6A343E", "#C07E89", "#AF707B"),
                        "pressed": ("#5F2E37", "#4A232A", "#8F5560", "#7E474F"),
                        "disabled": ("#4A3E42", "#3A2F34", "#66575D", "#564A50"),
                    },
                }
                spec = specs.get(style_name) or specs["Secondary.TButton"]
                radius = max(8, int(int(body_height) * 0.28))
                skins = {}
                for state_name in ("normal", "hover", "pressed", "disabled"):
                    top, bottom, border, glow = spec[state_name]
                    skins[state_name] = render_skin(int(body_width), int(body_height), radius, top, bottom, border, glow)
                self._button_skin_cache[key] = skins
                return skins
        if parent_ui is not None:
            skins_all = getattr(parent_ui, "_button_skins", {})
            skins = skins_all.get(style_name)
            if skins is None and skins_all:
                skins = next(iter(skins_all.values()))
            return skins
        return None

    def _create_canvas_button(
        self,
        name: str,
        text: str,
        command,
        style_name: str = "Secondary.TButton",
        button_width: int | None = None,
        button_height: int | None = None,
    ):
        skins = self._resolve_button_skins(style_name, button_width, button_height)
        if not skins:
            return
        tag = f"calibrate_btn_{name}"
        bg_item = self._canvas.create_image(0, 0, anchor="nw", image=skins["normal"], tags=(tag,))
        text_item = self._canvas.create_text(
            0,
            0,
            text=text,
            fill="#F2F6FF",
            font=("Segoe UI", 11, "bold"),
            anchor="center",
            tags=(tag,),
        )
        self._canvas_buttons[name] = {
            "command": command,
            "enabled": True,
            "hover": False,
            "pressed": False,
            "style": style_name,
            "body_w": int(button_width) if button_width else None,
            "body_h": int(button_height) if button_height else None,
            "skins": skins,
            "bg_item": bg_item,
            "text_item": text_item,
            "width": int(skins["normal"].width()),
            "height": int(skins["normal"].height()),
        }
        self._canvas_button_order.append(name)
        self._canvas.tag_bind(tag, "<Enter>", lambda _e, n=name: self._on_canvas_button_enter(n))
        self._canvas.tag_bind(tag, "<Leave>", lambda _e, n=name: self._on_canvas_button_leave(n))
        self._canvas.tag_bind(tag, "<ButtonPress-1>", lambda _e, n=name: self._on_canvas_button_press(n))
        self._canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, n=name: self._on_canvas_button_release(n))
        self._refresh_canvas_button_state(name)

    def _set_canvas_button_text(self, name: str, text: str):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        self._canvas.itemconfigure(btn["text_item"], text=text)

    def _set_canvas_button_style(self, name: str, style_name: str):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        skins = self._resolve_button_skins(style_name, btn.get("body_w"), btn.get("body_h"))
        if not skins:
            return
        btn["style"] = style_name
        btn["skins"] = skins
        btn["width"] = int(skins["normal"].width())
        btn["height"] = int(skins["normal"].height())
        self._refresh_canvas_button_state(name)
        self._layout_scene()

    def _set_canvas_button_enabled(self, name: str, enabled: bool):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        btn["enabled"] = bool(enabled)
        if not btn["enabled"]:
            btn["hover"] = False
            btn["pressed"] = False
        self._refresh_canvas_button_state(name)

    def _refresh_canvas_button_state(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        if not btn["enabled"]:
            state_key = "disabled"
            text_color = "#A5AFBF"
        elif btn["pressed"]:
            state_key = "pressed"
            text_color = "#FFFFFF"
        elif btn["hover"]:
            state_key = "hover"
            text_color = "#FFFFFF"
        else:
            state_key = "normal"
            text_color = "#F2F6FF"
        self._canvas.itemconfigure(btn["bg_item"], image=btn["skins"][state_key])
        self._canvas.itemconfigure(btn["text_item"], fill=text_color)

    def _on_canvas_button_enter(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        self._canvas.configure(cursor="hand2")
        btn["hover"] = True
        self._refresh_canvas_button_state(name)

    def _on_canvas_button_leave(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        self._canvas.configure(cursor="")
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_canvas_button_state(name)

    def _on_canvas_button_press(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        btn["pressed"] = True
        self._refresh_canvas_button_state(name)

    def _on_canvas_button_release(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        should_fire = bool(btn["enabled"] and btn["pressed"] and btn["hover"])
        btn["pressed"] = False
        self._refresh_canvas_button_state(name)
        if should_fire:
            try:
                btn["command"]()
            except Exception:
                pass

    def _set_instruction(self, text: str, color: str):
        if self._instruction_item is None:
            return
        # Keep calibration status text visually aligned with coordinate labels.
        self._canvas.itemconfigure(self._instruction_item, text=text, fill=self._theme["text"])

    def _set_test_status(self, text: str, color: str):
        if self._test_label_item is None:
            return
        self._canvas.itemconfigure(self._test_label_item, text=text, fill="#ffffff", state="normal")

    def _layout_scene(self):
        if not self._canvas or not self._canvas.winfo_exists():
            return
        
        s = self._s
        # Get actual dimensions or fallback to expected scaled values
        cw = max(int(self._s(640)), int(self._canvas.winfo_width()))
        ch = max(int(self._s(680)), int(self._canvas.winfo_height()))
        
        scene_top = s(72)
        footer_h = s(80)
        footer_y = ch - footer_h
        scene_bottom = footer_y - s(20)
        panel_gap = s(32)

        divider_x = cw // 2
        left_x = s(40)
        right_x = divider_x + s(24)
        left_w = max(s(220), divider_x - left_x - s(24))
        right_w = max(s(220), cw - right_x - s(40))

        # Position divider
        if getattr(self, "_divider_item", None):
            self._canvas.coords(self._divider_item, divider_x, scene_top, divider_x, scene_bottom)
        if getattr(self, "_divider_glow_item", None):
            self._canvas.coords(self._divider_glow_item, divider_x - 1, scene_top, divider_x - 1, scene_bottom)

        # 1. CAPTURE Header
        capture_title_y = scene_top + s(8)
        if getattr(self, "_capture_title_item", None):
            self._canvas.coords(self._capture_title_item, left_x, capture_title_y)
        if getattr(self, "_capture_underline_item", None):
            u_y = capture_title_y + s(34)
            self._canvas.coords(self._capture_underline_item, left_x, u_y, left_x + s(110), u_y)

        # 2. VERIFY Header
        if getattr(self, "_verify_title_item", None):
            self._canvas.coords(self._verify_title_item, right_x, capture_title_y)
        if getattr(self, "_verify_underline_item", None):
            u_y = capture_title_y + s(34)
            self._canvas.coords(self._verify_underline_item, right_x, u_y, right_x + s(100), u_y)

        # Dropdowns (labels removed)
        dropdown_y = capture_title_y + s(52)
        if getattr(self, "_dropdown_window", None):
            self._canvas.coords(self._dropdown_window, left_x, dropdown_y)

        # Calibrate Button
        cal_btn = self._canvas_buttons.get(self._calibrate_button_name)
        test_btn = self._canvas_buttons.get(self._test_button_name)
        cal_y = dropdown_y + s(60)
        cal_btn_h = s(32)
        cal_x = left_x
        test_x = right_x
        if cal_btn:
            cal_btn_h = int(cal_btn["height"])
            self._canvas.coords(cal_btn["bg_item"], cal_x, cal_y)
            self._canvas.coords(cal_btn["text_item"], cal_x + cal_btn["width"] // 2, cal_y + cal_btn["height"] // 2 - 2)

        save_btn = self._canvas_buttons.get("saved")
        back_btn = self._canvas_buttons.get("back")
        if save_btn:
            btn_y = footer_y + (footer_h - save_btn["height"]) // 2
        elif back_btn:
            btn_y = footer_y + (footer_h - back_btn["height"]) // 2
        else:
            btn_y = footer_y + s(24)

        # Panels: equal spacing above and below
        box_y = cal_y + cal_btn_h + panel_gap
        box_bottom = btn_y - panel_gap
        box_h = max(s(80), box_bottom - box_y)
        if getattr(self, "_capture_panel_item", None):
            self._canvas.coords(self._capture_panel_item, left_x, box_y, left_x + left_w, box_y + box_h)
        if getattr(self, "_capture_panel_title_item", None):
            self._canvas.coords(self._capture_panel_title_item, left_x + s(12), box_y + s(12))
        
        # X/Y Labels
        val_y = box_y + int(box_h * 0.42)
        status_y = box_y + int(box_h * 0.72)
        if getattr(self, "_x_label_item", None):
            self._canvas.coords(self._x_label_item, left_x + s(16), val_y)
        if getattr(self, "_x_value_item", None):
            self._canvas.coords(self._x_value_item, left_x + s(42), val_y)
        if getattr(self, "_y_label_item", None):
            self._canvas.coords(self._y_label_item, left_x + left_w // 2 + s(12), val_y)
        if getattr(self, "_y_value_item", None):
            self._canvas.coords(self._y_value_item, left_x + left_w // 2 + s(38), val_y)

        # Verify side dropdown
        if getattr(self, "_test_dropdown_window", None):
            self._canvas.coords(self._test_dropdown_window, right_x, dropdown_y)
        
        if test_btn:
            self._canvas.coords(test_btn["bg_item"], test_x, cal_y)
            self._canvas.coords(test_btn["text_item"], test_x + test_btn["width"] // 2, cal_y + test_btn["height"] // 2 - 2)

        if getattr(self, "_status_panel_item", None):
            self._canvas.coords(self._status_panel_item, right_x, box_y, right_x + right_w, box_y + box_h)
        if getattr(self, "_status_panel_title_item", None):
            self._canvas.itemconfigure(self._status_panel_title_item, state="hidden")
        if getattr(self, "_instruction_item", None):
            self._canvas.coords(self._instruction_item, left_x + s(12), status_y)
            self._canvas.itemconfigure(self._instruction_item, width=max(s(140), left_w - s(24)))
        if getattr(self, "_test_label_item", None):
            self._canvas.coords(self._test_label_item, right_x + s(12), box_y + s(12))
            self._canvas.itemconfigure(self._test_label_item, width=max(s(140), right_w - s(24)))
            self._canvas.tag_raise(self._test_label_item)

        # Footer Buttons
        if save_btn and back_btn:
            self._canvas.coords(save_btn["bg_item"], left_x, btn_y)
            self._canvas.coords(
                save_btn["text_item"],
                left_x + save_btn["width"] // 2,
                btn_y + save_btn["height"] // 2 - 2,
            )
            self._canvas.coords(back_btn["bg_item"], right_x, btn_y)
            self._canvas.coords(
                back_btn["text_item"],
                right_x + back_btn["width"] // 2,
                btn_y + back_btn["height"] // 2 - 2,
            )
            
            # Raise items
            self._canvas.tag_raise(save_btn["bg_item"])
            self._canvas.tag_raise(save_btn["text_item"])
            self._canvas.tag_raise(back_btn["bg_item"])
            self._canvas.tag_raise(back_btn["text_item"])

        if getattr(self, "_footer_item", None):
            self._canvas.coords(self._footer_item, 0, footer_y, cw, ch)
            self._canvas.tag_lower(self._footer_item)

    def _setup_ui(self):
        c = self._theme
        self._setup_calibrate_combobox_style()
        self._canvas.itemconfigure(self._title_item, text="", state="hidden")

        self.button_options = [
            "keep_hand",
            "queue_button",
            "next",
            "concede",
            "attack_all",
            "opponent_avatar",
            "hand_scan_p1",
            "hand_scan_p2",
            "assign_damage_done",
            "log_out_btn",
            "log_out_ok_btn"
        ]
        self._divider_item = self._canvas.create_line(0, 0, 0, 0, fill=c["border"], width=max(1, self._s(2)))
        self._divider_glow_item = self._canvas.create_line(0, 0, 0, 0, fill="#A96673", width=1)
        self._capture_title_item = self._canvas.create_text(
            0,
            0,
            text="1. CAPTURE",
            fill=c["warn"],
            font=("Segoe UI", max(10, self._s(12)), "bold"),
            anchor="nw",
        )
        self._capture_underline_item = self._canvas.create_line(0, 0, 0, 0, fill=c["warn"], width=max(1, self._s(1)))
        self._verify_title_item = self._canvas.create_text(
            0,
            0,
            text="2. VERIFY",
            fill=c["ok"],
            font=("Segoe UI", max(10, self._s(12)), "bold"),
            anchor="nw",
        )
        self._verify_underline_item = self._canvas.create_line(0, 0, 0, 0, fill=c["ok"], width=max(1, self._s(1)))
        self._select_label_item = self._canvas.create_text(
            0,
            0,
            text="",
            fill=c["text"],
            font=("Segoe UI", max(10, self._s(12))),
            anchor="nw",
            state="hidden",
        )

        self.selected_button = tk.StringVar(value=self.button_options[0])
        self.dropdown = ttk.Combobox(
            self._canvas,
            textvariable=self.selected_button,
            values=self.button_options,
            state="readonly",
            width=20,
            style="CalibrateFire.TCombobox",
        )
        self._dropdown_window = self._canvas.create_window(0, 0, anchor="nw", window=self.dropdown)
        self.update_idletasks()
        btn_body_w = max(160, int(self.dropdown.winfo_reqwidth()))
        btn_body_h = max(26, int(self.dropdown.winfo_reqheight()))
        self._button_body_w = btn_body_w
        self._button_body_h = btn_body_h
        self._create_canvas_button(
            self._calibrate_button_name,
            "Calibrate",
            self._start_calibration,
            "Secondary.TButton",
            button_width=btn_body_w,
            button_height=btn_body_h,
        )
        self._capture_panel_item = self._canvas.create_rectangle(
            0,
            0,
            0,
            0,
            fill=c["panel_alt"],
            outline=c["border"],
            stipple="gray50",
        )
        self._capture_panel_title_item = self._canvas.create_text(
            0,
            0,
            text="COORDINATES:",
            fill=c["text"],
            font=("Segoe UI", max(8, self._s(9)), "bold"),
            anchor="nw",
        )
        self._x_label_item = self._canvas.create_text(0, 0, text="X:", fill=c["text"], font=("Segoe UI", max(11, self._s(13)), "bold"), anchor="w")
        self._x_value_item = self._canvas.create_text(0, 0, text="0", fill=c["value"], font=("Consolas", max(12, self._s(14))), anchor="w")
        self._y_label_item = self._canvas.create_text(0, 0, text="Y:", fill=c["text"], font=("Segoe UI", max(11, self._s(13)), "bold"), anchor="w")
        self._y_value_item = self._canvas.create_text(0, 0, text="0", fill=c["value"], font=("Consolas", max(12, self._s(14))), anchor="w")

        self._instruction_item = self._canvas.create_text(
            0,
            0,
            text="Select a button and click 'Calibrate'",
            fill=c["text_muted"],
            font=("Segoe UI", max(9, self._s(11))),
            anchor="nw",
        )

        self._create_canvas_button(
            "saved",
            "Saved Buttons",
            self._show_saved_buttons,
            "Secondary.TButton",
            button_width=btn_body_w,
            button_height=btn_body_h,
        )
        self._create_canvas_button(
            "back",
            "Back",
            self.destroy,
            "Secondary.TButton",
            button_width=btn_body_w,
            button_height=btn_body_h,
        )

        self._test_label_item = self._canvas.create_text(
            0,
            0,
            text="",
            fill="#ffffff",
            font=("Segoe UI", max(10, self._s(12)), "bold"),
            anchor="nw",
            state="hidden",
        )

        self.test_button_var = tk.StringVar(value=self.button_options[0])
        self.test_dropdown = ttk.Combobox(
            self._canvas,
            textvariable=self.test_button_var,
            values=self.button_options,
            state="readonly",
            width=20,
            style="CalibrateFire.TCombobox",
        )
        self._test_dropdown_window = self._canvas.create_window(0, 0, anchor="nw", window=self.test_dropdown)
        self._create_canvas_button(
            self._test_button_name,
            "Test Click",
            self._test_saved_click,
            "Secondary.TButton",
            button_width=btn_body_w,
            button_height=btn_body_h,
        )
        self._status_panel_item = self._canvas.create_rectangle(
            0,
            0,
            0,
            0,
            fill=c["panel_alt"],
            outline=c["border"],
            stipple="gray50",
        )
        self._status_panel_title_item = self._canvas.create_text(
            0,
            0,
            text="",
            fill=c["text"],
            font=("Segoe UI", max(8, self._s(9)), "bold"),
            anchor="nw",
            state="hidden",
        )
        # self._footer_item = self._canvas.create_rectangle(0, 0, 0, 0, fill="#0B0E13", outline="")
        self._footer_item = None
        self._layout_scene()

    def _update_calibration_capabilities(self):
        c = self._theme
        # On macOS, use polling fallback by default for stability with Tk windows.
        if sys.platform == "darwin":
            self._set_canvas_button_enabled(self._calibrate_button_name, True)
            self._set_instruction("Calibration ready (macOS polling mode).", c["ok"])
            return

        # On non-macOS platforms, prefer global pynput listeners.
        can_use_pynput = True
        try:
            import pynput  # noqa: F401
        except Exception:
            can_use_pynput = False

        if not can_use_pynput:
            self._set_canvas_button_enabled(self._calibrate_button_name, True)
            self._set_instruction("pynput unavailable: using local polling mode.", c["warn"])
        else:
            self._set_canvas_button_enabled(self._calibrate_button_name, True)

        # Enable test click only when the configured backend can be initialized.
        try:
            backend = self.config_manager.get_input_backend()
            screen_bounds = self.config_manager.get_screen_bounds()
            input_controller = create_input_controller(backend)
            input_controller.configure_screen_bounds(screen_bounds)
            self._set_canvas_button_enabled(self._test_button_name, True)
        except Exception:
            self._set_canvas_button_enabled(self._test_button_name, False)

    def _start_calibration(self):
        c = self._theme
        if self.is_calibrating:
            self._stop_calibration()
            return

        self.is_calibrating = True
        self._set_canvas_button_text(self._calibrate_button_name, "Stop")
        self._set_canvas_button_style(self._calibrate_button_name, "Destructive.TButton")
        self._set_instruction("Move mouse to target. Press ENTER to save (ESC to cancel).", c["warn"])

        # macOS: avoid pynput global hooks due known instability with Tk integration.
        if sys.platform == "darwin":
            self._start_poll_calibration()
            return

        try:
            if self._pynput is None:
                from pynput import mouse, keyboard
                self._pynput = (mouse, keyboard)

            mouse, keyboard = self._pynput
            self._calibration_mode = "pynput"
            self.mouse_listener = mouse.Listener(on_move=self._on_mouse_move)
            self.mouse_listener.start()
            self.keyboard_listener = keyboard.Listener(on_press=self._on_key_press)
            self.keyboard_listener.start()
        except Exception as e:
            self._stop_calibration()
            self._set_instruction(f"Live tracking unavailable: {e}. Using polling mode.", c["warn"])
            self.is_calibrating = True
            self._set_canvas_button_text(self._calibrate_button_name, "Stop")
            self._set_canvas_button_style(self._calibrate_button_name, "Destructive.TButton")
            self._start_poll_calibration()

    def _start_poll_calibration(self):
        self._calibration_mode = "poll"
        self.bind("<Return>", self._on_local_calibration_enter)
        self.bind("<Escape>", self._on_local_calibration_escape)
        self._poll_calibration_pointer()

    def _poll_calibration_pointer(self):
        if not self.is_calibrating or self._calibration_mode != "poll":
            return
        try:
            self.current_x = int(self.winfo_pointerx())
            self.current_y = int(self.winfo_pointery())
            self._update_coordinates()
        except Exception:
            pass
        self._calibration_poll_job = self.after(40, self._poll_calibration_pointer)

    def _on_local_calibration_enter(self, _event=None):
        if self.is_calibrating:
            self._save_coordinates()

    def _on_local_calibration_escape(self, _event=None):
        if self.is_calibrating:
            self._stop_calibration()

    def _stop_calibration(self):
        c = self._theme
        self.is_calibrating = False
        self._set_canvas_button_text(self._calibrate_button_name, "Calibrate")
        self._set_canvas_button_style(self._calibrate_button_name, "Secondary.TButton")
        self._set_instruction("Select a button and click 'Calibrate'", c["text_muted"])
        self._calibration_mode = "none"

        if self._calibration_poll_job is not None:
            try:
                self.after_cancel(self._calibration_poll_job)
            except Exception:
                pass
            self._calibration_poll_job = None
        try:
            self.unbind("<Return>")
            self.unbind("<Escape>")
        except Exception:
            pass

        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
            except Exception:
                pass
            self.mouse_listener = None
        if self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
            except Exception:
                pass
            self.keyboard_listener = None

    def _on_mouse_move(self, x, y):
        self.current_x = x
        self.current_y = y
        # Update UI in main thread
        try:
            self.after(0, self._update_coordinates)
        except Exception:
            pass

    def _update_coordinates(self):
        try:
            self._canvas.itemconfigure(self._x_value_item, text=str(self.current_x))
            self._canvas.itemconfigure(self._y_value_item, text=str(self.current_y))
        except Exception:
            pass

    def _on_key_press(self, key):
        if self._pynput is None:
            return
        _, keyboard = self._pynput
        if key == keyboard.Key.enter and self.is_calibrating:
            self._save_coordinates()
        elif key == keyboard.Key.esc and self.is_calibrating:
            self._stop_calibration()

    def _save_coordinates(self):
        c = self._theme
        button_name = self.selected_button.get()
        self.config_manager.save_coordinate(button_name, self.current_x, self.current_y)
        self._stop_calibration()
        self._set_instruction(f"Saved {button_name}: ({self.current_x}, {self.current_y})", c["ok"])

    def _test_saved_click(self):
        c = self._theme
        button_name = self.test_button_var.get()
        coords = self.config_manager.get_all_coordinates()
        coord = coords.get(button_name)
        if not isinstance(coord, dict) or "x" not in coord or "y" not in coord:
            self._set_test_status(f"No saved point for '{button_name}'.", c["warn"])
            return

        x, y = int(coord["x"]), int(coord["y"])

        try:
            backend = self.config_manager.get_input_backend()
            screen_bounds = self.config_manager.get_screen_bounds()
            input_controller = create_input_controller(backend)
            input_controller.configure_screen_bounds(screen_bounds)
            input_controller.move_abs(x, y)
            input_controller.left_click(1)
            self._set_test_status(f"Test click: {button_name} ({x}, {y})", c["ok"])
        except InputControllerError as e:
            self._set_test_status(f"Test failed: {e}", c["error"])
        except Exception as e:
            self._set_test_status(f"Test failed: {e}", c["error"])

    def _show_saved_buttons(self):
        SavedButtonsWindow(self, self.config_manager)

    def destroy(self):
        callback = getattr(self, "_on_close_callback", None)
        try:
            self._stop_calibration()
            super().destroy()
        finally:
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass


class SavedButtonsWindow(tk.Toplevel):
    """Window showing all saved button coordinates"""

    def __init__(self, parent, config_manager):
        super().__init__(parent)
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self.config_manager = config_manager
        self.title("Saved Buttons")
        self.geometry(f"{self._s(380)}x{self._s(500)}")
        self.resizable(False, False)
        self.configure(bg="#2b2b2b")
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))

        self._setup_ui()
        _apply_submenu_theme(self)

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _setup_ui(self):
        # Main frame
        main_frame = tk.Frame(self, bg="#2b2b2b", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        title = tk.Label(main_frame, text="Calibrated Buttons", bg="#2b2b2b", fg="white",
                        font=("Segoe UI", 12, "bold"))
        title.pack(pady=(0, 15))

        # Scrollable list frame
        list_frame = tk.Frame(main_frame, bg="#3b3b3b")
        list_frame.pack(fill=tk.BOTH, expand=True)

        # Canvas with scrollbar
        canvas = tk.Canvas(list_frame, bg="#3b3b3b", highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#3b3b3b")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Get saved coordinates
        coords = self.config_manager.get_all_coordinates()

        if not coords:
            no_data = tk.Label(scrollable_frame, text="No buttons calibrated yet",
                              bg="#3b3b3b", fg="#aaaaaa", font=("Segoe UI", 10))
            no_data.pack(pady=20)
        else:
            for button_name in sorted(coords.keys()):
                coord = coords[button_name]
                item_frame = tk.Frame(scrollable_frame, bg="#3b3b3b", padx=10, pady=8)
                item_frame.pack(fill=tk.X)

                # Button name
                name_label = tk.Label(item_frame, text=button_name, bg="#3b3b3b", fg="white",
                                     font=("Segoe UI", 10, "bold"), anchor="w", width=15)
                name_label.pack(side=tk.LEFT)

                # Coordinates
                if isinstance(coord, dict):
                    coord_text = f"({coord.get('x', 0)}, {coord.get('y', 0)})"
                else:
                    coord_text = str(coord)
                coord_label = tk.Label(item_frame, text=coord_text, bg="#3b3b3b", fg="#00ff00",
                                       font=("Consolas", 10), anchor="e")
                coord_label.pack(side=tk.RIGHT)

                # Separator
                sep = tk.Frame(scrollable_frame, bg="#4a4a4a", height=1)
                sep.pack(fill=tk.X, padx=5)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        back_btn = ttk.Button(
            main_frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.pack(pady=(10, 0))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        back_btn = ttk.Button(
            main_frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.pack(pady=(10, 0))


class ConfigManager:
    """Manages loading and saving of calibration configuration"""

    def __init__(self, config_path: str | None = None):
        if config_path:
            self.config_path = config_path if os.path.isabs(config_path) else _app_path(config_path)
        else:
            self.config_path = str(runtime_file("config", "calibration_config.json"))
        self.config = self._load_config()

    def _detect_player_log_path(self) -> str:
        candidates: list[str] = []
        home = os.path.expanduser("~")

        if os.name == "nt":
            windows_roots: list[str] = []
            user_profile = os.environ.get("USERPROFILE", "")
            if user_profile:
                windows_roots.append(user_profile)
            if home and home not in windows_roots:
                windows_roots.append(home)
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            if local_app_data:
                app_data_root = os.path.abspath(os.path.join(local_app_data, os.pardir))
                user_root = os.path.abspath(os.path.join(app_data_root, os.pardir))
                if user_root and user_root not in windows_roots:
                    windows_roots.append(user_root)

            for base in windows_roots:
                full = os.path.join(
                    base,
                    "AppData",
                    "LocalLow",
                    "Wizards Of The Coast",
                    "MTGA",
                    "Player.log",
                )
                if os.path.isfile(full):
                    candidates.append(full)

        if sys.platform == "darwin":
            mac_candidates = [
                os.path.join(home, "Library", "Logs", "Wizards Of The Coast", "MTGA", "Player.log"),
                os.path.join(home, "Library", "Logs", "Wizards Of The Coast", "MTGA", "Player-prev.log"),
            ]
            for full in mac_candidates:
                if os.path.isfile(full):
                    candidates.append(full)

        steam_bases = [
            os.path.join(home, ".local", "share", "Steam"),
            os.path.join(home, ".steam", "steam"),
            os.path.join(home, ".steam", "root"),
            os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
        ]

        for base in steam_bases:
            compat = os.path.join(base, "steamapps", "compatdata")
            if not os.path.isdir(compat):
                continue
            for root, _dirs, files in os.walk(compat):
                if "Player.log" not in files:
                    continue
                full = os.path.join(root, "Player.log")
                if "Wizards Of The Coast/MTGA" in full:
                    candidates.append(full)

        # Linux fallback: accept any path ending in Wizards Of The Coast/MTGA/Player.log.
        if os.name != "nt":
            skip_dir_names = {
                ".cache",
                ".cargo",
                ".npm",
                ".gradle",
                "node_modules",
                ".git",
                "venv",
                ".venv",
                "Trash",
            }
            try:
                for root, dirs, files in os.walk(home, topdown=True):
                    dirs[:] = [d for d in dirs if d not in skip_dir_names]
                    if "Player.log" not in files:
                        continue
                    norm_root = root.replace("\\", "/")
                    if norm_root.endswith("/Wizards Of The Coast/MTGA"):
                        candidates.append(os.path.join(root, "Player.log"))
            except Exception:
                pass

        if not candidates:
            return ""

        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return candidates[0]

    def _load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    loaded = json.load(f)
                had_managed_accounts = bool(loaded.get("managed_accounts"))
                config = self._ensure_defaults(loaded)
                if had_managed_accounts and not config.get("managed_accounts"):
                    try:
                        with open(self.config_path, "w") as f:
                            json.dump(config, f, indent=4)
                    except Exception:
                        pass
                return config
            except (json.JSONDecodeError, IOError):
                return self._default_config()
        return self._default_config()

    def _default_config(self):
        detected_log = self._detect_player_log_path()
        return {
            "log_path": detected_log or _default_player_log_path(),
            "screen_bounds": [[0, 0], [1920, 1080]],
            "input_backend": "auto",
            "ui_windows_topmost": True,
            "ui_scale_percent": 50,
            "first_run_prereq_ack": False,
            "first_run_prereq_ack_version": 1,
            "account_switch_minutes": 0,
            "managed_accounts": [],
            "account_cycle_index": 0,
            "account_play_order": [],
            "click_targets": {
                "keep_hand": {"x": 1407, "y": 795},
                "queue_button": {"x": 1864, "y": 896},
                "next": {"x": 1909, "y": 853},
                "concede": {"x": 1286, "y": 611},
                "attack_all": {"x": 1897, "y": 848},
                "opponent_avatar": {"x": 1290, "y": 221},
                "assign_damage_done": {"x": 960, "y": 540},
                "hand_scan_points": {
                    "p1": {"x": 746, "y": 941},
                    "p2": {"x": 1816, "y": 940}
                }
            }
        }

    def _sanitize_managed_accounts_storage(self, config: dict) -> bool:
        if not isinstance(config, dict):
            return False
        current = config.get("managed_accounts", [])
        if current == []:
            return False
        config["managed_accounts"] = []
        return True

    def _ensure_defaults(self, config):
        defaults = self._default_config()

        def _merge(target, source):
            for key, value in source.items():
                if key not in target:
                    target[key] = value
                elif isinstance(value, dict) and isinstance(target.get(key), dict):
                    _merge(target[key], value)

        _merge(config, defaults)
        # Auto-heal stale log paths (e.g., copied config from another OS/user profile).
        detected_log = self._detect_player_log_path()
        current_log = str(config.get("log_path", "") or "").strip()
        if detected_log and (not current_log or not os.path.isfile(current_log)):
            config["log_path"] = detected_log
        # Remove deprecated click targets if present
        try:
            click_targets = config.get("click_targets", {})
            if isinstance(click_targets, dict) and "options_btn" in click_targets:
                click_targets.pop("options_btn", None)
            if isinstance(click_targets, dict) and "log_in_btn" in click_targets:
                click_targets.pop("log_in_btn", None)
        except Exception:
            pass
        self._sanitize_managed_accounts_storage(config)
        return config

    def _save_config(self):
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=4)

    def save_coordinate(self, button_name, x, y):
        if button_name in ["hand_scan_p1", "hand_scan_p2"]:
            # Handle hand scan points specially
            if "hand_scan_points" not in self.config["click_targets"]:
                self.config["click_targets"]["hand_scan_points"] = {}
            key = "p1" if button_name == "hand_scan_p1" else "p2"
            self.config["click_targets"]["hand_scan_points"][key] = {"x": x, "y": y}
        else:
            self.config["click_targets"][button_name] = {"x": x, "y": y}
        self._save_config()

    def get_all_coordinates(self):
        coords = {}
        for key, value in self.config.get("click_targets", {}).items():
            if key == "hand_scan_points":
                if "p1" in value:
                    coords["hand_scan_p1"] = value["p1"]
                if "p2" in value:
                    coords["hand_scan_p2"] = value["p2"]
            else:
                coords[key] = value
        return coords

    def get_click_targets(self):
        return self.config.get("click_targets", {})

    def get_log_path(self):
        return self.config.get("log_path", "")

    def set_log_path(self, path: str):
        value = str(path or "").strip()
        if not value:
            return
        self.config["log_path"] = os.path.abspath(value)
        self._save_config()

    def detect_player_log_path(self) -> str:
        return self._detect_player_log_path()

    def get_screen_bounds(self):
        bounds = self.config.get("screen_bounds", [[0, 0], [1920, 1080]])
        return tuple(tuple(b) for b in bounds)

    def get_input_backend(self):
        return self.config.get("input_backend", "auto")

    def set_input_backend(self, backend: str):
        self.config["input_backend"] = backend
        self._save_config()

    def get_account_switch_minutes(self) -> int:
        try:
            return int(self.config.get("account_switch_minutes", 0))
        except (TypeError, ValueError):
            return 0

    def get_ui_windows_topmost(self) -> bool:
        return bool(self.config.get("ui_windows_topmost", True))

    def set_ui_windows_topmost(self, enabled: bool) -> None:
        self.config["ui_windows_topmost"] = bool(enabled)
        self._save_config()

    def get_ui_scale_percent(self) -> int:
        try:
            value = int(self.config.get("ui_scale_percent", 50))
        except (TypeError, ValueError):
            value = 50
        return max(50, min(120, value))

    def set_ui_scale_percent(self, percent: int) -> None:
        try:
            value = int(percent)
        except (TypeError, ValueError):
            return
        self.config["ui_scale_percent"] = max(50, min(120, value))
        self._save_config()

    def get_first_run_prereq_ack(self) -> bool:
        return bool(self.config.get("first_run_prereq_ack", False))

    def set_first_run_prereq_ack(self, acknowledged: bool, version: int = 1) -> None:
        self.config["first_run_prereq_ack"] = bool(acknowledged)
        self.config["first_run_prereq_ack_version"] = int(version)
        self._save_config()

    def set_account_switch_minutes(self, minutes: int) -> None:
        try:
            minutes_i = int(minutes)
        except (TypeError, ValueError):
            return
        if minutes_i < 0:
            minutes_i = 0
        self.config["account_switch_minutes"] = minutes_i
        self._save_config()

    def _repo_root(self) -> str:
        return _app_root_dir()

    def _accounts_root(self) -> str:
        root = os.path.join(self._repo_root(), "Accounts")
        os.makedirs(root, exist_ok=True)
        return root

    def _account_scan_dirs(self) -> list[str]:
        dirs = [self._accounts_root(), self._repo_root()]
        cleaned = []
        seen = set()
        for path in dirs:
            full = os.path.abspath(path)
            key = full.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(full)
        return cleaned

    def _sanitize_folder_name(self, name: str) -> str:
        cleaned = []
        for ch in (name or "").strip():
            if ch.isalnum() or ch in ("_", "-"):
                cleaned.append(ch)
            elif ch == " ":
                cleaned.append(" ")
            else:
                cleaned.append("_")
        candidate = "".join(cleaned).strip("._-")
        return candidate or "account"

    def _next_unique_folder_name(self, desired: str, used: set[str]) -> str:
        if desired not in used:
            return desired
        i = 2
        while True:
            trial = f"{desired}_{i}"
            if trial not in used:
                return trial
            i += 1

    def _load_managed_accounts_from_dirs(self) -> list[dict]:
        accounts = []
        seen_folders = set()
        try:
            for base_dir in self._account_scan_dirs():
                if not os.path.isdir(base_dir):
                    continue
                for entry in os.listdir(base_dir):
                    full = os.path.join(base_dir, entry)
                    if not os.path.isdir(full):
                        continue
                    entry_key = entry.casefold()
                    if entry_key in seen_folders:
                        continue
                    creds_path = os.path.join(full, "credentials.json")
                    if not os.path.isfile(creds_path):
                        continue
                    try:
                        with open(creds_path, "r", encoding="utf-8") as f:
                            payload = json.load(f)
                    except Exception:
                        continue
                    if not isinstance(payload, dict) or not payload:
                        continue
                    account_name = str(next(iter(payload.keys()))).strip()
                    details = payload.get(account_name, {})
                    if not isinstance(details, dict):
                        continue
                    email = str(details.get("email", "")).strip()
                    pw = str(details.get("pw", "")).strip()
                    if not account_name or not email or not pw:
                        continue
                    accounts.append(
                        {
                            "name": account_name,
                            "email": email,
                            "pw": pw,
                            "folder": entry,
                        }
                    )
                    seen_folders.add(entry_key)
        except Exception:
            return []
        accounts.sort(key=lambda item: str(item.get("name", "")).casefold())
        return accounts[:10]

    def _remove_account_credentials(self, folder: str) -> None:
        folder_name = str(folder or "").strip()
        if not folder_name:
            return
        for base_dir in self._account_scan_dirs():
            creds_path = os.path.join(base_dir, folder_name, "credentials.json")
            try:
                if os.path.isfile(creds_path):
                    os.remove(creds_path)
            except Exception:
                continue

    def get_managed_accounts(self) -> list[dict]:
        return self._load_managed_accounts_from_dirs()

    def save_managed_accounts(self, accounts: list[dict]) -> list[dict]:
        if not isinstance(accounts, list):
            return self.get_managed_accounts()
        normalized = []
        seen_names = set()
        existing_accounts = self.get_managed_accounts()
        existing_by_name = {
            str(acc.get("name", "")).casefold(): str(acc.get("folder", "")).strip()
            for acc in existing_accounts
            if isinstance(acc, dict) and str(acc.get("name", "")).strip()
        }
        used_folders = {str(acc.get("folder", "")).strip() for acc in existing_accounts}
        used_folders = {name for name in used_folders if name}

        for item in accounts[:10]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            email = str(item.get("email", "")).strip()
            pw = str(item.get("pw", "")).strip()
            if not name:
                continue
            if not email or not pw:
                continue
            key = name.casefold()
            if key in seen_names:
                continue
            seen_names.add(key)

            folder = str(item.get("folder", "")).strip()
            if not folder:
                folder = existing_by_name.get(key, "")
            if not folder:
                desired = self._sanitize_folder_name(name)
                folder = self._next_unique_folder_name(desired, used_folders)
            used_folders.add(folder)

            folder_path = os.path.join(self._accounts_root(), folder)
            os.makedirs(folder_path, exist_ok=True)
            creds_path = os.path.join(folder_path, "credentials.json")
            with open(creds_path, "w", encoding="utf-8") as f:
                json.dump({name: {"email": email, "pw": pw}}, f, indent=2)

            normalized.append({
                "name": name,
                "email": email,
                "pw": pw,
                "folder": folder,
            })

        active_folders = {str(acc.get("folder", "")).strip() for acc in normalized if str(acc.get("folder", "")).strip()}
        for existing in existing_accounts:
            folder = str(existing.get("folder", "")).strip()
            if not folder or folder in active_folders:
                continue
            self._remove_account_credentials(folder)

        self.config["managed_accounts"] = []
        valid = {acc["name"].casefold() for acc in normalized}
        order = [x for x in self.get_account_play_order() if x.casefold() in valid]
        self.config["account_play_order"] = order
        if len(normalized) <= 1:
            self.config["account_cycle_index"] = 0
        elif self.get_account_cycle_index() >= len(normalized):
            self.config["account_cycle_index"] = 0
        self._save_config()
        return normalized

    def get_account_cycle_index(self) -> int:
        try:
            return int(self.config.get("account_cycle_index", 0))
        except (TypeError, ValueError):
            return 0

    def set_account_cycle_index(self, index: int) -> None:
        try:
            index_i = int(index)
        except (TypeError, ValueError):
            return
        if index_i < 0:
            index_i = 0
        self.config["account_cycle_index"] = index_i
        self._save_config()

    def get_account_play_order(self) -> list[str]:
        order = self.config.get("account_play_order", [])
        if isinstance(order, list):
            return [str(item) for item in order if item]
        return []

    def set_account_play_order(self, order: list[str]) -> None:
        if not isinstance(order, list):
            return
        valid_names = {acc["name"].casefold() for acc in self.get_managed_accounts() if acc.get("name")}
        cleaned = []
        seen = set()
        for item in order:
            name = str(item).strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            if key not in valid_names:
                continue
            seen.add(key)
            cleaned.append(name)
        self.config["account_play_order"] = cleaned
        self._save_config()


class MTGBotUI(tk.Tk):
    """Main application window"""

    def __init__(self):
        super().__init__()

        self.config_manager = ConfigManager()
        if not self._ensure_player_log_path_configured():
            self.after(0, self.destroy)
            return
        if not self._ensure_runtime_prerequisites_confirmed():
            self.after(0, self.destroy)
            return
        self.title("Burning Lotus")
        self._suppress_tk_default_icon()
        self._ui_scale = self._compute_ui_scale()
        width = self._scale_value(460)
        # Reserve extra space at the bottom for the global topmost toggle.
        extra_h = self._scale_value(134)
        x, y = 18, 24
        height = self._scale_value(780) + extra_h
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)

        self.bot_running = False
        self.game = None
        self.bot_thread = None
        self.session_games = 0
        self.session_wins = 0
        self.settings_window = None
        self._last_settings_xy: tuple[int, int] | None = None
        self.ui_settings_window = None
        self.current_session_window = None
        self._controller = None
        self._switch_eta_text = self._get_configured_switch_eta_text()

        self.ui_theme = self._build_ui_theme()
        self.configure(bg=self.ui_theme["colors"]["bg"])
        self._style = ttk.Style(self)
        self._bg_source_image = None
        self._bg_photo = None
        self._bg_canvas_item = None
        self._bg_cache_size = None
        self._load_main_background_image()
        self._setup_theme_styles()
        self._setup_ui()
        self.apply_window_topmost_mode(self.config_manager.get_ui_windows_topmost())
        self._setup_stop_hotkey()

    def _ensure_player_log_path_configured(self) -> bool:
        current_log = str(self.config_manager.get_log_path() or "").strip()
        if current_log and os.path.isfile(current_log):
            return True

        detected = str(self.config_manager.detect_player_log_path() or "").strip()
        if detected and os.path.isfile(detected):
            self.config_manager.set_log_path(detected)
            return True

        messagebox.showwarning(
            "Player.log erforderlich",
            "Player.log wurde nicht automatisch gefunden.\nBitte waehle die Datei manuell aus.",
            parent=self,
        )
        while True:
            selected = filedialog.askopenfilename(
                title="MTGA Player.log auswaehlen",
                filetypes=[("Player.log", "Player.log"), ("Log files", "*.log"), ("All files", "*.*")],
                parent=self,
            )
            selected = str(selected or "").strip()
            if selected and os.path.isfile(selected):
                if os.path.basename(selected).lower() != "player.log":
                    use_anyway = messagebox.askyesno(
                        "Dateiname pruefen",
                        "Die Datei heisst nicht Player.log. Trotzdem verwenden?",
                        parent=self,
                    )
                    if not use_anyway:
                        continue
                self.config_manager.set_log_path(selected)
                return True

            retry = messagebox.askretrycancel(
                "Player.log erforderlich",
                "Ohne gueltige Player.log kann der Bot nicht starten.\nErneut auswaehlen?",
                parent=self,
            )
            if not retry:
                return False

    def _ensure_runtime_prerequisites_confirmed(self) -> bool:
        if self.config_manager.get_first_run_prereq_ack():
            return True
        text = (
            "Before first start, please apply these required settings in MTGA and your OS:\\n\\n"
            "1) MTGA language: English\\n"
            "2) Display mode: Windowed\\n"
            "3) MTGA resolution: 1920x1080\\n"
            "4) OS display scaling: 100%\\n\\n"
            "Click OK only after these settings are applied."
        )
        acknowledged = messagebox.askokcancel(
            "First Start Requirements",
            text,
            parent=self,
        )
        if acknowledged:
            self.config_manager.set_first_run_prereq_ack(True, version=1)
            return True
        return False

    def _compute_ui_scale(self) -> float:
        ref_w, ref_h = 2560.0, 1440.0
        sw = float(max(1, self.winfo_screenwidth()))
        sh = float(max(1, self.winfo_screenheight()))
        auto_scale = min(sw / ref_w, sh / ref_h)
        auto_scale = max(0.82, min(1.0, auto_scale))
        user_percent = float(self.config_manager.get_ui_scale_percent()) / 100.0
        return max(0.50, min(1.20, auto_scale * user_percent))

    @staticmethod
    def _read_window_xy(window) -> tuple[int, int]:
        try:
            geo = str(window.geometry() or "")
            if "+" in geo:
                parts = geo.split("+")
                if len(parts) >= 3:
                    return int(parts[1]), int(parts[2])
        except Exception:
            pass
        try:
            return int(window.winfo_x()), int(window.winfo_y())
        except Exception:
            return (0, 0)

    def _scale_value(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def apply_window_topmost_mode(self, enabled: bool) -> None:
        enabled_flag = bool(enabled)
        _apply_window_topmost(self, enabled_flag)
        for window in (self.settings_window, self.ui_settings_window, self.current_session_window):
            if window is None:
                continue
            try:
                if window.winfo_exists():
                    _apply_window_topmost(window, enabled_flag)
            except Exception:
                pass

    def apply_ui_scale_live(self, reopen_ui_settings: bool = False) -> None:
        was_settings_open = bool(self.settings_window and self.settings_window.winfo_exists())
        was_current_open = bool(self.current_session_window and self.current_session_window.winfo_exists())
        should_reopen_ui_settings = bool(reopen_ui_settings and was_settings_open)

        # Close subwindows so they are recreated with the new scale.
        for attr in ("ui_settings_window", "settings_window", "current_session_window"):
            window = getattr(self, attr, None)
            if window is None:
                continue
            try:
                if window.winfo_exists():
                    window.destroy()
            except Exception:
                pass
            setattr(self, attr, None)

        x = int(self.winfo_x())
        y = int(self.winfo_y())
        self._ui_scale = self._compute_ui_scale()
        width = self._scale_value(460)
        extra_h = self._scale_value(134)
        height = self._scale_value(780) + extra_h
        self.geometry(f"{width}x{height}+{x}+{y}")

        was_loading = bool(getattr(self, "_loading_visible", False))
        self.ui_theme = self._build_ui_theme()
        self.configure(bg=self.ui_theme["colors"]["bg"])
        self._bg_photo = None
        self._bg_canvas_item = None
        self._bg_cache_size = None

        old_canvas = getattr(self, "_card_canvas", None)
        if old_canvas is not None:
            try:
                if old_canvas.winfo_exists():
                    old_canvas.destroy()
            except Exception:
                pass

        self._setup_theme_styles()
        self._setup_ui()
        self._set_running_state(bool(self.bot_running))
        self._set_startup_loading(was_loading)
        self.apply_window_topmost_mode(self.config_manager.get_ui_windows_topmost())

        if was_current_open:
            self._open_current_session()
            self._update_current_session_window()
        if was_settings_open:
            self._open_settings()
            if should_reopen_ui_settings:
                self._open_ui_settings()
    def _suppress_tk_default_icon(self):
        try:
            icon_path = _image_path("ui_symbol.png")
            icon_image = Image.open(icon_path).convert("RGBA")
            icon_sizes = [16, 24, 32, 48]
            self._window_icons = []
            for px in icon_sizes:
                resized = icon_image.resize((px, px), Image.Resampling.LANCZOS)
                self._window_icons.append(ImageTk.PhotoImage(resized))
            if self._window_icons:
                self.iconphoto(True, *self._window_icons)
                return
        except Exception:
            pass
        try:
            self._blank_icon = tk.PhotoImage(width=1, height=1)
            self.iconphoto(True, self._blank_icon)
        except Exception:
            pass

    def _setup_stop_hotkey(self):
        # Global hotkey via pynput (mouse wheel).
        try:
            from pynput import mouse
        except Exception:
            mouse = None

        if mouse:
            try:
                def _on_scroll(_x, _y, _dx, dy):
                    if dy < 0:
                        self.after(0, self._stop_bot)
                self._stop_mouse_listener = mouse.Listener(on_scroll=_on_scroll)
                self._stop_mouse_listener.daemon = True
                self._stop_mouse_listener.start()
            except Exception:
                pass

    def _pick_font_family(self):
        preferred = ["Segoe UI Variable", "Segoe UI", "Inter", "Arial"]
        available = {name.lower(): name for name in tkfont.families(self)}
        for candidate in preferred:
            resolved = available.get(candidate.lower())
            if resolved:
                return resolved
        return "TkDefaultFont"

    def _build_ui_theme(self):
        base_font = self._pick_font_family()
        s = self._scale_value
        title_size = max(18, s(26))
        subtitle_size = max(8, s(10))
        body_size = max(9, s(11))
        button_size = max(9, s(11))
        return {
            "colors": {
                "bg": "#0F1115",
                "surface": "#151A21",
                "surface_2": "#1B2230",
                "text": "#E7EAF0",
                "text_muted": "#9AA3B2",
                "accent": "#C8141E",
                "accent_primary": "#1F3A2D",
                "accent_hover": "#274837",
                "accent_pressed": "#1A3026",
                "accent_primary_border": "#2E5A45",
                "subtitle_green": "#8FB9A3",
                "border": "#242B36",
                "disabled_bg": "#1A202B",
                "disabled_text": "#6E7686",
                "shadow": "#0B0E13",
                "pill_bg": "#1B2230",
                "pill_border": "#30394A",
                "pill_running_bg": "#12301F",
                "pill_running_text": "#8FE0B0",
                "status_stopped_text": "#ffb02a",
            },
            "spacing": {"xs": s(8), "sm": s(12), "md": s(14), "lg": s(18), "xl": s(28), "card_pad": s(28), "outer_margin": s(20)},
            "size": {"logo": s(210), "button_width": s(30), "card_width": s(392)},
            "font": {
                "family": base_font,
                "title": (base_font, title_size, "bold"),
                "subtitle": (base_font, subtitle_size),
                "body": (base_font, body_size),
                "button": (base_font, button_size, "bold"),
            },
            "radius": {"card": s(18), "button": s(13)},
        }

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        color = color.lstrip("#")
        if len(color) != 6:
            return (0, 0, 0)
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def _mix_rgb(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
        t = max(0.0, min(1.0, t))
        return (
            int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t),
        )

    def _render_button_skin(
        self,
        width: int,
        height: int,
        radius: int,
        top_hex: str,
        bottom_hex: str,
        border_hex: str,
        glow_hex: str,
    ) -> ImageTk.PhotoImage:
        glow_pad = 6
        img_w = width + glow_pad * 2
        img_h = height + glow_pad * 2
        base = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))

        # Soft drop shadow to separate the button from busy backgrounds.
        shadow = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle(
            (glow_pad, glow_pad + 1, glow_pad + width - 1, glow_pad + height),
            radius=radius,
            fill=(0, 0, 0, 115),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(4))
        base = Image.alpha_composite(base, shadow)

        glow = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        gr, gg, gb = self._hex_to_rgb(glow_hex)
        glow_draw.rounded_rectangle(
            (glow_pad, glow_pad, glow_pad + width - 1, glow_pad + height - 1),
            radius=radius,
            outline=(gr, gg, gb, 205),
            width=2,
        )
        glow = glow.filter(ImageFilter.GaussianBlur(4))
        base = Image.alpha_composite(base, glow)

        shape_mask = Image.new("L", (width, height), 0)
        shape_mask_draw = ImageDraw.Draw(shape_mask)
        shape_mask_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)

        # Fixed button body color requested by user: #3D130E slightly more transparent.
        body_fill = Image.new("RGBA", (width, height), (61, 19, 14, 210))
        base.paste(body_fill, (glow_pad, glow_pad), shape_mask)

        rim = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        rim_draw = ImageDraw.Draw(rim)
        br, bg, bb = self._hex_to_rgb(border_hex)
        rim_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, outline=(br, bg, bb, 245), width=2)
        rim_draw.rounded_rectangle(
            (2, 2, width - 3, height - 3),
            radius=max(2, radius - 2),
            outline=(255, 255, 255, 92),
            width=1,
        )
        rim_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        rim_layer.paste(rim, (glow_pad, glow_pad), rim)
        base = Image.alpha_composite(base, rim_layer)

        sheen = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        sheen_draw = ImageDraw.Draw(sheen)
        half = max(1, height // 2)
        for y in range(half):
            alpha = int(96 * (1.0 - (y / half)))
            sheen_draw.line((2, y + 2, width - 3, y + 2), fill=(255, 255, 255, alpha))
        for y in range(half, height):
            t = (y - half) / max(1, (height - half))
            alpha = int(78 * t)
            sheen_draw.line((2, y, width - 3, y), fill=(0, 0, 0, alpha))
        sheen_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        sheen_layer.paste(sheen, (glow_pad, glow_pad), sheen)
        base = Image.alpha_composite(base, sheen_layer)

        inner = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        inner_draw = ImageDraw.Draw(inner)
        inner_draw.rounded_rectangle(
            (1, 1, width - 2, height - 2),
            radius=max(2, radius - 1),
            outline=(0, 0, 0, 45),
            width=1,
        )
        inner_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        inner_layer.paste(inner, (glow_pad, glow_pad), inner)
        base = Image.alpha_composite(base, inner_layer)

        return ImageTk.PhotoImage(base)

    def _install_button_skin_style(self, style_name: str, element_name: str, skins: dict[str, ImageTk.PhotoImage]):
        try:
            self._style.element_create(
                element_name,
                "image",
                skins["normal"],
                ("disabled", skins["disabled"]),
                ("pressed", skins["pressed"]),
                ("active", skins["hover"]),
                border=(22, 16),
                sticky="nsew",
            )
        except tk.TclError:
            pass

        self._style.layout(
            style_name,
            [
                (
                    element_name,
                    {
                        "sticky": "nsew",
                        "children": [
                            (
                                "Button.padding",
                                {
                                    "sticky": "nsew",
                                    "children": [("Button.label", {"sticky": "nsew"})],
                                },
                            )
                        ],
                    },
                )
            ],
        )
        self._style.configure(
            style_name,
            padding=(0, 0),
            borderwidth=0,
            relief="flat",
            anchor="center",
            foreground="#F2F6FF",
            font=self.ui_theme["font"]["button"],
        )
        self._style.map(
            style_name,
            foreground=[
                ("disabled", "#A5AFBF"),
                ("pressed", "#FFFFFF"),
                ("active", "#FFFFFF"),
            ],
        )

    def _setup_main_menu_button_skins(self):
        width = self._scale_value(336)
        height = self._scale_value(48)
        radius = self._scale_value(14)
        specs = {
            "Primary.TButton": {
                "element": "MainPrimaryGlow.button",
                "normal": ("#2FC07B", "#1F7F4F", "#6AE5A8", "#4DDC98"),
                "hover": ("#3AD58A", "#23975A", "#86EDBC", "#64E3AA"),
                "pressed": ("#1A6E43", "#145938", "#4FC087", "#2EA86D"),
                "disabled": ("#3C4B47", "#2C3835", "#55655F", "#3F504A"),
            },
            "Secondary.TButton": {
                "element": "MainSecondaryGlow.button",
                "normal": ("#3B4D74", "#24324D", "#6078A6", "#5E77A8"),
                "hover": ("#47608D", "#2B3C5C", "#7E98C6", "#728EBE"),
                "pressed": ("#253753", "#1C2940", "#4B628A", "#425A84"),
                "disabled": ("#39414F", "#2C3442", "#546078", "#46526A"),
            },
            "Destructive.TButton": {
                "element": "MainDangerGlow.button",
                "normal": ("#7D3F4A", "#5A2B33", "#A96673", "#985A66"),
                "hover": ("#92505C", "#6A343E", "#C07E89", "#AF707B"),
                "pressed": ("#5F2E37", "#4A232A", "#8F5560", "#7E474F"),
                "disabled": ("#4A3E42", "#3A2F34", "#66575D", "#564A50"),
            },
        }

        self._button_skins = {}
        for style_name, spec in specs.items():
            states = {}
            for state_name in ("normal", "hover", "pressed", "disabled"):
                top, bottom, border, glow = spec[state_name]
                states[state_name] = self._render_button_skin(width, height, radius, top, bottom, border, glow)
            self._button_skins[style_name] = states
            self._install_button_skin_style(style_name, spec["element"], states)

    def _create_canvas_menu_button(
        self,
        name: str,
        text: str,
        style_name: str,
        command,
        enabled: bool = True,
    ) -> None:
        skins = self._button_skins[style_name]
        tag = f"main_btn_{name}"
        bg_item = self._card_canvas.create_image(0, 0, anchor="n", image=skins["normal"], tags=(tag,))
        text_item = self._card_canvas.create_text(
            0,
            0,
            text=text,
            fill="#F2F6FF",
            font=self.ui_theme["font"]["button"],
            anchor="center",
            tags=(tag,),
        )
        self._menu_buttons[name] = {
            "style": style_name,
            "command": command,
            "enabled": bool(enabled),
            "hover": False,
            "pressed": False,
            "skins": skins,
            "bg_item": bg_item,
            "text_item": text_item,
            "width": int(skins["normal"].width()),
            "height": int(skins["normal"].height()),
        }
        self._menu_button_order.append(name)
        self._card_canvas.tag_bind(tag, "<Enter>", lambda _e, n=name: self._on_canvas_menu_button_enter(n))
        self._card_canvas.tag_bind(tag, "<Leave>", lambda _e, n=name: self._on_canvas_menu_button_leave(n))
        self._card_canvas.tag_bind(tag, "<ButtonPress-1>", lambda _e, n=name: self._on_canvas_menu_button_press(n))
        self._card_canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, n=name: self._on_canvas_menu_button_release(n))
        self._refresh_canvas_menu_button_state(name)

    def _refresh_canvas_menu_button_state(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn:
            return
        if not btn["enabled"]:
            state_key = "disabled"
            text_color = "#A5AFBF"
        elif btn["pressed"]:
            state_key = "pressed"
            text_color = "#FFFFFF"
        elif btn["hover"]:
            state_key = "hover"
            text_color = "#FFFFFF"
        else:
            state_key = "normal"
            text_color = "#F2F6FF"

        self._card_canvas.itemconfigure(btn["bg_item"], image=btn["skins"][state_key])
        self._card_canvas.itemconfigure(btn["text_item"], fill=text_color)

    def _set_canvas_menu_button_enabled(self, name: str, enabled: bool) -> None:
        btn = self._menu_buttons.get(name)
        if not btn:
            return
        btn["enabled"] = bool(enabled)
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_canvas_menu_button_state(name)

    def _on_canvas_menu_button_enter(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        self._card_canvas.configure(cursor="hand2")
        btn["hover"] = True
        self._refresh_canvas_menu_button_state(name)

    def _on_canvas_menu_button_leave(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn:
            return
        self._card_canvas.configure(cursor="")
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_canvas_menu_button_state(name)

    def _on_canvas_menu_button_press(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        btn["pressed"] = True
        self._refresh_canvas_menu_button_state(name)

    def _on_canvas_menu_button_release(self, name: str) -> None:
        btn = self._menu_buttons.get(name)
        if not btn:
            return
        should_fire = bool(btn["enabled"] and btn["pressed"] and btn["hover"])
        btn["pressed"] = False
        self._refresh_canvas_menu_button_state(name)
        if should_fire:
            try:
                btn["command"]()
            except Exception:
                pass

    def _setup_theme_styles(self):
        c = self.ui_theme["colors"]
        f = self.ui_theme["font"]
        style = self._style
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("App.TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["surface"])
        style.configure("Body.TLabel", background=c["surface"], foreground=c["text"], font=f["body"])
        style.configure("Muted.TLabel", background=c["surface"], foreground=c["text_muted"], font=f["body"])
        style.configure("Title.TLabel", background=c["surface"], foreground=c["text"], font=f["title"])
        style.configure("Subtitle.TLabel", background=c["surface"], foreground=c["subtitle_green"], font=f["subtitle"])
        style.configure("Status.TLabel", background=c["surface"], foreground=c["text_muted"], font=f["body"])
        style.configure("ETA.TLabel", background=c["surface"], foreground=c["text_muted"], font=f["body"])
        style.configure(
            "Card.Horizontal.TProgressbar",
            troughcolor=c["surface_2"],
            background=c["accent_primary"],
            bordercolor=c["border"],
            lightcolor=c["accent_hover"],
            darkcolor=c["accent_pressed"],
        )

        common_btn = {
            "font": f["button"],
            "padding": (18, 11),
            "borderwidth": 1,
            "relief": "flat",
            "focuscolor": c["surface_2"],
        }

        style.configure(
            "Primary.TButton",
            **common_btn,
            foreground=c["text"],
            background=c["accent_primary"],
            bordercolor=c["accent_primary_border"],
        )
        style.map(
            "Primary.TButton",
            background=[("pressed", c["accent_pressed"]), ("active", c["accent_hover"]), ("disabled", c["disabled_bg"])],
            foreground=[("disabled", c["disabled_text"])],
            bordercolor=[("pressed", c["accent_primary_border"]), ("active", c["accent_primary_border"]), ("disabled", c["border"])],
        )

        style.configure(
            "Secondary.TButton",
            **common_btn,
            foreground=c["text"],
            background=c["surface_2"],
            bordercolor=c["pill_border"],
        )
        style.map(
            "Secondary.TButton",
            background=[("pressed", "#202838"), ("active", "#253041"), ("disabled", c["disabled_bg"])],
            foreground=[("disabled", c["disabled_text"])],
            bordercolor=[("active", c["pill_border"]), ("pressed", c["pill_border"]), ("disabled", c["border"])],
        )

        style.configure(
            "Destructive.TButton",
            **common_btn,
            foreground=c["text"],
            background="#3A2025",
            bordercolor="#5A2A31",
        )
        style.map(
            "Destructive.TButton",
            background=[("pressed", "#311B20"), ("active", "#4A262C"), ("disabled", c["disabled_bg"])],
            foreground=[("disabled", c["disabled_text"])],
            bordercolor=[("pressed", "#5A2A31"), ("active", "#5A2A31"), ("disabled", c["border"])],
        )
        style.configure("Primary.TButton", font=f["button"])
        style.configure("Secondary.TButton", font=f["button"])
        style.configure("Destructive.TButton", font=f["button"])
        self._setup_main_menu_button_skins()

    def _load_main_background_image(self):
        self._bg_source_image = None
        bg_candidates = [
            _image_path("background"),
            _image_path("background.png"),
        ]
        for bg_path in bg_candidates:
            if not os.path.exists(bg_path):
                continue
            try:
                with Image.open(bg_path) as bg_image:
                    self._bg_source_image = bg_image.convert("RGB")
                return
            except Exception:
                continue

    def _refresh_canvas_background(self, canvas_w: int, canvas_h: int):
        if self._bg_source_image is None:
            return
        if canvas_w <= 1 or canvas_h <= 1:
            return

        target_size = (canvas_w, canvas_h)
        if self._bg_cache_size != target_size or self._bg_photo is None:
            fitted_bg = ImageOps.fit(
                self._bg_source_image,
                target_size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            self._bg_photo = ImageTk.PhotoImage(fitted_bg)
            self._bg_cache_size = target_size

        if self._bg_canvas_item is None:
            self._bg_canvas_item = self._card_canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
        else:
            self._card_canvas.coords(self._bg_canvas_item, 0, 0)
            self._card_canvas.itemconfigure(self._bg_canvas_item, image=self._bg_photo)
        self._card_canvas.tag_lower(self._bg_canvas_item)

    def _setup_ui(self):
        c = self.ui_theme["colors"]
        sp = self.ui_theme["spacing"]
        size = self.ui_theme["size"]

        # Start page content sits directly on the background canvas (no inner card panel).
        self._card_canvas = tk.Canvas(self, bg=c["surface"], highlightthickness=0, bd=0)
        self._card_canvas.pack(fill=tk.BOTH, expand=True)

        try:
            logo_path = _image_path("ui_symbol.png")
            logo_image = Image.open(logo_path).convert("RGBA")
            target_size = (size["logo"], size["logo"])
            fitted_logo = ImageOps.contain(logo_image, target_size, Image.Resampling.LANCZOS)
            composed_logo = Image.new("RGBA", target_size, (0, 0, 0, 0))
            x = (target_size[0] - fitted_logo.width) // 2
            y = (target_size[1] - fitted_logo.height) // 2
            composed_logo.alpha_composite(fitted_logo, dest=(x, y))
            self.logo_photo = ImageTk.PhotoImage(composed_logo)
            self._logo_item = self._card_canvas.create_image(0, 0, image=self.logo_photo, anchor="n")
            self._logo_fallback_item = None
        except Exception:
            self._logo_item = None
            self._logo_fallback_item = self._card_canvas.create_text(
                0,
                0,
                text="MTG",
                fill=c["text"],
                font=self.ui_theme["font"]["title"],
                anchor="n",
            )

        self._title_item = self._card_canvas.create_text(
            0,
            0,
            text="Burning Lotus",
            fill=c["text"],
            font=self.ui_theme["font"]["title"],
            anchor="n",
        )

        self._menu_buttons: dict[str, dict] = {}
        self._menu_button_order: list[str] = []
        self._create_canvas_menu_button("start", "Start Bot", "Primary.TButton", self._start_bot, enabled=True)
        self._create_canvas_menu_button("stop", "Stop Bot [Mouse Wheel]", "Destructive.TButton", self._stop_bot, enabled=False)
        self._create_canvas_menu_button("current_session", "Current Session", "Secondary.TButton", self._open_current_session, enabled=True)
        self._create_canvas_menu_button("settings", "Settings", "Secondary.TButton", self._open_settings, enabled=True)

        self._loading_text_item = self._card_canvas.create_text(
            0,
            0,
            text="Loading Carddata",
            fill=c["text_muted"],
            font=self.ui_theme["font"]["body"],
            anchor="n",
        )
        self.loading_bar = ttk.Progressbar(
            self._card_canvas,
            mode="indeterminate",
            style="Card.Horizontal.TProgressbar",
        )
        self._loading_bar_window = self._card_canvas.create_window(0, 0, anchor="n", window=self.loading_bar)
        self._loading_visible = False

        self._status_text_item = self._card_canvas.create_text(
            0,
            0,
            text="Status: not running",
            fill=c["status_stopped_text"],
            font=self.ui_theme["font"]["body"],
            anchor="n",
        )
        self._main_topmost_var = tk.BooleanVar(value=bool(self.config_manager.get_ui_windows_topmost()))
        self._main_topmost_panel_item = self._card_canvas.create_rectangle(
            0,
            0,
            0,
            0,
            fill="#320a02",
            outline="",
            width=0,
        )
        self._main_topmost_box_item = self._card_canvas.create_rectangle(
            0,
            0,
            0,
            0,
            fill="#320a02",
            outline="#ffb841",
            width=max(1, self._scale_value(1)),
        )
        self._main_topmost_tick_item = self._card_canvas.create_text(
            0,
            0,
            text="X",
            fill="#ffb841",
            font=("Segoe UI", max(10, self._scale_value(11)), "bold"),
            anchor="center",
        )
        self._main_topmost_label_item = self._card_canvas.create_text(
            0,
            0,
            text="Keep Window on Top",
            fill="#ffb841",
            font=("Segoe UI", max(9, self._scale_value(10))),
            anchor="w",
        )
        for item in (self._main_topmost_box_item, self._main_topmost_tick_item, self._main_topmost_label_item):
            self._card_canvas.tag_bind(item, "<Button-1>", self._toggle_main_topmost)
            self._card_canvas.tag_bind(item, "<Enter>", lambda _e: self._card_canvas.configure(cursor="hand2"))
            self._card_canvas.tag_bind(item, "<Leave>", lambda _e: self._card_canvas.configure(cursor=""))
        self._refresh_main_topmost_state()

        self._card_canvas.bind("<Configure>", lambda _e: self._refresh_card_layout())
        self.after(0, self._refresh_card_layout)
        self._set_startup_loading(False)
        self._set_running_state(False)

    def _refresh_card_layout(self):
        if not hasattr(self, "_card_canvas"):
            return
        sp = self.ui_theme["spacing"]
        size = self.ui_theme["size"]

        canvas_w = self._card_canvas.winfo_width()
        canvas_h = self._card_canvas.winfo_height()
        if canvas_w <= 1 or canvas_h <= 1:
            return
        self._refresh_canvas_background(canvas_w, canvas_h)

        self.update_idletasks()

        center_x = canvas_w // 2
        button_gap = self._scale_value(13)

        menu_buttons = [self._menu_buttons[name] for name in self._menu_button_order if name in self._menu_buttons]
        btn_h = max((btn["height"] for btn in menu_buttons), default=52)
        btn_w = max((btn["width"] for btn in menu_buttons), default=336)
        self._card_canvas.itemconfigure(self._loading_bar_window, width=max(240, btn_w - 14))

        title_font = tkfont.Font(font=self.ui_theme["font"]["title"])
        body_font = tkfont.Font(font=self.ui_theme["font"]["body"])
        title_h = title_font.metrics("linespace")
        body_h = body_font.metrics("linespace")
        logo_h = size["logo"] if self._logo_item is not None else title_h
        loading_bar_h = self.loading_bar.winfo_reqheight()
        footer_gap = self._scale_value(16)
        footer_h = self._scale_value(56)

        total_h = logo_h + 6 + title_h + sp["lg"]
        total_h += (btn_h * len(menu_buttons)) + (button_gap * max(0, len(menu_buttons) - 1))
        if self._loading_visible:
            total_h += body_h + sp["xs"] + loading_bar_h + sp["md"]
        total_h += sp["lg"] + body_h
        max_content_y = (canvas_h - footer_h) - footer_gap - total_h
        # Pull the whole main stack slightly upward (~1 cm) to reduce top logo whitespace.
        top_offset = int(self.winfo_fpixels("10m"))
        y = max(0, min((canvas_h - total_h) // 2, max_content_y) - top_offset)

        if self._logo_item is not None:
            self._card_canvas.coords(self._logo_item, center_x, y)
            y += logo_h + 6
            if self._logo_fallback_item is not None:
                self._card_canvas.itemconfigure(self._logo_fallback_item, state="hidden")
        else:
            if self._logo_fallback_item is not None:
                self._card_canvas.itemconfigure(self._logo_fallback_item, state="normal")
                self._card_canvas.coords(self._logo_fallback_item, center_x, y)
            y += logo_h + 6

        self._card_canvas.coords(self._title_item, center_x, y)
        y += title_h + sp["lg"]

        for idx, name in enumerate(self._menu_button_order):
            btn = self._menu_buttons.get(name)
            if not btn:
                continue
            self._card_canvas.coords(btn["bg_item"], center_x, y)
            self._card_canvas.coords(btn["text_item"], center_x, y + (btn["height"] // 2))
            is_last = idx == (len(self._menu_button_order) - 1)
            y += btn_h + (sp["lg"] if is_last else button_gap)

        if self._loading_visible:
            self._card_canvas.itemconfigure(self._loading_text_item, state="normal")
            self._card_canvas.itemconfigure(self._loading_bar_window, state="normal")
            self._card_canvas.coords(self._loading_text_item, center_x, y)
            y += body_h + sp["xs"]
            self._card_canvas.coords(self._loading_bar_window, center_x, y)
            y += loading_bar_h + sp["md"]
        else:
            self._card_canvas.itemconfigure(self._loading_text_item, state="hidden")
            self._card_canvas.itemconfigure(self._loading_bar_window, state="hidden")

        self._card_canvas.coords(self._status_text_item, center_x, y)
        footer_y1 = canvas_h - footer_h
        self._card_canvas.coords(self._main_topmost_panel_item, 0, footer_y1, canvas_w, canvas_h)
        box_size = self._scale_value(18)
        footer_center_y = footer_y1 + (footer_h // 2)
        label_font = tkfont.Font(font=("Segoe UI", max(9, self._scale_value(10))))
        label_text = "Keep Window on Top"
        label_w = max(1, label_font.measure(label_text))
        gap = self._scale_value(10)
        group_w = box_size + gap + label_w
        group_x = center_x - (group_w // 2)
        box_x1 = group_x
        box_y1 = footer_center_y - (box_size // 2)
        box_x2 = box_x1 + box_size
        box_y2 = box_y1 + box_size
        self._card_canvas.coords(self._main_topmost_box_item, box_x1, box_y1, box_x2, box_y2)
        self._card_canvas.coords(self._main_topmost_tick_item, box_x1 + (box_size // 2), box_y1 + (box_size // 2))
        self._card_canvas.coords(self._main_topmost_label_item, box_x2 + gap, footer_center_y)
        self._card_canvas.tag_raise(self._main_topmost_panel_item)
        self._card_canvas.tag_raise(self._main_topmost_box_item)
        self._card_canvas.tag_raise(self._main_topmost_tick_item)
        self._card_canvas.tag_raise(self._main_topmost_label_item)

    def _set_running_state(self, running: bool):
        c = self.ui_theme["colors"]
        if running:
            self._set_canvas_menu_button_enabled("start", False)
            self._set_canvas_menu_button_enabled("stop", True)
            self._card_canvas.itemconfigure(
                self._status_text_item,
                text="Status: Running",
                fill=c["pill_running_text"],
            )
            return

        self._set_canvas_menu_button_enabled("start", True)
        self._set_canvas_menu_button_enabled("stop", False)
        status_text = "Status: Stopped"
        self._card_canvas.itemconfigure(
            self._status_text_item,
            text=status_text,
            fill=c["status_stopped_text"],
        )
        self._switch_eta_text = self._get_configured_switch_eta_text()

    def _get_configured_switch_eta_text(self) -> str:
        minutes = 0
        try:
            minutes = int(self.config_manager.get_account_switch_minutes())
        except Exception:
            minutes = 0
        if minutes <= 0:
            return "Account switch: off"
        return f"{minutes} Min till Account Switch"

    def _set_startup_loading(self, loading: bool):
        if not hasattr(self, "loading_bar"):
            return
        if loading:
            self._loading_visible = True
            self.loading_bar.start(12)
            self._refresh_card_layout()
            return
        self._loading_visible = False
        self.loading_bar.stop()
        self._refresh_card_layout()

    def _toggle_main_topmost(self, _event=None) -> None:
        enabled = not bool(self._main_topmost_var.get())
        self._main_topmost_var.set(enabled)
        self.config_manager.set_ui_windows_topmost(enabled)
        self.apply_window_topmost_mode(enabled)
        self._refresh_main_topmost_state()
        self._card_canvas.configure(cursor="")

    def _refresh_main_topmost_state(self) -> None:
        is_enabled = bool(self._main_topmost_var.get())
        tick_state = "normal" if is_enabled else "hidden"
        self._card_canvas.itemconfigure(self._main_topmost_tick_item, state=tick_state)

    def _start_bot(self):
        if self.bot_running:
            return

        setup_ok = self._run_arena_setup_check(show_success=False)
        if not setup_ok:
            return

        self.bot_running = True
        self._set_running_state(True)
        self._set_startup_loading(True)

        # Start bot in separate thread
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()
        self._update_switch_eta()

    def _check_arena_setup(self):
        self._run_arena_setup_check(show_success=True)

    def _run_arena_setup_check(self, *, show_success: bool) -> bool:
        try:
            result = run_arena_setup_check(
                assets_dir=_app_path("assets", "assert"),
                expected_size=(1920, 1080),
                write_debug_on_fail=True,
            )
        except Exception as exc:
            messagebox.showerror(
                "Arena Setup",
                f"Arena setup check failed unexpectedly.\n\n{exc}",
                parent=self,
            )
            return False

        if result.ok:
            self._handle_arena_setup_success(result, show_success=show_success)
            return True

        self._handle_arena_setup_failure(result)
        return False

    def _handle_arena_setup_success(self, result: ArenaDetectionResult, *, show_success: bool) -> None:
        try:
            self._card_canvas.itemconfigure(
                self._status_text_item,
                text="Status: Arena Ready",
                fill=self.ui_theme["colors"]["status_stopped_text"],
            )
        except Exception:
            pass
        if not show_success:
            return

        region_text = "unknown"
        if result.region is not None:
            region_text = f"{result.region[0]},{result.region[1]} {result.region[2]}x{result.region[3]}"
        anchor_text = result.matched_anchor or "none"
        messagebox.showinfo(
            "Arena Setup",
            f"{result.message}\n\nRegion: {region_text}\nAnchor: {anchor_text}",
            parent=self,
        )

    def _handle_arena_setup_failure(self, result: ArenaDetectionResult) -> None:
        try:
            self._card_canvas.itemconfigure(
                self._status_text_item,
                text="Status: Arena Setup Failed",
                fill=self.ui_theme["colors"]["status_stopped_text"],
            )
        except Exception:
            pass

        lines = [result.message]
        lines.append("Required setup: MTGA visible with an exact windowed 1920x1080 client area and Windows display scaling set to 100%.")
        if result.debug_dir:
            lines.append(f"Debug bundle: {result.debug_dir}")
        messagebox.showerror(
            "Arena Setup",
            "\n\n".join(lines),
            parent=self,
        )

    def _run_bot(self):
        try:
            import bot_logger
            log_path = self.config_manager.get_log_path()
            click_targets = self.config_manager.get_click_targets()
            screen_bounds = self.config_manager.get_screen_bounds()
            input_backend = self.config_manager.get_input_backend()
            account_switch_minutes = self.config_manager.get_account_switch_minutes()
            account_cycle_index = self.config_manager.get_account_cycle_index()
            account_play_order = self.config_manager.get_account_play_order()
            bot_logger.log_info(
                "UI start: init controller log_path={} screen_bounds={} input_backend={} account_switch_minutes={}".format(
                    log_path,
                    screen_bounds,
                    input_backend,
                    account_switch_minutes,
                )
            )
            controller = Controller(log_path=log_path, screen_bounds=screen_bounds,
                                   click_targets=click_targets, input_backend=input_backend,
                                   account_switch_minutes=account_switch_minutes,
                                   account_cycle_index=account_cycle_index,
                                   account_play_order=account_play_order)
            self._controller = controller
            ai = DummyAI()
            self.game = Game(controller, ai)
            bot_logger.log_info("UI start: game.start() begin")
            self.game.start()
            bot_logger.log_info("UI start: game.start() completed")
            self.after(0, lambda: self._set_startup_loading(False))

            # Wrap match end callback so we can update session stats and still restart games.
            def _on_match_end(won=None):
                self.session_games += 1
                if won is True:
                    self.session_wins += 1
                self.after(0, self._update_current_session_window)
                try:
                    if self.game:
                        self.game.on_match_end(won)
                except TypeError:
                    if self.game:
                        self.game.on_match_end()

            controller.set_match_end_callback(_on_match_end)

            # Keep running while bot is active
            while self.bot_running:
                import time
                time.sleep(1)

        except Exception as e:
            err_msg = str(e)
            try:
                import bot_logger
                bot_logger.log_error(f"UI start failed: {err_msg}")
            except Exception:
                pass
            self.after(0, lambda msg=err_msg: self._handle_bot_error(msg))

    def _handle_bot_error(self, error_msg):
        self._stop_bot()
        messagebox.showerror("Bot Error", f"An error occurred:\n{error_msg}")

    def _stop_bot(self):
        self.bot_running = False

        if self.game:
            try:
                self.game.stop()
            except:
                pass
            self.game = None

        self._set_running_state(False)
        self._set_startup_loading(False)
        self._controller = None

    def _open_calibration(self):
        CalibrationWindow(self, self.config_manager)

    def _open_calibration_window(self, spawn_xy: tuple[int, int] | None = None, on_close=None):
        CalibrationWindow(
            self,
            self.config_manager,
            spawn_xy=spawn_xy,
            on_close=on_close,
        )

    def _open_current_session(self):
        gap_px = int(self.winfo_fpixels("5m"))
        self.update_idletasks()
        target_x = int(self.winfo_x())
        target_y = int(self.winfo_rooty() + self.winfo_height() + gap_px)
        if self.current_session_window and self.current_session_window.winfo_exists():
            try:
                w = int(self.current_session_window.winfo_width() or self.current_session_window.winfo_reqwidth())
                h = int(self.current_session_window.winfo_height() or self.current_session_window.winfo_reqheight())
                max_x = max(0, self.winfo_screenwidth() - w)
                max_y = max(0, self.winfo_screenheight() - h)
                x = min(max(0, target_x), max_x)
                y = min(max(0, target_y), max_y)
                self.current_session_window.geometry(f"{w}x{h}+{x}+{y}")
            except Exception:
                pass
            self.current_session_window.lift()
            self.current_session_window.focus_force()
            return
        self.current_session_window = CurrentSessionWindow(
            self,
            self.session_games,
            self.session_wins,
            spawn_xy=(target_x, target_y),
        )
        self.current_session_window.update_stats(self.session_games, self.session_wins, self._switch_eta_text)
        self.apply_window_topmost_mode(self.config_manager.get_ui_windows_topmost())

    def _update_current_session_window(self):
        if self.current_session_window and self.current_session_window.winfo_exists():
            self.current_session_window.update_stats(self.session_games, self.session_wins, self._switch_eta_text)

    def _open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        self.settings_window = SettingsWindow(self, self.config_manager)
        try:
            self._last_settings_xy = self._read_window_xy(self.settings_window)
            self.settings_window.bind(
                "<Configure>",
                lambda _e: setattr(
                    self,
                    "_last_settings_xy",
                    self._read_window_xy(self.settings_window),
                ),
                add="+",
            )
        except Exception:
            pass
        self.apply_window_topmost_mode(self.config_manager.get_ui_windows_topmost())

    def _open_ui_settings(self, spawn_xy: tuple[int, int] | None = None, on_close=None):
        if self.ui_settings_window and self.ui_settings_window.winfo_exists():
            if spawn_xy is not None:
                try:
                    x, y = int(spawn_xy[0]), int(spawn_xy[1])
                    w = int(self.ui_settings_window.winfo_width() or self.ui_settings_window.winfo_reqwidth())
                    h = int(self.ui_settings_window.winfo_height() or self.ui_settings_window.winfo_reqheight())
                    max_x = max(0, self.winfo_screenwidth() - w)
                    max_y = max(0, self.winfo_screenheight() - h)
                    x = min(max(0, x), max_x)
                    y = min(max(0, y), max_y)
                    self.ui_settings_window.geometry(f"{w}x{h}+{x}+{y}")
                except Exception:
                    pass
            if on_close is not None:
                try:
                    self.ui_settings_window._on_close_callback = on_close
                except Exception:
                    pass
            self.ui_settings_window.lift()
            self.ui_settings_window.focus_force()
            return
        self.ui_settings_window = UISettingsWindow(
            self,
            self.config_manager,
            spawn_xy=spawn_xy,
            on_close=on_close,
        )
        self.apply_window_topmost_mode(self.config_manager.get_ui_windows_topmost())

    def _update_switch_eta(self):
        if not self.bot_running:
            return
        minutes = 0
        interval_minutes = self.config_manager.get_account_switch_minutes()
        try:
            if getattr(self, "_controller", None):
                remaining_sec = self._controller.get_account_switch_remaining_sec()
                minutes = int((remaining_sec + 59) / 60) if remaining_sec > 0 else 0
                interval_minutes = self._controller.get_account_switch_interval_minutes()
        except Exception:
            minutes = 0
        if interval_minutes <= 0:
            self._switch_eta_text = "Account switch: off"
        else:
            self._switch_eta_text = f"{minutes} Min till Account Switch"
        self._update_current_session_window()
        self.after(10000, self._update_switch_eta)


class CurrentSessionWindow(tk.Toplevel):
    def __init__(self, parent, games: int, wins: int, spawn_xy: tuple[int, int] | None = None):
        super().__init__(parent)
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self.title("Current Session")
        width, height = self._s(460), self._s(320)
        parent.update_idletasks()
        if spawn_xy is not None:
            x, y = int(spawn_xy[0]), int(spawn_xy[1])
        else:
            gap_px = int(parent.winfo_fpixels("5m"))  # ~5 mm
            x = parent.winfo_x()
            y = parent.winfo_rooty() + parent.winfo_height() + gap_px
        max_x = max(0, self.winfo_screenwidth() - width)
        max_y = max(0, self.winfo_screenheight() - height)
        x = min(max(0, x), max_x)
        y = min(y, max_y)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)
        self.configure(bg="#0F1115")
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))
        self._theme = {
            "bg": "#0F1115",
            "text": "#E7EAF0",
            "text_muted": "#B8A9AE",
            "value": "#F7E5B1",
            "card_bg": "#320a02",
            "card_border": "#ff9318",
            "card_body": "#ffb841",
        }
        self._bg_source_image = None
        self._bg_photo = None
        self._bg_canvas_item = None
        self._stats_panel_photo = None
        self._stats_panel_size = (0, 0)
        self._canvas = tk.Canvas(self, bg=self._theme["bg"], highlightthickness=0, bd=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", self._on_canvas_resize_background)

        self._stats_panel_item = self._canvas.create_image(0, 0, anchor="nw")
        self._stats_text_item = self._canvas.create_text(
            0,
            0,
            text="",
            font=("Segoe UI", 11),
            anchor="nw",
            justify="left",
            fill=self._theme["card_body"],
        )
        self._back_btn = None

        self._load_background_image()
        self.bind("<Configure>", self._on_resize_background)
        self._create_back_button()

        self.update_stats(games, wins, "Account switch: off")
        self.after(40, self._refresh_scene)
        self.after(160, self._refresh_scene)

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _load_background_image(self):
        self._bg_source_image = None
        for path in (_image_path("background"), _image_path("background.png")):
            if not os.path.exists(path):
                continue
            try:
                with Image.open(path) as image:
                    self._bg_source_image = image.convert("RGB")
                    return
            except Exception:
                continue

    def _on_resize_background(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._refresh_scene()

    def _on_canvas_resize_background(self, event=None):
        if event is not None and event.widget is not self._canvas:
            return
        self._refresh_scene()

    def _refresh_scene(self):
        self._refresh_background()
        self._layout_scene()

    def _refresh_background(self):
        if self._bg_source_image is None:
            return
        width = max(2, self._canvas.winfo_width())
        height = max(2, self._canvas.winfo_height())
        try:
            fitted = ImageOps.fit(
                self._bg_source_image,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            self._bg_photo = ImageTk.PhotoImage(fitted)
            if self._bg_canvas_item is None:
                self._bg_canvas_item = self._canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
            else:
                self._canvas.coords(self._bg_canvas_item, 0, 0)
                self._canvas.itemconfigure(self._bg_canvas_item, image=self._bg_photo)
            self._canvas.tag_lower(self._bg_canvas_item)
        except Exception:
            pass

    def _create_back_button(self):
        parent_ui = getattr(self, "master", None)
        skins = None
        if parent_ui is not None:
            skins_all = getattr(parent_ui, "_button_skins", {})
            skins = skins_all.get("Secondary.TButton")
            if skins is None and skins_all:
                skins = next(iter(skins_all.values()))
        if not skins:
            return

        tag = "current_session_back_btn"
        bg_item = self._canvas.create_image(0, 0, anchor="n", image=skins["normal"], tags=(tag,))
        text_item = self._canvas.create_text(
            0,
            0,
            text="Close",
            fill="#F2F6FF",
            font=("Segoe UI", 11, "bold"),
            anchor="center",
            tags=(tag,),
        )
        self._back_btn = {
            "skins": skins,
            "bg_item": bg_item,
            "text_item": text_item,
            "hover": False,
            "pressed": False,
            "enabled": True,
            "width": int(skins["normal"].width()),
            "height": int(skins["normal"].height()),
        }
        self._canvas.tag_bind(tag, "<Enter>", self._on_back_enter)
        self._canvas.tag_bind(tag, "<Leave>", self._on_back_leave)
        self._canvas.tag_bind(tag, "<ButtonPress-1>", self._on_back_press)
        self._canvas.tag_bind(tag, "<ButtonRelease-1>", self._on_back_release)
        self._refresh_back_button_state()

    def _render_stats_panel(self, width: int, height: int):
        if width <= 2 or height <= 2:
            return
        if self._stats_panel_photo is not None and self._stats_panel_size == (width, height):
            return

        panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(panel)
        # Match button skin transparency behavior (alpha 210 on dark red base).
        draw.rectangle((0, 0, width - 1, height - 1), fill=(50, 10, 2, 210), outline=(255, 147, 24, 255), width=3)
        self._stats_panel_photo = ImageTk.PhotoImage(panel)
        self._stats_panel_size = (width, height)
        self._canvas.itemconfigure(self._stats_panel_item, image=self._stats_panel_photo)

    def _refresh_back_button_state(self):
        btn = self._back_btn
        if not btn:
            return
        if not btn["enabled"]:
            state_key = "disabled"
            text_color = "#A5AFBF"
        elif btn["pressed"]:
            state_key = "pressed"
            text_color = "#FFFFFF"
        elif btn["hover"]:
            state_key = "hover"
            text_color = "#FFFFFF"
        else:
            state_key = "normal"
            text_color = "#F2F6FF"
        self._canvas.itemconfigure(btn["bg_item"], image=btn["skins"][state_key])
        self._canvas.itemconfigure(btn["text_item"], fill=text_color)

    def _on_back_enter(self, _event=None):
        btn = self._back_btn
        if not btn or not btn["enabled"]:
            return
        self._canvas.configure(cursor="hand2")
        btn["hover"] = True
        self._refresh_back_button_state()

    def _on_back_leave(self, _event=None):
        btn = self._back_btn
        if not btn:
            return
        self._canvas.configure(cursor="")
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_back_button_state()

    def _on_back_press(self, _event=None):
        btn = self._back_btn
        if not btn or not btn["enabled"]:
            return
        btn["pressed"] = True
        self._refresh_back_button_state()

    def _on_back_release(self, _event=None):
        btn = self._back_btn
        if not btn:
            return
        should_close = bool(btn["enabled"] and btn["pressed"] and btn["hover"])
        btn["pressed"] = False
        self._refresh_back_button_state()
        if should_close:
            self.destroy()

    def _layout_scene(self):
        if not self._canvas or not self._canvas.winfo_exists():
            return
        s = self._s
        cw = max(2, int(self._canvas.winfo_width()))
        box_w = min(s(408), max(s(320), cw - s(48)))
        box_x = (cw - box_w) // 2
        box_y = s(38)
        box_h = s(146)
        self._render_stats_panel(box_w, box_h)
        self._canvas.coords(self._stats_panel_item, box_x, box_y)
        self._canvas.coords(self._stats_text_item, box_x + s(20), box_y + s(16))
        self._canvas.tag_raise(self._stats_text_item)
        if self._back_btn:
            y = box_y + box_h + s(22)
            self._canvas.coords(self._back_btn["bg_item"], cw // 2, y)
            self._canvas.coords(self._back_btn["text_item"], cw // 2, y + self._back_btn["height"] // 2 - 2)

    def update_stats(self, games: int, wins: int, switch_eta_text: str | None = None):
        switch_line = switch_eta_text if switch_eta_text is not None else "Account switch: off"
        self._canvas.itemconfigure(self._stats_text_item, text=f"{switch_line}\nGames played: {games}\nWin: {wins}")
        self._layout_scene()


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, config_manager: ConfigManager):
        super().__init__(parent)
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self.title("Settings")
        width, height = self._s(460), self._s(430)
        gap_px = int(parent.winfo_fpixels("5m"))  # ~5 mm
        parent.update_idletasks()
        x = parent.winfo_x()
        y = parent.winfo_rooty() + parent.winfo_height() + gap_px
        max_y = max(0, self.winfo_screenheight() - height)
        y = min(y, max_y)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)
        self.configure(bg="#0F1115")
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))
        self._config_manager = config_manager
        self._recording = False
        self._record_ignore_first = False
        self._mouse_listener = None
        self._keyboard_listener = None
        self._playback_thread = None
        self._playback_keyboard_listener = None
        self._playback_stop_event = threading.Event()
        self._current_record_events = []
        self._records_path = str(runtime_file("records", "recorded_actions_records.json"))
        self._switch_save_job = None
        self.record_btn = None
        self.show_records_btn = None
        self._theme = {
            "bg": "#0F1115",
            "text": "#E7EAF0",
            "text_muted": "#9AA3B2",
            "button_bg": "#3D130E",
            "button_hover": "#4A1A14",
            "button_active": "#32100C",
            "button_border": "#4B628A",
            "button_border_active": "#728EBE",
        }
        self._settings_bg_source_image = None
        self._settings_bg_photo = None
        self._settings_bg_canvas_item = None
        self._settings_bg_cache_size = None
        self._settings_canvas = None
        self._title_item = None
        self._load_settings_background_image()
        self._build_settings_shell()
        self.bind("<Configure>", self._on_settings_configure)
        self.after(40, self._refresh_settings_scene)
        self.after(160, self._refresh_settings_scene)
        self.after(220, self._apply_content_minsize)

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _load_settings_background_image(self):
        self._settings_bg_source_image = None
        for path in (_image_path("background"), _image_path("background.png")):
            if not os.path.exists(path):
                continue
            try:
                with Image.open(path) as image:
                    self._settings_bg_source_image = image.convert("RGB")
                    return
            except Exception:
                continue

    def _on_settings_configure(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._refresh_settings_scene()

    def _refresh_settings_scene(self):
        self._refresh_settings_background()
        self._layout_settings_canvas()
        self._apply_content_minsize()

    def _apply_content_minsize(self):
        _fit_window_to_canvas_content(
            self,
            self._settings_canvas,
            exclude_items={self._settings_bg_canvas_item} if self._settings_bg_canvas_item else None,
            pad_x=self._s(22),
            pad_y=self._s(22),
            floor_w=self._s(340),
            floor_h=self._s(300),
        )

    def _refresh_settings_background(self):
        if not self._settings_canvas or not self._settings_canvas.winfo_exists():
            return
        width = max(2, self._settings_canvas.winfo_width())
        height = max(2, self._settings_canvas.winfo_height())
        if self._settings_bg_source_image is None:
            self._settings_canvas.configure(bg=self._theme["bg"])
            return
        try:
            target_size = (width, height)
            if self._settings_bg_photo is None or self._settings_bg_cache_size != target_size:
                fitted = ImageOps.fit(
                    self._settings_bg_source_image,
                    target_size,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
                self._settings_bg_photo = ImageTk.PhotoImage(fitted)
                self._settings_bg_cache_size = target_size
            if self._settings_bg_canvas_item is None:
                self._settings_bg_canvas_item = self._settings_canvas.create_image(
                    0, 0, anchor="nw", image=self._settings_bg_photo
                )
            else:
                self._settings_canvas.coords(self._settings_bg_canvas_item, 0, 0)
                self._settings_canvas.itemconfigure(self._settings_bg_canvas_item, image=self._settings_bg_photo)
            self._settings_canvas.tag_lower(self._settings_bg_canvas_item)
        except Exception:
            pass

    def _build_settings_shell(self):
        c = self._theme
        self._settings_canvas = tk.Canvas(
            self,
            bg=c["bg"],
            highlightthickness=0,
            bd=0,
        )
        self._settings_canvas.pack(fill=tk.BOTH, expand=True)

        self._title_item = None

        self._settings_buttons = {}
        self._settings_button_order = []
        self._create_settings_canvas_button(
            "manage",
            "Manage Accounts",
            self._open_switch_account_window,
            style_name="Secondary.TButton",
        )
        self._create_settings_canvas_button(
            "record",
            "Record Action",
            self._open_record_actions_window,
            style_name="Secondary.TButton",
        )
        self._create_settings_canvas_button(
            "calibrate",
            "Calibrate",
            self._open_advanced_fallback_window,
            style_name="Secondary.TButton",
        )
        self._create_settings_canvas_button(
            "ui",
            "User Interface",
            self._open_ui_settings_window,
            style_name="Secondary.TButton",
        )
        self._create_settings_canvas_button(
            "back",
            "Close",
            self.destroy,
            style_name="Secondary.TButton",
        )
        self._layout_settings_canvas()

    def _create_settings_canvas_button(self, name: str, text: str, command, style_name: str = "Secondary.TButton") -> None:
        parent_ui = getattr(self, "master", None)
        skins = None
        if parent_ui is not None:
            skins_all = getattr(parent_ui, "_button_skins", {})
            skins = skins_all.get(style_name)
            if skins is None and skins_all:
                skins = next(iter(skins_all.values()))
        if not skins:
            width = 336
            height = 48
            tag = f"settings_btn_{name}"
            bg_item = self._settings_canvas.create_rectangle(
                0,
                0,
                width,
                height,
                fill=self._theme["button_bg"],
                outline=self._theme["button_border"],
                width=1,
                tags=(tag,),
            )
            text_item = self._settings_canvas.create_text(
                0,
                0,
                text=text,
                fill="#F2F6FF",
                font=("Segoe UI", 11, "bold"),
                anchor="center",
                tags=(tag,),
            )
            self._settings_buttons[name] = {
                "command": command,
                "enabled": True,
                "hover": False,
                "pressed": False,
                "skins": None,
                "bg_item": bg_item,
                "text_item": text_item,
                "width": width,
                "height": height,
            }
            self._settings_button_order.append(name)
            self._settings_canvas.tag_bind(tag, "<Enter>", lambda _e, n=name: self._on_settings_button_enter(n))
            self._settings_canvas.tag_bind(tag, "<Leave>", lambda _e, n=name: self._on_settings_button_leave(n))
            self._settings_canvas.tag_bind(tag, "<ButtonPress-1>", lambda _e, n=name: self._on_settings_button_press(n))
            self._settings_canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, n=name: self._on_settings_button_release(n))
            self._refresh_settings_canvas_button_state(name)
            return

        tag = f"settings_btn_{name}"
        bg_item = self._settings_canvas.create_image(0, 0, anchor="n", image=skins["normal"], tags=(tag,))
        text_item = self._settings_canvas.create_text(
            0,
            0,
            text=text,
            fill="#F2F6FF",
            font=("Segoe UI", 11, "bold"),
            anchor="center",
            tags=(tag,),
        )
        self._settings_buttons[name] = {
            "command": command,
            "enabled": True,
            "hover": False,
            "pressed": False,
            "skins": skins,
            "bg_item": bg_item,
            "text_item": text_item,
            "width": int(skins["normal"].width()),
            "height": int(skins["normal"].height()),
        }
        self._settings_button_order.append(name)

        self._settings_canvas.tag_bind(tag, "<Enter>", lambda _e, n=name: self._on_settings_button_enter(n))
        self._settings_canvas.tag_bind(tag, "<Leave>", lambda _e, n=name: self._on_settings_button_leave(n))
        self._settings_canvas.tag_bind(tag, "<ButtonPress-1>", lambda _e, n=name: self._on_settings_button_press(n))
        self._settings_canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, n=name: self._on_settings_button_release(n))
        self._refresh_settings_canvas_button_state(name)

    def _refresh_settings_canvas_button_state(self, name: str) -> None:
        btn = self._settings_buttons.get(name)
        if not btn:
            return
        if not btn["enabled"]:
            state_key = "disabled"
            text_color = "#A5AFBF"
        elif btn["pressed"]:
            state_key = "pressed"
            text_color = "#FFFFFF"
        elif btn["hover"]:
            state_key = "hover"
            text_color = "#FFFFFF"
        else:
            state_key = "normal"
            text_color = "#F2F6FF"
        if btn["skins"]:
            self._settings_canvas.itemconfigure(btn["bg_item"], image=btn["skins"][state_key])
        else:
            fill_color = self._theme["button_bg"]
            outline_color = self._theme["button_border"]
            if not btn["enabled"]:
                fill_color = "#2A1210"
                outline_color = "#5A3F3A"
            elif btn["pressed"]:
                fill_color = self._theme["button_active"]
                outline_color = self._theme["button_border_active"]
            elif btn["hover"]:
                fill_color = self._theme["button_hover"]
                outline_color = self._theme["button_border_active"]
            self._settings_canvas.itemconfigure(btn["bg_item"], fill=fill_color, outline=outline_color)
        self._settings_canvas.itemconfigure(btn["text_item"], fill=text_color)

    def _on_settings_button_enter(self, name: str) -> None:
        btn = self._settings_buttons.get(name)
        if not btn:
            return
        self._settings_canvas.configure(cursor="hand2")
        btn["hover"] = True
        self._refresh_settings_canvas_button_state(name)

    def _on_settings_button_leave(self, name: str) -> None:
        btn = self._settings_buttons.get(name)
        if not btn:
            return
        self._settings_canvas.configure(cursor="")
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_settings_canvas_button_state(name)

    def _on_settings_button_press(self, name: str) -> None:
        btn = self._settings_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        btn["pressed"] = True
        self._refresh_settings_canvas_button_state(name)

    def _on_settings_button_release(self, name: str) -> None:
        btn = self._settings_buttons.get(name)
        if not btn:
            return
        should_fire = bool(btn["enabled"] and btn["pressed"] and btn["hover"])
        btn["pressed"] = False
        self._refresh_settings_canvas_button_state(name)
        if should_fire:
            try:
                btn["command"]()
            except Exception:
                pass

    def _layout_settings_canvas(self):
        if not hasattr(self, "_settings_canvas"):
            return
        s = self._s
        cw = max(10, int(self._settings_canvas.winfo_width()))

        if not getattr(self, "_settings_button_order", None):
            return
        x = cw // 2
        y_start = s(62)
        y_step = s(76)
        for idx, name in enumerate(self._settings_button_order):
            btn = self._settings_buttons.get(name)
            if not btn:
                continue
            y = y_start + idx * y_step
            if btn["skins"]:
                self._settings_canvas.coords(btn["bg_item"], x, y)
            else:
                w = btn["width"]
                h = btn["height"]
                self._settings_canvas.coords(btn["bg_item"], x - (w // 2), y, x + (w // 2), y + h)
            self._settings_canvas.coords(btn["text_item"], x, y + btn["height"] // 2 - 2)
    def _open_ui_settings_window(self):
        parent_ui = getattr(self, "master", None)
        if parent_ui is None or not hasattr(parent_ui, "_open_ui_settings"):
            return
        self._open_replacement_subwindow(
            lambda xy: parent_ui._open_ui_settings(
                spawn_xy=xy,
                on_close=self._restore_after_subwindow_close,
            )
        )

    def _open_advanced_fallback_window(self):
        parent_ui = getattr(self, "master", None)
        if parent_ui is None or not hasattr(parent_ui, "_open_calibration_window"):
            return
        self._open_replacement_subwindow(
            lambda xy: parent_ui._open_calibration_window(
                spawn_xy=xy,
                on_close=self._restore_after_subwindow_close,
            )
        )

    def _record_actions_prompt(self):
        if self._recording:
            self._stop_recording()
            return
        prompt = tk.Toplevel(self)
        prompt.title("Record")
        prompt.geometry(f"{self._s(280)}x{self._s(80)}")
        prompt.resizable(False, False)
        prompt.configure(bg="#2b2b2b")
        label = tk.Label(
            prompt,
            text="record action press enter",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        label.pack(expand=True)

        def _start_and_close(_event=None):
            try:
                prompt.destroy()
            finally:
                self._start_recording()

        prompt.bind("<Return>", _start_and_close)
        prompt.focus_force()
        _apply_submenu_theme(prompt)

    def _start_recording(self):
        if self._recording:
            return
        try:
            from pynput import mouse, keyboard
        except Exception as e:
            messagebox.showerror("Record", f"pynput not available: {e}")
            return
        self._recording = True
        self._record_ignore_first = True
        if self.record_btn:
            self.record_btn.config(text="Stop")
        if self.show_records_btn:
            self.show_records_btn.config(state=tk.DISABLED)
        self._current_record_events = []

        def _append_event(event: dict) -> None:
            self._current_record_events.append(event)

        def _on_click(x, y, button, pressed):
            if not pressed or not self._recording:
                return
            if self._record_ignore_first:
                self._record_ignore_first = False
                return
            _append_event({
                "type": "click",
                "ts": datetime.now(),
                "x": int(x),
                "y": int(y),
                "button": str(button).split(".")[-1],
            })

        def _on_key_press(key):
            if not self._recording:
                return False
            if key == keyboard.Key.f8:
                self.after(0, self._stop_recording)
                return False
            try:
                key_name = key.char
            except AttributeError:
                key_name = key.name if hasattr(key, "name") else str(key)
            _append_event({
                "type": "key",
                "ts": datetime.now(),
                "key": key_name,
            })

        self._mouse_listener = mouse.Listener(on_click=_on_click)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()
        self._keyboard_listener = keyboard.Listener(on_press=_on_key_press)
        self._keyboard_listener.daemon = True
        self._keyboard_listener.start()

    def _stop_recording(self):
        if not self._recording:
            return
        self._recording = False
        if self.record_btn:
            self.record_btn.config(text="Record")
        if self.show_records_btn:
            self.show_records_btn.config(state=tk.NORMAL)
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
        self._mouse_listener = None
        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
            except Exception:
                pass
        self._keyboard_listener = None
        self._prompt_record_name_and_save()

    def _show_records(self):
        RecordsWindow(self, self._records_path, self._play_record_actions)

    def _play_record_actions(self, actions: list[dict]) -> None:
        if self._recording:
            return
        if self._playback_thread and self._playback_thread.is_alive():
            return
        if not actions:
            messagebox.showinfo("Test Action", "No actions to play.")
            return

        try:
            from pynput import mouse, keyboard
        except Exception as e:
            messagebox.showerror("Test Action", f"pynput not available: {e}")
            return

        self.show_records_btn.config(state=tk.DISABLED)
        self._playback_stop_event.clear()

        def _on_playback_key(key):
            if key == keyboard.Key.f8:
                self._playback_stop_event.set()
                return False
            return True

        self._playback_keyboard_listener = keyboard.Listener(on_press=_on_playback_key)
        self._playback_keyboard_listener.daemon = True
        self._playback_keyboard_listener.start()

        def _run():
            m = mouse.Controller()
            k = keyboard.Controller()
            prev_delay = 0.0
            for ev in actions:
                if self._playback_stop_event.is_set():
                    break
                delay = float(ev.get("delay", 0.0))
                if delay > 0:
                    end_time = time.time() + delay
                    while time.time() < end_time:
                        if self._playback_stop_event.is_set():
                            break
                        time.sleep(0.05)
                    if self._playback_stop_event.is_set():
                        break
                if ev.get("type") == "click":
                    try:
                        x = int(float(ev.get("x", 0)))
                        y = int(float(ev.get("y", 0)))
                    except Exception:
                        x, y = 0, 0
                    btn_raw = (ev.get("button") or "").split(".")[-1]
                    btn = mouse.Button.left
                    if btn_raw == "right":
                        btn = mouse.Button.right
                    elif btn_raw == "middle":
                        btn = mouse.Button.middle
                    m.position = (x, y)
                    time.sleep(0.05)
                    m.press(btn)
                    time.sleep(0.05)
                    m.release(btn)
                elif ev.get("type") == "key":
                    key_name = ev.get("key", "")
                    key_obj = None
                    if len(key_name) == 1:
                        key_obj = key_name
                    else:
                        if hasattr(keyboard.Key, key_name):
                            key_obj = getattr(keyboard.Key, key_name)
                    if key_obj is not None:
                        k.press(key_obj)
                        k.release(key_obj)
                prev_delay = delay
            self.after(0, self._finish_playback)

        self._playback_thread = threading.Thread(target=_run, daemon=True)
        self._playback_thread.start()

    def _prompt_record_name_and_save(self):
        prompt = tk.Toplevel(self)
        prompt.title("Save Record")
        prompt.geometry(f"{self._s(300)}x{self._s(120)}")
        prompt.resizable(False, False)
        prompt.configure(bg="#2b2b2b")

        label = tk.Label(
            prompt,
            text="Record name",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10),
        )
        label.pack(pady=(10, 4))

        name_var = tk.StringVar(value="Account Switch")
        entry = tk.Entry(
            prompt,
            textvariable=name_var,
            bg="#1e1e1e",
            fg="white",
            insertbackground="white",
            relief=tk.FLAT,
        )
        entry.pack(padx=10, fill=tk.X)
        entry.focus_set()

        def _save_and_close(_event=None):
            name = (name_var.get() or "Account Switch").strip() or "Account Switch"
            prompt.destroy()
            self._save_record_snapshot(name)

        ok_btn = ttk.Button(
            prompt,
            text="Save",
            command=_save_and_close,
        )
        ok_btn.pack(pady=10)
        prompt.bind("<Return>", _save_and_close)
        _apply_submenu_theme(prompt)

    def _save_record_snapshot(self, name: str):
        events = list(self._current_record_events)
        if not events:
            return
        events.sort(key=lambda e: e["ts"])
        actions = []
        prev_ts = events[0]["ts"]
        for ev in events:
            delay = (ev["ts"] - prev_ts).total_seconds()
            item = {"type": ev["type"], "delay": delay, "ts": ev["ts"].isoformat()}
            if ev["type"] == "click":
                item["x"] = ev.get("x", 0)
                item["y"] = ev.get("y", 0)
                item["button"] = ev.get("button", "left")
            elif ev["type"] == "key":
                item["key"] = ev.get("key", "")
            actions.append(item)
            prev_ts = ev["ts"]

        record = {
            "name": name,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "actions": actions,
        }

        data = {"records": []}
        try:
            if os.path.exists(self._records_path):
                with open(self._records_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            data = {"records": []}

        data.setdefault("records", []).append(record)
        try:
            with open(self._records_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _finish_playback(self):
        if self._playback_keyboard_listener:
            try:
                self._playback_keyboard_listener.stop()
            except Exception:
                pass
        self._playback_keyboard_listener = None
        if hasattr(self, "test_action_btn") and self.test_action_btn:
            self.test_action_btn.config(state=tk.NORMAL)

    def _open_switch_account_window(self):
        self._open_replacement_subwindow(
            lambda xy: SwitchAccountWindow(
                self,
                self._config_manager,
                spawn_xy=xy,
                on_close=self._restore_after_subwindow_close,
            )
        )

    def _open_record_actions_window(self):
        self._open_replacement_subwindow(
            lambda xy: RecordActionsWindow(
                self,
                spawn_xy=xy,
                on_close=self._restore_after_subwindow_close,
            )
        )

    def _open_replacement_subwindow(self, opener):
        if not self.winfo_exists():
            return
        self.update_idletasks()
        geo = self.geometry()
        x = int(self.winfo_x())
        y = int(self.winfo_y())
        try:
            # Use WM geometry directly to avoid platform-dependent root offset quirks.
            if "+" in geo:
                parts = geo.split("+")
                if len(parts) >= 3:
                    x = int(parts[1])
                    y = int(parts[2])
        except Exception:
            pass
        try:
            parent_ui = getattr(self, "master", None)
            if parent_ui is not None and hasattr(parent_ui, "_last_settings_xy"):
                parent_ui._last_settings_xy = (int(x), int(y))
        except Exception:
            pass
        self.withdraw()
        try:
            opener((x, y))
        except Exception:
            self._restore_after_subwindow_close()

    def _restore_after_subwindow_close(self):
        try:
            if self.winfo_exists():
                self.deiconify()
                self.lift()
                self.focus_force()
        except Exception:
            pass

    def destroy(self):
        try:
            parent_ui = getattr(self, "master", None)
            if parent_ui is not None and hasattr(parent_ui, "_last_settings_xy"):
                parent_ui._last_settings_xy = (int(self.winfo_x()), int(self.winfo_y()))
        except Exception:
            pass
        try:
            if self._recording:
                self._stop_recording()
        finally:
            super().destroy()


class UISettingsWindow(tk.Toplevel):
    def __init__(self, parent, config_manager: ConfigManager, spawn_xy: tuple[int, int] | None = None, on_close=None):
        super().__init__(parent)
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self._config_manager = config_manager
        self._on_close_callback = on_close
        self.title("User Interface")
        width, height = self._s(460), self._s(430)
        parent.update_idletasks()
        if spawn_xy is not None:
            x, y = int(spawn_xy[0]), int(spawn_xy[1])
        else:
            gap_px = int(parent.winfo_fpixels("5m"))
            x = parent.winfo_x()
            y = parent.winfo_rooty() + parent.winfo_height() + gap_px
        max_x = max(0, self.winfo_screenwidth() - width)
        max_y = max(0, self.winfo_screenheight() - height)
        x = min(max(0, x), max_x)
        y = min(y, max_y)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)
        self.configure(bg="#0F1115")
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))

        self._theme = {
            "bg": "#0F1115",
            "text": "#E7EAF0",
            "text_muted": "#9AA3B2",
            "card_border": "#ff9318",
            "card_body": "#ffb841",
            "card_bg": "#320a02",
        }
        self._bg_source_image = None
        self._bg_photo = None
        self._bg_canvas_item = None
        self._bg_cache_size = None
        self._refresh_job = None
        self._canvas = tk.Canvas(self, bg=self._theme["bg"], highlightthickness=0, bd=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", self._on_canvas_resize)

        self._scale_var = tk.IntVar(value=self._config_manager.get_ui_scale_percent())
        self._scale_text_var = tk.StringVar(value=f"{self._scale_var.get()}%")

        self._settings_panel_item = self._canvas.create_rectangle(
            0, 0, 0, 0, fill=self._theme["card_bg"], outline=self._theme["card_border"], width=3
        )
        self._scale_label_item = self._canvas.create_text(
            0,
            0,
            text="UI Scale",
            fill=self._theme["card_body"],
            font=("Segoe UI", max(10, self._s(12)), "bold"),
            anchor="w",
        )
        self._scale_value_item = self._canvas.create_text(
            0,
            0,
            text=self._scale_text_var.get(),
            fill=self._theme["card_body"],
            font=("Segoe UI", max(10, self._s(11))),
            anchor="e",
        )
        self._slider = tk.Scale(
            self._canvas,
            from_=50,
            to=120,
            orient=tk.HORIZONTAL,
            showvalue=False,
            resolution=1,
            variable=self._scale_var,
            command=self._on_slider_change,
            bg=self._theme["card_bg"],
            fg=self._theme["card_body"],
            troughcolor="#8A4B13",
            activebackground="#3D130E",
            highlightthickness=0,
            bd=0,
            length=self._s(320),
        )
        self._slider_window = self._canvas.create_window(0, 0, anchor="w", window=self._slider)

        self._buttons = {}
        self._button_order = []
        self._create_button("save", "Save", self._save_ui_settings, "Secondary.TButton")
        self._create_button("back", "Back", self.destroy, "Secondary.TButton")

        self._load_background_image()
        self.bind("<Configure>", self._on_resize)
        self.after_idle(self._schedule_refresh)

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _on_slider_change(self, _value=None):
        text = f"{int(self._scale_var.get())}%"
        self._scale_text_var.set(text)
        if hasattr(self, "_canvas") and self._canvas and hasattr(self, "_scale_value_item"):
            try:
                self._canvas.itemconfigure(self._scale_value_item, text=text)
            except Exception:
                pass

    def _load_background_image(self):
        self._bg_source_image = None
        for path in (_image_path("background"), _image_path("background.png")):
            if not os.path.exists(path):
                continue
            try:
                with Image.open(path) as image:
                    self._bg_source_image = image.convert("RGB")
                    return
            except Exception:
                continue

    def _on_resize(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._schedule_refresh()

    def _on_canvas_resize(self, event=None):
        if event is not None and event.widget is not self._canvas:
            return
        self._schedule_refresh()

    def _schedule_refresh(self):
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after(12, self._refresh_scene)

    def _refresh_scene(self):
        self._refresh_job = None
        self._refresh_background()
        self._layout_scene()
        self._apply_content_minsize()

    def _apply_content_minsize(self):
        _fit_window_to_canvas_content(
            self,
            self._canvas,
            exclude_items={self._bg_canvas_item} if self._bg_canvas_item else None,
            pad_x=self._s(22),
            pad_y=self._s(20),
            floor_w=self._s(330),
            floor_h=self._s(270),
        )

    def _refresh_background(self):
        if self._bg_source_image is None:
            return
        width = max(2, self._canvas.winfo_width())
        height = max(2, self._canvas.winfo_height())
        try:
            target_size = (width, height)
            if self._bg_photo is None or self._bg_cache_size != target_size:
                fitted = ImageOps.fit(
                    self._bg_source_image,
                    target_size,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
                self._bg_photo = ImageTk.PhotoImage(fitted)
                self._bg_cache_size = target_size
            if self._bg_canvas_item is None:
                self._bg_canvas_item = self._canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
            else:
                self._canvas.coords(self._bg_canvas_item, 0, 0)
                self._canvas.itemconfigure(self._bg_canvas_item, image=self._bg_photo)
            self._canvas.tag_lower(self._bg_canvas_item)
        except Exception:
            pass

    def _create_button(self, name: str, text: str, command, style_name: str):
        parent_ui = getattr(self, "master", None)
        skins = None
        if parent_ui is not None:
            skins_all = getattr(parent_ui, "_button_skins", {})
            skins = skins_all.get(style_name)
            if skins is None and skins_all:
                skins = next(iter(skins_all.values()))
        if not skins:
            return
        tag = f"ui_settings_btn_{name}"
        bg_item = self._canvas.create_image(0, 0, anchor="n", image=skins["normal"], tags=(tag,))
        text_item = self._canvas.create_text(
            0, 0, text=text, fill="#F2F6FF", font=("Segoe UI", max(9, self._s(11)), "bold"), anchor="center", tags=(tag,)
        )
        self._buttons[name] = {
            "command": command,
            "enabled": True,
            "hover": False,
            "pressed": False,
            "skins": skins,
            "bg_item": bg_item,
            "text_item": text_item,
            "width": int(skins["normal"].width()),
            "height": int(skins["normal"].height()),
        }
        self._button_order.append(name)
        self._canvas.tag_bind(tag, "<Enter>", lambda _e, n=name: self._on_button_enter(n))
        self._canvas.tag_bind(tag, "<Leave>", lambda _e, n=name: self._on_button_leave(n))
        self._canvas.tag_bind(tag, "<ButtonPress-1>", lambda _e, n=name: self._on_button_press(n))
        self._canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, n=name: self._on_button_release(n))

    def _on_button_enter(self, name: str):
        btn = self._buttons.get(name)
        if not btn:
            return
        btn["hover"] = True
        self._canvas.itemconfigure(btn["bg_item"], image=btn["skins"]["hover"])

    def _on_button_leave(self, name: str):
        btn = self._buttons.get(name)
        if not btn:
            return
        btn["hover"] = False
        btn["pressed"] = False
        self._canvas.itemconfigure(btn["bg_item"], image=btn["skins"]["normal"])

    def _on_button_press(self, name: str):
        btn = self._buttons.get(name)
        if not btn:
            return
        btn["pressed"] = True
        self._canvas.itemconfigure(btn["bg_item"], image=btn["skins"]["pressed"])

    def _on_button_release(self, name: str):
        btn = self._buttons.get(name)
        if not btn:
            return
        fire = bool(btn["pressed"] and btn["hover"])
        btn["pressed"] = False
        self._canvas.itemconfigure(btn["bg_item"], image=btn["skins"]["hover"] if btn["hover"] else btn["skins"]["normal"])
        if fire:
            try:
                btn["command"]()
            except Exception:
                pass

    def _layout_scene(self):
        if not self._canvas or not self._canvas.winfo_exists():
            return
        s = self._s
        cw = max(2, self._canvas.winfo_width())
        x = cw // 2
        panel_w = min(s(408), max(s(320), cw - s(48)))
        panel_x1 = (cw - panel_w) // 2
        panel_x2 = panel_x1 + panel_w

        panel_y1 = s(72)
        panel_h = s(152)
        panel_y2 = panel_y1 + panel_h
        self._canvas.coords(self._settings_panel_item, panel_x1, panel_y1, panel_x2, panel_y2)
        self._canvas.coords(self._scale_label_item, panel_x1 + s(20), panel_y1 + s(42))
        self._canvas.coords(self._scale_value_item, panel_x2 - s(20), panel_y1 + s(42))
        self._canvas.coords(self._slider_window, panel_x1 + s(18), panel_y1 + s(78))
        self._canvas.itemconfigure(self._slider_window, width=panel_w - s(36), height=s(36))

        y = panel_y2 + s(26)
        gap = s(70)
        for name in self._button_order:
            btn = self._buttons.get(name)
            if not btn:
                continue
            self._canvas.coords(btn["bg_item"], x, y)
            self._canvas.coords(btn["text_item"], x, y + btn["height"] // 2 - 2)
            y += gap

    def _save_ui_settings(self):
        self._config_manager.set_ui_scale_percent(int(self._scale_var.get()))
        parent_ui = getattr(self, "master", None)
        if parent_ui is not None and hasattr(parent_ui, "apply_ui_scale_live"):
            try:
                parent_ui.apply_ui_scale_live(reopen_ui_settings=True)
                return
            except Exception as exc:
                messagebox.showerror("User Interface", f"Apply failed: {exc}")
                return
        messagebox.showinfo("User Interface", "Gespeichert.")

    def destroy(self):
        callback = getattr(self, "_on_close_callback", None)
        try:
            super().destroy()
        finally:
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass


class SwitchAccountWindow(tk.Toplevel):
    def __init__(
        self,
        parent: SettingsWindow,
        config_manager: ConfigManager,
        spawn_xy: tuple[int, int] | None = None,
        on_close=None,
    ):
        super().__init__(parent)
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self._parent = parent
        self._config_manager = config_manager
        self._on_close_callback = on_close
        self._theme = {
            "bg": "#0F1115",
            "panel": "#121923",
            "panel_alt": "#182231",
            "panel_hover": "#223145",
            "border": "#2E3B50",
            "widget_border": "#3A4A63",
            "table_border": "#2A3548",
            "text": "#E7EAF0",
            "text_muted": "#9AA3B2",
            "accent": "#2FC07B",
            "accent_hover": "#3AD58A",
            "accent_pressed": "#1A6E43",
            "entry_bg": "#3D130E",
            "badge_bg": "#12301F",
            "badge_text": "#8FE0B0",
            "button_bg": "#1B2230",
            "button_hover": "#253041",
            "button_active": "#202838",
            "button_border": "#4B628A",
            "button_border_active": "#728EBE",
            "row_bg": "#0F1115",
            "row_selected_bg": "#151E2B",
            "row_selected_text": "#F2F6FF",
        }
        self.title("Manage Accounts")
        width = self._s(460)
        height = min(self._s(720), max(560, self.winfo_screenheight() - 80))
        parent.update_idletasks()
        base_main = getattr(parent, "master", None)
        main_y = None
        try:
            if base_main is not None and hasattr(base_main, "winfo_y"):
                main_y = int(base_main.winfo_y())
        except Exception:
            main_y = None
        if spawn_xy is not None:
            x = int(spawn_xy[0])
            y = int(main_y if main_y is not None else spawn_xy[1])
        else:
            gap_px = int(parent.winfo_fpixels("5m"))
            x = parent.winfo_x()
            y = int(main_y if main_y is not None else (parent.winfo_rooty() + parent.winfo_height() + gap_px))
        max_x = max(0, self.winfo_screenwidth() - width)
        max_y = max(0, self.winfo_screenheight() - height)
        x = min(max(0, x), max_x)
        y = min(y, max_y)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)
        self.minsize(460, 560)
        self.configure(bg=self._theme["bg"])
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))

        self._max_accounts = 10
        self._order_slots = 10

        self._order_combos = []
        self._order_vars = []
        self._accounts_data = []
        self._table_rows = []
        self._selected_account_idx = 0
        self.account_name_var = tk.StringVar()
        self.account_email_var = tk.StringVar()
        self.account_pw_var = tk.StringVar()

        self._manage_bg_source_image = None
        self._manage_bg_photo = None
        self._bg_canvas_item = None
        self._group_panel_photos = {}
        self._group_panel_items = {}
        self._heading_bg_images = {}
        self._translucent_widgets = []
        self._content = None
        self._title_label = None
        self._accounts_title_label = None
        self._canvas = tk.Canvas(self, bg=self._theme["bg"], highlightthickness=0, bd=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", self._on_resize_manage_background)
        self._load_accounts_from_config()
        self._setup_styles()
        self._build_ui()
        self._canvas.bind("<Delete>", lambda _e: self._clear_selected_account_row())
        self._canvas.bind("<BackSpace>", lambda _e: self._clear_selected_account_row())
        self._load_manage_background_image()
        self.after(40, self._refresh_manage_background)
        self.after(160, self._refresh_manage_background)
        self.after(420, self._refresh_manage_background)
        self._populate_details_fields()
        self._refresh_accounts_table()
        self._refresh_order_choices()
        self.after(220, self._apply_content_minsize)

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _load_accounts_from_config(self):
        existing = self._config_manager.get_managed_accounts()[: self._max_accounts]
        self._accounts_data = []
        for idx in range(self._max_accounts):
            account = existing[idx] if idx < len(existing) else {}
            self._accounts_data.append(
                {
                    "name": str(account.get("name", "")),
                    "email": str(account.get("email", "")),
                    "pw": str(account.get("pw", "")),
                    "folder": str(account.get("folder", "")),
                }
            )
        for idx, account in enumerate(self._accounts_data):
            if account["name"] or account["email"] or account["pw"]:
                self._selected_account_idx = idx
                break

    def _setup_styles(self):
        c = self._theme
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "ManageFire.TCombobox",
            fieldbackground="#3D130E",
            background="#3D130E",
            foreground=c["text"],
            bordercolor="#3D130E",
            lightcolor="#3D130E",
            darkcolor="#3D130E",
            arrowcolor=c["text"],
            borderwidth=0,
            padding=0,
        )
        style.map(
            "ManageFire.TCombobox",
            fieldbackground=[("readonly", "#3D130E")],
            background=[("readonly", "#3D130E")],
            foreground=[("readonly", c["text"])],
        )

    def _load_manage_background_image(self):
        self._manage_bg_source_image = None
        candidates = [
            _image_path("background"),
            _image_path("background.png"),
        ]
        for bg_path in candidates:
            if not os.path.exists(bg_path):
                continue
            try:
                with Image.open(bg_path) as image:
                    self._manage_bg_source_image = image.convert("RGB")
                    return
            except Exception:
                continue

    def _on_resize_manage_background(self, _event=None):
        self._refresh_manage_background()
        self._apply_content_minsize()

    def _apply_content_minsize(self):
        _fit_window_to_canvas_content(
            self,
            self._canvas,
            exclude_items={self._bg_canvas_item} if self._bg_canvas_item else None,
            pad_x=18,
            pad_y=20,
            floor_w=460,
            floor_h=820,
        )

    def _fit_window_to_content_width(self):
        try:
            self.update_idletasks()
            max_right = 0
            max_bottom = 0
            for widget in self.winfo_children():
                if widget is self._background_label:
                    continue
                if not widget.winfo_ismapped():
                    continue
                right = int(widget.winfo_x()) + int(widget.winfo_width())
                bottom = int(widget.winfo_y()) + int(widget.winfo_height())
                if right > max_right:
                    max_right = right
                if bottom > max_bottom:
                    max_bottom = bottom
            if max_right <= 0:
                return
            target_width = max_right + 24
            target_height = max(900, max_bottom + 40)
            self.geometry(f"{target_width}x{target_height}")
        except Exception:
            pass

    def _refresh_manage_background(self):
        if self._manage_bg_source_image is None:
            return
        width = max(2, self._canvas.winfo_width())
        height = max(2, self._canvas.winfo_height())
        try:
            fitted = ImageOps.fit(
                self._manage_bg_source_image,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            self._manage_bg_photo = ImageTk.PhotoImage(fitted)
            if self._bg_canvas_item is None:
                self._bg_canvas_item = self._canvas.create_image(0, 0, anchor="nw", image=self._manage_bg_photo)
            else:
                self._canvas.coords(self._bg_canvas_item, 0, 0)
                self._canvas.itemconfigure(self._bg_canvas_item, image=self._manage_bg_photo)
            self._canvas.tag_lower(self._bg_canvas_item)
            for item in self._group_panel_items.values():
                self._canvas.tag_raise(item, self._bg_canvas_item)
        except Exception:
            pass

    @staticmethod
    def _hex_to_rgb(hex_color: str):
        c = (hex_color or "").lstrip("#")
        if len(c) != 6:
            return (0, 0, 0)
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def _rgb_to_hex(rgb):
        r, g, b = [max(0, min(255, int(v))) for v in rgb]
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _mix_rgb(base_rgb, sample_rgb, base_weight: float):
        t = max(0.0, min(1.0, float(base_weight)))
        return (
            int(base_rgb[0] * t + sample_rgb[0] * (1.0 - t)),
            int(base_rgb[1] * t + sample_rgb[1] * (1.0 - t)),
            int(base_rgb[2] * t + sample_rgb[2] * (1.0 - t)),
        )

    def _sample_bg_rgb_for_widget(self, image, widget):
        x0 = max(0, int(widget.winfo_rootx() - self.winfo_rootx()))
        y0 = max(0, int(widget.winfo_rooty() - self.winfo_rooty()))
        x1 = min(image.width, x0 + max(1, int(widget.winfo_width())))
        y1 = min(image.height, y0 + max(1, int(widget.winfo_height())))
        if x1 <= x0 or y1 <= y0:
            return self._hex_to_rgb(self._theme["bg"])
        region = image.crop((x0, y0, x1, y1)).convert("RGB")
        stat = ImageStat.Stat(region)
        return tuple(int(v) for v in stat.mean[:3])

    def _register_translucent(self, widget, base_key: str = "panel", base_weight: float = 0.78):
        if widget is None:
            return
        self._translucent_widgets.append((widget, base_key, base_weight))

    def _apply_translucent_backgrounds(self, fitted_image):
        if fitted_image is None:
            return
        for widget, _base_key, _weight in list(self._translucent_widgets):
            if widget is None or not widget.winfo_exists():
                continue
            try:
                sample_rgb = self._sample_bg_rgb_for_widget(fitted_image, widget)
                color = self._rgb_to_hex(sample_rgb)
                if isinstance(widget, tk.Entry):
                    widget.configure(bg=color, insertbackground=self._theme["text"])
                elif isinstance(widget, tk.Checkbutton):
                    widget.configure(bg=color, activebackground=color)
                else:
                    widget.configure(bg=color)
            except Exception:
                continue

    def _blend_heading_labels_with_background(self, fitted_image):
        return

    def _render_manage_group_panel(self, width: int, height: int):
        key = (int(width), int(height))
        cached = self._group_panel_photos.get(key)
        if cached is not None:
            return cached
        panel = Image.new("RGBA", key, (0, 0, 0, 0))
        draw = ImageDraw.Draw(panel)
        draw.rectangle((0, 0, key[0] - 1, key[1] - 1), fill=(50, 10, 2, 210), outline=(255, 147, 24, 255), width=3)
        photo = ImageTk.PhotoImage(panel)
        self._group_panel_photos[key] = photo
        return photo

    def _create_manage_group_panel(self, name: str, x: int, y: int, width: int, height: int):
        panel_photo = self._render_manage_group_panel(width, height)
        item = self._canvas.create_image(x, y, anchor="nw", image=panel_photo)
        self._group_panel_items[name] = item

    def _resolve_manage_button_skins(self, primary: bool, body_w: int, body_h: int):
        key = ("primary" if primary else "secondary", int(body_w), int(body_h))
        cache = getattr(self, "_manage_button_skin_cache", None)
        if cache is None:
            self._manage_button_skin_cache = {}
            cache = self._manage_button_skin_cache
        if key in cache:
            return cache[key]

        parent_ui = getattr(self._parent, "master", None)
        render_skin = getattr(parent_ui, "_render_button_skin", None) if parent_ui is not None else None
        if not callable(render_skin):
            return None

        if primary:
            spec = {
                "normal": ("#8A2D2D", "#5E1E1E", "#8F3A3A", "#742C2C"),
                "hover": ("#A23838", "#6F2525", "#A64646", "#8A3333"),
                "pressed": ("#6A2323", "#4F1818", "#6D2A2A", "#5A2121"),
                "disabled": ("#4A3030", "#3A2525", "#5A3A3A", "#4A2E2E"),
            }
        else:
            spec = {
                "normal": ("#6E2A2A", "#4B1D1D", "#6F3333", "#5E2626"),
                "hover": ("#873333", "#5A2323", "#884040", "#733030"),
                "pressed": ("#572121", "#3E1717", "#5A2A2A", "#4A1E1E"),
                "disabled": ("#453030", "#352424", "#564040", "#463434"),
            }

        radius = max(10, int(body_h * 0.28))
        skins = {}
        for state_name in ("normal", "hover", "pressed", "disabled"):
            top, bottom, border, glow = spec[state_name]
            skins[state_name] = render_skin(int(body_w), int(body_h), radius, top, bottom, border, glow)
        cache[key] = skins
        return skins

    def _create_manage_canvas_button(
        self,
        name: str,
        text: str,
        x: int,
        y: int,
        body_w: int,
        body_h: int,
        command,
        primary: bool,
    ):
        skins = self._resolve_manage_button_skins(primary=primary, body_w=body_w, body_h=body_h)
        if not skins:
            return
        tag = f"manage_btn_{name}"
        bg_item = self._canvas.create_image(x, y, anchor="nw", image=skins["normal"], tags=(tag,))
        text_item = self._canvas.create_text(
            x + (skins["normal"].width() // 2),
            y + (skins["normal"].height() // 2) - 2,
            text=text,
            fill="#F2F6FF",
            font=("Segoe UI", 11, "bold"),
            anchor="center",
            tags=(tag,),
        )
        self._canvas_buttons[name] = {
            "skins": skins,
            "bg_item": bg_item,
            "text_item": text_item,
            "command": command,
            "enabled": True,
            "hover": False,
            "pressed": False,
        }

        self._canvas.tag_bind(tag, "<Enter>", lambda _e, n=name: self._on_manage_button_enter(n))
        self._canvas.tag_bind(tag, "<Leave>", lambda _e, n=name: self._on_manage_button_leave(n))
        self._canvas.tag_bind(tag, "<ButtonPress-1>", lambda _e, n=name: self._on_manage_button_press(n))
        self._canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, n=name: self._on_manage_button_release(n))
        self._refresh_manage_button_state(name)

    def _refresh_manage_button_state(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        if not btn["enabled"]:
            state = "disabled"
            color = "#A5AFBF"
        elif btn["pressed"]:
            state = "pressed"
            color = "#FFFFFF"
        elif btn["hover"]:
            state = "hover"
            color = "#FFFFFF"
        else:
            state = "normal"
            color = "#F2F6FF"
        self._canvas.itemconfigure(btn["bg_item"], image=btn["skins"][state])
        self._canvas.itemconfigure(btn["text_item"], fill=color)

    def _on_manage_button_enter(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        self._canvas.configure(cursor="hand2")
        btn["hover"] = True
        self._refresh_manage_button_state(name)

    def _on_manage_button_leave(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        self._canvas.configure(cursor="")
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_manage_button_state(name)

    def _on_manage_button_press(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        btn["pressed"] = True
        self._refresh_manage_button_state(name)

    def _on_manage_button_release(self, name: str):
        btn = self._canvas_buttons.get(name)
        if not btn:
            return
        should_fire = bool(btn["enabled"] and btn["pressed"] and btn["hover"])
        btn["pressed"] = False
        self._refresh_manage_button_state(name)
        if should_fire:
            try:
                btn["command"]()
            except Exception:
                pass

    def _make_entry(self, parent, textvariable=None, width=16, show=None):
        c = self._theme
        entry = tk.Entry(
            parent,
            textvariable=textvariable,
            width=width,
            show=show,
            bg=c["entry_bg"],
            fg=c["text"],
            insertbackground=c["text"],
            bd=0,
            relief=tk.FLAT,
            highlightthickness=0,
            font=("Segoe UI", 9),
        )
        return entry

    def _make_button(self, parent, text, command, primary=False, width=12):
        c = self._theme
        bg = c["accent_pressed"] if primary else c["button_bg"]
        hover = c["accent"] if primary else c["button_hover"]
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=bg,
            fg=c["text"],
            activebackground=hover,
            activeforeground=c["text"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=5,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )
        return btn

    def _build_ui(self):
        c = self._theme
        cv = self._canvas
        self._content = cv
        self._canvas_buttons = {}
        self._manage_button_skin_cache = {}
        self._create_manage_group_panel("switch_block", x=16, y=30, width=428, height=94)
        self._create_manage_group_panel("accounts_block", x=16, y=136, width=428, height=410)
        self._create_manage_group_panel("order_block", x=16, y=558, width=428, height=220)

        cv.create_text(26, 48, text="Switch account (min)", fill=c["text"], font=("Segoe UI", 10), anchor="nw")
        self.switch_minutes_var = tk.StringVar(value=str(self._config_manager.get_account_switch_minutes()))
        switch_entry = self._make_entry(self, textvariable=self.switch_minutes_var, width=6)
        switch_entry.bind("<Return>", lambda _e: self._save_switch_minutes())
        cv.create_window(178, 50, anchor="nw", window=switch_entry)
        cv.create_text(244, 48, text="0 = off", fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw")
        self._create_manage_canvas_button(
            name="save_switch",
            text="Save Time",
            x=26,
            y=76,
            body_w=150,
            body_h=34,
            command=self._save_switch_minutes,
            primary=True,
        )

        cv.create_text(26, 146, text="Accounts (max 10)", fill=c["text"], font=("Segoe UI", 13, "bold"), anchor="nw")
        cv.create_text(26, 174, text="#", fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw")
        cv.create_text(62, 174, text="Name", fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw")
        cv.create_text(208, 174, text="Email", fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw")

        self._table_rows = []
        row_y_start = 194
        row_step = 20
        for idx in range(self._max_accounts):
            y = row_y_start + idx * row_step
            tag = f"acct_row_{idx}"
            bg_item = cv.create_rectangle(22, y - 2, 438, y + 16, fill=c["row_bg"], outline=c["table_border"], tags=(tag,))
            idx_item = cv.create_text(30, y, text=str(idx + 1), fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw", tags=(tag,))
            name_item = cv.create_text(62, y, text="", fill=c["text"], font=("Segoe UI", 9, "bold"), anchor="nw", tags=(tag,))
            email_item = cv.create_text(208, y, text="", fill=c["text"], font=("Segoe UI", 9), anchor="nw", tags=(tag,))
            self._table_rows.append({"idx": idx_item, "name": name_item, "email": email_item, "bg": bg_item})
            self._canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, row_idx=idx: self._select_account_row(row_idx))
            self._canvas.tag_bind(tag, "<Enter>", lambda _e: self._canvas.configure(cursor="hand2"))
            self._canvas.tag_bind(tag, "<Leave>", lambda _e: self._canvas.configure(cursor=""))

        cv.create_text(26, 404, text="Name", fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw")
        name_entry = self._make_entry(self, textvariable=self.account_name_var, width=34)
        name_entry.bind("<Return>", lambda _e: self._save_selected_account())
        cv.create_window(84, 402, anchor="nw", window=name_entry)

        cv.create_text(26, 434, text="Email", fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw")
        email_entry = self._make_entry(self, textvariable=self.account_email_var, width=34)
        email_entry.bind("<Return>", lambda _e: self._save_selected_account())
        cv.create_window(84, 432, anchor="nw", window=email_entry)

        cv.create_text(26, 464, text="Password", fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw")
        password_entry = self._make_entry(self, textvariable=self.account_pw_var, width=34, show="*")
        password_entry.bind("<Return>", lambda _e: self._save_selected_account())
        cv.create_window(84, 462, anchor="nw", window=password_entry)

        self._create_manage_canvas_button(
            name="save_row",
            text="Save Row",
            x=26,
            y=498,
            body_w=104,
            body_h=30,
            command=self._save_selected_account,
            primary=True,
        )

        self._create_manage_canvas_button(
            name="delete_row",
            text="Delete Row",
            x=134,
            y=498,
            body_w=104,
            body_h=30,
            command=self._clear_selected_account_row,
            primary=False,
        )

        self._create_manage_canvas_button(
            name="save_accounts",
            text="Save Accounts",
            x=244,
            y=498,
            body_w=158,
            body_h=30,
            command=self._save_accounts,
            primary=False,
        )

        cv.create_text(26, 570, text="Account Play Order", fill=c["text"], font=("Segoe UI", 10), anchor="nw")
        cv.create_line(22, 587, 438, 587, fill=c["table_border"], width=1)
        current_order = self._config_manager.get_account_play_order()
        self._order_vars = []
        self._order_combos = []
        # Two columns: slots 1–5 (left) and 6–10 (right)
        col_x = [(30, 48), (238, 256)]
        for idx in range(self._order_slots):
            col = idx // 5
            row = idx % 5
            lx, cx = col_x[col]
            y = 594 + row * 24
            cv.create_text(lx, y, text=str(idx + 1), fill=c["text_muted"], font=("Segoe UI", 9), anchor="nw")
            var = tk.StringVar(value=current_order[idx] if idx < len(current_order) else "")
            combo = ttk.Combobox(
                self,
                textvariable=var,
                values=[],
                state="readonly",
                width=11,
            )
            combo.configure(style="ManageFire.TCombobox")
            cv.create_window(cx, y - 2, anchor="nw", window=combo)
            self._order_vars.append(var)
            self._order_combos.append(combo)
        # vertical divider between columns
        cv.create_line(230, 590, 230, 716, fill=c["table_border"], width=1)

        self._create_manage_canvas_button(
            name="save_order",
            text="Save Order",
            x=26,
            y=726,
            body_w=140,
            body_h=34,
            command=self._save_account_play_order,
            primary=False,
        )
        self._create_manage_canvas_button(
            name="close_bottom",
            text="Back",
            x=246,
            y=726,
            body_w=120,
            body_h=34,
            command=self.destroy,
            primary=False,
        )

    def _truncate_text(self, text: str, max_len: int) -> str:
        value = text or ""
        if len(value) <= max_len:
            return value
        return value[: max_len - 1] + "..."

    def _refresh_accounts_table(self):
        c = self._theme
        for idx, row_widgets in enumerate(self._table_rows):
            account = self._accounts_data[idx]
            selected = idx == self._selected_account_idx
            bg_fill = c["row_selected_bg"] if selected else c["row_bg"]
            bg_outline = c["accent"] if selected else c["table_border"]
            self._canvas.itemconfigure(row_widgets["bg"], fill=bg_fill, outline=bg_outline)
            self._canvas.itemconfigure(row_widgets["idx"], fill=c["accent"] if selected else c["text_muted"])
            name_fg = c["accent"] if selected else (c["text"] if account["name"] else c["text_muted"])
            email_fg = c["accent"] if selected else (c["text"] if account["email"] else c["text_muted"])
            name_text = self._truncate_text(account["name"], 14) if account["name"] else "click to edit"
            self._canvas.itemconfigure(row_widgets["name"], fill=name_fg, text=name_text)
            email_text = self._truncate_text(account["email"], 22) if account["email"] else ""
            self._canvas.itemconfigure(row_widgets["email"], fill=email_fg, text=email_text)

    def _populate_details_fields(self):
        account = self._accounts_data[self._selected_account_idx]
        self.account_name_var.set(str(account.get("name", "")))
        self.account_email_var.set(str(account.get("email", "")))
        self.account_pw_var.set(str(account.get("pw", "")))

    def _apply_details_to_selected(self, validate: bool, show_error: bool = False) -> bool:
        name = (self.account_name_var.get() or "").strip()
        email = (self.account_email_var.get() or "").strip()
        pw = (self.account_pw_var.get() or "").strip()
        if validate and (name or email or pw) and (not name or not email or not pw):
            if show_error:
                messagebox.showerror(
                    "Manage Accounts",
                    "Name, Email and Password are required for a non-empty row.",
                    parent=self,
                )
            return False
        row = self._accounts_data[self._selected_account_idx]
        row["name"] = name
        row["email"] = email
        row["pw"] = pw
        if not name:
            row["folder"] = ""
        return True

    def _select_account_row(self, idx: int):
        if idx < 0 or idx >= len(self._accounts_data):
            return
        if not self._apply_details_to_selected(validate=False, show_error=False):
            return
        self._selected_account_idx = idx
        self._populate_details_fields()
        self._refresh_accounts_table()
        self._refresh_order_choices()
        self._canvas.focus_set()

    def _clear_selected_account_row(self):
        row = self._accounts_data[self._selected_account_idx]
        row["name"] = ""
        row["email"] = ""
        row["pw"] = ""
        row["folder"] = ""
        self.account_name_var.set("")
        self.account_email_var.set("")
        self.account_pw_var.set("")
        self._refresh_accounts_table()
        self._refresh_order_choices()
        self.lift()
        self.focus_force()

    def _save_selected_account(self):
        if not self._apply_details_to_selected(validate=True, show_error=True):
            return
        self._refresh_accounts_table()
        self._refresh_order_choices()
        self.lift()
        self.focus_force()

    def _collect_accounts_for_save(self):
        accounts = []
        seen = set()
        for idx, row in enumerate(self._accounts_data, start=1):
            name = (row.get("name", "") or "").strip()
            email = (row.get("email", "") or "").strip()
            pw = (row.get("pw", "") or "").strip()
            if not name and not email and not pw:
                continue
            if not name or not email or not pw:
                raise ValueError(f"Row {idx}: Name, Email and Password are required.")
            key = name.casefold()
            if key in seen:
                raise ValueError(f"Duplicate account name: {name}")
            seen.add(key)
            accounts.append(
                {
                    "name": name,
                    "email": email,
                    "pw": pw,
                    "folder": row.get("folder", ""),
                }
            )
        return accounts

    def _save_switch_minutes(self):
        raw = (self.switch_minutes_var.get() or "").strip()
        if raw == "":
            return
        try:
            minutes = int(raw)
        except ValueError:
            return
        if minutes < 0:
            minutes = 0
        self.switch_minutes_var.set(str(minutes))
        self._config_manager.set_account_switch_minutes(minutes)
        messagebox.showinfo("Saved", "Switch account minutes saved.")

    def _refresh_order_choices(self):
        names = []
        for row in self._accounts_data:
            name = (row.get("name", "") or "").strip()
            if name and name not in names:
                names.append(name)
        choices = [""] + names
        for combo in self._order_combos:
            combo.configure(values=choices)
        for var in self._order_vars:
            if var.get() and var.get() not in names:
                var.set("")

    def _save_accounts(self):
        if not self._apply_details_to_selected(validate=False, show_error=False):
            return
        self._refresh_accounts_table()
        self._refresh_order_choices()
        try:
            accounts = self._collect_accounts_for_save()
        except ValueError as e:
            messagebox.showerror("Save Accounts", str(e))
            return

        try:
            saved_accounts = self._config_manager.save_managed_accounts(accounts)
        except Exception as e:
            messagebox.showerror("Save Accounts", f"Failed to save accounts: {e}")
            return

        saved_iter = iter(saved_accounts)
        for row in self._accounts_data:
            if row["name"] and row["email"] and row["pw"]:
                saved = next(saved_iter, {})
                row["folder"] = str(saved.get("folder", ""))
            else:
                row["folder"] = ""

        self._refresh_order_choices()
        self._refresh_accounts_table()
        self.lift()
        self.focus_force()

    def _save_account_play_order(self):
        self._refresh_order_choices()
        order = [var.get().strip() for var in getattr(self, "_order_vars", [])]
        order = [item for item in order if item]
        self._config_manager.set_account_play_order(order)
        self._config_manager.set_account_cycle_index(0)
        parent = getattr(self._parent, "master", None)
        if parent and getattr(parent, "bot_running", False) and getattr(parent, "_controller", None):
            try:
                parent._controller.set_account_play_order(order)
                parent._controller.set_account_cycle_index(0)
            except Exception:
                pass
        self.lift()
        self.focus_force()

    def destroy(self):
        callback = getattr(self, "_on_close_callback", None)
        try:
            super().destroy()
        finally:
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass
class _RecordActionsButtonProxy:
    def __init__(self, window, name: str):
        self._window = window
        self._name = name

    def config(self, **kwargs):
        self.configure(**kwargs)

    def configure(self, **kwargs):
        if "text" in kwargs:
            self._window._set_canvas_button_text(self._name, str(kwargs["text"]))
        if "state" in kwargs:
            state = str(kwargs["state"]).lower()
            enabled = state not in (str(tk.DISABLED).lower(), "disabled")
            self._window._set_canvas_button_enabled(self._name, enabled)


class RecordActionsWindow(tk.Toplevel):
    def __init__(self, parent: SettingsWindow, spawn_xy: tuple[int, int] | None = None, on_close=None):
        super().__init__(parent)
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self._parent = parent
        self._on_close_callback = on_close
        self.title("Record Actions")
        width, height = self._s(460), self._s(430)
        parent.update_idletasks()
        if spawn_xy is not None:
            x, y = int(spawn_xy[0]), int(spawn_xy[1])
        else:
            gap_px = int(parent.winfo_fpixels("4m"))  # ~0.4 cm
            x = parent.winfo_x() + parent.winfo_width() + gap_px
            y = parent.winfo_y()
        max_x = max(0, self.winfo_screenwidth() - width)
        max_y = max(0, self.winfo_screenheight() - height)
        x = min(max(0, x), max_x)
        y = min(max(0, y), max_y)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)
        self.configure(bg="#0F1115")
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))
        self._theme = {
            "bg": "#0F1115",
            "text": "#E7EAF0",
        }
        self._bg_source_image = None
        self._bg_photo = None
        self._bg_canvas_item = None
        self._canvas = tk.Canvas(self, bg=self._theme["bg"], highlightthickness=0, bd=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", self._on_canvas_resize_background)
        self._title_item = None

        self._buttons = {}
        self._button_order = []
        self._create_canvas_button("record", "Record", self._parent._record_actions_prompt, "Secondary.TButton")
        self._create_canvas_button("show_records", "Show Records", self._parent._show_records, "Secondary.TButton")
        self._create_canvas_button("back", "Back", self.destroy, "Secondary.TButton")

        self._record_btn_proxy = _RecordActionsButtonProxy(self, "record")
        self._show_btn_proxy = _RecordActionsButtonProxy(self, "show_records")
        self._parent.record_btn = self._record_btn_proxy
        self._parent.show_records_btn = self._show_btn_proxy

        self._load_background_image()
        self.bind("<Configure>", self._on_resize_background)
        self.after(30, self._refresh_scene)
        self.after(120, self._refresh_scene)

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _create_canvas_button(self, name: str, text: str, command, style_name: str = "Secondary.TButton"):
        parent_ui = getattr(self._parent, "master", None)
        skins = None
        if parent_ui is not None:
            skins_all = getattr(parent_ui, "_button_skins", {})
            skins = skins_all.get(style_name)
            if skins is None and skins_all:
                skins = next(iter(skins_all.values()))
        if not skins:
            return

        tag = f"record_actions_btn_{name}"
        bg_item = self._canvas.create_image(0, 0, anchor="n", image=skins["normal"], tags=(tag,))
        text_item = self._canvas.create_text(
            0,
            0,
            text=text,
            fill="#F2F6FF",
            font=("Segoe UI", 11, "bold"),
            anchor="center",
            tags=(tag,),
        )
        self._buttons[name] = {
            "command": command,
            "enabled": True,
            "hover": False,
            "pressed": False,
            "skins": skins,
            "bg_item": bg_item,
            "text_item": text_item,
            "width": int(skins["normal"].width()),
            "height": int(skins["normal"].height()),
        }
        self._button_order.append(name)

        self._canvas.tag_bind(tag, "<Enter>", lambda _e, n=name: self._on_button_enter(n))
        self._canvas.tag_bind(tag, "<Leave>", lambda _e, n=name: self._on_button_leave(n))
        self._canvas.tag_bind(tag, "<ButtonPress-1>", lambda _e, n=name: self._on_button_press(n))
        self._canvas.tag_bind(tag, "<ButtonRelease-1>", lambda _e, n=name: self._on_button_release(n))
        self._refresh_canvas_button_state(name)

    def _set_canvas_button_text(self, name: str, text: str):
        btn = self._buttons.get(name)
        if not btn:
            return
        self._canvas.itemconfigure(btn["text_item"], text=text)

    def _set_canvas_button_enabled(self, name: str, enabled: bool):
        btn = self._buttons.get(name)
        if not btn:
            return
        btn["enabled"] = bool(enabled)
        if not btn["enabled"]:
            btn["hover"] = False
            btn["pressed"] = False
        self._refresh_canvas_button_state(name)

    def _refresh_canvas_button_state(self, name: str):
        btn = self._buttons.get(name)
        if not btn:
            return
        if not btn["enabled"]:
            state_key = "disabled"
            text_color = "#A5AFBF"
        elif btn["pressed"]:
            state_key = "pressed"
            text_color = "#FFFFFF"
        elif btn["hover"]:
            state_key = "hover"
            text_color = "#FFFFFF"
        else:
            state_key = "normal"
            text_color = "#F2F6FF"
        self._canvas.itemconfigure(btn["bg_item"], image=btn["skins"][state_key])
        self._canvas.itemconfigure(btn["text_item"], fill=text_color)

    def _on_button_enter(self, name: str):
        btn = self._buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        self._canvas.configure(cursor="hand2")
        btn["hover"] = True
        self._refresh_canvas_button_state(name)

    def _on_button_leave(self, name: str):
        btn = self._buttons.get(name)
        if not btn:
            return
        self._canvas.configure(cursor="")
        btn["hover"] = False
        btn["pressed"] = False
        self._refresh_canvas_button_state(name)

    def _on_button_press(self, name: str):
        btn = self._buttons.get(name)
        if not btn or not btn["enabled"]:
            return
        btn["pressed"] = True
        self._refresh_canvas_button_state(name)

    def _on_button_release(self, name: str):
        btn = self._buttons.get(name)
        if not btn:
            return
        should_fire = bool(btn["enabled"] and btn["pressed"] and btn["hover"])
        btn["pressed"] = False
        self._refresh_canvas_button_state(name)
        if should_fire:
            try:
                btn["command"]()
            except Exception:
                pass

    def _load_background_image(self):
        self._bg_source_image = None
        for path in (_image_path("background"), _image_path("background.png")):
            if not os.path.exists(path):
                continue
            try:
                with Image.open(path) as image:
                    self._bg_source_image = image.convert("RGB")
                    return
            except Exception:
                continue

    def _on_resize_background(self, event=None):
        if event is not None and event.widget is not self:
            return
        self._refresh_scene()

    def _on_canvas_resize_background(self, event=None):
        if event is not None and event.widget is not self._canvas:
            return
        self._refresh_scene()

    def _refresh_scene(self):
        self._refresh_background()
        self._layout_scene()

    def _layout_scene(self):
        if not self._canvas or not self._canvas.winfo_exists():
            return
        s = self._s
        cw = max(2, self._canvas.winfo_width())
        x = cw // 2
        y_start = s(64)
        y_step = s(78)
        for idx, name in enumerate(self._button_order):
            btn = self._buttons.get(name)
            if not btn:
                continue
            y = y_start + idx * y_step
            self._canvas.coords(btn["bg_item"], x, y)
            self._canvas.coords(btn["text_item"], x, y + btn["height"] // 2 - 2)

    def _refresh_background(self):
        if self._bg_source_image is None:
            return
        width = max(2, self._canvas.winfo_width())
        height = max(2, self._canvas.winfo_height())
        try:
            fitted = ImageOps.fit(
                self._bg_source_image,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            self._bg_photo = ImageTk.PhotoImage(fitted)
            if self._bg_canvas_item is None:
                self._bg_canvas_item = self._canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
            else:
                self._canvas.coords(self._bg_canvas_item, 0, 0)
                self._canvas.itemconfigure(self._bg_canvas_item, image=self._bg_photo)
            self._canvas.tag_lower(self._bg_canvas_item)
        except Exception:
            pass

    def destroy(self):
        if getattr(self._parent, "record_btn", None) is self._record_btn_proxy:
            self._parent.record_btn = None
        if getattr(self._parent, "show_records_btn", None) is self._show_btn_proxy:
            self._parent.show_records_btn = None
        callback = getattr(self, "_on_close_callback", None)
        try:
            super().destroy()
        finally:
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass


class LogWindow(tk.Toplevel):
    def __init__(self, parent, log_path: str):
        super().__init__(parent)
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self.title(log_path)
        self.geometry(f"{self._s(800)}x{self._s(500)}")
        self.resizable(True, True)
        self.configure(bg="#1e1e1e")
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))
        self._log_path = log_path
        self._stopped = False

        frame = tk.Frame(self, bg="#1e1e1e", padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(frame, wrap=tk.NONE, bg="#111111", fg="#dddddd", insertbackground="#dddddd")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.text.config(state=tk.DISABLED)
        self._refresh()

        back_btn = ttk.Button(
            frame,
            text="Back",
            command=self.destroy,
        )
        back_btn.grid(row=2, column=0, sticky="w", pady=(8, 0))
        _apply_submenu_theme(self)

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _refresh(self):
        if self._stopped:
            return
        try:
            with open(self._log_path, "r") as f:
                content = f.read()
        except Exception as e:
            content = f"(unable to read {self._log_path}: {e})"

        at_bottom = self.text.yview()[1] >= 0.999
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        self.text.config(state=tk.DISABLED)
        if at_bottom:
            self.text.see(tk.END)

        self.after(1000, self._refresh)

    def destroy(self):
        self._stopped = True
        super().destroy()


class RecordsWindow(tk.Toplevel):
    def __init__(self, parent, records_path: str, play_callback):
        super().__init__(parent)
        self._ui_scale = _get_ui_scale_from_widget(parent)
        self.title("Show Records")
        self.geometry(f"{self._s(760)}x{self._s(560)}")
        self.minsize(self._s(700), self._s(500))
        self.resizable(True, True)
        self.configure(bg="#2b2b2b")
        _apply_window_topmost(self, _get_ui_topmost_setting_from_widget(parent))
        self._records_path = records_path
        self._play_callback = play_callback
        self._setup_ui()
        _apply_submenu_theme(self)

    def _s(self, value: int | float) -> int:
        return max(1, int(round(float(value) * float(self._ui_scale))))

    def _setup_ui(self):
        main_frame = tk.Frame(self, bg="#2b2b2b", padx=16, pady=16)
        main_frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            main_frame,
            text="Recorded Actions",
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 12, "bold"),
        )
        title.pack(pady=(0, 10))

        list_frame = tk.Frame(main_frame, bg="#3b3b3b")
        list_frame.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(list_frame, bg="#3b3b3b", highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#3b3b3b")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        records = self._load_records()
        if not records:
            if self._migrate_from_text():
                records = self._load_records()

        if not records:
            no_data = tk.Label(
                scrollable_frame,
                text="No records saved yet",
                bg="#3b3b3b",
                fg="#aaaaaa",
                font=("Segoe UI", 10),
            )
            no_data.pack(pady=20)
        else:
            for idx, rec in enumerate(records):
                item = tk.Frame(scrollable_frame, bg="#3b3b3b", padx=10, pady=8)
                item.pack(fill=tk.X)

                name = rec.get("name", "Unnamed")
                created = rec.get("created_at", "")
                name_label = tk.Label(
                    item,
                    text=name,
                    bg="#3b3b3b",
                    fg="white",
                    font=("Segoe UI", 10, "bold"),
                    anchor="w",
                    width=18,
                )
                name_label.pack(side=tk.LEFT)

                ts_label = tk.Label(
                    item,
                    text=created,
                    bg="#3b3b3b",
                    fg="#aaaaaa",
                    font=("Consolas", 9),
                    anchor="w",
                )
                ts_label.pack(side=tk.LEFT, padx=(6, 0))

                test_btn = ttk.Button(
                    item,
                    text="Test Action",
                    command=lambda a=rec.get("actions", []): self._play_callback(a),
                )
                test_btn.pack(side=tk.RIGHT)

                del_btn = ttk.Button(
                    item,
                    text="Delete",
                    command=lambda i=idx: self._delete_record(i),
                )
                del_btn.pack(side=tk.RIGHT, padx=(0, 8))

                sep = tk.Frame(scrollable_frame, bg="#4a4a4a", height=1)
                sep.pack(fill=tk.X, padx=5)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _load_records(self) -> list[dict]:
        try:
            if os.path.exists(self._records_path):
                with open(self._records_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("records", [])
        except Exception:
            return []
        return []

    def _migrate_from_text(self) -> bool:
        record_path = _app_path("recorded_actions.txt")
        if not os.path.exists(record_path):
            return False
        try:
            with open(record_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
        except Exception:
            return False

        if not lines:
            return False

        events = []
        for line in lines:
            if not line.startswith("[") or "]" not in line:
                continue
            ts_raw = line[1: line.index("]")]
            try:
                ts = datetime.fromisoformat(ts_raw)
            except Exception:
                continue
            rest = line[line.index("]") + 1 :].strip()
            if rest.startswith("click"):
                data = {"type": "click", "ts": ts}
                for part in rest.split():
                    if part.startswith("x="):
                        data["x"] = part.split("=", 1)[1]
                    elif part.startswith("y="):
                        data["y"] = part.split("=", 1)[1]
                    elif part.startswith("button="):
                        data["button"] = part.split("=", 1)[1]
                events.append(data)
            elif rest.startswith("key="):
                key = None
                for part in rest.split():
                    if part.startswith("key="):
                        key = part.split("=", 1)[1]
                        break
                events.append({"type": "key", "key": key, "ts": ts})

        if not events:
            return False

        events.sort(key=lambda e: e["ts"])
        actions = []
        prev_ts = events[0]["ts"]
        for ev in events:
            delay = (ev["ts"] - prev_ts).total_seconds()
            item = {"type": ev["type"], "delay": delay}
            if ev["type"] == "click":
                item["x"] = ev.get("x", 0)
                item["y"] = ev.get("y", 0)
                item["button"] = ev.get("button", "left")
            elif ev["type"] == "key":
                item["key"] = ev.get("key", "")
            actions.append(item)
            prev_ts = ev["ts"]

        record = {
            "name": "Account Switch",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "actions": actions,
        }

        data = {"records": []}
        try:
            if os.path.exists(self._records_path):
                with open(self._records_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            data = {"records": []}
        data.setdefault("records", []).append(record)
        try:
            with open(self._records_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            return False
        return True

    def _delete_record(self, index: int) -> None:
        records = self._load_records()
        if index < 0 or index >= len(records):
            return
        records.pop(index)
        try:
            with open(self._records_path, "w", encoding="utf-8") as f:
                json.dump({"records": records}, f, indent=2)
        except Exception:
            pass
        for widget in self.winfo_children():
            widget.destroy()
        self._setup_ui()

def main():
    app = MTGBotUI()
    app.mainloop()


if __name__ == "__main__":
    main()
