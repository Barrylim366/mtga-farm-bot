import json
import random
import re
import threading
import time
import os

from Controller.ControllerInterface import ControllerSecondary
from Controller.MTGAController.LogReader import LogReader
from Controller.Utilities.GameState import GameState
from Controller.Utilities.input_controller import InputControllerError, create_input_controller
import bot_logger

_TARGET_FIELD_UNSET = object()
_GUILD_COLOR_MAP = {
    "azorius": "WU",
    "dimir": "UB",
    "rakdos": "RB",
    "gruul": "RG",
    "selesnya": "GW",
    "orzhov": "WB",
    "izzet": "UR",
    "golgari": "BG",
    "boros": "RW",
    "simic": "UG",
}
_COLOR_LETTERS = set("WUBRG")


class Controller(ControllerSecondary):

    def __init__(
        self,
        log_path,
        screen_bounds=((0, 0), (1600, 900)),
        click_targets=None,
        input_backend: str | None = None,
        account_switch_minutes: int | None = None,
        credentials_path: str | None = None,
        account_cycle_index: int | None = None,
        account_play_order: list[str] | None = None,
    ):
        self.__decision_callback = None
        self.__mulligan_decision_callback = None
        self.__action_success_callback = None
        self.__decision_execution_thread = None
        self.__mulligan_execution_thread = None
        self.__inactivity_timer = None
        self.__inactivity_timeout = 180  # 3 minutes in seconds
        self.__has_mulled_keep = False
        self.__intro_delay = 15
        self.__decision_delay = 4
        self.screen_bounds = screen_bounds
        self.patterns = {
            'game_state': '"type": "GREMessageType_GameStateMessage"',
            'hover_id': 'objectId',
            'match_completed': 'MatchGameRoomStateType_MatchCompleted',
            'assign_damage': '"type": "GREMessageType_AssignDamageReq"',
            'declare_attackers': '"type": "GREMessageType_DeclareAttackersReq"',
            'select_n': '"type": "GREMessageType_SelectNReq"',
            'select_targets': '"type": "GREMessageType_SelectTargetsReq"',
            'main_nav_loaded': 'MainNav load in',
            'queue_ready_marker': 'Unloading 1 Unused Serialized files (Serialized files now loaded:',
        }
        self.log_reader = LogReader(self.patterns.values(), log_path=log_path, callback=self.__log_callback)
        self._log_path = log_path
        try:
            self.input = create_input_controller(input_backend)
        except InputControllerError as e:
            raise RuntimeError(f"Failed to initialize input backend {input_backend!r}: {e}") from e
        try:
            self.input.configure_screen_bounds(self.screen_bounds)
        except Exception as e:
            raise RuntimeError(f"Failed to configure input backend with screen bounds: {e}") from e
        self.cast_speed = 0.01
        # Height of the mouse when cards are scanned for casting
        self.cast_height = 30
        # Offset of the resolve button from the bottom right
        self.main_br_button_offset = (165, 136)
        self.mulligan_keep_coors = (1101, 870)
        self.mulligan_mull_coors = (801, 870)
        self.player_button_coors = (1699, 996)
        self.home_play_button_coors = (1699, 996)
        self.assign_damage_done_coors = (1280, 720)
        self.opponent_avatar_coors = (
            int(self.screen_bounds[1][0] * 0.67),
            int(self.screen_bounds[1][1] * 0.2),
        )
        self.cast_card_dist = 10
        self.main_br_button_coordinates = (
            self.screen_bounds[1][0] - self.main_br_button_offset[0],
            self.screen_bounds[1][1] - self.main_br_button_offset[1],
        )

        self.log_out_btn_coors = None
        self.log_out_ok_btn_coors = None
        
        self.hand_scan_p1 = (self.screen_bounds[0][0], self.screen_bounds[1][1] - 30)
        self.hand_scan_p2 = (self.screen_bounds[1][0], self.screen_bounds[1][1] - 30)
        self.stack_scan_p1 = (
            int(self.screen_bounds[1][0] * 0.65),
            int(self.screen_bounds[1][1] * 0.25),
        )
        self.stack_scan_p2 = (
            int(self.screen_bounds[1][0] * 0.95),
            int(self.screen_bounds[1][1] * 0.6),
        )
        self.stack_scan_step = 80
        self.stack_scan_fallback_p1 = (
            int(self.screen_bounds[1][0] * 0.35),
            int(self.screen_bounds[1][1] * 0.2),
        )
        self.stack_scan_fallback_p2 = (
            int(self.screen_bounds[1][0] * 0.8),
            int(self.screen_bounds[1][1] * 0.75),
        )
        self.stack_scan_fallback_step = 50
        
        if click_targets:
            if "keep_hand" in click_targets:
                self.mulligan_keep_coors = (click_targets["keep_hand"]["x"], click_targets["keep_hand"]["y"])
            if "queue_button" in click_targets:
                self.home_play_button_coors = (click_targets["queue_button"]["x"], click_targets["queue_button"]["y"])
                self.player_button_coors = (click_targets["queue_button"]["x"], click_targets["queue_button"]["y"])
            if "next" in click_targets:
                self.main_br_button_coordinates = (click_targets["next"]["x"], click_targets["next"]["y"])
            if "assign_damage_done" in click_targets:
                self.assign_damage_done_coors = (click_targets["assign_damage_done"]["x"], click_targets["assign_damage_done"]["y"])
            if "opponent_avatar" in click_targets:
                self.opponent_avatar_coors = (click_targets["opponent_avatar"]["x"], click_targets["opponent_avatar"]["y"])
            if "hand_scan_points" in click_targets:
                self.hand_scan_p1 = (click_targets["hand_scan_points"]["p1"]["x"], click_targets["hand_scan_points"]["p1"]["y"])
                self.hand_scan_p2 = (click_targets["hand_scan_points"]["p2"]["x"], click_targets["hand_scan_points"]["p2"]["y"])
            if "stack_scan_points" in click_targets:
                self.stack_scan_p1 = (click_targets["stack_scan_points"]["p1"]["x"], click_targets["stack_scan_points"]["p1"]["y"])
                self.stack_scan_p2 = (click_targets["stack_scan_points"]["p2"]["x"], click_targets["stack_scan_points"]["p2"]["y"])
            if "stack_scan_step" in click_targets:
                try:
                    self.stack_scan_step = int(click_targets["stack_scan_step"])
                except (TypeError, ValueError):
                    pass
            if "stack_scan_fallback_points" in click_targets:
                self.stack_scan_fallback_p1 = (
                    click_targets["stack_scan_fallback_points"]["p1"]["x"],
                    click_targets["stack_scan_fallback_points"]["p1"]["y"],
                )
                self.stack_scan_fallback_p2 = (
                    click_targets["stack_scan_fallback_points"]["p2"]["x"],
                    click_targets["stack_scan_fallback_points"]["p2"]["y"],
                )
            if "stack_scan_fallback_step" in click_targets:
                try:
                    self.stack_scan_fallback_step = int(click_targets["stack_scan_fallback_step"])
                except (TypeError, ValueError):
                    pass
            if "log_out_btn" in click_targets:
                self.log_out_btn_coors = (click_targets["log_out_btn"]["x"], click_targets["log_out_btn"]["y"])
            if "log_out_ok_btn" in click_targets:
                self.log_out_ok_btn_coors = (click_targets["log_out_ok_btn"]["x"], click_targets["log_out_ok_btn"]["y"])
            elif "logout_ok_btn" in click_targets:
                self.log_out_ok_btn_coors = (click_targets["logout_ok_btn"]["x"], click_targets["logout_ok_btn"]["y"])

        self.updated_game_state = GameState()
        self.__inst_id_grp_id_dict = {}
        self.__match_end_callback = None
        self.__last_match_won: bool | None = None
        self.__attack_target_required = False
        # MTGA system seat id for the local player (can be 1 or 2)
        self.__system_seat_id = None
        self.__last_target_select_source_id = None
        self.__last_target_select_ts = 0.0
        self.__pending_target_select = None
        self.__last_submit_targets_ts = 0.0
        self.__pending_select_n = None
        self.__target_submit_cooldown_sec = 1.0
        self._account_switch_interval = max(0, int(account_switch_minutes or 0)) * 60
        self._credentials_path = credentials_path or ""
        self._account_cycle_index = int(account_cycle_index or 0)
        self._account_play_order = account_play_order or []
        self._last_account_switch_ts = time.time()
        self._account_switch_pending = False
        self._account_switch_in_progress = False
        self._queue_after_login = False
        self._queue_spam_thread = None
        self._stop_queue_spam = False
        self._queue_ready = False
        self._match_end_dismissed = False
        self._post_match_ready_ts = None
        self._post_match_delay_sec = 30
        self._stop_requested = False
        self._post_login_action_done = False
        # Fixed timing for login phase
        self._login_delete_delay_sec = 20.0
        # Fallback coords if recorded playback isn't available
        self.log_out_btn_coors = (1716, 851)
        self.log_out_ok_btn_coors = (1875, 809)

    def _buttons_dir(self) -> str:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "Buttons"))

    def _click_image(self, image_path: str, label: str, confidence: float = 0.82, timeout: float = 20.0) -> bool:
        try:
            import pyautogui
        except Exception as e:
            bot_logger.log_error(f"{label}: pyautogui not available: {e}")
            return False

        if not os.path.exists(image_path):
            bot_logger.log_error(f"{label}: image not found at {image_path}")
            return False

        start = time.time()
        bot_logger.log_info(
            f"{label}: searching image with confidence={confidence:.2f}, timeout={timeout:.1f}s."
        )
        while (time.time() - start) < timeout:
            if self._stop_requested:
                bot_logger.log_info(f"{label}: search aborted (stop requested).")
                return False
            try:
                pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            except Exception:
                pos = None
            if pos:
                x, y = int(pos.x), int(pos.y)
                bot_logger.log_click(x, y, label)
                self.input.move_abs(x, y)
                time.sleep(0.1)
                self.input.left_down()
                time.sleep(0.06)
                self.input.left_up()
                return True
            time.sleep(0.5)
            if int((time.time() - start) * 10) % 20 == 0:
                elapsed = time.time() - start
                bot_logger.log_info(f"{label}: still searching ({elapsed:.1f}s elapsed).")
        bot_logger.log_error(f"{label}: image not found within {timeout:.1f}s")
        return False

    def _read_log_tail(self, path: str, max_bytes: int = 600000) -> str:
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                data = f.read()
            return data.decode("utf-8", errors="ignore")
        except Exception as e:
            bot_logger.log_error(f"Failed to read player.log tail: {e}")
            return ""

    def _extract_latest_quests(self) -> list[dict]:
        if not self._log_path:
            return []
        log_tail = self._read_log_tail(self._log_path)
        if not log_tail:
            return []
        idx = log_tail.rfind('"quests"')
        if idx == -1:
            return []
        start = log_tail.rfind("{", 0, idx)
        if start == -1:
            return []
        decoder = json.JSONDecoder()
        try:
            payload, _ = decoder.raw_decode(log_tail[start:])
        except Exception:
            return []
        quests = payload.get("quests", [])
        if isinstance(quests, list):
            return quests
        return []

    def _parse_guild_quests(self) -> list[dict]:
        quests = self._extract_latest_quests()
        parsed = []
        bot_logger.log_info(f"Post-login: parsed {len(quests)} quest entries from player.log.")
        for quest in quests:
            loc_key = str(quest.get("locKey", "")).lower()
            guild = None
            for name in _GUILD_COLOR_MAP:
                if name in loc_key:
                    guild = name
                    break
            if not guild:
                continue
            gold = 0
            chest = quest.get("chestDescription") or {}
            loc_params = chest.get("locParams") or {}
            if isinstance(loc_params, dict):
                try:
                    gold = int(loc_params.get("number1") or 0)
                except (TypeError, ValueError):
                    gold = 0
            parsed.append({"guild": guild, "gold": gold})
            bot_logger.log_info(f"Post-login: quest guild={guild} gold={gold}.")
        return parsed

    def _select_best_quest(self) -> dict | None:
        quests = self._parse_guild_quests()
        if not quests:
            return None
        quests.sort(key=lambda q: q.get("gold", 0), reverse=True)
        return quests[0]

    def _resolve_account_dir(self, account_index: int) -> str | None:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        target = f"acc{account_index + 1}"
        try:
            for entry in os.listdir(base_dir):
                full = os.path.join(base_dir, entry)
                if not os.path.isdir(full):
                    continue
                normalized = entry.lower().replace("_", "")
                if normalized == target:
                    return full
        except Exception:
            return None
        return None

    def _choose_deck_image(self, account_index: int, target_letters: str | None) -> str | None:
        account_dir = self._resolve_account_dir(account_index)
        if not account_dir:
            bot_logger.log_error("Post-login: account folder not found.")
            return None
        images = []
        for name in os.listdir(account_dir):
            if name.lower().endswith((".png", ".jpg", ".jpeg")):
                images.append(name)
        if not images:
            bot_logger.log_error("Post-login: no deck images found in account folder.")
            return None
        if not target_letters:
            bot_logger.log_info(f"Post-login: no target letters; defaulting to {images[0]}.")
            return os.path.join(account_dir, images[0])

        target_set = set(target_letters.upper())
        best = None
        best_score = (-1, -999, 0, "")
        for name in images:
            stem = os.path.splitext(name)[0]
            name_letters = {ch for ch in stem.upper() if ch in _COLOR_LETTERS}
            score = len(name_letters & target_set)
            extra = len(name_letters - target_set)
            bot_logger.log_info(
                f"Post-login: deck candidate={name} letters={''.join(sorted(name_letters))} "
                f"score={score} extra={extra}."
            )
            tie = (score, -extra, -len(stem), name.lower())
            if tie > best_score:
                best_score = tie
                best = name
        if best is None or best_score[0] <= 0:
            bot_logger.log_info("Post-login: no strong deck match, using first image.")
            return os.path.join(account_dir, images[0])
        bot_logger.log_info(
            f"Post-login: selected deck={best} with score={best_score[0]} extra={-best_score[1]}."
        )
        return os.path.join(account_dir, best)

    def _run_post_login_routine(self, account_index: int) -> bool:
        if self._stop_requested:
            return False
        quest = self._select_best_quest()
        if quest:
            guild = quest.get("guild")
            gold = quest.get("gold", 0)
            colors = _GUILD_COLOR_MAP.get(guild or "", "")
            bot_logger.log_info(
                f"Post-login: selected quest guild={guild} colors={colors} gold={gold}."
            )
        else:
            guild = None
            colors = ""
            bot_logger.log_info("Post-login: no guild quests found; using fallback deck.")

        deck_image = self._choose_deck_image(account_index, colors)
        if not deck_image:
            return False

        buttons_dir = self._buttons_dir()
        play_btn = os.path.join(buttons_dir, "play_btn.png")
        find_btn = os.path.join(buttons_dir, "find_match_btn.png")
        hist_btn = os.path.join(buttons_dir, "hist_play_btn.png")
        decks_btn = os.path.join(buttons_dir, "my_decks.png")

        bot_logger.log_info("Post-login: navigating Play > Find Match > Historic Play > My Decks.")
        if not self._click_image(play_btn, "POST_LOGIN_PLAY"):
            return False
        time.sleep(1.0)
        if not self._click_image(find_btn, "POST_LOGIN_FIND_MATCH"):
            return False
        time.sleep(1.0)
        if not self._click_image(hist_btn, "POST_LOGIN_HIST_PLAY"):
            return False
        time.sleep(1.0)
        if not self._click_image(decks_btn, "POST_LOGIN_MY_DECKS"):
            return False
        time.sleep(1.0)

        bot_logger.log_info(f"Post-login: selecting deck image {os.path.basename(deck_image)}.")
        if not self._click_image(deck_image, "POST_LOGIN_DECK"):
            return False
        time.sleep(1.0)
        if not self._click_image(play_btn, "POST_LOGIN_PLAY_CONFIRM"):
            return False

        bot_logger.log_info("Post-login: deck selected and play clicked.")
        return True

    def start_game_from_home_screen(self):
        if self._account_switch_in_progress or self._account_switch_due():
            self._account_switch_pending = True
            bot_logger.log_info("Account switch pending; skipping queue click.")
            return
        bot_logger.log_info("Queue attempt: clicking queue button.")
        bot_logger.log_click(self.home_play_button_coors[0], self.home_play_button_coors[1], "QUEUE_BUTTON")
        self.input.move_abs(self.home_play_button_coors[0], self.home_play_button_coors[1])
        self.input.left_down()
        time.sleep(0.2)
        self.input.left_up()
        time.sleep(1)
        self.input.left_down()
        time.sleep(0.2)
        self.input.left_up()

    def start_monitor(self) -> None:
        self.log_reader.start_log_monitor()

    def start_game(self) -> None:
        self._stop_requested = False
        self.start_monitor()
        self.start_queueing()

    def dismiss_remote_request(self) -> None:
        return

    def set_decision_callback(self, method) -> None:
        self.__decision_callback = method

    def set_mulligan_decision_callback(self, method) -> None:
        self.__mulligan_decision_callback = method

    def set_action_success_callback(self, method) -> None:
        self.__action_success_callback = method

    def set_match_end_callback(self, method) -> None:
        self.__match_end_callback = method

    def end_game(self) -> None:
        self._stop_requested = True
        # Prevent any future decisions / restarts from firing after a UI stop.
        if self.__decision_execution_thread is not None:
            try:
                self.__decision_execution_thread.cancel()
            except Exception:
                pass
            self.__decision_execution_thread = None
        if self.__mulligan_execution_thread is not None:
            try:
                self.__mulligan_execution_thread.cancel()
            except Exception:
                pass
            self.__mulligan_execution_thread = None

        try:
            self.stop_inactivity_timer()
        except Exception:
            pass

        # Stop any background queue spam/account switch loops.
        self._stop_queue_spam = True
        self._account_switch_pending = False
        self._account_switch_in_progress = False
        self._queue_after_login = False

        self.__decision_callback = None
        self.__mulligan_decision_callback = None
        self.__action_success_callback = None

        try:
            if hasattr(self.log_reader, "is_monitoring") and self.log_reader.is_monitoring():
                self.log_reader.stop_log_monitor()
        except Exception:
            # UI stop should never crash; at worst the monitor thread will exit on process end.
            pass

    def cast(self, card_id: int) -> None:
        # Clear any stale hover events from previous scans
        self.log_reader.clear_new_line_flag(self.patterns['hover_id'])

        # Move above start point first to reset any hover states
        reset_pos = (self.hand_scan_p1[0], self.hand_scan_p1[1] - 100)
        bot_logger.log_move(reset_pos[0], reset_pos[1], f"RESET_BEFORE_SCAN (target card_id={card_id})")
        self.input.move_abs(reset_pos[0], reset_pos[1])
        time.sleep(0.5)

        # Move to start of hand scan
        bot_logger.log_move(self.hand_scan_p1[0], self.hand_scan_p1[1], "START_HAND_SCAN")
        self.input.move_abs(self.hand_scan_p1[0], self.hand_scan_p1[1])

        current_hovered_id = None
        start_x = self.hand_scan_p1[0]
        end_x = self.hand_scan_p2[0]

        # Ensure we are scanning in the correct direction (left to right usually)
        direction = 1 if end_x > start_x else -1
        total_dx = (end_x - start_x) if end_x != start_x else 1
        start_y = self.hand_scan_p1[1]
        end_y = self.hand_scan_p2[1]

        while current_hovered_id != card_id:
            # Check if we have exceeded the scan area
            current_x = self.input.position().x
            if (direction == 1 and current_x >= end_x) or (direction == -1 and current_x <= end_x):
                bot_logger.log_error(f"SCAN_FAILED: Card {card_id} not found. Scanned from x={start_x} to x={end_x}, ended at x={current_x}")
                print(f"Scanned entire hand area but did not find card_id: {card_id}")
                break

            # Move slightly to find next card
            # We move until we get a log update, or until we move a certain distance?
            # Original code waited for log update. We should do the same but with bounds check.

            # Inner loop: move until log updates or bounds hit
            while not self.log_reader.has_new_line(self.patterns['hover_id']):
                step_dx = self.cast_card_dist * direction
                pos = self.input.position()
                next_x = pos.x + step_dx
                # Follow a (potentially sloped) scan line from p1 -> p2 to better match fanned hands.
                t = (next_x - start_x) / total_dx
                if t < 0:
                    t = 0
                elif t > 1:
                    t = 1
                desired_y = int(round(start_y + t * (end_y - start_y)))
                dy = desired_y - pos.y
                self.input.move_rel(step_dx, dy)
                time.sleep(self.cast_speed)

                # Check bounds inside inner loop too
                current_x = self.input.position().x
                if (direction == 1 and current_x >= end_x) or (direction == -1 and current_x <= end_x):
                    break

            if self.log_reader.has_new_line(self.patterns['hover_id']):
                parsed = self.__parse_hover_id_line(
                    self.log_reader.get_latest_line_containing_pattern(self.patterns['hover_id'])
                )
                if parsed is None:
                    continue
                current_hovered_id = parsed
                bot_logger.log_hover(current_hovered_id)
                print(str(current_hovered_id) + '|' + str(card_id))
            else:
                 # Break outer loop if we hit bounds without finding new log line
                 bot_logger.log_error(
                     f"SCAN_STOPPED: No hover update before bounds (target={card_id}, start=({start_x},{start_y}), end=({end_x},{end_y}))"
                 )
                 break

        if current_hovered_id == card_id:
            click_pos = self.input.position()
            bot_logger.log_click(click_pos.x, click_pos.y, f"CAST_CARD (id={card_id})")
            time.sleep(0.5)
            self.input.left_click(1)
            time.sleep(0.1)
            self.input.left_click(1)
            time.sleep(0.7)

        # Reset position
        reset_pos = (self.hand_scan_p1[0], self.hand_scan_p1[1] - 100)
        bot_logger.log_move(reset_pos[0], reset_pos[1], "RESET_AFTER_CAST")
        self.input.move_abs(reset_pos[0], reset_pos[1])

    def all_attack(self) -> None:
        bot_logger.log_click(self.main_br_button_coordinates[0], self.main_br_button_coordinates[1], "ATTACK_ALL")
        self.input.move_abs(self.main_br_button_coordinates[0], self.main_br_button_coordinates[1])
        self.input.left_click(1)
        time.sleep(1)
        self.input.left_click(1)
        if self.__attack_target_required:
            time.sleep(0.3)
            self.select_target(-1)

    def select_target(self, target_id: int) -> None:
        bot_logger.log_click(
            self.opponent_avatar_coors[0],
            self.opponent_avatar_coors[1],
            f"SELECT_OPPONENT_AVATAR (target_id={target_id})",
        )
        self.input.move_abs(self.opponent_avatar_coors[0], self.opponent_avatar_coors[1])
        time.sleep(0.2)
        self.input.left_click(1)
        time.sleep(0.2)
        self.__attack_target_required = False

    def activate_ability(self, card_id: int, ability_id: int) -> None:
        bot_logger.log_info(f"Activating ability: card_id={card_id}, ability_id={ability_id}")
        # Most optional triggers are confirmed via the bottom-right prompt button.
        time.sleep(0.2)
        self.submit_selection()
    
    def select_hand_card(self, card_id: int, clicks: int = 1) -> bool:
        """Select a card in hand by hovering until objectId matches, then click."""
        # Clear any stale hover events from previous scans
        self.log_reader.clear_new_line_flag(self.patterns['hover_id'])

        # Move above start point first to reset any hover states
        reset_pos = (self.hand_scan_p1[0], self.hand_scan_p1[1] - 100)
        bot_logger.log_move(reset_pos[0], reset_pos[1], f"RESET_BEFORE_HAND_SELECT (target card_id={card_id})")
        self.input.move_abs(reset_pos[0], reset_pos[1])
        time.sleep(0.3)

        # Move to start of hand scan
        bot_logger.log_move(self.hand_scan_p1[0], self.hand_scan_p1[1], "START_HAND_SELECT_SCAN")
        self.input.move_abs(self.hand_scan_p1[0], self.hand_scan_p1[1])

        current_hovered_id = None
        start_x = self.hand_scan_p1[0]
        end_x = self.hand_scan_p2[0]

        # Ensure we are scanning in the correct direction (left to right usually)
        direction = 1 if end_x > start_x else -1
        total_dx = (end_x - start_x) if end_x != start_x else 1
        start_y = self.hand_scan_p1[1]
        end_y = self.hand_scan_p2[1]

        while current_hovered_id != card_id:
            current_x = self.input.position().x
            if (direction == 1 and current_x >= end_x) or (direction == -1 and current_x <= end_x):
                bot_logger.log_error(
                    f"HAND_SELECT_FAILED: Card {card_id} not found. Scanned x={start_x}..{end_x}, end={current_x}"
                )
                return False

            while not self.log_reader.has_new_line(self.patterns['hover_id']):
                step_dx = self.cast_card_dist * direction
                pos = self.input.position()
                next_x = pos.x + step_dx
                t = (next_x - start_x) / total_dx
                if t < 0:
                    t = 0
                elif t > 1:
                    t = 1
                desired_y = int(round(start_y + t * (end_y - start_y)))
                dy = desired_y - pos.y
                self.input.move_rel(step_dx, dy)
                time.sleep(self.cast_speed)

                current_x = self.input.position().x
                if (direction == 1 and current_x >= end_x) or (direction == -1 and current_x <= end_x):
                    break

            if self.log_reader.has_new_line(self.patterns['hover_id']):
                parsed = self.__parse_hover_id_line(
                    self.log_reader.get_latest_line_containing_pattern(self.patterns['hover_id'])
                )
                if parsed is None:
                    continue
                current_hovered_id = parsed
                bot_logger.log_hover(current_hovered_id)
            else:
                bot_logger.log_error(
                    f"HAND_SELECT_STOPPED: No hover update before bounds (target={card_id})"
                )
                return False

        click_pos = self.input.position()
        bot_logger.log_click(click_pos.x, click_pos.y, f"SELECT_HAND_CARD (id={card_id})")
        for _ in range(max(1, int(clicks))):
            self.input.left_click(1)
            time.sleep(0.1)
        return True

    def select_stack_item(self, card_id: int, clicks: int = 1) -> bool:
        """Select a stack/prompt item by scanning a grid for matching hover objectId."""
        if self.__select_object_in_region(
            card_id=card_id,
            p1=self.stack_scan_p1,
            p2=self.stack_scan_p2,
            step=self.stack_scan_step,
            clicks=clicks,
            label="STACK_ITEM",
        ):
            return True
        bot_logger.log_info("Stack scan fallback to center region")
        return self.__select_object_in_region(
            card_id=card_id,
            p1=self.stack_scan_fallback_p1,
            p2=self.stack_scan_fallback_p2,
            step=self.stack_scan_fallback_step,
            clicks=clicks,
            label="STACK_ITEM_FALLBACK",
        )

    def submit_selection(self) -> None:
        bot_logger.log_click(self.main_br_button_coordinates[0], self.main_br_button_coordinates[1], "SUBMIT_SELECTION")
        self.input.move_abs(self.main_br_button_coordinates[0], self.main_br_button_coordinates[1])
        time.sleep(0.1)
        self.input.left_click(1)

    def resolve(self) -> None:
        turn_info = self.updated_game_state.get_turn_info() or {}
        my_seat = self.__system_seat_id or turn_info.get('decisionPlayer') or 1

        # MTGA's bottom-right "pass/next/resolve/no-blocks" button sometimes shifts vertically during
        # opponent DeclareAttack. Historically we clicked slightly above to compensate, but that can
        # miss depending on UI scale/layout. Use the calibrated button position first, then a small
        # upward fallback only for that specific case.
        positions = [self.main_br_button_coordinates]
        if turn_info.get('step') == 'Step_DeclareAttack' and turn_info.get('activePlayer') != my_seat:
            fallback_y = self.main_br_button_coordinates[1] - 50
            min_y = self.screen_bounds[0][1]
            positions.append((self.main_br_button_coordinates[0], max(min_y, fallback_y)))

        for pos in positions:
            bot_logger.log_click(pos[0], pos[1], "RESOLVE")
            self.input.move_abs(pos[0], pos[1])
            self.input.left_click(1)
            time.sleep(0.05)

    def auto_pass(self) -> None:
        self.input.tap_enter()
        time.sleep(0.4)

    def __select_object_in_region(
        self,
        card_id: int,
        p1: tuple[int, int],
        p2: tuple[int, int],
        step: int,
        clicks: int,
        label: str,
    ) -> bool:
        self.log_reader.clear_new_line_flag(self.patterns['hover_id'])
        x1, y1 = p1
        x2, y2 = p2
        x_min, x_max = (x1, x2) if x1 <= x2 else (x2, x1)
        y_min, y_max = (y1, y2) if y1 <= y2 else (y2, y1)
        step = max(10, int(step))

        reset_x = x_min
        reset_y = max(self.screen_bounds[0][1], y_min - 80)
        bot_logger.log_move(reset_x, reset_y, f"RESET_BEFORE_{label} (target card_id={card_id})")
        self.input.move_abs(reset_x, reset_y)
        time.sleep(0.1)

        for y in range(y_min, y_max + 1, step):
            for x in range(x_min, x_max + 1, step):
                self.log_reader.clear_new_line_flag(self.patterns['hover_id'])
                self.input.move_abs(x, y)
                time.sleep(0.05)
                if not self.log_reader.has_new_line(self.patterns['hover_id']):
                    continue
                parsed = self.__parse_hover_id_line(
                    self.log_reader.get_latest_line_containing_pattern(self.patterns['hover_id'])
                )
                if parsed is None:
                    continue
                bot_logger.log_hover(parsed)
                if parsed != card_id:
                    continue
                bot_logger.log_click(x, y, f"SELECT_{label} (id={card_id})")
                for _ in range(max(1, int(clicks))):
                    self.input.left_click(1)
                    time.sleep(0.1)
                return True

        bot_logger.log_error(f"{label}_FAILED: Card {card_id} not found in scan region")
        return False

    def unconditional_auto_pass(self) -> None:
        self.input.tap_shift_enter()
        time.sleep(0.4)

    def get_game_state(self) -> 'GameStateSecondary':
        return self.updated_game_state

    def keep(self, keep: bool):
        if keep:
            bot_logger.log_click(self.mulligan_keep_coors[0], self.mulligan_keep_coors[1], "KEEP_HAND")
            self.input.move_abs(self.mulligan_keep_coors[0], self.mulligan_keep_coors[1])
        else:
            bot_logger.log_click(self.mulligan_mull_coors[0], self.mulligan_mull_coors[1], "MULLIGAN")
            self.input.move_abs(self.mulligan_mull_coors[0], self.mulligan_mull_coors[1])
        self.input.left_click(1)

    def click_assign_damage_done(self):
        """Click the Done button during damage assignment"""
        bot_logger.log_click(self.assign_damage_done_coors[0], self.assign_damage_done_coors[1], "ASSIGN_DAMAGE_DONE")
        self.input.move_abs(self.assign_damage_done_coors[0], self.assign_damage_done_coors[1])
        time.sleep(0.5)
        self.input.left_click(1)
        time.sleep(0.2)
        # Maybe click twice to be sure? The user said "click", usually one is enough but delays help.
        # Just one click for now as per other methods.
        bot_logger.log_info("Clicked Assign Damage Done button")

    def __handle_inactivity_timeout(self):
        """Handle timeout when no activity for 3 minutes - click next button repeatedly"""
        bot_logger.log_info("TIMEOUT: No activity for 3 minutes - clicking next button")
        self.resolve()  # Click the "next" button
        # Reschedule timer to keep clicking until turn ends
        self.__inactivity_timer = threading.Timer(
            5.0,  # Click every 5 seconds until something happens
            self.__handle_inactivity_timeout
        )
        self.__inactivity_timer.start()

    def reset_inactivity_timer(self):
        """Reset the inactivity timer - called when a decision is made"""
        if self.__inactivity_timer is not None:
            self.__inactivity_timer.cancel()
            self.__inactivity_timer = None
        # Start fresh 3-minute timer
        self.__inactivity_timer = threading.Timer(
            self.__inactivity_timeout,
            self.__handle_inactivity_timeout
        )
        self.__inactivity_timer.start()
        bot_logger.log_info("Inactivity timer reset (3 minutes)")

    def stop_inactivity_timer(self):
        """Stop the inactivity timer completely"""
        if self.__inactivity_timer is not None:
            self.__inactivity_timer.cancel()
            self.__inactivity_timer = None

    def dismiss_end_screen(self):
        """Click to dismiss match end screen and return to main menu"""
        if self._stop_requested:
            bot_logger.log_info("Dismiss end screen skipped: stop requested.")
            return
        # Click in center of screen to dismiss end screen
        center_x = (self.screen_bounds[0][0] + self.screen_bounds[1][0]) // 2
        center_y = (self.screen_bounds[0][1] + self.screen_bounds[1][1]) // 2
        bot_logger.log_click(center_x, center_y, "DISMISS_END_SCREEN")
        self.input.move_abs(center_x, center_y)
        time.sleep(0.5)
        self.input.left_click(1)
        time.sleep(1)
        # Click again in case first click wasn't enough
        self.input.left_click(1)
        bot_logger.log_info("Match completed - dismissed end screen")
        self._match_end_dismissed = True
        self._post_match_ready_ts = time.time()
        threading.Timer(self._post_match_delay_sec, self._maybe_post_match_action).start()
        if self._queue_ready:
            self._maybe_post_match_action()

        # Call match end callback to trigger restart
        if self.__match_end_callback:
            try:
                self.__match_end_callback(self.__last_match_won)
            except TypeError:
                # Backwards compatible: callback may not accept args
                self.__match_end_callback()

    def reset_for_new_game(self):
        """Reset controller state for a new game - complete fresh start"""
        bot_logger.log_info("Resetting controller state for new game")
        self.__has_mulled_keep = False
        self.__system_seat_id = None
        self.__last_match_won = None
        self.__attack_target_required = False
        self.updated_game_state = GameState()
        self.__inst_id_grp_id_dict = {}
        # Cancel any pending decision timers
        if self.__decision_execution_thread is not None:
            self.__decision_execution_thread.cancel()
            self.__decision_execution_thread = None
        if self.__mulligan_execution_thread is not None:
            self.__mulligan_execution_thread.cancel()
            self.__mulligan_execution_thread = None
        # Cancel inactivity timer
        self.stop_inactivity_timer()
        # Reset all cached log data for fresh start
        self.log_reader.reset_all_patterns()
        bot_logger.log_info("Controller state reset complete")

    def get_inst_id_grp_id_dict(self):
        return self.__inst_id_grp_id_dict

    def __parse_hover_id_line(self, line):
        """
        Extracts `objectId` from hover log lines, filtering to our seat if possible.
        MTGA logs sometimes emit full JSON lines, sometimes fragments like `"objectId": 123`.
        """
        if not line:
            return None
        try:
            start = line.find("{")
            if start != -1:
                payload = json.loads(line[start:])
                # Prefer UI hover messages with seatIds filtering.
                messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
                for msg in messages:
                    ui_msg = msg.get("uiMessage") if isinstance(msg, dict) else None
                    if not ui_msg:
                        continue
                    seat_ids = ui_msg.get("seatIds", [])
                    if isinstance(seat_ids, list) and self.__system_seat_id is not None:
                        if self.__system_seat_id not in seat_ids:
                            continue
                    hover = ui_msg.get("onHover", {})
                    if isinstance(hover, dict) and isinstance(hover.get("objectId"), int):
                        return hover["objectId"]
                # Fallback: Look for the first objectId key in nested dicts.
                stack = [payload]
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        if "objectId" in cur and isinstance(cur["objectId"], int):
                            return cur["objectId"]
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
        except Exception:
            pass
        m = re.search(r'"objectId"\s*:\s*(\d+)', line)
        if m:
            return int(m.group(1))
        return None

    def __log_callback(self, pattern: str, line_containing_pattern: str):
        if pattern == self.patterns["game_state"]:
            self.__update_game_state(json.loads(line_containing_pattern))
            if self._queue_spam_thread and self._queue_spam_thread.is_alive():
                self._stop_queue_spam = True
            if self._queue_spam_thread and self._queue_spam_thread.is_alive():
                self._stop_queue_spam = True
        elif pattern == self.patterns["match_completed"]:
            bot_logger.log_info("Detected match completed event")
            outcome = self.__infer_match_won(line_containing_pattern)
            if outcome is not None:
                self.__last_match_won = outcome
            self._match_end_dismissed = False
            self._post_match_ready_ts = None
            # Wait a moment for end screen to fully appear, then dismiss it
            threading.Timer(6.0, self.dismiss_end_screen).start()
            if self._account_switch_due():
                self._account_switch_pending = True
        elif pattern == self.patterns["queue_ready_marker"]:
            self._handle_queue_ready()
        elif pattern == self.patterns["main_nav_loaded"]:
            self._handle_main_nav_loaded()
        elif pattern == self.patterns["assign_damage"]:
            # Wait a small delay to ensure UI is ready
            threading.Timer(1.0, self.click_assign_damage_done).start()
        elif pattern == self.patterns["declare_attackers"]:
            self.__handle_declare_attackers_req(line_containing_pattern)
        elif pattern == self.patterns["select_n"]:
            self.__handle_select_n_req(line_containing_pattern)
        elif pattern == self.patterns["select_targets"]:
            self.__handle_select_targets_req(line_containing_pattern)

    def _account_switch_due(self) -> bool:
        if self._account_switch_interval <= 0:
            return False
        return (time.time() - self._last_account_switch_ts) >= self._account_switch_interval

    def _replay_recorded_logout(self) -> bool:
        return self._replay_named_record("Account Switch", tag_prefix="LOGOUT", allow_keys={"esc"})

    def _replay_named_record(self, name: str, tag_prefix: str = "REPLAY", allow_keys: set[str] | None = None) -> bool:
        path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "recorded_actions_records.json")
        )
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False

        records = data.get("records", [])
        if not records:
            return False

        record = None
        for rec in reversed(records):
            if rec.get("name") == name:
                record = rec
                break
        if record is None:
            return False

        actions = record.get("actions", [])
        if not actions:
            return False

        bot_logger.log_info(f"Replaying record '{name}' (pynput playback).")
        try:
            from pynput import mouse, keyboard
        except Exception as e:
            bot_logger.log_error(f"{tag_prefix}_REPLAY_FAILED: pynput not available: {e}")
            return False

        m = mouse.Controller()
        k = keyboard.Controller()
        for ev in actions:
            if self._stop_requested:
                bot_logger.log_info(f"{tag_prefix}_REPLAY_ABORTED: stop requested.")
                return False
            delay = float(ev.get("delay", 0.0))
            if delay > 0:
                time.sleep(delay)
            if ev.get("type") == "key":
                key_name = ev.get("key", "")
                if allow_keys is not None and key_name not in allow_keys:
                    continue
                if key_name == "esc":
                    k.press(keyboard.Key.esc)
                    k.release(keyboard.Key.esc)
                elif len(key_name) == 1:
                    k.press(key_name)
                    k.release(key_name)
                else:
                    if hasattr(keyboard.Key, key_name):
                        key_obj = getattr(keyboard.Key, key_name)
                        k.press(key_obj)
                        k.release(key_obj)
            elif ev.get("type") == "click":
                try:
                    x = int(float(ev.get("x", 0)))
                    y = int(float(ev.get("y", 0)))
                except Exception:
                    x, y = 0, 0
                if x and y:
                    bot_logger.log_click(x, y, f"{tag_prefix}_REPLAY_CLICK")
                    m.position = (x, y)
                    time.sleep(0.05)
                    m.press(mouse.Button.left)
                    time.sleep(0.05)
                    m.release(mouse.Button.left)
        return True



    def _handle_queue_ready(self) -> None:
        if not self._queue_ready:
            self._queue_ready = True
            bot_logger.log_info("Queue-ready marker detected.")
        if self._account_switch_in_progress:
            bot_logger.log_info("Queue-ready marker ignored: account switch in progress.")
            return
        if self._match_end_dismissed:
            self._maybe_post_match_action()

    def _handle_main_nav_loaded(self) -> None:
        bot_logger.log_info("MainNav loaded.")
        if not self._queue_ready:
            return
        time.sleep(1.5)
        if self._account_switch_in_progress:
            return
        if self._match_end_dismissed:
            self._maybe_post_match_action()

    def _maybe_post_match_action(self) -> None:
        if self._stop_requested:
            return
        if self._account_switch_in_progress:
            return
        if self._post_match_ready_ts is None:
            return
        elapsed = time.time() - self._post_match_ready_ts
        if elapsed < self._post_match_delay_sec:
            remaining = self._post_match_delay_sec - elapsed
            bot_logger.log_info(f"Post-match delay active ({remaining:.1f}s remaining).")
            # Ensure we re-check when the delay elapses to avoid getting stuck at ~0s.
            threading.Timer(max(0.1, remaining + 0.1), self._maybe_post_match_action).start()
            return
        if self._account_switch_pending or self._account_switch_due():
            bot_logger.log_info("Post-match UI ready; starting account switch.")
            threading.Thread(target=self._perform_account_switch, daemon=True).start()
            return
        if self._queue_after_login:
            self._queue_after_login = False
            bot_logger.log_info("Post-match UI ready after login; resuming queue spam.")
            self.start_queueing()
            return
        bot_logger.log_info("Post-match UI ready; resuming queue spam.")
        self.start_queueing()

    def should_defer_post_match_actions(self) -> bool:
        if self._account_switch_in_progress:
            return True
        if self._account_switch_pending or self._account_switch_due():
            return True
        if self._post_match_ready_ts is None:
            return False
        return (time.time() - self._post_match_ready_ts) < self._post_match_delay_sec

    def start_queueing(self) -> None:
        if self._account_switch_in_progress:
            bot_logger.log_info("Queue start requested but account switch in progress; ignoring.")
            return
        if self._queue_spam_thread and self._queue_spam_thread.is_alive():
            bot_logger.log_info("Queue spam already running.")
            return
        self._stop_queue_spam = False
        self._queue_ready = False
        bot_logger.log_info("Starting queue spam loop.")
        self._queue_spam_thread = threading.Thread(target=self._queue_spam_loop, daemon=True)
        self._queue_spam_thread.start()

    def _queue_spam_loop(self) -> None:
        while not self._stop_queue_spam:
            if self._account_switch_in_progress:
                bot_logger.log_info("Queue spam stopping: account switch in progress.")
                return
            if self._account_switch_due():
                self._account_switch_pending = True
                bot_logger.log_info("Account switch due; stopping queue spam and waiting for queue-ready marker.")
                return
            self.start_game_from_home_screen()
            time.sleep(3.0)

    def _perform_account_switch(self) -> None:
        if self._account_switch_in_progress:
            return
        self._account_switch_in_progress = True
        queued_after_login = False
        try:
            if self._stop_requested:
                bot_logger.log_info("Account switch aborted: stop requested.")
                return
            bot_logger.log_info("Account switch: starting logout/login flow.")
            if not self.log_out_btn_coors or not self.log_out_ok_btn_coors:
                bot_logger.log_error("Account switch failed: missing calibrated button(s).")
                self._account_switch_pending = False
                return

            accounts = self._load_accounts_from_credentials(self._credentials_path)
            if not accounts:
                bot_logger.log_error("Account switch failed: no accounts found in credentials file.")
                self._account_switch_pending = False
                return
            bot_logger.log_info(
                "Accounts loaded: count={} indices={}".format(
                    len(accounts), [a.get("index") for a in accounts]
                )
            )

            custom_order = self._resolve_account_play_order(accounts)
            if custom_order:
                if len(custom_order) == 1:
                    target_index = custom_order[0]
                    if target_index == self._account_cycle_index:
                        bot_logger.log_info("Account switch skipped: only one account in play order.")
                        self._last_account_switch_ts = time.time()
                        self._account_switch_pending = False
                        self._account_switch_in_progress = False
                        if not self._stop_requested:
                            self.start_queueing()
                        return
                    next_index = target_index
                else:
                    try:
                        pos = custom_order.index(self._account_cycle_index)
                    except ValueError:
                        pos = -1
                    if pos == -1:
                        next_index = custom_order[0]
                    else:
                        next_index = custom_order[(pos + 1) % len(custom_order)]
            else:
                # Default cycle order: Acc_2 -> Acc_3 -> Acc_1
                if len(accounts) >= 3:
                    order = [1, 2, 0]
                    try:
                        pos = order.index(self._account_cycle_index)
                    except ValueError:
                        pos = 2  # default to Acc_1 position
                    next_index = order[(pos + 1) % len(order)]
                else:
                    next_index = (self._account_cycle_index + 1) % len(accounts)
            account = accounts[next_index]

            bot_logger.log_info(f"Switching account to Acc_{next_index + 1}")
            bot_logger.log_info(f"Account cycle index: {self._account_cycle_index} -> {next_index}")
            self._post_login_action_done = False
            if not self._replay_recorded_logout():
                bot_logger.log_info("Recorded logout replay unavailable; falling back to fixed logout clicks.")
                bot_logger.log_info("Account switch: pressing ESC to open options menu.")
                self.input.tap_escape()
                time.sleep(2.0)
                bot_logger.log_info(
                    f"Account switch: clicking LOG_OUT_BTN at ({self.log_out_btn_coors[0]}, {self.log_out_btn_coors[1]})."
                )
                self._click(self.log_out_btn_coors, "LOG_OUT_BTN")
                time.sleep(0.15)
                self._click(self.log_out_btn_coors, "LOG_OUT_BTN")
                time.sleep(1.7)
                bot_logger.log_info(
                    f"Account switch: clicking LOG_OUT_OK_BTN at ({self.log_out_ok_btn_coors[0]}, {self.log_out_ok_btn_coors[1]})."
                )
                self._click(self.log_out_ok_btn_coors, "LOG_OUT_OK_BTN")
                time.sleep(0.15)
                self._click(self.log_out_ok_btn_coors, "LOG_OUT_OK_BTN")
            if self._stop_requested:
                bot_logger.log_info("Account switch aborted after logout: stop requested.")
                return
            bot_logger.log_info(f"Account switch: waiting {self._login_delete_delay_sec:.2f}s for login screen.")
            for _ in range(int(self._login_delete_delay_sec * 10)):
                if self._stop_requested:
                    bot_logger.log_info("Account switch aborted while waiting for login screen.")
                    return
                time.sleep(0.1)

            bot_logger.log_info("Account switch: entering credentials.")
            if self._stop_requested:
                bot_logger.log_info("Account switch aborted before typing: stop requested.")
                return
            self.input.tap_delete()
            time.sleep(0.2)
            self.input.type_text(account.get("email", ""))
            time.sleep(0.2)
            self.input.tap_tab()
            time.sleep(0.2)
            self.input.type_text(account.get("pw", ""))
            time.sleep(0.2)
            bot_logger.log_info("Account switch: submitting login with Enter.")
            self.input.tap_enter()
            bot_logger.log_info("Account switch: login submitted.")

            if not self._stop_requested:
                bot_logger.log_info("Account switch: waiting 40s before post-login record.")
                for _ in range(400):
                    if self._stop_requested:
                        break
                    time.sleep(0.1)
            if not self._stop_requested and not self._post_login_action_done:
                if self._run_post_login_routine(next_index):
                    self._post_login_action_done = True
            if not self._stop_requested and self._post_login_action_done:
                bot_logger.log_info("Post-login routine done; waiting 5s before queueing.")
                for _ in range(50):
                    if self._stop_requested:
                        break
                    time.sleep(0.1)
                if not self._stop_requested:
                    # Reset switch timer before queueing so we don't immediately mark as due.
                    self._last_account_switch_ts = time.time()
                    # Mark switch complete before queueing so start_queueing won't ignore.
                    self._account_switch_in_progress = False
                    self._queue_after_login = False
                    self.start_queueing()
                    queued_after_login = True

            self._account_cycle_index = next_index
            self._last_account_switch_ts = time.time()
            self._account_switch_pending = False
            if not queued_after_login:
                self._queue_after_login = True
            self._persist_account_cycle_index()
        except Exception as e:
            bot_logger.log_error(f"Account switch failed: {e}")
        finally:
            self._account_switch_in_progress = False

    def _resolve_account_play_order(self, accounts: list[dict]) -> list[int]:
        if not self._account_play_order:
            return []
        account_num_to_pos = {}
        for pos, acc in enumerate(accounts):
            try:
                num = int(acc.get("index"))
            except (TypeError, ValueError):
                continue
            account_num_to_pos[num] = pos

        order = []
        for raw in self._account_play_order:
            name = str(raw).strip().lower().replace(" ", "")
            if not name:
                continue
            num = None
            m = re.search(r"acc[_-]?(\\d+)", name)
            if m:
                try:
                    num = int(m.group(1))
                except ValueError:
                    num = None
            if num is None:
                continue
            pos = account_num_to_pos.get(num)
            if pos is None or pos in order:
                continue
            order.append(pos)
        return order

    def _load_accounts_from_credentials(self, path: str) -> list[dict]:
        if not path:
            return []
        try:
            with open(path, "r") as f:
                content = f.read()
        except Exception as e:
            bot_logger.log_error(f"Failed to read credentials file: {e}")
            return []

        accounts = []
        for match in re.finditer(r"Acc_(\d+)\s*=\s*{([^}]*)}", content, re.DOTALL | re.IGNORECASE):
            idx = int(match.group(1))
            block = match.group(2)
            email_m = re.search(r'["\']email["\']\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
            pw_m = re.search(r'["\']pw["\']\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
            if not email_m or not pw_m:
                continue
            accounts.append({"index": idx, "email": email_m.group(1), "pw": pw_m.group(1)})

        accounts.sort(key=lambda a: a["index"])
        return accounts

    def _persist_account_cycle_index(self) -> None:
        try:
            config_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "calibration_config.json")
            )
            if not os.path.exists(config_path):
                return
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["account_cycle_index"] = int(self._account_cycle_index)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            bot_logger.log_error(f"Failed to persist account cycle index: {e}")

    def _click(self, pos: tuple[int, int], tag: str) -> None:
        x, y = pos
        bot_logger.log_click(x, y, tag)
        self.input.move_abs(x, y)
        time.sleep(0.2)
        self.input.left_click(1)

    def __handle_select_n_req(self, line: str) -> None:
        try:
            start = line.find("{")
            if start == -1:
                return
            payload = json.loads(line[start:])
            messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
            for message in messages:
                if message.get("type") != "GREMessageType_SelectNReq":
                    continue
                if self.__system_seat_id is None:
                    return
                seat_ids = message.get("systemSeatIds") or []
                if self.__system_seat_id not in seat_ids:
                    continue
                req = message.get("selectNReq", {})
                ids = list(req.get("ids", []) or [])
                if not ids:
                    continue
                self.__pending_select_n = {"ids": ids, "ts": time.time()}
                min_sel = int(req.get("minSel", 1))
                if min_sel < 1:
                    min_sel = 1
                random.shuffle(ids)
                context = req.get("context")
                option_context = req.get("optionContext")
                use_stack_selection = (
                    context == "SelectionContext_TriggeredAbility"
                    or option_context == "OptionContext_Stacking"
                )
                hand_zone = self.updated_game_state.get_zone("ZoneType_Hand", self.__system_seat_id)
                hand_ids = set(hand_zone.get("objectInstanceIds", []) or []) if hand_zone else set()
                ids_in_hand = [cid for cid in ids if cid in hand_ids]
                use_hand_selection = bool(ids_in_hand) or not use_stack_selection
                if not ids_in_hand:
                    # Hand zone can be missing in this update (e.g., discard prompts from opponent effects).
                    # Fall back to the provided ids and retry selection after a brief delay.
                    bot_logger.log_info(
                        f"SelectN ids not in hand; attempting selection from prompt list. ids={ids}"
                    )
                    if use_stack_selection:
                        bot_logger.log_info("SelectN using stack scan for triggered ability selection")
                else:
                    ids = ids_in_hand

                def _verify_selection(selected_ids: list[int], attempt: int) -> None:
                    try:
                        if self.__system_seat_id is None:
                            return
                        hand_zone = self.updated_game_state.get_zone("ZoneType_Hand", self.__system_seat_id)
                        if not hand_zone:
                            return
                        hand_ids = set(hand_zone.get("objectInstanceIds", []) or [])
                        still_in_hand = [cid for cid in selected_ids if cid in hand_ids]
                        if still_in_hand and attempt < 2:
                            bot_logger.log_info(
                                f"SelectN verify: ids still in hand {still_in_hand}, retrying (attempt {attempt + 1})"
                            )
                            _attempt_selection(attempt + 1, delay=0.8)
                    except Exception as e:
                        bot_logger.log_error(f"SelectN verify failed: {e}")

                def _attempt_selection(attempt: int, delay: float) -> None:
                    def _do_selection():
                        selected = 0
                        selected_ids: list[int] = []
                        clicks = 1 if attempt == 1 else 2
                        for card_id in ids:
                            if selected >= min_sel:
                                break
                            if use_hand_selection:
                                selected_ok = self.select_hand_card(card_id, clicks=clicks)
                            else:
                                selected_ok = self.select_stack_item(card_id, clicks=clicks)
                            if selected_ok:
                                selected += 1
                                selected_ids.append(card_id)
                                time.sleep(0.3)
                        if not selected_ids:
                            bot_logger.log_error("SelectN failed to select any cards")
                            self.__pending_select_n = None
                            return
                        time.sleep(0.4)
                        self.submit_selection()
                        self.__pending_select_n = None
                        threading.Timer(1.2, _verify_selection, args=(selected_ids, attempt)).start()

                    threading.Timer(delay, _do_selection).start()

                delay = 0.6
                if not ids_in_hand:
                    delay = 1.0
                _attempt_selection(1, delay=delay)
        except Exception as e:
            bot_logger.log_error(f"Failed to handle SelectNReq: {e}")

    def __handle_select_targets_req(self, line: str) -> None:
        try:
            start = line.find("{")
            if start == -1:
                return
            payload = json.loads(line[start:])
            messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
            for message in messages:
                if message.get("type") != "GREMessageType_SelectTargetsReq":
                    continue
                if self.__system_seat_id is None:
                    return
                seat_ids = message.get("systemSeatIds") or []
                if self.__system_seat_id not in seat_ids:
                    continue
                source_id = message.get("selectTargetsReq", {}).get("sourceId")
                self.__update_pending_target_select(source_id)
                self.__schedule_target_selection(source_id, reason="SelectTargetsReq")
        except Exception as e:
            bot_logger.log_error(f"Failed to handle SelectTargetsReq: {e}")

    def __get_delay_timer_remaining(self) -> float:
        try:
            timers = self.updated_game_state.get_full_state().get("timers", []) or []
            for timer in timers:
                if timer.get("type") != "TimerType_Delay":
                    continue
                if not timer.get("running", False):
                    continue
                duration = float(timer.get("durationSec", 0) or 0)
                if "elapsedSec" in timer:
                    elapsed = float(timer.get("elapsedSec", 0) or 0)
                else:
                    elapsed = float(timer.get("elapsedMs", 0) or 0) / 1000.0
                remaining = duration - elapsed
                return max(0.0, remaining)
        except Exception:
            return 0.0
        return 0.0

    def __update_pending_target_select(
        self,
        source_id: int | None,
        *,
        min_t=_TARGET_FIELD_UNSET,
        max_t=_TARGET_FIELD_UNSET,
        selected=_TARGET_FIELD_UNSET,
    ) -> None:
        if source_id is None:
            source_id = -1
        pending = self.__pending_target_select or {}
        if pending.get("source_id") != source_id:
            pending = {"source_id": source_id}
        pending["ts"] = time.time()
        if min_t is not _TARGET_FIELD_UNSET:
            pending["min"] = min_t
        if max_t is not _TARGET_FIELD_UNSET:
            pending["max"] = max_t
        if selected is not _TARGET_FIELD_UNSET:
            pending["selected"] = selected
        self.__pending_target_select = pending

    def __pending_target_ready_to_submit(self) -> bool:
        pending = self.__pending_target_select or {}
        selected = pending.get("selected")
        min_t = pending.get("min", 1)
        try:
            selected_count = int(selected)
        except Exception:
            return False
        try:
            min_req = int(min_t) if min_t is not None else 1
        except Exception:
            min_req = 1
        return selected_count >= min_req

    def __get_target_click_offsets(self) -> list[tuple[int, int]]:
        # Try a small fan of offsets so we don't accidentally click the library.
        return [
            (0, 0),
            (-80, 0),
            (-120, 10),
            (-60, 25),
            (0, 35),
            (-90, 45),
        ]

    def __click_opponent_avatar_with_offset(self, offset: tuple[int, int], tag: str) -> None:
        x = self.opponent_avatar_coors[0] + offset[0]
        y = self.opponent_avatar_coors[1] + offset[1]
        bot_logger.log_click(x, y, tag)
        self.input.move_abs(x, y)
        time.sleep(0.4)
        self.input.left_click(1)
        time.sleep(0.3)

    def __schedule_target_selection(self, source_id: int | None, reason: str) -> None:
        now = time.time()
        if source_id is None:
            source_id = -1
        if self.__last_target_select_source_id == source_id and now - self.__last_target_select_ts < 1.0:
            return
        self.__last_target_select_source_id = source_id
        self.__last_target_select_ts = now
        self.__update_pending_target_select(source_id)
        bot_logger.log_info(f"{reason}: targeting opponent avatar")

        def _attempt_submit():
            if self.__pending_target_ready_to_submit():
                self.__last_submit_targets_ts = time.time()
                threading.Timer(0.3, self.submit_selection).start()
                return True
            return False

        def _retry_if_needed():
            if self.__pending_target_select and self.__is_selecting_targets():
                delay_remaining = self.__get_delay_timer_remaining()
                if delay_remaining > 0.05:
                    threading.Timer(delay_remaining + 0.2, _retry_if_needed).start()
                    return
                if _attempt_submit():
                    return
                age = time.time() - self.__last_target_select_ts
                pending = self.__pending_target_select or {}
                attempts = int(pending.get("attempts", 0))
                offsets = self.__get_target_click_offsets()
                if age < 5.0 and attempts < len(offsets):
                    offset = offsets[attempts]
                    pending["attempts"] = attempts + 1
                    self.__pending_target_select = pending
                    bot_logger.log_info(f"Target still pending, retrying opponent avatar (attempt {attempts + 1})")
                    self.__click_opponent_avatar_with_offset(offset, f"SELECT_OPPONENT_AVATAR_RETRY_{attempts + 1}")
                    threading.Timer(0.8, _attempt_submit).start()

        def _do_click():
            if _attempt_submit():
                return
            pending = self.__pending_target_select or {}
            pending["attempts"] = 1
            self.__pending_target_select = pending
            self.__click_opponent_avatar_with_offset((0, 0), "SELECT_OPPONENT_AVATAR")
            threading.Timer(0.8, _attempt_submit).start()
            threading.Timer(1.2, _retry_if_needed).start()

        delay_remaining = self.__get_delay_timer_remaining()
        start_delay = 0.8
        if delay_remaining > 0.05:
            start_delay = delay_remaining + 0.4
        threading.Timer(start_delay, _do_click).start()

    def __is_selecting_targets(self) -> bool:
        try:
            annotations = self.updated_game_state.get_annotations()
            for annotation in annotations:
                types = annotation.get("type", []) or []
                if "AnnotationType_PlayerSelectingTargets" not in types:
                    continue
                affector_id = annotation.get("affectorId")
                if self.__system_seat_id is None or affector_id is None:
                    return True
                if affector_id == self.__system_seat_id:
                    return True
        except Exception:
            return False
        return False

    def __should_pause_for_select_n(self) -> bool:
        if not self.__pending_select_n:
            return False
        ts = self.__pending_select_n.get("ts", 0.0)
        ids = set(self.__pending_select_n.get("ids", []) or [])
        pending_zone = self.updated_game_state.get_zone("ZoneType_Pending")
        pending_ids = set(pending_zone.get("objectInstanceIds", []) or []) if pending_zone else set()
        if pending_ids and ids.intersection(pending_ids):
            return True
        if time.time() - ts < 3.0:
            return True
        self.__pending_select_n = None
        return False

    def __should_pause_for_targets(self) -> bool:
        if self.__should_pause_for_select_n():
            return True
        if self.__pending_target_select is not None:
            if self.__last_submit_targets_ts and time.time() - self.__last_submit_targets_ts < self.__target_submit_cooldown_sec:
                return True
            return self.__is_selecting_targets()
        return self.__is_selecting_targets()

    def __handle_target_selection_from_raw_dict(self, raw_dict: dict) -> None:
        try:
            messages = raw_dict.get("greToClientEvent", {}).get("greToClientMessages", [])
            if self.__system_seat_id is None:
                return
            for message in messages:
                if message.get("type") != "GREMessageType_SelectTargetsReq":
                    continue
                seat_ids = message.get("systemSeatIds") or []
                if self.__system_seat_id not in seat_ids:
                    continue
                req = message.get("selectTargetsReq", {}) or {}
                targets = req.get("targets", []) or []
                if targets:
                    t0 = targets[0]
                    min_t = t0.get("minTargets")
                    max_t = t0.get("maxTargets")
                    selected = t0.get("selectedTargets")
                    bot_logger.log_info(
                        f"SelectTargetsReq details: sourceId={req.get('sourceId')}, min={min_t}, max={max_t}, selected={selected}, targetCount={len(t0.get('targets', []) or [])}"
                    )
                    self.__update_pending_target_select(
                        req.get("sourceId"),
                        min_t=min_t,
                        max_t=max_t,
                        selected=selected,
                    )
                    if self.__pending_target_ready_to_submit():
                        threading.Timer(0.2, self.submit_selection).start()
                source_id = message.get("selectTargetsReq", {}).get("sourceId")
                self.__schedule_target_selection(source_id, reason="SelectTargetsReq (from game state)")
                return
            for message in messages:
                if message.get("type") != "GREMessageType_GameStateMessage":
                    continue
                annotations = message.get("gameStateMessage", {}).get("annotations", []) or []
                for annotation in annotations:
                    types = annotation.get("type", []) or []
                    if "AnnotationType_PlayerSelectingTargets" not in types:
                        continue
                    affector_id = annotation.get("affectorId")
                    if affector_id is not None and affector_id != self.__system_seat_id:
                        continue
                    affected_ids = annotation.get("affectedIds") or []
                    source_id = affected_ids[0] if affected_ids else None
                    self.__schedule_target_selection(source_id, reason="PlayerSelectingTargets")
                    return
            for message in messages:
                if message.get("type") != "GREMessageType_SubmitTargetsResp":
                    continue
                seat_ids = message.get("systemSeatIds") or []
                if self.__system_seat_id not in seat_ids:
                    continue
                resp = message.get("submitTargetsResp", {}) or {}
                result = resp.get("result")
                if result == "ResultCode_Success":
                    self.__last_submit_targets_ts = time.time()
                    self.__pending_target_select = None
                    bot_logger.log_info("SubmitTargetsResp: success")
        except Exception as e:
            bot_logger.log_error(f"Failed to handle target selection from game state: {e}")

    def __handle_declare_attackers_req(self, line: str) -> None:
        try:
            start = line.find("{")
            if start == -1:
                return
            payload = json.loads(line[start:])
            messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
            for message in messages:
                if message.get("type") != "GREMessageType_DeclareAttackersReq":
                    continue
                req = message.get("declareAttackersReq", {})
                attackers = req.get("attackers", []) or req.get("qualifiedAttackers", [])
                for attacker in attackers:
                    recipients = attacker.get("legalDamageRecipients", []) or []
                    for rec in recipients:
                        if rec.get("type") == "DamageRecType_PlanesWalker":
                            self.__attack_target_required = True
                            bot_logger.log_info("DeclareAttackersReq: planeswalker target present")
                            return
        except Exception as e:
            bot_logger.log_error(f"Failed to parse DeclareAttackersReq: {e}")

    @staticmethod
    def __infer_match_won(line: str) -> bool | None:
        """
        Best-effort inference from a single log line. Returns True/False/None if unknown.
        MTGA log formats vary by version; we try JSON parsing and fallback to keyword matching.
        """
        def _has_token(text: str, token: str) -> bool:
            return re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", text) is not None

        def _scan_text(text: str) -> bool | None:
            lowered = text.lower()
            win_tokens = ("victory", "win", "won")
            loss_tokens = ("defeat", "loss", "lose", "lost")
            has_win = any(_has_token(lowered, t) for t in win_tokens)
            has_loss = any(_has_token(lowered, t) for t in loss_tokens)
            if has_win and not has_loss:
                return True
            if has_loss and not has_win:
                return False
            return None

        try:
            start = line.find("{")
            if start != -1:
                payload = json.loads(line[start:])
                stack = [payload]
                strings: list[str] = []
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
                    elif isinstance(cur, str):
                        strings.append(cur)

                joined = " ".join(strings)
                outcome = _scan_text(joined)
                if outcome is not None:
                    return outcome
        except Exception:
            pass

        return _scan_text(line)

    def __infer_match_won_from_raw_dict(self, raw_dict: dict) -> bool | None:
        try:
            messages = raw_dict.get("greToClientEvent", {}).get("greToClientMessages", [])
            for message in messages:
                if message.get("type") != "GREMessageType_GameStateMessage":
                    continue
                game_state_msg = message.get("gameStateMessage", {})
                game_info = game_state_msg.get("gameInfo", {})
                results = game_info.get("results", [])
                if not results:
                    continue
                winning_team_id = None
                for result in results:
                    if result.get("result") == "ResultType_WinLoss" and "winningTeamId" in result:
                        winning_team_id = result.get("winningTeamId")
                        break
                if winning_team_id is None:
                    continue

                players = game_state_msg.get("players", [])
                my_team_id = None
                if self.__system_seat_id is not None:
                    for player in players:
                        if player.get("systemSeatNumber") == self.__system_seat_id:
                            my_team_id = player.get("teamId")
                            break
                if my_team_id is None:
                    seat_ids = message.get("systemSeatIds") or []
                    for player in players:
                        if player.get("systemSeatNumber") in seat_ids:
                            my_team_id = player.get("teamId")
                            break
                if my_team_id is None:
                    return None

                return winning_team_id == my_team_id
        except Exception as e:
            bot_logger.log_error(f"Failed to infer match result from game state: {e}")
        return None

    def __update_inst_id__grp_id_dict(self, object_dict_arr):
        for object_dict in object_dict_arr:
            if object_dict['instanceId'] not in self.__inst_id_grp_id_dict.keys():
                self.__inst_id_grp_id_dict[object_dict['instanceId']] = object_dict['grpId']

    def __update_game_state(self, raw_dict: [str, str or int]):
        # Derive the local player's systemSeatId from incoming messages (if present)
        system_seat_id = Controller.__get_system_seat_id_from_raw_dict(raw_dict)
        if system_seat_id is not None and system_seat_id != self.__system_seat_id:
            self.__system_seat_id = system_seat_id
            bot_logger.log_info(f"Detected local systemSeatId={self.__system_seat_id}")

        outcome = self.__infer_match_won_from_raw_dict(raw_dict)
        if outcome is not None:
            self.__last_match_won = outcome

        game_state = Controller.__get_game_state_from_raw_dict(raw_dict, fallback_seat_id=self.__system_seat_id or 1)
        self.updated_game_state.update(game_state)
        print(self.updated_game_state)

        # Log all parsed game state data to bot.log
        bot_logger.log_game_state_update(self.updated_game_state.get_full_state())

        self.__handle_target_selection_from_raw_dict(raw_dict)

        # Check for successful actions in the log update
        if self.__action_success_callback:
            # Pass to avoid log spam, as requested by user.
            # The original implementation here was checking GameStateMessage actions
            # which caused false positives for every action in the list.
            pass

        turn_info_dict = self.updated_game_state.get_turn_info()
        is_complete = self.updated_game_state.is_complete()

        # Log controller state
        bot_logger.log_controller_event(
            f"is_complete={is_complete}",
            f"decisionPlayer={turn_info_dict.get('decisionPlayer') if turn_info_dict else None}, has_mulled_keep={self.__has_mulled_keep}"
        )

        if self.__should_pause_for_targets():
            if self.__decision_execution_thread is not None:
                self.__decision_execution_thread.cancel()
                self.__decision_execution_thread = None
            bot_logger.log_info("Pausing decision while target selection is pending")
            # Let target selection resolve before scheduling new decisions.
            return

        if is_complete:
            self.__update_inst_id__grp_id_dict(self.updated_game_state.get_game_objects())
            my_seat = self.__system_seat_id
            if my_seat is None:
                bot_logger.log_info("Skipping decision (local systemSeatId unknown)")
            elif turn_info_dict['decisionPlayer'] == my_seat and self.__has_mulled_keep:
                # Cancel any existing timer before starting a new one
                if self.__decision_execution_thread is not None:
                    self.__decision_execution_thread.cancel()

                def _decision_if_still_my_priority():
                    try:
                        ti = self.updated_game_state.get_turn_info() or {}
                        if self.__should_pause_for_targets():
                            bot_logger.log_info("Deferring decision; target selection still pending")
                            self.__decision_execution_thread = threading.Timer(0.5, _decision_if_still_my_priority)
                            self.__decision_execution_thread.start()
                            return
                        still_my_priority = (ti.get('decisionPlayer') == my_seat)
                        if still_my_priority and self.__decision_callback and self.__has_mulled_keep:
                            self.__decision_callback(self.updated_game_state)
                        else:
                            bot_logger.log_info(
                                f"Skipping delayed decision (decisionPlayer={ti.get('decisionPlayer')}, my_seat={my_seat})"
                            )
                    except Exception as e:
                        bot_logger.log_error(f"Error in delayed decision callback: {e}")

                self.__decision_execution_thread = threading.Timer(self.__decision_delay, _decision_if_still_my_priority)
                self.__decision_execution_thread.start()

        # Start mulligan timer if we haven't made a mulligan decision yet
        # This needs to trigger regardless of is_complete to handle game restarts
        my_seat = self.__system_seat_id
        if my_seat is not None and not self.__has_mulled_keep and turn_info_dict and turn_info_dict.get('decisionPlayer') == my_seat:
            if self.__mulligan_execution_thread is not None:
                self.__mulligan_execution_thread.cancel()

            def _mulligan_if_still_mine():
                try:
                    ti = self.updated_game_state.get_turn_info() or {}
                    if ti.get('decisionPlayer') == my_seat and self.__mulligan_decision_callback:
                        self.__mulligan_decision_callback([])
                    else:
                        bot_logger.log_info(
                            f"Skipping delayed mulligan (decisionPlayer={ti.get('decisionPlayer')}, my_seat={my_seat})"
                        )
                        # Allow rescheduling if needed
                        self.__has_mulled_keep = False
                except Exception as e:
                    bot_logger.log_error(f"Error in delayed mulligan callback: {e}")
                    self.__has_mulled_keep = False

            self.__mulligan_execution_thread = threading.Timer(self.__intro_delay, _mulligan_if_still_mine)
            self.__mulligan_execution_thread.start()
            self.__has_mulled_keep = True
            print('making mull decision')

    @staticmethod
    def __get_system_seat_id_from_raw_dict(raw_dict: [str, str or int]):
        try:
            temp_dict = raw_dict.get('greToClientEvent', {})
            messages = temp_dict.get('greToClientMessages', [])
            preferred_types = {
                "GREMessageType_ActionsAvailableReq",
                "GREMessageType_SelectNReq",
                "GREMessageType_SelectTargetsReq",
                "GREMessageType_DeclareAttackersReq",
                "GREMessageType_AssignDamageReq",
                "GREMessageType_MulliganReq",
            }
            for message in messages:
                if message.get('type') not in preferred_types:
                    continue
                seat_ids = message.get('systemSeatIds')
                if isinstance(seat_ids, list) and len(seat_ids) == 1 and isinstance(seat_ids[0], int):
                    return seat_ids[0]
            for message in messages:
                seat_ids = message.get('systemSeatIds')
                if isinstance(seat_ids, list) and len(seat_ids) == 1 and isinstance(seat_ids[0], int):
                    return seat_ids[0]
        except Exception:
            return None
        return None

    @staticmethod
    def __get_game_state_from_raw_dict(raw_dict: [str, str or int], fallback_seat_id: int = 1):
        temp_dict = raw_dict['greToClientEvent']
        temp_arr = temp_dict['greToClientMessages']
        return_game_state = GameState({})
        for message in temp_arr:
            if message['type'] == "GREMessageType_GameStateMessage":
                raw_game_state_dict = message['gameStateMessage']
                game_state_dict = {}
                for key in GameState.GAME_STATE_KEYS:
                    if key in raw_game_state_dict:
                        game_state_dict[key] = raw_game_state_dict[key]
                generated_game_state = GameState(game_state_dict)
                return_game_state.update(generated_game_state)
            # Also parse ActionsAvailableReq for available actions
            elif message['type'] == "GREMessageType_ActionsAvailableReq":
                req = message.get('actionsAvailableReq', {})
                active_actions = req.get('actions', [])
                bot_logger.log_actions_available(active_actions)
                # Wrap each action in the expected format with seatId
                seat_ids = message.get('systemSeatIds') or []
                seat_id = seat_ids[0] if isinstance(seat_ids, list) and len(seat_ids) > 0 else fallback_seat_id
                wrapped_actions = [{'seatId': seat_id, 'action': action} for action in active_actions]
                if wrapped_actions:
                    actions_state = GameState({'actions': wrapped_actions})
                    return_game_state.update(actions_state)
        return return_game_state
