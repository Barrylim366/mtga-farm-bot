"""One small window for the session: the bot's Current Session figures on top, each move and why below.

Opened under the bot's main window when the bot starts, where its Current Session window normally
goes, and the bot's "Current Session" button brings this window up instead of a second one. It only
reads: the session counters from the bot's main window, gold from the bot's status file, and the
feed forge_ai publishes. It never touches the game.

Switch off with MTGA_FORGE_WINDOW=off.
"""
from __future__ import annotations

import os
import tkinter as tk
from typing import Any, Optional

from forge_bridge import forge_ai

# the bot's own palette (ui.py, CurrentSessionWindow)
BG = "#0F1115"
CARD_BG = "#320a02"
ACCENT = "#ff9318"
BODY = "#ffb841"
MUTED = "#B8A9AE"
POLL_MS = 300
SESSION_POLL_MS = 2500
# kept small on purpose: the bot clicks inside the Arena window, and anything lying over it gets in the way
WIDTH, HEIGHT = 360, 250


def enabled() -> bool:
    return (os.environ.get("MTGA_FORGE_WINDOW") or "on").strip().lower() not in ("off", "0", "false", "no")


class MovesWindow(tk.Toplevel):
    def __init__(self, parent: Any) -> None:
        super().__init__(parent)
        self.title("Session · Forge Brain")
        self.configure(bg=BG)
        self._parent = parent
        parent.update_idletasks()
        # directly under the bot's window, as its own Current Session window would be
        # exactly as wide as the bot's menu window, and lined up with it
        width = parent.winfo_width() if parent.winfo_width() > 1 else WIDTH
        x = max(0, min(parent.winfo_x(), self.winfo_screenwidth() - width))
        y = parent.winfo_rooty() + parent.winfo_height() + 12
        y = max(0, min(y, self.winfo_screenheight() - HEIGHT - 60))
        self.geometry(f"{width}x{HEIGHT}+{x}+{y}")
        self.minsize(min(width, 200), 160)
        try:
            # follow the bot's own "keep windows on top" setting
            self.attributes("-topmost", bool(parent.config_manager.get_ui_windows_topmost()))
        except Exception:
            pass
        # closing would leave no way to bring it back, so the close button minimises instead
        self.protocol("WM_DELETE_WINDOW", self.iconify)

        self._session = tk.Label(
            self, text="", bg=BG, fg="#F7E5B1", font=("Segoe UI", 9, "bold"), anchor="w", justify="left"
        )
        self._session.pack(fill=tk.X, padx=8, pady=(6, 0))
        self._status = tk.Label(self, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self._status.pack(fill=tk.X, padx=8, pady=(0, 2))

        frame = tk.Frame(self, bg=ACCENT, bd=0)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 8))
        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 1), pady=1)
        self._text = tk.Text(
            frame, bg=CARD_BG, fg=BODY, font=("Segoe UI", 9), wrap=tk.WORD, bd=0, highlightthickness=0,
            padx=8, pady=6, state=tk.DISABLED, cursor="arrow", yscrollcommand=scrollbar.set,
        )
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        scrollbar.config(command=self._text.yview)
        self._text.tag_configure("heading", foreground="#F7E5B1", font=("Segoe UI", 9, "bold"), spacing1=6)
        self._text.tag_configure("why", foreground=BODY, lmargin1=10, lmargin2=10, spacing3=2)

        self._seen = 0
        self._gold = 0
        self._take_over_session_button()
        self._poll_session()
        self._poll()

    def show(self) -> None:
        self.deiconify()
        self.lift()

    def _take_over_session_button(self) -> None:
        """Point the bot's "Current Session" menu button at this window, so there is only one."""
        try:
            self._parent._menu_buttons["current_session"]["command"] = self.show
        except Exception:
            pass  # a different bot version: its own window still works, this one just sits beside it

    def _session_text(self) -> str:
        games = getattr(self._parent, "session_games", None)
        wins = getattr(self._parent, "session_wins", None)
        parts = []
        if games is not None and wins is not None:
            parts.append(f"Games {games}")
            parts.append(f"Wins {wins}")
        parts.append(f"Gold {self._gold}")
        line = "   ·   ".join(parts)
        switch = getattr(self._parent, "_switch_eta_text", None)
        if switch and "off" not in str(switch).lower():
            line += "\n" + str(switch)
        return line

    def _poll_session(self) -> None:
        try:
            try:
                import runtime_status  # type: ignore  (the bot's module; absent outside the bot)

                gold = (runtime_status.read_status() or {}).get("gold_farmed") or {}
                if isinstance(gold, dict):
                    self._gold = sum(int(v or 0) for v in gold.values())
            except Exception:
                pass
            self._session.config(text=self._session_text())
        except tk.TclError:
            return
        self.after(SESSION_POLL_MS, self._poll_session)

    def _status_text(self) -> str:
        config = forge_ai.Config()
        bridge = forge_ai.Bridge._instance
        if config.off:
            return "Forge off: the bot decides."
        if bridge is None or not bridge.ready:
            return "Forge starting... the bot decides until then."
        return "Forge is choosing. Newest at the bottom." if config.acting else "Shadow mode: Forge only comments."

    def _poll(self) -> None:
        try:
            self._status.config(text=self._status_text())
            fresh = [entry for entry in list(forge_ai.FEED) if entry[0] > self._seen]
            if fresh:
                # stay pinned to the newest entry only if the reader has not scrolled up
                at_bottom = self._text.yview()[1] >= 0.999
                self._text.config(state=tk.NORMAL)
                for number, heading, explanation in fresh:
                    self._text.insert(tk.END, heading + "\n", "heading")
                    if explanation:
                        self._text.insert(tk.END, explanation + "\n", "why")
                    self._seen = number
                self._text.config(state=tk.DISABLED)
                if at_bottom:
                    self._text.see(tk.END)
        except tk.TclError:
            return  # the window is gone
        self.after(POLL_MS, self._poll)


_window: Optional[MovesWindow] = None


def attach(root: Any) -> Optional[MovesWindow]:
    """Open the window on the bot's root window. Safe to call more than once."""
    global _window
    if not enabled():
        return None
    if _window is None or not _window.winfo_exists():
        _window = MovesWindow(root)
    return _window


def install() -> None:
    """Open the window as the bot's main loop starts, without editing the bot's UI code."""
    original = tk.Tk.mainloop

    def mainloop(self, n: int = 0):
        try:
            attach(self)
        except Exception as exc:  # the window is a convenience; the bot must start regardless
            forge_ai._log(f"moves window not opened ({exc})")
        return original(self, n)

    tk.Tk.mainloop = mainloop  # type: ignore[method-assign]
