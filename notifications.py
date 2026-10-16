"""Discord & Telegram Webhook Notification Dispatcher for MTGA Farm Bot.

Sends non-blocking background notifications for match wins/losses,
daily quest completions, account rotations, and status alerts.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

import bot_logger
from runtime_paths import runtime_file


def _get_config_path() -> Path:
    return runtime_file("config", "notifications.json")


class NotificationManager:
    """Singleton background notification dispatcher."""
    _instance: "NotificationManager | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=100)
        self._worker_thread: threading.Thread | None = None
        self._running = True
        self._config = self._load_config()
        self._match_history: list[bool] = []
        self._start_worker()

    @classmethod
    def get_instance(cls) -> "NotificationManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = NotificationManager()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            if cls._instance is not None:
                cls._instance._running = False
                cls._instance = None

    def _load_config(self) -> dict:
        path = _get_config_path()
        try:
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {
            "discord_webhook_url": "",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "enabled": False,
        }

    def save_config(self, config: dict) -> None:
        self._config.update(config)
        path = _get_config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
        except Exception as e:
            bot_logger.log_error(f"Failed to save notifications config: {e}")

    def _start_worker(self) -> None:
        def _worker() -> None:
            while self._running:
                try:
                    task = self._queue.get(timeout=1.0)
                    if task is None:
                        break
                    self._dispatch(task)
                    self._queue.task_done()
                except queue.Empty:
                    continue
                except Exception as exc:
                    bot_logger.log_error(f"Notification worker error: {exc}")

        self._worker_thread = threading.Thread(target=_worker, name="notification-dispatcher", daemon=True)
        self._worker_thread.start()

    def _dispatch(self, task: dict) -> None:
        title = task.get("title", "MTGA Farm Bot Alert")
        description = task.get("description", "")
        color = task.get("color", 0x3498DB)  # Default blue

        discord_url = self._config.get("discord_webhook_url", "").strip()
        if discord_url and discord_url.startswith("https://discord.com/api/webhooks/"):
            self._send_discord(discord_url, title, description, color)

        tg_token = self._config.get("telegram_bot_token", "").strip()
        tg_chat_id = self._config.get("telegram_chat_id", "").strip()
        if tg_token and tg_chat_id:
            self._send_telegram(tg_token, tg_chat_id, f"*{title}*\n{description}")

    def _send_discord(self, url: str, title: str, description: str, color: int) -> None:
        payload = {
            "embeds": [{
                "title": title,
                "description": description,
                "color": color,
                "footer": {"text": "MTGA Farm Bot • Automated Notifications"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }]
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "MTGAFarmBot/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as e:
            bot_logger.log_error(f"Discord webhook failed: {e}")

    def _send_telegram(self, token: str, chat_id: str, message: str) -> None:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "MTGAFarmBot/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as e:
            bot_logger.log_error(f"Telegram alert failed: {e}")

    def send(self, title: str, description: str, color: int = 0x3498DB) -> None:
        """Enqueue notification for background delivery."""
        if not self._config.get("enabled", False):
            return
        if not (self._config.get("discord_webhook_url") or self._config.get("telegram_bot_token")):
            return
        try:
            self._queue.put_nowait({"title": title, "description": description, "color": color})
        except queue.Full:
            pass

    def notify_match_result(self, won: bool | None, duration_sec: float | None = None,
                            account_name: str | None = None) -> None:
        if won is True:
            status = "VICTORY"
            color = 0x2ECC71
        elif won is False:
            status = "DEFEAT"
            color = 0xE74C3C
        else:
            status = "UNKNOWN / DRAW"
            color = 0x95A5A6

        dur_str = f"{int(duration_sec // 60)}m {int(duration_sec % 60)}s" if duration_sec else "Unknown"
        acc_str = f"Account: `{account_name}`\n" if account_name else ""
        
        if won is not None:
            self._match_history.append(won)
        rolling_str = ""
        recent = self._match_history[-10:]
        if len(recent) >= 3:
            w_count = sum(1 for w in recent if w)
            l_count = len(recent) - w_count
            pct = (w_count / len(recent)) * 100
            rolling_str = f"\nRecent (last {len(recent)}): **{w_count}W - {l_count}L** ({pct:.0f}% win rate)"

        self.send(
            title=f"Match Complete: {status}",
            description=f"{acc_str}Result: **{status}**\nDuration: {dur_str}{rolling_str}",
            color=color,
        )

    def notify_quest_completed(self, quest_name: str, gold_reward: int = 500) -> None:
        self.send(
            title="Daily Quest Completed!",
            description=f"Quest: **{quest_name}**\nReward: **+{gold_reward} Gold**",
            color=0xF1C40F,
        )

    def notify_account_switched(self, previous_account: str, next_account: str, reason: str = "Targets met") -> None:
        lines = []
        if previous_account:
            lines.append(f"Completed: `{previous_account}`")
        lines.append(f"Now Farming: `{next_account}`")
        lines.append(f"Reason: {reason}")
        self.send(
            title="Account Switched",
            description="\n".join(lines),
            color=0x9B59B6,
        )


def notify(title: str, description: str, color: int = 0x3498DB) -> None:
    NotificationManager.get_instance().send(title, description, color)
