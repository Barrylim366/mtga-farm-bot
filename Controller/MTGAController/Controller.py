import json
import random
import re
import threading
import time
import os
import sys
from datetime import datetime
from pathlib import Path

from Controller.ControllerInterface import ControllerSecondary
import AI.Utilities.RemovalLogic as RemovalLogic
import AI.Utilities.FightLogic as FightLogic
import AI.Utilities.CounterLogic as CounterLogic
import AI.Utilities.CardInfo as CardInfo
from Controller.MTGAController.LogReader import LogReader
from Controller.Utilities.GameState import GameState
from Controller.Utilities.input_controller import InputControllerError, create_input_controller
from actions.actions import run_action
from actions.navigation_flow import build_post_login_navigation_actions
from state.state_machine import BotState, PlayerLogStateTracker, get_state_from_playerlog
from vision.vision import VisionEngine
from vision.window_locator import ArenaRegionProvider, focus_mtga_window
import bot_logger
import runtime_status
from runtime_paths import runtime_file

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
_COLOR_LETTERS = set("WUBRGC")
_MY_TIMER_TYPES = {
    "TimerType_ActivePlayer",
    "TimerType_NonActivePlayer",
    "TimerType_Inactivity",
}


class Controller(ControllerSecondary):

    # MTGA holds up to 3 daily quests; completed ones drop out of the list. Used
    # to derive absolute completions (slots - remaining) for the switch decision.
    _DAILY_QUEST_SLOTS = 3

    def __init__(
        self,
        log_path,
        screen_bounds=((0, 0), (1600, 900)),
        click_targets=None,
        input_backend: str | None = None,
        account_switch_minutes: int | None = None,
        account_switch_mode: str | None = None,
        account_switch_main_quests: int | None = None,
        account_switch_daily_wins: int | None = None,
        account_cycle_index: int | None = None,
        account_play_order: list[str] | None = None,
        game_mode: str | None = None,
        gold_per_win: int | None = None,
        account_switch_enabled: bool = True,
    ):
        self.__decision_callback = None
        self.__mulligan_decision_callback = None
        self.__action_success_callback = None
        self.__decision_execution_thread = None
        self.__decision_delay_key = None
        self.__decision_delay_scheduled_at = 0.0
        self.__assign_damage_execution_thread = None
        self.__assign_damage_in_progress = False
        self.__mulligan_execution_thread = None
        self.__mulligan_decision_armed = False
        self.__inactivity_timer = None
        self.__inactivity_timeout = 180  # 3 minutes in seconds
        self.__has_mulled_keep = False
        self.__intro_delay = 15
        self.__decision_delay = 4
        self.screen_bounds = screen_bounds
        self.patterns = {
            'game_state': '"type": "GREMessageType_GameStateMessage"',
            'timer_state': '"type": "GREMessageType_TimerStateMessage"',
            'hover_id': 'objectId',
            'match_completed': 'MatchGameRoomStateType_MatchCompleted',
            'assign_damage': '"type": "GREMessageType_AssignDamageReq"',
            'declare_attackers': '"type": "GREMessageType_DeclareAttackersReq"',
            # Defending: we have no blocking logic yet, so we just declare no
            # blocks to avoid freezing on 'Choose blockers'.
            'declare_blockers': '"type": "GREMessageType_DeclareBlockersReq"',
            'select_n': '"type": "GREMessageType_SelectNReq"',
            # Scry/surveil and other ordered-grouping prompts. We only click
            # Done for now so the bot does not stall on them.
            'group_req': '"type": "GREMessageType_GroupReq"',
            'select_targets': '"type": "GREMessageType_SelectTargetsReq"',
            'pay_costs': '"type": "GREMessageType_PayCostsReq"',
            # Optional casting-time costs (Kicker etc.): MTGA blocks the cast
            # behind a mid-screen "Choose One" dialog until a version is picked.
            'casting_time_options': '"type": "GREMessageType_CastingTimeOptionsReq"',
            # Client->server messages: cheap ground truth about whether our own
            # clicks registered (target response, attack submit) or misfired
            # into the phase strip (SetSettings toggling a transient stop).
            'client_select_targets_resp': '"type": "ClientMessageType_SelectTargetsResp"',
            'client_submit_attackers': '"type": "ClientMessageType_SubmitAttackersReq"',
            'client_set_settings': '"type": "ClientMessageType_SetSettingsReq"',
            'main_nav_loaded': 'MainNav load in',
            'queue_ready_marker': 'Unloading 1 Unused Serialized files (Serialized files now loaded:',
        }
        if not log_path or not os.path.isfile(log_path):
            raise FileNotFoundError(
                f"Player.log not found at configured path: {log_path!r}. "
                "Set a valid path before starting the bot."
            )
        self.log_reader = LogReader(self.patterns.values(), log_path=log_path, callback=self.__log_callback)
        self._log_path = log_path
        self._supervisor_active = str(os.environ.get("MTGA_SUPERVISOR_ACTIVE", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        runtime_status.reset_status(log_path=log_path)
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
        self._default_mulligan_keep_coors = (1101, 870)
        self._default_mulligan_mull_coors = (801, 870)
        self.mulligan_keep_coors = self._default_mulligan_keep_coors
        self.mulligan_mull_coors = self._default_mulligan_mull_coors
        self.player_button_coors = (1699, 996)
        self.home_play_button_coors = (1699, 996)
        self.assign_damage_done_coors = (1280, 720)
        self._default_opponent_avatar_coors = (int(1920 * 0.67), int(1080 * 0.2))
        self.opponent_avatar_coors = self._default_opponent_avatar_coors
        self.cast_card_dist = 10
        self.main_br_button_coordinates = (
            1920 - self.main_br_button_offset[0],
            1080 - self.main_br_button_offset[1],
        )

        self.log_out_btn_coors = None
        self.log_out_ok_btn_coors = None
        self.log_out_focus_coors = None
        
        self.hand_scan_p1 = (0, 1050)
        self.hand_scan_p2 = (1920, 1050)
        self.battlefield_scan_p1 = (
            int(1920 * 0.10),
            int(1080 * 0.50),
        )
        self.battlefield_scan_p2 = (
            int(1920 * 0.92),
            int(1080 * 0.90),
        )
        self.battlefield_scan_step = 55
        # Opponent battlefield = upper arena band (mirror of ours). Calibrated
        # from a full-res board capture: the enemy creature row sits between the
        # opponent's lands and the centre divider (~y 0.24-0.45), full width.
        self.opponent_battlefield_scan_p1 = (int(1920 * 0.10), int(1080 * 0.24))
        self.opponent_battlefield_scan_p2 = (int(1920 * 0.92), int(1080 * 0.45))
        # Central 'choose a card' overlay (graveyard/exile target choosers,
        # e.g. Zombify). First estimate -- NEEDS IN-GAME CALIBRATION.
        self.chooser_scan_p1 = (int(1920 * 0.20), int(1080 * 0.29))
        self.chooser_scan_p2 = (int(1920 * 0.78), int(1080 * 0.70))
        self.stack_scan_p1 = (
            int(1920 * 0.65),
            int(1080 * 0.25),
        )
        self.stack_scan_p2 = (
            int(1920 * 0.95),
            int(1080 * 0.6),
        )
        self.stack_scan_step = 80
        self.stack_scan_fallback_p1 = (
            int(1920 * 0.35),
            int(1080 * 0.2),
        )
        self.stack_scan_fallback_p2 = (
            int(1920 * 0.8),
            int(1080 * 0.75),
        )
        self.stack_scan_fallback_step = 50
        self._default_points_1920 = {
            "mulligan_keep_coors": self.mulligan_keep_coors,
            "mulligan_mull_coors": self.mulligan_mull_coors,
            "player_button_coors": self.player_button_coors,
            "home_play_button_coors": self.home_play_button_coors,
            "main_br_button_coordinates": self.main_br_button_coordinates,
            "assign_damage_done_coors": self.assign_damage_done_coors,
            "opponent_avatar_coors": self._default_opponent_avatar_coors,
            "hand_scan_p1": self.hand_scan_p1,
            "hand_scan_p2": self.hand_scan_p2,
            "battlefield_scan_p1": self.battlefield_scan_p1,
            "battlefield_scan_p2": self.battlefield_scan_p2,
            "stack_scan_p1": self.stack_scan_p1,
            "stack_scan_p2": self.stack_scan_p2,
            "stack_scan_fallback_p1": self.stack_scan_fallback_p1,
            "stack_scan_fallback_p2": self.stack_scan_fallback_p2,
            "log_out_focus_coors": self.home_play_button_coors,
            "log_out_btn_coors": (1716, 851),
            "log_out_ok_btn_coors": (1875, 809),
        }
        self._loaded_click_targets = {}
        self._legacy_origin_hint: tuple[int, int] | None = None
        
        if click_targets:
            try:
                self._loaded_click_targets = dict(click_targets)
            except Exception:
                self._loaded_click_targets = {}
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
            if "battlefield_scan_points" in click_targets:
                self.battlefield_scan_p1 = (
                    click_targets["battlefield_scan_points"]["p1"]["x"],
                    click_targets["battlefield_scan_points"]["p1"]["y"],
                )
                self.battlefield_scan_p2 = (
                    click_targets["battlefield_scan_points"]["p2"]["x"],
                    click_targets["battlefield_scan_points"]["p2"]["y"],
                )
            if "battlefield_scan_step" in click_targets:
                try:
                    self.battlefield_scan_step = int(click_targets["battlefield_scan_step"])
                except Exception:
                    pass
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
            if "log_out_focus" in click_targets:
                self.log_out_focus_coors = (click_targets["log_out_focus"]["x"], click_targets["log_out_focus"]["y"])
            if "log_out_ok_btn" in click_targets:
                self.log_out_ok_btn_coors = (click_targets["log_out_ok_btn"]["x"], click_targets["log_out_ok_btn"]["y"])
            elif "logout_ok_btn" in click_targets:
                self.log_out_ok_btn_coors = (click_targets["logout_ok_btn"]["x"], click_targets["logout_ok_btn"]["y"])
        self._seed_logout_points_from_record_once()
        self._normalize_loaded_click_targets_to_1920()
        self._legacy_origin_hint = self._infer_legacy_origin_from_loaded_targets()
        if self._legacy_origin_hint is not None:
            bot_logger.log_info(f"Inferred legacy window origin from loaded calibration: {self._legacy_origin_hint}")

        self.updated_game_state = GameState()
        self.__inst_id_grp_id_dict = {}
        self.__match_end_callback = None
        self.__last_match_won: bool | None = None
        self.__last_seen_match_id: str | None = None
        self.__attack_target_required = False
        self.__attack_target_attacker_ids: list[int] = []
        self.__attack_target_flow_lock = threading.Lock()
        # MTGA system seat id for the local player (can be 1 or 2)
        self.__system_seat_id = None
        self.__last_target_select_source_id = None
        self.__last_target_select_ts = 0.0
        self.__pending_target_select = None
        self.__target_select_token_counter = 0
        self.__last_submit_targets_ts = 0.0
        self.__pending_select_n = None
        self.__select_n_in_progress = False
        self.__select_n_in_progress_since = 0.0
        self.__last_submit_selection_ts = 0.0
        self.__submit_selection_lock = threading.Lock()
        self.__select_n_token_counter = 0
        self.__select_n_stack_wait_timeout_sec = 8.0
        self.__target_submit_cooldown_sec = 1.0
        self.__pending_pay_costs_ts = 0.0
        self.__last_casting_time_options_ts = 0.0
        self.__last_modal_choice_ts = 0.0
        self.__last_group_req_ts = 0.0
        self.__group_req_active_until = 0.0
        self.__last_declare_blockers_ts = 0.0
        self.__combat_recovery_key = None
        self.__combat_recovery_attempts = 0
        self.__declare_attackers_turn_key = ""
        self.__declare_attackers_cycle_count = 0
        self.__declare_attackers_cycle_limit = 5
        self.__combat_recovery_deadline_ts = 0.0
        self.__combat_recovery_timer = None
        self.__last_attack_submit_ts = 0.0
        self.__my_timer_state = {}
        self.__emergency_concede_in_progress = False
        self.__emergency_concede_threshold_sec = 20.0
        self.__emergency_concede_timer: threading.Timer | None = None
        self.__emergency_concede_scheduled_at: float = 0.0
        _mode = str(game_mode or "historic").strip().lower()
        self._game_mode = _mode if _mode in ("historic", "starter") else "historic"
        bot_logger.log_info(f"Queue game_mode configured: {self._game_mode}")
        self._account_switch_interval = max(0, int(account_switch_minutes or 0)) * 60
        # Account-switch trigger mode: "time" (every N minutes) or "quests"
        # (when main-quest completions and daily wins reach the thresholds).
        # Mutually exclusive -- only one mode drives the switch.
        _sw_mode = str(account_switch_mode or "time").strip().lower()
        self._account_switch_mode = _sw_mode if _sw_mode in ("time", "quests") else "time"
        # Master on/off toggled from the main UI. When False, no switching happens
        # regardless of thresholds or configured accounts.
        self._account_switch_enabled = bool(account_switch_enabled)
        self._account_switch_main_quests = max(0, min(3, int(account_switch_main_quests or 0)))
        self._account_switch_daily_wins = max(0, min(15, int(account_switch_daily_wins or 0)))
        # Quest-mode tracking: a local win counter (the bot knows every match
        # result) and the absolute completed-quest count captured at the first
        # valid Home read this session, used as a baseline so the switch check
        # only counts quests completed DURING this session (see
        # _main_quests_completed_this_session).
        self._account_start_quests_completed_absolute: int | None = None
        self._daily_wins_this_account = 0
        self._win_counted_this_match = False
        # ABSOLUTE quest state for the account-switch decision: the number of
        # still-incomplete daily quests from the last VALID Home read (None until
        # we have read one). MTGA holds up to _DAILY_QUEST_SLOTS daily quests and
        # completed ones drop out of the list, so completed(absolute) = slots -
        # incomplete. This counts quests done by anyone (bot OR human), which is
        # what "switch if the account already meets the criteria at start" needs.
        # None means "not read yet" -> never switch on a guess.
        self._last_valid_quest_active_incomplete: int | None = None
        # One-shot: read this account's quests from Home before the first queue,
        # so the switch check reflects real state on landing. Reset per account.
        self._home_quest_check_done = False
        # One-shot per account: whether _navigate_starter_deck's "chooser already
        # open" fast path has been tried yet. That shortcut trusts the mere
        # presence of a "Submit Deck" button as proof we landed directly on the
        # Starter Deck Duel chooser -- but MTGA reuses that same button on other
        # formats' deckbuilders too (e.g. Draft), so blindly trusting it on every
        # queue cycle risks clicking the fixed Starter Deck Duel grid coordinates
        # on an unrelated event's screen if the player has one open/in-progress.
        # Limiting it to a single try per account matches the shortcut's actual
        # purpose (MTGA drops a brand-new account directly onto this chooser
        # the very first time only); every later cycle uses the verified
        # Play > Events > banner-click navigation instead.
        self._starter_picker_shortcut_tried = False
        # Anti-storm guard: consecutive account switches with no match played in
        # between. If it reaches the number of configured accounts, every account
        # already meets the criteria -> stop cycling (a logout/login loop is worse
        # than playing). Reset whenever a match completes.
        self._switches_without_match = 0
        self._known_account_count = 0
        # The screenName that owns this account's quests block, latched from the
        # first block we see after (re)start / account switch. Comparing against
        # this fixed value -- instead of "whichever screenName is last in the log
        # tail" -- avoids misreading an opponent's screenName (match-room events
        # log both players' names) as a sign that the account changed.
        self._current_account_screen_name: str | None = None
        # Per-account gold farmed this SESSION. The controller is recreated every
        # time the bot is started, so this dict naturally starts empty (each
        # account resets to 0 on bot open, as required). Keyed by the account's
        # MTGA screenName -- the only account identity the log exposes reliably
        # for both the initial and every switched-in account. Published to
        # runtime_status so the "Current Session" window can list it.
        #
        # The real gold balance is NOT in the player.log, so "farmed" is derived
        # from what the bot does see: the gold reward of each daily quest it
        # completes, plus a fixed estimate per match won (gold_per_win).
        self._gold_per_win = max(0, int(gold_per_win or 0))
        self._gold_farmed_by_account: dict[str, int] = {}
        # Gold reward of each of the current account's quests, captured while the
        # quest is still in the list so we still know its value once it completes
        # and drops out. Reset on account switch.
        self._account_quest_gold: dict[str, int] = {}
        # Quest ids already credited as completed for the current account (credit
        # a completed quest once). Reset on account switch.
        self._credited_quest_ids: set[str] = set()
        # Map screenName -> configured alias, so the UI can show the alias the
        # user configured instead of (or beside) the raw in-game name. Built
        # reliably from switches: when we switch TO alias A and then latch the
        # screenName of the account that logs in, we know screenName -> A.
        self._screenname_to_alias: dict[str, str] = {}
        self._pending_switch_alias: str | None = None
        bot_logger.log_info(
            "Account-switch config: mode={} time_min={} main_quests={} daily_wins={}".format(
                self._account_switch_mode,
                self._account_switch_interval // 60,
                self._account_switch_main_quests,
                self._account_switch_daily_wins,
            )
        )
        self._account_cycle_index = int(account_cycle_index or 0)
        self._account_play_order = account_play_order or []
        if self._account_play_order:
            bot_logger.log_info(f"Account play order configured: {self._account_play_order}")
        self._last_account_switch_ts = time.time()
        self._account_switch_pending = False
        self._account_switch_in_progress = False
        self._queue_after_login = False
        self._queue_spam_thread = None
        self._stop_queue_spam = False
        self._queue_ready = False
        # Quests parsed once from the player.log (at startup / between matches)
        # and reused locally, so we don't re-parse the log on every queue cycle.
        self._cached_quests: list[dict] = []
        self._cached_active_quest_id: str = ""
        self._cached_active_colors: str = ""
        # MTGA only logs quest progress on Home (via QuestGetQuests), never on the
        # event page where the bot re-queues, so quest data/UI would otherwise
        # freeze at startup values. Periodically dip back to Home to refresh.
        self._matches_since_quest_refresh = 0
        self._quest_refresh_every_n_matches = 3
        self._match_end_dismissed = False
        self._unknown_screen_strikes = 0
        self._post_match_ready_ts = None
        self._post_match_delay_sec = 30
        self._stop_requested = False
        self._post_login_action_done = False
        self._suppress_selections = False
        self._state_tracker = PlayerLogStateTracker(max_lines=500)
        self._vision = VisionEngine()
        self._arena_region_provider = ArenaRegionProvider(
            vision=self._vision,
            assets_dir=self._app_path("assets", "assert"),
        )
        self._arena_region: tuple[int, int, int, int] | None = None
        self._last_good_arena_region: tuple[int, int, int, int] | None = None
        self._last_good_arena_region_ts = 0.0
        self._arena_region_missing_logged_ts = 0.0
        self._arena_region_cached_reuse_logged_ts = 0.0
        self._hand_select_debug_logged_ts = 0.0
        self._arena_correction_xy: tuple[int, int] = (0, 0)
        self._logout_play_origin: tuple[int, int] | None = None
        self._navigation_verify_failures = 0
        self._queue_button_rel = (
            int(self.home_play_button_coors[0]),
            int(self.home_play_button_coors[1]),
        )
        # Fixed timing for login phase
        self._login_delete_delay_sec = 5.0
        # Keep loaded/seeded logout points; only fallback if still missing.
        if self.log_out_btn_coors is None:
            self.log_out_btn_coors = (1716, 851)
        if self.log_out_ok_btn_coors is None:
            self.log_out_ok_btn_coors = (1875, 809)
        if self.log_out_focus_coors is None:
            self.log_out_focus_coors = self.home_play_button_coors
        runtime_status.set_mode("ready", bot_state=str(self._get_state_from_log()))

    def _resource_root_dir(self) -> str:
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "")
            if isinstance(meipass, str) and meipass and os.path.isdir(meipass):
                return os.path.abspath(meipass)
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _buttons_dir(self) -> str:
        bundled_path = os.path.join(self._resource_root_dir(), "Buttons")
        local_path = os.path.join(self._app_root_dir(), "Buttons")
        if os.path.isdir(bundled_path):
            return bundled_path
        return local_path

    def _app_root_dir(self) -> str:
        if getattr(sys, "frozen", False):
            return os.path.abspath(os.path.dirname(sys.executable))
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _app_path(self, *parts: str) -> str:
        return os.path.join(self._app_root_dir(), *parts)

    def _normalize_point_to_1920(self, point: tuple[int, int]) -> tuple[tuple[int, int], str]:
        try:
            px = int(point[0])
            py = int(point[1])
        except Exception:
            return point, "invalid"

        if 0 <= px <= 1920 and 0 <= py <= 1080:
            return (px, py), "already_1920"
        return (px, py), "outside_1920"

    def _normalize_loaded_click_targets_to_1920(self) -> None:
        self._legacy_absolute_click_profile = False
        points_to_normalize = [
            ("mulligan_keep_coors", "keep_hand"),
            ("mulligan_mull_coors", "mulligan"),
            ("player_button_coors", "queue_player_button"),
            ("home_play_button_coors", "queue_button"),
            ("main_br_button_coordinates", "next_resolve"),
            ("assign_damage_done_coors", "assign_damage_done"),
            ("opponent_avatar_coors", "opponent_avatar"),
            ("stack_scan_p1", "stack_scan_p1"),
            ("stack_scan_p2", "stack_scan_p2"),
            ("stack_scan_fallback_p1", "stack_scan_fallback_p1"),
            ("stack_scan_fallback_p2", "stack_scan_fallback_p2"),
            ("log_out_focus_coors", "log_out_focus"),
            ("log_out_btn_coors", "log_out_btn"),
            ("log_out_ok_btn_coors", "log_out_ok_btn"),
        ]
        for attr, label in points_to_normalize:
            raw = getattr(self, attr, None)
            if raw is None:
                continue
            try:
                rx = int(raw[0])
                ry = int(raw[1])
                if rx > 1920 or ry > 1080:
                    self._legacy_absolute_click_profile = True
            except Exception:
                pass
            normalized, source = self._normalize_point_to_1920(raw)
            if source == "already_1920":
                setattr(self, attr, normalized)
            else:
                fallback = self._default_points_1920.get(attr)
                if fallback is not None:
                    setattr(self, attr, fallback)
                bot_logger.log_info(
                    f"Ignoring non-1920 {label}: raw={raw} source={source}. Using default={getattr(self, attr)}; recalibrate in 1920."
                )
            if tuple(getattr(self, attr)) != tuple(raw):
                bot_logger.log_info(
                    f"Using {label}: raw={raw} active={getattr(self, attr)}"
                )

        # Hand scan must be direct 1920-space (same philosophy as keep-hand fallback):
        # if loaded values are not valid 1920 coordinates, use robust defaults.
        hs_p1 = getattr(self, "hand_scan_p1", (0, 1050))
        hs_p2 = getattr(self, "hand_scan_p2", (1920, 1050))
        hand_valid = (
            0 <= int(hs_p1[0]) <= 1920 and 0 <= int(hs_p1[1]) <= 1080
            and 0 <= int(hs_p2[0]) <= 1920 and 0 <= int(hs_p2[1]) <= 1080
        )
        if not hand_valid:
            self.hand_scan_p1 = (0, 1050)
            self.hand_scan_p2 = (1920, 1050)
            bot_logger.log_info(
                f"Hand scan points fallback to 1920 defaults: p1={self.hand_scan_p1} p2={self.hand_scan_p2}"
            )
        try:
            hsx1 = int(self.hand_scan_p1[0])
            hsy1 = int(self.hand_scan_p1[1])
            hsx2 = int(self.hand_scan_p2[0])
            hsy2 = int(self.hand_scan_p2[1])
            if hsx1 > 1920 or hsy1 > 1080 or hsx2 > 1920 or hsy2 > 1080:
                self._legacy_absolute_click_profile = True
        except Exception:
            pass
        if self._legacy_absolute_click_profile:
            bot_logger.log_info("Detected legacy absolute click profile from loaded calibration values.")

    def _infer_legacy_origin_from_loaded_targets(self) -> tuple[int, int] | None:
        ct = self._loaded_click_targets or {}
        anchors = [
            ("queue_button", self._default_points_1920.get("home_play_button_coors")),
            ("keep_hand", self._default_points_1920.get("mulligan_keep_coors")),
            ("next", self._default_points_1920.get("main_br_button_coordinates")),
            ("assign_damage_done", self._default_points_1920.get("assign_damage_done_coors")),
        ]
        origins: list[tuple[int, int]] = []
        for key, rel in anchors:
            if rel is None:
                continue
            raw = ct.get(key)
            if not isinstance(raw, dict):
                continue
            try:
                rx = int(raw.get("x"))
                ry = int(raw.get("y"))
                if rx > 1920 or ry > 1080:
                    origins.append((int(rx - rel[0]), int(ry - rel[1])))
            except Exception:
                continue
        if not origins:
            return None
        xs = sorted(o[0] for o in origins)
        ys = sorted(o[1] for o in origins)
        return (xs[len(xs) // 2], ys[len(ys) // 2])

    def _resolve_opponent_avatar_base(self, *, force_reacquire: bool = True) -> tuple[tuple[int, int], str]:
        arena = self._ensure_arena_region(force_reacquire=force_reacquire)
        raw = self.opponent_avatar_coors
        if arena is None:
            return self._map_abs_point_to_arena(
                raw,
                label="OPPONENT_AVATAR_BASE",
                force_reacquire=False,
                apply_correction=False,
            )
        try:
            px = int(raw[0])
            py = int(raw[1])
        except Exception:
            return self._map_abs_point_to_arena(
                raw,
                label="OPPONENT_AVATAR_BASE",
                force_reacquire=False,
                apply_correction=False,
            )

        # Preferred: legacy absolute -> relative conversion using queue anchor from loaded calibration.
        ct = self._loaded_click_targets or {}
        raw_avatar_cfg = ct.get("opponent_avatar")
        raw_queue_cfg = ct.get("queue_button")
        queue_rel_default = self._default_points_1920.get("home_play_button_coors")
        if (
            isinstance(raw_avatar_cfg, dict)
            and isinstance(raw_queue_cfg, dict)
            and queue_rel_default is not None
        ):
            try:
                avx = int(raw_avatar_cfg.get("x"))
                avy = int(raw_avatar_cfg.get("y"))
                qx = int(raw_queue_cfg.get("x"))
                qy = int(raw_queue_cfg.get("y"))
                qrelx = int(queue_rel_default[0])
                qrely = int(queue_rel_default[1])
                # Reconstruct old window origin from queue anchor, then rebase avatar.
                old_origin_x = int(qx - qrelx)
                old_origin_y = int(qy - qrely)
                relx = int(avx - old_origin_x)
                rely = int(avy - old_origin_y)
                if 0 <= relx <= 1920 and 0 <= rely <= 1080:
                    mapped = self._map_base_point_into_arena(arena, (relx, rely))
                    self.opponent_avatar_coors = (relx, rely)
                    bot_logger.log_info(
                        "OPPONENT_AVATAR rebased via queue anchor: raw_avatar_cfg={} raw_queue_cfg={} "
                        "old_origin=({}, {}) relative=({}, {}) mapped={} arena={}".format(
                            (avx, avy),
                            (qx, qy),
                            old_origin_x,
                            old_origin_y,
                            relx,
                            rely,
                            mapped,
                            arena,
                        )
                    )
                    return mapped, "opponent_avatar_rebased_from_queue_anchor"
            except Exception:
                pass

        candidates: list[tuple[tuple[int, int], str]] = []
        # Candidate A: interpret configured point as 1920-relative (new mode).
        if 0 <= px <= 1920 and 0 <= py <= 1080:
            candidates.append((self._map_base_point_into_arena(arena, (px, py)), "relative_1920"))
        # Candidate B: interpret configured point as absolute desktop coordinate (legacy calibration mode).
        if arena[0] <= px <= arena[0] + arena[2] and arena[1] <= py <= arena[1] + arena[3]:
            candidates.append(((px, py), "absolute_legacy"))
        # Candidate C: rebase legacy absolute coordinate via inferred old window origin.
        if self._legacy_origin_hint is not None:
            try:
                relx = int(px - self._legacy_origin_hint[0])
                rely = int(py - self._legacy_origin_hint[1])
                if 0 <= relx <= 1920 and 0 <= rely <= 1080:
                    candidates.append((self._map_base_point_into_arena(arena, (relx, rely)), "legacy_rebased_relative"))
            except Exception:
                pass

        if not candidates:
            return self._map_abs_point_to_arena(
                raw,
                label="OPPONENT_AVATAR_BASE",
                force_reacquire=False,
                apply_correction=False,
            )
        if len(candidates) == 1:
            return candidates[0][0], f"opponent_avatar_{candidates[0][1]}"

        for pt, lbl in candidates:
            if lbl == "legacy_rebased_relative":
                bot_logger.log_info(
                    "OPPONENT_AVATAR resolve: raw={} candidates={} selected={} arena={} (legacy-rebased preferred)".format(
                        raw,
                        [{"mode": l, "pt": p} for p, l in candidates],
                        {"mode": lbl, "pt": pt},
                        arena,
                    )
                )
                return pt, "opponent_avatar_legacy_rebased"

        # Ambiguous case: pick the candidate that lands in the plausible enemy avatar area.
        # Enemy avatar/face target is expected in upper-middle area of arena.
        def _score(pt: tuple[int, int]) -> float:
            lx = float(pt[0] - arena[0])
            ly = float(pt[1] - arena[1])
            rx = lx / float(arena[2] or 1)
            ry = ly / float(arena[3] or 1)
            cx, cy = 0.50, 0.18
            dist = ((rx - cx) ** 2 + (ry - cy) ** 2) ** 0.5
            zone_bonus = 2.0 if (0.28 <= rx <= 0.72 and 0.05 <= ry <= 0.40) else 0.0
            top_bonus = 0.5 if ry <= 0.45 else 0.0
            return zone_bonus + top_bonus - dist

        best_target, best_label = max(candidates, key=lambda c: _score(c[0]))
        bot_logger.log_info(
            "OPPONENT_AVATAR resolve: raw={} candidates={} selected={} arena={}".format(
                raw,
                [{"mode": lbl, "pt": pt} for pt, lbl in candidates],
                {"mode": best_label, "pt": best_target},
                arena,
            )
        )
        return best_target, f"opponent_avatar_{best_label}_auto"

    def _get_state_from_log(self) -> BotState:
        state = self._state_tracker.get_state()
        if state != BotState.UNKNOWN:
            return state
        tail = self._read_log_tail(self._log_path, max_bytes=250000)
        return get_state_from_playerlog(tail)

    def _ensure_arena_region(self, force_reacquire: bool = False) -> tuple[int, int, int, int] | None:
        arena = None
        if force_reacquire:
            arena = self._arena_region_provider.reacquire()
        elif self._arena_region is None:
            arena = self._arena_region_provider.acquire()
        else:
            arena = self._arena_region

        if arena is not None:
            try:
                self._arena_region = (
                    int(arena[0]),
                    int(arena[1]),
                    int(arena[2]),
                    int(arena[3]),
                )
            except Exception:
                self._arena_region = arena
            self._remember_arena_region(self._arena_region)
            return self._arena_region

        self._arena_region = None
        if self._should_reuse_cached_arena_region():
            cached = self._get_reusable_cached_arena_region("reacquire" if force_reacquire else "acquire")
            if cached is not None:
                self._log_missing_arena_region(
                    "reacquire" if force_reacquire else "acquire",
                    reuse_cached=True,
                )
                self._arena_region = cached
                return cached

        self._log_missing_arena_region(
            "reacquire" if force_reacquire else "acquire",
            reuse_cached=False,
        )
        return None

    def _get_reusable_cached_arena_region(self, context: str) -> tuple[int, int, int, int] | None:
        cached = self._arena_region or self._last_good_arena_region
        if cached is None:
            return None
        if not self._should_reuse_cached_arena_region():
            return None
        try:
            arena = (int(cached[0]), int(cached[1]), int(cached[2]), int(cached[3]))
        except Exception:
            return None
        if arena[2] <= 0 or arena[3] <= 0:
            return None
        age = max(0.0, time.time() - self._last_good_arena_region_ts)
        now = time.time()
        if (now - self._arena_region_cached_reuse_logged_ts) >= 1.0:
            self._arena_region_cached_reuse_logged_ts = now
            bot_logger.log_info(
                f"Arena region {context}: reusing cached arena_region={arena} age={age:.1f}s during active gameplay."
            )
        return arena

    def _remember_arena_region(self, arena: tuple[int, int, int, int] | None) -> None:
        if arena is None:
            return
        try:
            ax = int(arena[0])
            ay = int(arena[1])
            aw = int(arena[2])
            ah = int(arena[3])
        except Exception:
            return
        if aw <= 0 or ah <= 0:
            return
        self._last_good_arena_region = (ax, ay, aw, ah)
        self._last_good_arena_region_ts = time.time()

    def _should_reuse_cached_arena_region(self) -> bool:
        try:
            if self._get_state_from_log() == BotState.IN_GAME:
                return True
        except Exception:
            pass
        turn_info = self.updated_game_state.get_turn_info() or {}
        if turn_info.get("phase") == "Phase_Combat":
            return True
        if turn_info.get("step") == "Step_DeclareAttack":
            return True
        if self.__pending_select_n is not None or self.__select_n_in_progress:
            return True
        if self.__pending_target_select is not None:
            return True
        if self.__is_selecting_targets():
            return True
        return False

    def _log_missing_arena_region(self, context: str, *, reuse_cached: bool) -> None:
        now = time.time()
        if (now - self._arena_region_missing_logged_ts) < 1.0:
            return
        self._arena_region_missing_logged_ts = now
        cached = self._last_good_arena_region
        if cached is None:
            bot_logger.log_error(f"Arena region unavailable during {context}; no cached arena_region available.")
            return
        age = max(0.0, now - self._last_good_arena_region_ts)
        if reuse_cached:
            bot_logger.log_error(
                f"Arena region unavailable during {context}; reusing cached arena_region={cached} age={age:.1f}s."
            )
        else:
            bot_logger.log_error(
                f"Arena region unavailable during {context}; cached arena_region={cached} age={age:.1f}s not reused."
            )

    def _click_abs(self, x: int, y: int, tag: str) -> None:
        bot_logger.log_click(int(x), int(y), tag)
        runtime_status.touch_input(tag, (int(x), int(y)))
        self.input.move_abs(int(x), int(y))
        time.sleep(0.1)
        self.input.left_down()
        time.sleep(0.06)
        self.input.left_up()

    def _get_ui_action_arena_region(self, *, force_reacquire: bool = True, label: str = "UI_ACTION") -> tuple[int, int, int, int] | None:
        arena = self._ensure_arena_region(force_reacquire=force_reacquire)
        if arena is not None:
            return arena
        cached = self._last_good_arena_region
        if cached is None:
            bot_logger.log_error(f"{label}: no arena_region available for UI action.")
            return None
        age = max(0.0, time.time() - self._last_good_arena_region_ts)
        bot_logger.log_info(f"{label}: using cached arena_region={cached} age={age:.1f}s for UI action.")
        self._arena_region = cached
        return cached

    def _map_base_point_into_arena(
        self,
        arena: tuple[int, int, int, int],
        point: tuple[int, int],
    ) -> tuple[int, int]:
        ax, ay, aw, ah = [int(v) for v in arena]
        px = max(0, min(1920, int(point[0])))
        py = max(0, min(1080, int(point[1])))
        mapped_x = int(round((float(px) / 1920.0) * float(aw)))
        mapped_y = int(round((float(py) / 1080.0) * float(ah)))
        return (
            int(ax + min(max(0, mapped_x), max(0, aw - 1))),
            int(ay + min(max(0, mapped_y), max(0, ah - 1))),
        )

    def _scale_base_region_to_arena(
        self,
        arena: tuple[int, int, int, int],
        rel_region: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        ax, ay, aw, ah = [int(v) for v in arena]
        rx, ry, rw, rh = [int(v) for v in rel_region]
        left, top = self._map_base_point_into_arena(arena, (rx, ry))
        width = max(1, int(round((float(rw) / 1920.0) * float(aw))))
        height = max(1, int(round((float(rh) / 1080.0) * float(ah))))
        width = min(width, max(1, (ax + aw) - left))
        height = min(height, max(1, (ay + ah) - top))
        return (left, top, width, height)

    def _map_abs_point_to_arena(
        self,
        point: tuple[int, int],
        *,
        label: str = "point",
        force_reacquire: bool = False,
        apply_correction: bool = True,
    ) -> tuple[tuple[int, int], str]:
        arena = self._ensure_arena_region(force_reacquire=force_reacquire)
        if arena is None:
            return (int(point[0]), int(point[1])), "absolute_no_arena"
        try:
            px = int(point[0])
            py = int(point[1])

            # 1) 1920-relative coordinate inside arena.
            if 0 <= px <= 1920 and 0 <= py <= 1080:
                return self._map_base_point_into_arena(arena, (px, py)), "arena_relative_1920_direct"

            # 2) Absolute point already inside arena extents.
            local_x = int(px - arena[0])
            local_y = int(py - arena[1])
            if 0 <= local_x <= arena[2] and 0 <= local_y <= arena[3]:
                return (px, py), "arena_absolute_inside"

            # 3) Non-1920/outside point should not be used in 1920-only mode.
            bot_logger.log_error(
                f"{label}: point outside 1920-space and arena bounds: raw={point}, arena={arena}. Using absolute fallback."
            )
        except Exception as e:
            bot_logger.log_error(f"{label}: point map failed, using absolute. err={e}")
        return (int(point[0]), int(point[1])), "absolute_fallback"

    def _write_nav_debug_bundle(self, reason: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        debug_dir = Path(bot_logger.ensure_debug_dir(stamp))
        try:
            state_payload = {
                "reason": reason,
                "state": str(self._get_state_from_log()),
                "arena_region": self._arena_region,
                "log_path": self._log_path,
            }
            with open(debug_dir / "state.json", "w", encoding="utf-8") as f:
                json.dump(state_payload, f, indent=2)
        except Exception:
            pass
        try:
            tail = self._state_tracker.get_tail(180)
            if not tail:
                tail = self._read_log_tail(self._log_path, max_bytes=150000)
            with open(debug_dir / "log_tail.txt", "w", encoding="utf-8") as f:
                f.write(tail or "")
        except Exception:
            pass
        try:
            self._vision.begin_tick()
            if self._arena_region:
                arena_img = self._vision.capture(self._arena_region)
                self._vision.save_image(arena_img, str(debug_dir / "arena_region.png"))
            full = self._vision.capture(None)
            self._vision.save_image(full, str(debug_dir / "full_screen.png"))
        except Exception:
            pass
        bot_logger.log_error(f"Navigation debug bundle saved: {debug_dir}")

    def _write_keep_click_debug_bundle(
        self,
        *,
        decision: str,
        raw_point: tuple[int, int],
        mapped_point: tuple[int, int],
        source: str,
    ) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        debug_dir = Path(bot_logger.ensure_debug_dir(f"keep-click-{stamp}"))
        try:
            payload = {
                "reason": "mulligan_click_debug",
                "decision": decision,
                "state": str(self._get_state_from_log()),
                "arena_region": self._arena_region,
                "screen_bounds": self.screen_bounds,
                "raw_point": [int(raw_point[0]), int(raw_point[1])],
                "mapped_point": [int(mapped_point[0]), int(mapped_point[1])],
                "source": source,
                "arena_correction_xy": [
                    int(self._arena_correction_xy[0]),
                    int(self._arena_correction_xy[1]),
                ],
                "log_path": self._log_path,
            }
            with open(debug_dir / "keep_click_state.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        try:
            self._vision.begin_tick()
            full = self._vision.capture(None)
            self._vision.save_image(full, str(debug_dir / "full_screen_after_click.png"))
            if self._arena_region:
                arena_img = self._vision.capture(self._arena_region)
                self._vision.save_image(arena_img, str(debug_dir / "arena_region_after_click.png"))
            # Small focus crop around clicked point for quick inspection.
            focus_region = (
                int(mapped_point[0] - 220),
                int(mapped_point[1] - 140),
                440,
                280,
            )
            focus_img = self._vision.capture(focus_region)
            self._vision.save_image(focus_img, str(debug_dir / "click_focus_after_click.png"))
        except Exception:
            pass
        bot_logger.log_info(f"KEEP_HAND debug bundle saved: {debug_dir}")

    def _resolve_target_from_queue_anchor_rebase(
        self,
        *,
        config_key: str,
        raw_point: tuple[int, int],
        label: str,
        force_reacquire: bool = True,
    ) -> tuple[tuple[int, int], str]:
        arena = self._get_ui_action_arena_region(force_reacquire=force_reacquire, label=label)
        if arena is None:
            return (int(raw_point[0]), int(raw_point[1])), f"{label}_no_arena"
        ct = self._loaded_click_targets or {}
        raw_target_cfg = ct.get(config_key)
        raw_queue_cfg = ct.get("queue_button")
        queue_rel_default = self._default_points_1920.get("home_play_button_coors")

        # Prefer direct 1920-relative mapping when the configured target already
        # looks like a normalized session point. This avoids mixed-space rebasing
        # (legacy absolute queue anchor + relative target), which can drift.
        try:
            if isinstance(raw_target_cfg, dict):
                tx_cfg = int(raw_target_cfg.get("x"))
                ty_cfg = int(raw_target_cfg.get("y"))
                if 0 <= tx_cfg <= 1920 and 0 <= ty_cfg <= 1080:
                    mapped = self._map_base_point_into_arena(arena, (tx_cfg, ty_cfg))
                    self._loaded_click_targets[config_key] = {"x": tx_cfg, "y": ty_cfg}
                    if config_key == "log_out_focus":
                        self.log_out_focus_coors = (tx_cfg, ty_cfg)
                    elif config_key == "log_out_btn":
                        self.log_out_btn_coors = (tx_cfg, ty_cfg)
                    elif config_key == "log_out_ok_btn":
                        self.log_out_ok_btn_coors = (tx_cfg, ty_cfg)
                    return mapped, f"{label}_relative_1920_from_config"
        except Exception:
            pass

        if (
            isinstance(raw_target_cfg, dict)
            and isinstance(raw_queue_cfg, dict)
            and queue_rel_default is not None
        ):
            try:
                tx = int(raw_target_cfg.get("x"))
                ty = int(raw_target_cfg.get("y"))
                qx = int(raw_queue_cfg.get("x"))
                qy = int(raw_queue_cfg.get("y"))
                # Rebase only when queue anchor is clearly in legacy absolute space.
                if not (qx > 1920 or qy > 1080):
                    raise ValueError("queue anchor not legacy-absolute")
                qrelx = int(queue_rel_default[0])
                qrely = int(queue_rel_default[1])
                old_origin_x = int(qx - qrelx)
                old_origin_y = int(qy - qrely)
                relx = int(tx - old_origin_x)
                rely = int(ty - old_origin_y)
                if 0 <= relx <= 1920 and 0 <= rely <= 1080:
                    mapped = self._map_base_point_into_arena(arena, (relx, rely))
                    # Align with opponent-avatar behavior: persist resolved 1920-relative
                    # point for the current session so repeated clicks stay consistent.
                    self._loaded_click_targets[config_key] = {"x": relx, "y": rely}
                    if config_key == "log_out_focus":
                        self.log_out_focus_coors = (relx, rely)
                    elif config_key == "log_out_btn":
                        self.log_out_btn_coors = (relx, rely)
                    elif config_key == "log_out_ok_btn":
                        self.log_out_ok_btn_coors = (relx, rely)
                    return mapped, f"{label}_rebased_from_queue_anchor"
            except Exception:
                pass
        mapped, src = self._map_abs_point_to_arena(
            raw_point,
            label=label,
            force_reacquire=False,
            apply_correction=False,
        )
        return mapped, f"{label}_{src}"

    def _write_logout_click_debug_bundle(
        self,
        *,
        click_label: str,
        raw_point: tuple[int, int],
        mapped_point: tuple[int, int],
        source: str,
    ) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        debug_dir = Path(bot_logger.ensure_debug_dir(f"logout-click-{stamp}"))
        try:
            payload = {
                "reason": "logout_click_debug",
                "click_label": click_label,
                "state": str(self._get_state_from_log()),
                "arena_region": self._arena_region,
                "screen_bounds": self.screen_bounds,
                "raw_point": [int(raw_point[0]), int(raw_point[1])],
                "mapped_point": [int(mapped_point[0]), int(mapped_point[1])],
                "source": source,
                "log_path": self._log_path,
            }
            with open(debug_dir / "logout_click_state.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        try:
            self._vision.begin_tick()
            full = self._vision.capture(None)
            self._vision.save_image(full, str(debug_dir / "full_screen_after_click.png"))
            if self._arena_region:
                arena_img = self._vision.capture(self._arena_region)
                self._vision.save_image(arena_img, str(debug_dir / "arena_region_after_click.png"))
            focus_region = (
                int(mapped_point[0] - 240),
                int(mapped_point[1] - 150),
                480,
                300,
            )
            focus_img = self._vision.capture(focus_region)
            self._vision.save_image(focus_img, str(debug_dir / "logout_focus_after_click.png"))
        except Exception:
            pass
        bot_logger.log_info(f"{click_label} debug bundle saved: {debug_dir}")

    def _write_hand_select_debug_bundle(
        self,
        *,
        reason: str,
        card_id: int,
        scan_start: tuple[int, int],
        scan_end: tuple[int, int],
        current_pos: tuple[int, int] | None = None,
        current_hovered_id: int | None = None,
    ) -> None:
        now = time.time()
        if (now - self._hand_select_debug_logged_ts) < 1.5:
            return
        self._hand_select_debug_logged_ts = now
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        debug_dir = Path(bot_logger.ensure_debug_dir(f"hand-select-{stamp}"))
        try:
            payload = {
                "reason": reason,
                "card_id": int(card_id),
                "state": str(self._get_state_from_log()),
                "arena_region": self._arena_region,
                "last_good_arena_region": self._last_good_arena_region,
                "last_good_arena_region_age_sec": max(0.0, time.time() - float(self._last_good_arena_region_ts or 0.0)),
                "scan_start": [int(scan_start[0]), int(scan_start[1])],
                "scan_end": [int(scan_end[0]), int(scan_end[1])],
                "current_pos": [int(current_pos[0]), int(current_pos[1])] if current_pos is not None else None,
                "current_hovered_id": current_hovered_id,
                "pending_select_n": self.__pending_select_n,
                "select_n_in_progress": self.__select_n_in_progress,
                "turn_info": self.updated_game_state.get_turn_info() or {},
                "log_path": self._log_path,
            }
            with open(debug_dir / "hand_select_state.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        try:
            tail = self._state_tracker.get_tail(180)
            if not tail:
                tail = self._read_log_tail(self._log_path, max_bytes=150000)
            with open(debug_dir / "log_tail.txt", "w", encoding="utf-8") as f:
                f.write(tail or "")
        except Exception:
            pass
        try:
            self._vision.begin_tick()
            full = self._vision.capture(None)
            self._vision.save_image(full, str(debug_dir / "full_screen.png"))
            if self._arena_region:
                arena_img = self._vision.capture(self._arena_region)
                self._vision.save_image(arena_img, str(debug_dir / "arena_region.png"))
            focus_center = current_pos or scan_start
            focus_region = (
                int(focus_center[0] - 320),
                int(focus_center[1] - 220),
                640,
                440,
            )
            focus_img = self._vision.capture(focus_region)
            self._vision.save_image(focus_img, str(debug_dir / "scan_focus.png"))
        except Exception:
            pass
        bot_logger.log_error(f"Hand select debug bundle saved: {debug_dir}")

    def _write_hand_overlay_debug_bundle(
        self,
        *,
        reason: str,
        matched_anchor: str | None,
    ) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        debug_dir = Path(bot_logger.ensure_debug_dir(f"hand-overlay-{stamp}"))
        try:
            payload = {
                "reason": reason,
                "matched_anchor": matched_anchor,
                "state": str(self._get_state_from_log()),
                "arena_region": self._arena_region,
                "last_good_arena_region": self._last_good_arena_region,
                "turn_info": self.updated_game_state.get_turn_info() or {},
                "log_path": self._log_path,
            }
            with open(debug_dir / "overlay_state.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        try:
            tail = self._state_tracker.get_tail(180)
            if not tail:
                tail = self._read_log_tail(self._log_path, max_bytes=150000)
            with open(debug_dir / "log_tail.txt", "w", encoding="utf-8") as f:
                f.write(tail or "")
        except Exception:
            pass
        try:
            self._vision.begin_tick()
            full = self._vision.capture(None)
            self._vision.save_image(full, str(debug_dir / "full_screen.png"))
            if self._arena_region:
                arena_img = self._vision.capture(self._arena_region)
                self._vision.save_image(arena_img, str(debug_dir / "arena_region.png"))
        except Exception:
            pass
        bot_logger.log_error(f"Hand overlay debug bundle saved: {debug_dir}")

    def _ensure_options_overlay_closed(self, *, context: str, max_attempts: int = 2) -> bool:
        if focus_mtga_window():
            time.sleep(0.2)
        last_anchor = None
        for attempt in range(1, max_attempts + 1):
            detection = self._arena_region_provider.detect(write_debug_on_fail=False)
            last_anchor = detection.matched_anchor
            if detection.ok and detection.region is not None:
                self._arena_region = detection.region
                self._last_good_arena_region = detection.region
                self._last_good_arena_region_ts = time.time()
            if last_anchor != "options_anchor.png":
                return True
            bot_logger.log_error(
                f"{context}: options overlay detected before interaction; sending ESC (attempt {attempt}/{max_attempts})."
            )
            self.input.tap_escape()
            time.sleep(0.9)

        detection = self._arena_region_provider.detect(write_debug_on_fail=False)
        last_anchor = detection.matched_anchor
        if detection.ok and detection.region is not None:
            self._arena_region = detection.region
            self._last_good_arena_region = detection.region
            self._last_good_arena_region_ts = time.time()
        if last_anchor == "options_anchor.png":
            bot_logger.log_error(f"{context}: options overlay still visible after ESC retries.")
            self._write_hand_overlay_debug_bundle(
                reason="options_overlay_blocking_hand_scan",
                matched_anchor=last_anchor,
            )
            return False
        return True

    def _click_logout_target(self, raw_point: tuple[int, int], config_key: str, click_label: str) -> None:
        mapped = None
        source = ""
        play_origin = getattr(self, "_logout_play_origin", None)
        if isinstance(play_origin, tuple) and len(play_origin) == 2:
            rel = self._get_logout_target_relative_1920(config_key=config_key, raw_point=raw_point)
            if rel is not None:
                mapped = (int(play_origin[0] + rel[0]), int(play_origin[1] + rel[1]))
                source = f"{click_label}_mapped_from_play_button_origin"
        if mapped is None:
            mapped, source = self._resolve_target_from_queue_anchor_rebase(
                config_key=config_key,
                raw_point=raw_point,
                label=click_label,
                force_reacquire=True,
            )
        bot_logger.log_info(
            "{} target: source={} arena={} raw={} mapped={}".format(
                click_label,
                source,
                self._arena_region,
                raw_point,
                mapped,
            )
        )
        # Mirror record-playback click behavior for logout reliability.
        bot_logger.log_click(mapped[0], mapped[1], click_label)
        runtime_status.touch_input(click_label, mapped)
        self.input.move_abs(mapped[0], mapped[1])
        time.sleep(0.05)
        self.input.left_down()
        time.sleep(0.05)
        self.input.left_up()
        self._write_logout_click_debug_bundle(
            click_label=click_label,
            raw_point=raw_point,
            mapped_point=mapped,
            source=source,
        )

    def _get_logout_target_relative_1920(
        self,
        *,
        config_key: str,
        raw_point: tuple[int, int],
    ) -> tuple[int, int] | None:
        ct = self._loaded_click_targets or {}
        cfg = ct.get(config_key)
        if isinstance(cfg, dict):
            try:
                x = int(cfg.get("x"))
                y = int(cfg.get("y"))
                if 0 <= x <= 1920 and 0 <= y <= 1080:
                    return (x, y)
            except Exception:
                pass
        try:
            rx = int(raw_point[0])
            ry = int(raw_point[1])
            if 0 <= rx <= 1920 and 0 <= ry <= 1080:
                return (rx, ry)
        except Exception:
            pass
        return None

    def _resolve_logout_play_button_origin(self) -> tuple[int, int] | None:
        template = os.path.join(self._buttons_dir(), "play_btn.png")
        if not os.path.exists(template):
            return None
        try:
            point = self._locate_image_center_in_scaled_arena_region(
                template,
                "LOGOUT_PLAY_BTN_ORIGIN",
                rel_region=None,
                confidence=0.80,
                timeout=1.0,
            )
            if point is None:
                return None
            qrel = self._get_logout_target_relative_1920(
                config_key="queue_button",
                raw_point=self.home_play_button_coors,
            )
            if qrel is None:
                default_q = self._default_points_1920.get("home_play_button_coors")
                if default_q is None:
                    return None
                qrel = (int(default_q[0]), int(default_q[1]))
            origin = (int(point[0] - qrel[0]), int(point[1] - qrel[1]))
            bot_logger.log_info(
                f"Logout mapping: play_btn template origin={origin} match={point} qrel={qrel}"
            )
            return origin
        except Exception as e:
            bot_logger.log_info(f"Logout mapping: play_btn origin detect failed: {e}")
            return None

    def _get_hand_scan_points_mapped(
        self,
        *,
        force_reacquire: bool = False,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        p1, s1 = self._map_abs_point_to_arena(
            self.hand_scan_p1,
            label="HAND_SCAN_P1",
            force_reacquire=force_reacquire,
            apply_correction=False,
        )
        p2, s2 = self._map_abs_point_to_arena(
            self.hand_scan_p2,
            label="HAND_SCAN_P2",
            force_reacquire=False,
            apply_correction=False,
        )
        bot_logger.log_info(
            "HAND_SCAN mapped: arena={} raw_p1={} raw_p2={} mapped_p1={} mapped_p2={} src_p1={} src_p2={}".format(
                self._arena_region,
                self.hand_scan_p1,
                self.hand_scan_p2,
                p1,
                p2,
                s1,
                s2,
            )
        )
        return p1, p2

    def _get_battlefield_scan_points_mapped(
        self,
        *,
        force_reacquire: bool = False,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        p1, s1 = self._map_abs_point_to_arena(
            self.battlefield_scan_p1,
            label="BATTLEFIELD_SCAN_P1",
            force_reacquire=force_reacquire,
            apply_correction=False,
        )
        p2, s2 = self._map_abs_point_to_arena(
            self.battlefield_scan_p2,
            label="BATTLEFIELD_SCAN_P2",
            force_reacquire=False,
            apply_correction=False,
        )
        bot_logger.log_info(
            "BATTLEFIELD_SCAN mapped: arena={} raw_p1={} raw_p2={} mapped_p1={} mapped_p2={} src_p1={} src_p2={}".format(
                self._arena_region,
                self.battlefield_scan_p1,
                self.battlefield_scan_p2,
                p1,
                p2,
                s1,
                s2,
            )
        )
        return p1, p2

    @staticmethod
    def _normalize_search_region(
        region: tuple[int, int, int, int] | None,
    ) -> tuple[tuple[int, int, int, int] | None, str]:
        region_info = ""
        if region is None:
            return None, region_info
        try:
            normalized = (
                int(region[0]),
                int(region[1]),
                max(1, int(region[2])),
                max(1, int(region[3])),
            )
            region_info = f", region={normalized}"
            return normalized, region_info
        except Exception:
            return None, ""

    def _locate_image_center_direct(
        self,
        image_path: str,
        label: str,
        *,
        confidence: float = 0.82,
        timeout: float = 20.0,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int] | None:
        try:
            import pyautogui
        except Exception as e:
            bot_logger.log_error(f"{label}: pyautogui not available: {e}")
            return None

        if not os.path.exists(image_path):
            bot_logger.log_error(f"{label}: image not found at {image_path}")
            return None

        start = time.time()
        region, region_info = self._normalize_search_region(region)
        bot_logger.log_info(
            f"{label}: locating image with confidence={confidence:.2f}, timeout={timeout:.1f}s{region_info}."
        )
        while (time.time() - start) < timeout:
            if self._stop_requested:
                bot_logger.log_info(f"{label}: locate aborted (stop requested).")
                return None
            try:
                if region is not None:
                    pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence, region=region)
                else:
                    pos = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
            except Exception:
                pos = None
            if pos:
                return (int(pos.x), int(pos.y))
            time.sleep(0.5)
            if int((time.time() - start) * 10) % 20 == 0:
                elapsed = time.time() - start
                bot_logger.log_info(f"{label}: still locating ({elapsed:.1f}s elapsed).")
        return None

    def _click_image(
        self,
        image_path: str,
        label: str,
        confidence: float = 0.82,
        timeout: float = 20.0,
        region: tuple[int, int, int, int] | None = None,
    ) -> bool:
        point = self._locate_image_center(
            image_path,
            label,
            confidence=confidence,
            timeout=timeout,
            region=region,
        )
        if point is not None:
            self._click_abs(point[0], point[1], label)
            return True
        bot_logger.log_error(f"{label}: image not found within {timeout:.1f}s")
        return False

    def _locate_image_center(
        self,
        image_path: str,
        label: str,
        confidence: float = 0.82,
        timeout: float = 20.0,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int] | None:
        if not os.path.exists(image_path):
            bot_logger.log_error(f"{label}: image not found at {image_path}")
            return None
        region, _region_info = self._normalize_search_region(region)
        direct_point = self._locate_image_center_direct(
            image_path,
            label,
            confidence=confidence,
            timeout=min(timeout, 1.5 if region is None else timeout),
            region=region,
        )
        if direct_point is not None:
            return direct_point
        if region is not None:
            point = self._locate_image_center_in_rescaled_region(
                image_path,
                f"{label}_RESCALED",
                region=region,
                normalized_size=(int(region[2]), int(region[3])),
                confidence=confidence,
                timeout=timeout,
            )
            if point is not None:
                return point
        else:
            point = self._locate_image_center_in_scaled_arena_region(
                image_path,
                label,
                rel_region=None,
                confidence=confidence,
                timeout=timeout,
                use_direct=False,
            )
            if point is not None:
                return point
        bot_logger.log_error(f"{label}: image not found within {timeout:.1f}s")
        return None

    def _locate_image_center_in_rescaled_region(
        self,
        image_path: str,
        label: str,
        *,
        region: tuple[int, int, int, int],
        normalized_size: tuple[int, int],
        confidence: float = 0.82,
        timeout: float = 20.0,
    ) -> tuple[int, int] | None:
        if self._vision is None:
            return None
        if not os.path.exists(image_path):
            bot_logger.log_error(f"{label}: image not found at {image_path}")
            return None
        try:
            import cv2
        except Exception as e:
            bot_logger.log_error(f"{label}: cv2 not available: {e}")
            return None

        left, top, width, height = (
            int(region[0]),
            int(region[1]),
            max(1, int(region[2])),
            max(1, int(region[3])),
        )
        norm_w, norm_h = max(1, int(normalized_size[0])), max(1, int(normalized_size[1]))
        bot_logger.log_info(
            f"{label}: locating image in rescaled region={region}, normalized_size=({norm_w}, {norm_h}), "
            f"confidence={confidence:.2f}, timeout={timeout:.1f}s."
        )
        start = time.time()
        while (time.time() - start) < timeout:
            if self._stop_requested:
                bot_logger.log_info(f"{label}: locate aborted (stop requested).")
                return None
            self._vision.begin_tick()
            roi = self._vision.capture((left, top, width, height))
            if roi is None or getattr(roi, "size", 0) == 0:
                time.sleep(0.2)
                continue
            ih, iw = roi.shape[:2]
            if iw <= 0 or ih <= 0:
                time.sleep(0.2)
                continue
            if iw != norm_w or ih != norm_h:
                search_image = cv2.resize(roi, (norm_w, norm_h), interpolation=cv2.INTER_LINEAR)
            else:
                search_image = roi
            match = self._vision.find_template(search_image, image_path, threshold=confidence)
            if match is not None:
                hit_x = left + int(round((float(match.x) / float(norm_w)) * float(width)))
                hit_y = top + int(round((float(match.y) / float(norm_h)) * float(height)))
                return (hit_x, hit_y)
            time.sleep(0.2)
        bot_logger.log_error(f"{label}: image not found within {timeout:.1f}s")
        return None

    def _locate_image_center_in_scaled_arena_region(
        self,
        image_path: str,
        label: str,
        *,
        rel_region: tuple[int, int, int, int] | None = None,
        confidence: float = 0.82,
        timeout: float = 1.5,
        use_direct: bool = True,
    ) -> tuple[int, int] | None:
        arena = self._get_ui_action_arena_region(force_reacquire=True, label=label)
        if arena is None:
            return None
        if rel_region is None:
            region = tuple(int(v) for v in arena)
            normalized_size = (1920, 1080)
        else:
            region = self._scale_base_region_to_arena(arena, rel_region)
            normalized_size = (int(rel_region[2]), int(rel_region[3]))
        if use_direct:
            point = self._locate_image_center_direct(
                image_path,
                f"{label}_LOCATE",
                confidence=confidence,
                timeout=min(timeout, 1.0),
                region=region,
            )
            if point is not None:
                return point
        return self._locate_image_center_in_rescaled_region(
            image_path,
            f"{label}_RESCALED",
            region=region,
            normalized_size=normalized_size,
            confidence=confidence,
            timeout=timeout,
        )

    def _click_image_in_scaled_arena_region(
        self,
        image_path: str,
        label: str,
        *,
        rel_region: tuple[int, int, int, int] | None = None,
        confidence: float = 0.82,
        timeout: float = 1.5,
    ) -> bool:
        point = self._locate_image_center_in_scaled_arena_region(
            image_path,
            label,
            rel_region=rel_region,
            confidence=confidence,
            timeout=timeout,
        )
        if point is None:
            return False
        self._click_abs(point[0], point[1], label)
        return True

    def _get_log_size(self, path: str) -> int:
        try:
            return int(os.path.getsize(path))
        except Exception:
            return 0

    def _read_log_since(self, path: str, start_offset: int, max_bytes: int = 400000) -> str:
        try:
            offset = max(0, int(start_offset or 0))
        except Exception:
            offset = 0
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size <= offset:
                    return ""
                f.seek(offset)
                data = f.read(min(max_bytes, size - offset))
            return data.decode("utf-8", errors="ignore")
        except Exception as e:
            bot_logger.log_error(f"Failed to read player.log delta: {e}")
            return ""

    def _wait_for_playerlog_marker(
        self,
        markers: list[str],
        *,
        start_offset: int,
        timeout_sec: float,
        label: str,
    ) -> bool:
        normalized = [str(m or "").strip() for m in markers if str(m or "").strip()]
        if not normalized:
            return False
        deadline = time.time() + max(0.5, float(timeout_sec))
        while time.time() < deadline:
            if self._stop_requested:
                bot_logger.log_info(f"{label}: wait aborted (stop requested).")
                return False
            delta = self._read_log_since(self._log_path, start_offset=start_offset)
            if delta:
                lowered = delta.lower()
                for marker in normalized:
                    if marker.lower() in lowered:
                        bot_logger.log_info(f"{label}: matched player.log marker '{marker}'.")
                        return True
            time.sleep(0.25)
        return False

    def _wait_for_logout_to_reach_login_screen(self, *, start_offset: int, timeout_sec: float = 8.0) -> bool:
        return self._wait_for_playerlog_marker(
            [
                "Sending player back to Login screen.",
                "ALT_Prefab.LoginPanelPrefab",
                "CredentialLoginContext created",
            ],
            start_offset=start_offset,
            timeout_sec=timeout_sec,
            label="LOGOUT_WAIT",
        )

    def _playerlog_contains_marker_since(self, markers: list[str], *, start_offset: int) -> bool:
        if not self._log_path or not markers:
            return False
        try:
            with open(self._log_path, "rb") as f:
                f.seek(max(0, int(start_offset)))
                delta = f.read().decode("utf-8", errors="ignore")
        except Exception:
            return False
        lowered = delta.lower()
        return any(str(marker).lower() in lowered for marker in markers)

    def _set_runtime_home_mode(self, mode: str) -> None:
        runtime_status.set_mode(
            mode,
            bot_state=str(BotState.HOME),
            turn_info={},
            my_timer_running=False,
            my_timer_type="",
            my_timer_remaining_sec=None,
            my_timer_elapsed_sec=None,
            my_timer_duration_sec=None,
        )

    def _click_logout_image_if_visible(
        self,
        image_name: str,
        *,
        label: str,
        confidence: float = 0.84,
        timeout_sec: float = 1.2,
        region: tuple[int, int, int, int] | None = None,
    ) -> bool:
        image_path = os.path.join(self._buttons_dir(), image_name)
        if not os.path.exists(image_path):
            return False
        return self._click_image(
            image_path,
            label,
            confidence=confidence,
            timeout=timeout_sec,
            region=region,
        )

    def _region_around_point(
        self,
        point: tuple[int, int],
        *,
        width: int,
        height: int,
    ) -> tuple[int, int, int, int]:
        try:
            px = int(point[0])
            py = int(point[1])
        except Exception:
            px, py = 0, 0
        half_w = max(1, int(width // 2))
        half_h = max(1, int(height // 2))
        left = max(0, px - half_w)
        top = max(0, py - half_h)
        return (left, top, max(1, int(width)), max(1, int(height)))

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

    def _get_last_scene_name(self) -> str | None:
        if not self._log_path:
            return None
        log_tail = self._read_log_tail(self._log_path, max_bytes=250000)
        if not log_tail:
            return None
        idx = log_tail.rfind("Client.SceneChange")
        if idx == -1:
            return None
        line_start = log_tail.rfind("\n", 0, idx)
        line_end = log_tail.find("\n", idx)
        if line_start == -1:
            line_start = 0
        if line_end == -1:
            line_end = len(log_tail)
        line = log_tail[line_start:line_end]
        match = re.search(r'"toSceneName":"([^"]+)"', line)
        if not match:
            return None
        return match.group(1)

    def _last_scene_is_store(self) -> bool:
        return self._get_last_scene_name() == "Store"

    def _extract_latest_quests(self) -> list[dict] | None:
        """Latest quests block from the log, or None when none is available.

        Returns None for "no readable/valid block" (no log, no block, parse error,
        or a stale block from a previous account) so callers keep their cache.
        Returns a list (possibly EMPTY) when a current block exists -- an empty
        list means all daily quests are completed, which the quest-mode account
        switch must be able to see."""
        if not self._log_path:
            return None
        log_tail = self._read_log_tail(self._log_path)
        if not log_tail:
            return None
        idx = log_tail.rfind('"quests"')
        if idx == -1:
            return None
        # Account-safety: a "quests" block belongs to whichever account was logged
        # in when MTGA wrote it. After an account switch the PREVIOUS account's
        # block is still in the log tail, and using it makes the bot farm the wrong
        # account's quests (its colors/deck). We latch the owning screenName once
        # per account session (the first quests block we parse after a fresh start
        # / switch) and compare every later block against that fixed value.
        #
        # We deliberately do NOT use "the last screenName anywhere in the log
        # tail" as "the current account": match-room events log BOTH players'
        # screenNames (reservedPlayers), so after a single match the last
        # occurrence is frequently the opponent's, which would make every
        # subsequent (genuinely current) quests block look stale forever.
        try:
            name_matches = list(re.finditer(r'"screenName"\s*:\s*"([^"]+)"', log_tail))
            owner = None
            for m in name_matches:
                if m.start() < idx:
                    owner = m.group(1)
                else:
                    break
            if owner is not None:
                if self._current_account_screen_name is None:
                    self._current_account_screen_name = owner
                    self._register_current_account_for_gold()
                elif owner != self._current_account_screen_name:
                    bot_logger.log_info(
                        f"Quests block ignored: belongs to '{owner}' but this session's "
                        f"account is '{self._current_account_screen_name}' (stale after "
                        f"account switch)."
                    )
                    return None
        except Exception:
            pass
        start = log_tail.rfind("{", 0, idx)
        if start == -1:
            return None
        decoder = json.JSONDecoder()
        try:
            payload, _ = decoder.raw_decode(log_tail[start:])
        except Exception:
            return None
        quests = payload.get("quests", [])
        if isinstance(quests, list):
            return quests
        return None

    def _parse_guild_quests(self, quests: list[dict]) -> list[dict]:
        parsed = []
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

    def _has_creature_quest(self, quests: list[dict]) -> bool:
        for quest in quests:
            loc_key = str(quest.get("locKey", "")).lower()
            if "quest_creature" in loc_key:
                return True
        return False

    def _has_quest_loc_key(self, quests: list[dict], key_fragment: str) -> bool:
        needle = key_fragment.lower()
        for quest in quests:
            loc_key = str(quest.get("locKey", "")).lower()
            if needle in loc_key:
                return True
        return False

    def _select_best_quest(self) -> dict | None:
        quests = self._extract_latest_quests() or []
        bot_logger.log_info(f"Post-login: parsed {len(quests)} quest entries from player.log.")
        guild_quests = self._parse_guild_quests(quests)
        if guild_quests:
            guild_quests.sort(key=lambda q: q.get("gold", 0), reverse=True)
            top = guild_quests[0]
            top["type"] = "guild"
            return top
        if self._has_quest_loc_key(quests, "quest_fatal_push"):
            return {"type": "forced_file", "file": "B.png", "reason": "fatal_push"}
        if self._has_quest_loc_key(quests, "quest_raiding_party"):
            return {"type": "forced_file", "file": "C.png", "reason": "raiding_party"}
        if self._has_creature_quest(quests):
            return {"type": "creature"}
        return None

    @staticmethod
    def _quest_display_name(loc_key: str) -> str:
        """'Quests/Quest_Dimir_Cutpurse' -> 'Dimir Cutpurse'."""
        stem = str(loc_key or "").split("/")[-1]
        stem = re.sub(r"^quest_", "", stem, flags=re.I)
        name = stem.replace("_", " ").strip()
        return name or "Quest"

    @staticmethod
    def _quest_colors_for_loc_key(loc_key: str) -> str:
        lk = str(loc_key or "").lower()
        for guild, colors in _GUILD_COLOR_MAP.items():
            if guild in lk:
                return colors
        return ""

    def _build_quest_view(self, quests: list[dict]) -> list[dict]:
        """Convert raw player.log quests into a compact, UI-friendly list."""
        view = []
        for q in quests or []:
            loc_key = q.get("locKey", "")
            gold = 0
            chest = q.get("chestDescription") or {}
            loc_params = chest.get("locParams") or {}
            if isinstance(loc_params, dict):
                try:
                    gold = int(loc_params.get("number1") or 0)
                except (TypeError, ValueError):
                    gold = 0
            try:
                progress = int(q.get("endingProgress") or 0)
            except (TypeError, ValueError):
                progress = 0
            try:
                goal = int(q.get("goal") or 0)
            except (TypeError, ValueError):
                goal = 0
            view.append({
                "id": str(q.get("questId", "")),
                "name": self._quest_display_name(loc_key),
                "colors": self._quest_colors_for_loc_key(loc_key),
                "gold": gold,
                "progress": progress,
                "goal": goal,
            })
        return view

    def _current_account_key(self) -> str:
        """Label used to attribute farmed gold. The MTGA screenName is the only
        identity the log exposes for every account (initial + switched-in); fall
        back to a generic label until it is latched from the first quests block."""
        return self._current_account_screen_name or "(current account)"

    def _publish_gold_farmed(self) -> None:
        try:
            runtime_status.update_status(
                gold_farmed=dict(self._gold_farmed_by_account),
                account_aliases=dict(self._screenname_to_alias),
            )
        except Exception:
            pass

    def set_gold_per_win(self, gold: int) -> None:
        """Live-adjust the per-win gold estimate. Not currently called from any
        UI control (the Current Session window is read-only) -- kept for a
        future live-editable setting. Only affects future credits."""
        try:
            self._gold_per_win = max(0, int(gold))
        except (TypeError, ValueError):
            pass

    def _match_configured_alias(self, screen_name: str) -> str | None:
        """Best-effort alias for an account we did NOT switch into (e.g. the one
        already logged in at startup): match the screenName against a configured
        account name, case-insensitively. Returns None when there's no exact hit
        (we never guess)."""
        try:
            for acc in (self._load_accounts_from_dirs() or []):
                name = str(acc.get("name") or "")
                if name and name.casefold() == str(screen_name).casefold():
                    return name
        except Exception:
            pass
        return None

    def _register_current_account_for_gold(self) -> None:
        """Ensure the current account has a (0-gold) row as soon as its screenName
        is known, map it to its configured alias when we can, and fold in any gold
        credited before it was latched (a win that landed under the fallback key)."""
        key = self._current_account_key()
        if key != "(current account)" and key not in self._screenname_to_alias:
            alias = self._pending_switch_alias or self._match_configured_alias(key)
            if alias:
                self._screenname_to_alias[key] = alias
        self._pending_switch_alias = None
        pending = self._gold_farmed_by_account.pop("(current account)", 0) if key != "(current account)" else 0
        self._gold_farmed_by_account[key] = self._gold_farmed_by_account.get(key, 0) + int(pending or 0)
        self._publish_gold_farmed()

    def _credit_gold(self, amount: int, reason: str = "") -> None:
        amount = int(amount or 0)
        if amount <= 0:
            return
        key = self._current_account_key()
        self._gold_farmed_by_account[key] = self._gold_farmed_by_account.get(key, 0) + amount
        bot_logger.log_info(
            "Gold farmed +{} ({}) for '{}': session total {}.".format(
                amount, reason or "reward", key, self._gold_farmed_by_account[key]
            )
        )
        self._publish_gold_farmed()

    def _credit_completed_quest_gold(self, current_ids: set[str]) -> None:
        """Credit the gold of any tracked quest that has dropped out of the list
        (i.e. was completed) and hasn't been credited yet for this account."""
        for quest_id, gold in self._account_quest_gold.items():
            if quest_id in current_ids or quest_id in self._credited_quest_ids:
                continue
            self._credited_quest_ids.add(quest_id)
            self._credit_gold(gold, reason=f"quest {quest_id} completed")

    def refresh_quests_cache(self) -> list[dict]:
        """Parse the account's quests once and cache them for local reuse.

        Called at startup (from Home) and between matches. Populates
        self._cached_quests / _cached_active_* and publishes them to
        runtime_status so the bot UI can show them. Keeps the last known list if
        the log has no quests block right now.
        """
        quests = self._extract_latest_quests()
        if quests is None:
            # No readable/valid quests block right now -> keep the last known list.
            return self._cached_quests
        # quests may be an empty list here, meaning all daily quests are done.
        view = self._build_quest_view(quests)

        # This is a VALID read (a quests block was parsed), so update the absolute
        # incomplete-quest count used by the switch decision. A listed quest at/
        # past its goal (rare -- usually it has dropped out) is NOT counted as
        # incomplete; an empty list means every daily quest is done -> 0.
        self._last_valid_quest_active_incomplete = sum(
            1 for v in view
            if not (v.get("goal") and v.get("progress", 0) >= v.get("goal", 0))
        )

        current_ids = {str(v.get("id")) for v in view if v.get("id")}
        # Record each quest's gold reward while it is still listed, so we still
        # know its value once it completes (drops out), then credit "gold farmed"
        # for any tracked quest that has since disappeared.
        for v in view:
            qid = str(v.get("id") or "")
            if qid:
                self._account_quest_gold[qid] = int(v.get("gold") or 0)
        if self._account_quest_gold:
            self._credit_completed_quest_gold(current_ids)

        # Active quest = the one whose colors we play. Mirror _select_best_quest:
        # the highest-gold guild (two-color) quest -- but skip quests already at
        # their goal so the bot switches to an unfinished quest instead of grinding
        # a completed one forever. Fall back to all guild quests if every one is done.
        active_id = ""
        active_colors = ""
        guild_entries = [v for v in view if v.get("colors")]
        incomplete_guild = [
            v for v in guild_entries
            if not v.get("goal") or v.get("progress", 0) < v.get("goal", 0)
        ]
        quest_pool = incomplete_guild or guild_entries
        if quest_pool:
            best = max(quest_pool, key=lambda v: v.get("gold", 0))
            active_id = best.get("id", "")
            active_colors = best.get("colors", "")

        self._cached_quests = view
        self._cached_active_quest_id = active_id
        self._cached_active_colors = active_colors
        for v in view:
            v["active"] = (v.get("id") == active_id and bool(active_id))
        try:
            runtime_status.update_status(
                quests=view,
                active_quest_id=active_id,
                active_quest_colors=active_colors,
            )
        except Exception:
            pass
        bot_logger.log_info(
            "Quests cached: {} quest(s); active={} colors={}.".format(
                len(view), active_id or "-", active_colors or "-"
            )
        )
        return view

    # The Home tab sits at a fixed spot in the top-left nav bar (1920x1080 frame).
    # A TEMPLATE match only works when Home is the ACTIVE tab, so from the event
    # page (where the bot re-queues) the anchor never matches and navigation fails.
    # Measured live at (104, 39); clicking that fixed point returns Home from any
    # main screen regardless of which tab is currently active.
    _HOME_TAB_POINT_1920 = (104, 39)

    def _navigate_to_home(self) -> bool:
        """Return to the Home screen by clicking the fixed top-left Home tab.

        Clicks a fixed 1920-frame coordinate (not a template match, which only
        works when Home is already the active tab) and verifies we actually landed
        on Home (its anchor then matches). Returns True only if Home was reached."""
        if self._stop_requested:
            return False
        arena = self._ensure_arena_region(force_reacquire=True)
        if arena is None:
            return False
        point = self._map_base_point_into_arena(arena, self._HOME_TAB_POINT_1920)
        self._click_abs(int(point[0]), int(point[1]), "GO_HOME")
        time.sleep(1.5)
        # Verify we reached Home: its tab is now active, so the anchor matches.
        home_tab = self._app_path("assets", "assert", "home_anchor.png")
        reached = self._locate_image_center_in_scaled_arena_region(
            home_tab, "GO_HOME_VERIFY", rel_region=(0, 0, 760, 260),
            confidence=0.75, timeout=2.0,
        ) is not None
        if reached:
            bot_logger.log_info("Quest refresh: reached Home (fixed-coord click).")
        else:
            bot_logger.log_info("Quest refresh: Home click did not land on Home; will retry.")
        return reached

    def _refresh_quests_from_home(self) -> bool:
        """Dip back to Home so MTGA re-fetches quests, then refresh the local
        cache + UI.

        MTGA only logs quest progress on Home (QuestGetQuests); the event page the
        bot re-queues from never re-logs it, so quest data and the UI would freeze
        at their startup values. This returns to Home, waits for a fresh quests
        block, and refreshes. Bounded and self-healing: if Home is not reached it
        aborts quickly and the caller falls back to the normal re-queue, so it can
        never permanently stall the bot. Returns True if Home was reached."""
        if self._stop_requested:
            return False
        if self._get_state_from_log() == BotState.IN_GAME:
            return False
        bot_logger.log_info("Quest refresh: dipping to Home to re-fetch quest progress.")
        reached = False
        for _ in range(3):
            if self._stop_requested:
                return False
            if self._navigate_to_home():
                reached = True
                break
            time.sleep(0.8)
        if not reached:
            bot_logger.log_info("Quest refresh: Home tab not found; will retry after more matches.")
            return False
        # Give MTGA a moment to land on Home and log a fresh quests block, polling
        # the parser until it picks the new data up (or a short timeout elapses).
        prev_active = self._cached_active_quest_id
        prev_progress = {q.get("id"): q.get("progress") for q in self._cached_quests}
        deadline = time.time() + 8.0
        while time.time() < deadline and not self._stop_requested:
            time.sleep(1.0)
            self.refresh_quests_cache()
            new_progress = {q.get("id"): q.get("progress") for q in self._cached_quests}
            if new_progress != prev_progress or self._cached_active_quest_id != prev_active:
                break
        bot_logger.log_info(
            "Quest refresh: done (active {} -> {}, colors {}).".format(
                prev_active or "-",
                self._cached_active_quest_id or "-",
                self._cached_active_colors or "-",
            )
        )
        return True

    def _accounts_base_dir(self) -> str:
        base = self._app_path("Accounts")
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        return base

    def _legacy_accounts_base_dir(self) -> str:
        return self._app_root_dir()

    def _resolve_account_dir(self, account: dict) -> str | None:
        folder_name = str(account.get("folder", "")).strip()
        if not folder_name:
            return None
        bases = [self._accounts_base_dir(), self._legacy_accounts_base_dir()]
        for base in bases:
            full = os.path.join(base, folder_name)
            if os.path.isdir(full):
                return full
        return None

    # Center of the first deck tile in the Historic "My Decks" grid (the tile
    # right after the "+" add-deck tile), in the 1920x1080 arena reference
    # frame. Used as a coordinate fallback when an account folder has no deck
    # thumbnail screenshots to template-match against -- MTGA always lists an
    # account's decks starting from this fixed top-left slot on a fresh,
    # unscrolled "My Decks" view (the same page this is clicked from).
    _MY_DECKS_FIRST_SLOT_BASE = (456, 552)

    def _my_decks_grid_open(self) -> bool:
        """True if the My Decks grid is already expanded (the "+" add-deck
        tile is visible). "My Decks" is a collapsible section that MTGA may
        remember as already-open from a previous session; clicking its header
        again would COLLAPSE it instead of opening it. Callers must check this
        before clicking the header, and skip the click if it's already open."""
        anchor = os.path.join(self._buttons_dir(), "my_decks_grid_open.png")
        return os.path.exists(anchor) and self._locate_image_center_in_scaled_arena_region(
            anchor, "MY_DECKS_GRID_OPEN_PROBE", rel_region=None, confidence=0.80, timeout=1.5,
        ) is not None

    def _click_first_deck_slot(self) -> bool:
        """Click the first (top-left) deck tile in the My Decks grid.

        Fallback for accounts with no deck thumbnail screenshots configured:
        rather than fail with no deck selected, just pick whatever deck MTGA
        lists first."""
        if not self._my_decks_grid_open():
            # A prior click may have collapsed the section (see
            # _my_decks_grid_open's docstring) -- try once to (re-)open it via
            # its header before giving up, rather than clicking blind.
            decks_btn = os.path.join(self._buttons_dir(), "my_decks.png")
            self._click_image_in_scaled_arena_region(
                decks_btn, "POST_LOGIN_FIRST_DECK_REOPEN", rel_region=None, confidence=0.80, timeout=1.5
            )
            time.sleep(1.0)
            if not self._my_decks_grid_open():
                bot_logger.log_info(
                    "Post-login: My Decks grid not visible before first-deck fallback; "
                    "keeping current deck rather than clicking blind."
                )
                return False
        target, source = self._map_abs_point_to_arena(
            self._MY_DECKS_FIRST_SLOT_BASE, label="POST_LOGIN_FIRST_DECK"
        )
        bot_logger.log_info(
            f"Post-login: no deck thumbnails configured; selecting first deck in the list "
            f"base={self._MY_DECKS_FIRST_SLOT_BASE} -> {target} ({source})."
        )
        self._click_abs(target[0], target[1], "POST_LOGIN_FIRST_DECK")
        return True

    def _choose_deck_image(
        self,
        account: dict,
        target_letters: str | None,
        forced_filename: str | None = None,
    ) -> str | None:
        account_dir = self._resolve_account_dir(account)
        if not account_dir:
            bot_logger.log_error("Post-login: account folder not found.")
            return None
        images = []
        for name in os.listdir(account_dir):
            if name.lower().endswith((".png", ".jpg", ".jpeg")):
                images.append(name)
        if not images:
            bot_logger.log_info("Post-login: no deck images configured in account folder.")
            return None
        if forced_filename:
            force_lower = forced_filename.lower()
            for name in images:
                if name.lower() == force_lower:
                    bot_logger.log_info(f"Post-login: forced quest deck selected {name}.")
                    return os.path.join(account_dir, name)
            bot_logger.log_info(
                f"Post-login: forced quest deck {forced_filename} not found; using fallback logic."
            )
        if not target_letters:
            choice = random.choice(images)
            bot_logger.log_info(f"Post-login: no target letters; randomly selected {choice}.")
            return os.path.join(account_dir, choice)

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

    def _run_post_login_navigation_oob(self) -> bool:
        arena = self._ensure_arena_region(force_reacquire=False)
        if arena is None:
            bot_logger.log_error("Post-login navigation: failed to acquire MTGA window region.")
            self._write_nav_debug_bundle("arena_region_not_found")
            return False

        assets_dir = self._app_path("assets", "assert")
        buttons_dir = self._buttons_dir()
        actions = build_post_login_navigation_actions(assets_dir=assets_dir, buttons_dir=buttons_dir)

        def _recover(action_name: str, attempt: int) -> None:
            bot_logger.log_info(
                f"Post-login navigation recover: action={action_name} attempt={attempt} (ESC + reacquire)."
            )
            try:
                self.input.tap_escape()
            except Exception:
                pass
            time.sleep(0.5)
            self._ensure_arena_region(force_reacquire=True)

        for spec in actions:
            result = run_action(
                spec,
                state_getter=self._get_state_from_log,
                vision=self._vision,
                arena_region_getter=lambda: self._ensure_arena_region(force_reacquire=False),
                click_abs=self._click_abs,
                recover_once=_recover,
            )
            if not result.ok:
                self._navigation_verify_failures += 1
                bot_logger.log_error(
                    f"Post-login navigation action failed: {spec.name} reason={result.reason}"
                )
                self._write_nav_debug_bundle(result.reason)
                return False

        self._navigation_verify_failures = 0
        return True

    def _run_starter_deck_routine(self) -> bool:
        # Post-login entry point simply delegates to the shared navigation, which
        # is also used by the between-matches queue loop.
        return self._navigate_starter_deck()

    def _dismiss_reward_popup(self) -> bool:
        """Click 'Claim' on the Starter Deck Duel reward screen.

        After winning games the event shows a full-window 'Reward' popup with a
        Claim button; it covers the Play/Events controls, so the queue loop gets
        stuck retrying navigation forever. This is template-gated (Buttons/claim.png)
        so it is a safe no-op on any screen where the Claim button is absent.
        """
        if self._stop_requested:
            return False
        claim_btn = os.path.join(self._buttons_dir(), "claim.png")
        if not os.path.exists(claim_btn):
            return False
        # Claim button sits at the bottom-right of the window (1920x1080 frame).
        claim_roi = (1450, 850, 470, 230)
        if self._click_image_in_scaled_arena_region(
            claim_btn, "REWARD_CLAIM", rel_region=claim_roi, confidence=0.80, timeout=1.5
        ):
            bot_logger.log_info("Reward screen detected: clicked Claim to continue.")
            time.sleep(1.0)
            return True
        return False

    def _dismiss_match_end_screen(self) -> bool:
        """Safety net for a DEFEAT/VICTORY result screen the post-match timer
        failed to dismiss, so the queue loop never stalls on it.

        The full-screen result hides every nav anchor, so detect() returns not-ok
        -- but so do brief loading transitions. To avoid clicking mid-transition we
        only act after the screen has stayed unrecognized across several
        consecutive navigation attempts, then click the continue prompt + center
        (language-independent, no result-text template needed). Returns True if it
        clicked, so the caller restarts navigation on the next loop."""
        if self._stop_requested:
            return False
        # In a game the anchors are legitimately different; never click there.
        if self._get_state_from_log() == BotState.IN_GAME:
            self._unknown_screen_strikes = 0
            return False
        try:
            det = self._arena_region_provider.detect(write_debug_on_fail=False)
        except Exception:
            det = None
        if det is not None and det.ok:
            # Back on a recognizable Arena screen -- let normal navigation proceed.
            self._unknown_screen_strikes = 0
            return False
        # Unrecognized/blank screen. Require persistence so a normal loading
        # transition is not mistaken for a stuck result screen.
        self._unknown_screen_strikes = getattr(self, "_unknown_screen_strikes", 0) + 1
        if self._unknown_screen_strikes < 3:
            return False
        arena = det.region if (det is not None and det.region is not None) else self._get_ui_action_arena_region(
            force_reacquire=True, label="MATCH_END_RECOVER"
        )
        if arena is None:
            return False
        cx = int(arena[0] + (arena[2] // 2))
        continue_y = int(arena[1] + (arena[3] * 0.93))
        center_y = int(arena[1] + (arena[3] // 2))
        bot_logger.log_info(
            f"Match-end recovery: screen unrecognized for {self._unknown_screen_strikes} tries; clicking continue to advance."
        )
        if focus_mtga_window():
            time.sleep(0.2)
        for ty in (continue_y, center_y):
            if self._stop_requested:
                break
            self.input.move_abs(cx, ty)
            time.sleep(0.25)
            self.input.left_click(1)
            time.sleep(0.5)
        self._unknown_screen_strikes = 0
        return True

    def _queue_from_event_landing(self, target_colors: str) -> bool:
        """Re-queue directly from the Starter Deck Duel event landing page.

        After a match MTGA returns to the event's own page (not Home), which has
        its own Play button. The Home->Events->banner navigation can't start from
        here (there is no Events tab), so the bot would stall. This clicks that
        Play button, swapping to the quest-matched deck first. Template-gated on
        Buttons/event_play.png, so it is a no-op on any other screen and the
        caller can fall back to full navigation.
        """
        event_play = os.path.join(self._buttons_dir(), "event_play.png")
        if not os.path.exists(event_play):
            return False
        # Play button at the bottom-right of the event page (1920x1080 frame).
        event_play_roi = (1400, 900, 520, 180)
        # Confirm we are actually on the event page before touching the deck box.
        if self._locate_image_center_in_scaled_arena_region(
            event_play, "STARTER_EVENT_PLAY_PROBE", rel_region=event_play_roi,
            confidence=0.80, timeout=1.0,
        ) is None:
            return False
        # Between matches: refresh quest progress best-effort (keeps the cache and
        # the UI display current when Home has logged a fresh quests block).
        self.refresh_quests_cache()
        # Swap to the deck that best advances the top quest, then queue.
        self._swap_starter_deck_for_quest(target_colors)
        if self._click_image_in_scaled_arena_region(
            event_play, "STARTER_EVENT_PLAY", rel_region=event_play_roi,
            confidence=0.80, timeout=2.0,
        ):
            bot_logger.log_info("Starter: re-queued from event landing page via Play button.")
            time.sleep(1.0)
            return True
        return False

    def _navigate_starter_deck(self) -> bool:
        """Navigate Home -> Play -> Events -> In Progress -> Starter Deck Duel.

        This mirrors how the Historic flow selects its queue, but instead of the
        Historic button it clicks the "In Progress" filter and then the Starter
        Deck Duel banner on the left. Every click is done through
        _click_image_in_scaled_arena_region, so all ROIs are 1920x1080 arena
        references scaled to the real arena -> resolution independent. Template
        matching also makes the flow self-limiting: if we are already in
        matchmaking or in a game, the buttons are not on screen and each step
        returns without disrupting anything.
        """
        if self._stop_requested:
            return False
        if self._get_state_from_log() == BotState.IN_GAME:
            return False

        # A post-match reward popup covers the Play/Events controls; clear it
        # first so navigation is not stuck retrying against a blocked screen.
        if self._dismiss_reward_popup():
            return False

        # Safety net: a DEFEAT/VICTORY result screen the post-match timer failed
        # to dismiss also covers the Play/Events controls. Clear it so the queue
        # loop never spins forever on a screen that still shows the result.
        if self._dismiss_match_end_screen():
            return False

        # Populate the quest cache the first time we reach navigation (in case the
        # startup pass ran before Home had logged the quests block).
        if not self._cached_quests:
            self.refresh_quests_cache()

        # Decide up-front which deck colors advance the most valuable quest;
        # the swap below only fires when we have a concrete color target.
        target_colors = self._resolve_starter_target_colors()

        buttons_dir = self._buttons_dir()
        assets_dir = self._app_path("assets", "assert")
        play_btn = os.path.join(buttons_dir, "play_btn.png")
        events_tpl = os.path.join(assets_dir, "events_tab.png")
        in_progress_tpl = os.path.join(assets_dir, "in_progress_anchor.PNG")
        starter_tpl = os.path.join(assets_dir, "starter_deck.PNG")

        # ROIs in the 1920x1080 arena reference frame (scaled to the real arena).
        home_play_roi = (1450, 820, 440, 220)
        events_tab_roi = (1150, 40, 770, 320)
        in_progress_roi = (1380, 200, 540, 500)
        # Wide enough for both banner columns: the event is not always in the
        # first slot (e.g. Jump In! can occupy it, pushing Starter Deck Duel
        # into the second column at x ~760..1460 in the 1920 frame).
        starter_banner_roi = (20, 80, 1500, 900)
        play_confirm_roi = (1160, 680, 740, 360)

        bot_logger.log_info("Starter: navigating Play > Events > In Progress > Starter Deck Duel.")

        # 1) Open the Play blade from Home. Best-effort: if we are already in the
        #    blade the home Play button is not found and we just continue.
        if self._click_image_in_scaled_arena_region(
            play_btn, "STARTER_PLAY", rel_region=home_play_roi, confidence=0.80, timeout=1.5
        ):
            time.sleep(1.0)

        # 2) Events tab (top-right of the Play blade).
        if not self._click_image_in_scaled_arena_region(
            events_tpl, "STARTER_EVENTS", rel_region=events_tab_roi, confidence=0.74, timeout=2.0
        ):
            # Not in the Play blade. We may already be on the event landing page
            # (MTGA returns here after each match) -- re-queue from its Play
            # button instead of stalling.
            if self._queue_from_event_landing(target_colors):
                return True
            # Last resort: the very first time this event is entered (no deck
            # ever picked for it yet on this account), MTGA drops the player
            # directly onto the full-page deck-grid picker instead of the normal
            # Home/Play-blade screens, so neither the Play button nor the Events
            # tab is present there -- only try this once we have positively
            # confirmed BOTH are missing (not just on the very first check of the
            # session), and only once per account. "Submit Deck" is a generic
            # button MTGA also shows on other formats' deckbuilders (e.g. Draft),
            # so trusting it before ruling out normal navigation risks clicking
            # the fixed Starter Deck Duel grid coordinates on an unrelated event
            # the player has open/in-progress.
            if not self._starter_picker_shortcut_tried:
                self._starter_picker_shortcut_tried = True
                if self._starter_deck_picker_open():
                    bot_logger.log_info(
                        "Starter: no Play/Events chrome found but deck picker is open; "
                        "selecting quest deck directly (first-time entry)."
                    )
                    self._swap_starter_deck_for_quest(target_colors)
                    return True
            bot_logger.log_info("Starter: Events tab not found (not in Play blade yet); will retry.")
            return False
        time.sleep(0.8)

        # 3) Best-effort "In Progress" filter. It only helps surface the banner
        #    when the default Events view does not already show it, so a miss is
        #    not a failure -- the banner match below is the real success gate.
        if self._click_image_in_scaled_arena_region(
            in_progress_tpl, "STARTER_IN_PROGRESS", rel_region=in_progress_roi, confidence=0.66, timeout=1.5
        ):
            bot_logger.log_info("Starter: In Progress filter selected.")
            time.sleep(0.8)
        else:
            bot_logger.log_info("Starter: In Progress filter not clicked (banner likely already visible).")

        # 4) Starter Deck Duel banner on the left.
        if not self._click_image_in_scaled_arena_region(
            starter_tpl, "STARTER_BANNER", rel_region=starter_banner_roi, confidence=0.72, timeout=3.0
        ):
            bot_logger.log_error("Starter: Starter Deck Duel banner not found on screen.")
            return False
        time.sleep(1.0)

        # 4.5) Swap to the quest-matched starter deck before pressing Play.
        self._swap_starter_deck_for_quest(target_colors)

        # 5) Best-effort Play/Resume confirm. Clicking the "Resume" banner may
        #    already launch the match, so a missing Play button is not a failure.
        if self._click_image_in_scaled_arena_region(
            play_btn, "STARTER_PLAY_CONFIRM", rel_region=play_confirm_roi, confidence=0.80, timeout=1.5
        ):
            bot_logger.log_info("Starter: Play confirm button clicked.")
        else:
            bot_logger.log_info("Starter: no separate Play button (likely launched from Resume).")

        bot_logger.log_info("Starter: Starter Deck Duel selected.")
        return True

    def _resolve_starter_target_colors(self) -> str:
        """Colors of the deck that best advances the current top quest.

        Prefers the locally cached quests (parsed once at startup / between
        matches) so we don't re-parse the player.log on every queue cycle; falls
        back to a live parse if the cache is empty. Returns an empty string when
        there is no concrete two-color target (keep the current deck).
        """
        if self._cached_quests:
            if self._cached_active_colors:
                bot_logger.log_info(
                    f"Starter: using cached quest colors {self._cached_active_colors}."
                )
            else:
                bot_logger.log_info("Starter: cached quests have no guild target; keeping current deck.")
            return self._cached_active_colors
        quest = self._select_best_quest()
        if not quest:
            bot_logger.log_info("Starter: no active quest; keeping current deck.")
            return ""
        qtype = quest.get("type")
        if qtype == "guild":
            colors = _GUILD_COLOR_MAP.get(quest.get("guild") or "", "")
            bot_logger.log_info(
                f"Starter: best quest guild={quest.get('guild')} colors={colors} "
                f"gold={quest.get('gold', 0)}."
            )
            return colors
        if qtype == "forced_file":
            # Single-letter file (e.g. B.png): use its color letters and let the
            # chooser pick the best two-color deck that contains them.
            stem = os.path.splitext(str(quest.get("file") or ""))[0]
            colors = "".join(ch for ch in stem.upper() if ch in _COLOR_LETTERS)
            bot_logger.log_info(
                f"Starter: forced quest ({quest.get('reason')}) colors={colors}."
            )
            return colors
        bot_logger.log_info(
            f"Starter: quest type={qtype} has no starter-deck color mapping; keeping current deck."
        )
        return ""

    def _choose_starter_deck_template(self, target_letters: str | None) -> str | None:
        """Pick the starter-deck template best matching the target colors.

        Mirrors :meth:`_choose_deck_image` scoring (shared colors win, fewer extra
        colors break ties) but over assets/assert/starter_decks and returns None on
        no match, so we never swap to a wrong-color deck.
        """
        if not target_letters:
            return None
        decks_dir = self._app_path("assets", "assert", "starter_decks")
        if not os.path.isdir(decks_dir):
            bot_logger.log_error("Starter: starter_decks folder not found.")
            return None
        images = [
            n for n in os.listdir(decks_dir)
            if n.lower().endswith((".png", ".jpg", ".jpeg"))
        ]
        if not images:
            bot_logger.log_error("Starter: no starter deck templates found.")
            return None
        target_set = set(target_letters.upper())
        best = None
        best_score = (-1, -999, 0, "")
        for name in images:
            stem = os.path.splitext(name)[0]
            name_letters = {ch for ch in stem.upper() if ch in _COLOR_LETTERS}
            score = len(name_letters & target_set)
            extra = len(name_letters - target_set)
            tie = (score, -extra, -len(stem), name.lower())
            if tie > best_score:
                best_score = tie
                best = name
        if best is None or best_score[0] <= 0:
            bot_logger.log_info(
                f"Starter: no starter deck matches colors '{target_letters}'; keeping current deck."
            )
            return None
        bot_logger.log_info(
            f"Starter: chose deck template {best} for colors '{target_letters}'."
        )
        return os.path.join(decks_dir, best)

    # The 10 Starter Deck Duel decks are shown in the chooser in a FIXED
    # alphabetical grid (6 on the top row, 4 on the bottom). Their positions
    # never change, so we select by grid coordinate instead of template matching
    # (the deck art templates are a different scale than the chooser thumbnails
    # and never match). Map deck color code -> grid index (alphabetical):
    #   0 Arcane Aerialists WU | 1 Cat Attack WG | 2 Graveyard Gifts UB
    #   3 Learn from the Land UG | 4 Might of the Legion WR | 5 Morbid Machinations BG
    #   6 Path of Power RG | 7 Reckless Raid BR | 8 Vampiric Hunger WB
    #   9 Wondrous Wizardry UR
    _STARTER_DECK_GRID_INDEX = {
        "WU": 0, "WG": 1, "UB": 2, "UG": 3, "WR": 4, "BG": 5,
        "RG": 6, "BR": 7, "WB": 8, "UR": 9,
    }
    # Card centers in the 1920x1080 reference frame (measured from the chooser).
    _STARTER_DECK_COL_X = (187, 480, 773, 1066, 1359, 1652)
    _STARTER_DECK_ROW_Y = (400, 705)
    _STARTER_DECK_BOX_BASE = (1734, 640)       # current-deck box on the event page
    _STARTER_SUBMIT_DECK_BASE = (1735, 1035)   # "Submit Deck" button in the chooser

    def _starter_deck_grid_point(self, deck_code: str) -> tuple[int, int] | None:
        """Base-1920 (x, y) of a deck's card in the chooser grid, or None."""
        idx = self._STARTER_DECK_GRID_INDEX.get(str(deck_code or "").upper())
        if idx is None:
            return None
        row, col = divmod(idx, len(self._STARTER_DECK_COL_X))
        if row >= len(self._STARTER_DECK_ROW_Y):
            return None
        return (self._STARTER_DECK_COL_X[col], self._STARTER_DECK_ROW_Y[row])

    def _starter_deck_picker_open(self) -> bool:
        """True if the Starter Deck Duel deck-grid chooser is currently on
        screen. The Submit Deck button only renders while that chooser is
        open, so its presence is a reliable, deck-independent anchor -- used
        both to verify the chooser opened after a deck-box click, and to
        detect the first-time entry case where MTGA shows this chooser
        directly (see _swap_starter_deck_for_quest and _navigate_starter_deck).
        """
        submit_btn = os.path.join(self._buttons_dir(), "submit_deck.PNG")
        # 0.72 (not 0.80): this is a detection-only probe, not a click target --
        # measured live, the Submit Deck template only scores ~0.78 at this
        # window's actual scale (0.80 made this probe never fire at all,
        # leaving the bot unable to tell it was already on the chooser).
        # Matches the threshold already used for the similarly-purposed
        # STARTER_BANNER anchor above.
        return os.path.exists(submit_btn) and self._locate_image_center_in_scaled_arena_region(
            submit_btn, "STARTER_DECK_CHOOSER_PROBE", rel_region=None, confidence=0.72, timeout=2.0,
        ) is not None

    def _swap_starter_deck_for_quest(self, target_colors: str) -> None:
        """Change the selected starter deck to one matching the quest colors.

        Best-effort: a miss leaves the current deck in place -- we never block the
        queue. Flow: click the current-deck box to open the chooser, click the
        quest deck at its fixed grid position, then Submit Deck. All clicks are by
        coordinate (the fixed alphabetical grid), which is robust to window scale.
        """
        desired_tpl = self._choose_starter_deck_template(target_colors)
        if not desired_tpl:
            return
        desired_name = os.path.splitext(os.path.basename(desired_tpl))[0].upper()
        grid_base = self._starter_deck_grid_point(desired_name)
        if grid_base is None:
            bot_logger.log_error(
                f"Starter: no chooser grid position for deck '{desired_name}'; keeping current deck."
            )
            return

        # 1) Open the deck chooser by clicking the current-deck box (fixed square,
        #    same position regardless of which deck art it currently shows) --
        #    but only if the chooser isn't already open. The very first time this
        #    event is entered (no deck ever picked for it yet) MTGA drops the
        #    player directly onto this full-page deck grid instead of the normal
        #    event-detail page with a small deck box, so clicking the deck-box
        #    coordinate here would either miss or land on an unrelated deck card.
        # The Submit Deck button only renders while the chooser is open, so its
        # presence is a reliable, deck-independent anchor for "already open".
        submit_btn = os.path.join(self._buttons_dir(), "submit_deck.PNG")

        if not self._starter_deck_picker_open():
            box_target, box_src = self._map_abs_point_to_arena(
                self._STARTER_DECK_BOX_BASE, label="STARTER_DECK_BOX"
            )
            bot_logger.log_info(
                f"Starter: opening deck chooser via deck box base={self._STARTER_DECK_BOX_BASE} "
                f"-> {box_target} ({box_src})."
            )
            self._click_abs(box_target[0], box_target[1], "STARTER_DECK_BOX")
            time.sleep(1.3)

            # 1b) Verify the chooser actually opened before clicking the fixed
            # grid position blindly. A miss here means the deck-box click did not
            # land (or something else covers the event page), so we abort and
            # keep the current deck rather than clicking the grid coordinates on
            # whatever screen is actually showing.
            if not self._starter_deck_picker_open():
                bot_logger.log_error(
                    "Starter: deck chooser did not open (Submit Deck not found); keeping current deck."
                )
                return
        else:
            bot_logger.log_info("Starter: deck chooser already open (first-time deck pick); skipping box click.")

        # 2) Select the quest deck at its fixed grid position.
        deck_target, deck_src = self._map_abs_point_to_arena(
            grid_base, label=f"STARTER_DECK_PICK_{desired_name}"
        )
        bot_logger.log_info(
            f"Starter: selecting deck {desired_name} at grid base={grid_base} "
            f"-> {deck_target} ({deck_src})."
        )
        self._click_abs(deck_target[0], deck_target[1], f"STARTER_DECK_PICK_{desired_name}")
        time.sleep(0.8)

        # 3) Confirm via Submit Deck. Prefer the template (exact), fall back to the
        #    fixed button position.
        if os.path.exists(submit_btn) and self._click_image_in_scaled_arena_region(
            submit_btn, "STARTER_SUBMIT_DECK", rel_region=None, confidence=0.80, timeout=1.5,
        ):
            bot_logger.log_info("Starter: submitted deck (template).")
        else:
            sub_target, sub_src = self._map_abs_point_to_arena(
                self._STARTER_SUBMIT_DECK_BASE, label="STARTER_SUBMIT_DECK"
            )
            bot_logger.log_info(
                f"Starter: submitting deck via fixed button base={self._STARTER_SUBMIT_DECK_BASE} "
                f"-> {sub_target} ({sub_src})."
            )
            self._click_abs(sub_target[0], sub_target[1], "STARTER_SUBMIT_DECK")
        time.sleep(1.2)

    def _run_post_login_routine(self, account: dict, all_accounts: list[dict]) -> bool:
        if self._stop_requested:
            return False
        if self._game_mode == "starter":
            return self._run_starter_deck_routine()
        quest = self._select_best_quest()
        forced_filename = None
        if quest:
            if quest.get("type") == "guild":
                guild = quest.get("guild")
                gold = quest.get("gold", 0)
                colors = _GUILD_COLOR_MAP.get(guild or "", "")
                bot_logger.log_info(
                    f"Post-login: selected quest guild={guild} colors={colors} gold={gold}."
                )
            elif quest.get("type") == "forced_file":
                guild = None
                colors = ""
                forced_filename = str(quest.get("file") or "")
                reason = str(quest.get("reason") or "forced_file")
                bot_logger.log_info(
                    f"Post-login: selected quest rule={reason}; forcing deck {forced_filename}."
                )
            else:
                guild = None
                colors = "C"
                bot_logger.log_info("Post-login: selected creature quest; using colors=C.")
        else:
            guild = None
            colors = ""
            bot_logger.log_info("Post-login: no guild quests found; using fallback deck.")

        buttons_dir = self._buttons_dir()
        play_btn = os.path.join(buttons_dir, "play_btn.png")

        bot_logger.log_info("Post-login: navigating Play > Find Match > Historic Play > My Decks.")
        if not self._run_post_login_navigation_oob():
            bot_logger.log_info("Post-login: oob navigation failed, falling back to legacy full-screen image search.")
            find_btn = os.path.join(buttons_dir, "find_match_btn.png")
            play_subtab_btn = os.path.join(buttons_dir, "play_format_tab.png")
            hist_btn = os.path.join(buttons_dir, "hist_play_btn.png")
            decks_btn = os.path.join(buttons_dir, "my_decks.png")
            if not self._click_image_in_scaled_arena_region(
                play_btn,
                "POST_LOGIN_PLAY",
                rel_region=(1160, 680, 740, 360),
                confidence=0.80,
                timeout=1.5,
            ) and not self._click_image(play_btn, "POST_LOGIN_PLAY"):
                return False
            time.sleep(1.0)
            if not self._click_image_in_scaled_arena_region(find_btn, "POST_LOGIN_FIND_MATCH", rel_region=None, confidence=0.80, timeout=1.5) and not self._click_image(find_btn, "POST_LOGIN_FIND_MATCH"):
                return False
            time.sleep(1.0)
            # The Find Match panel has 3 sub-tabs (Ranked / Play / Brawl); MTGA
            # remembers whichever one this account last had open, so "Historic
            # Play" is only visible once "Play" is selected. Best-effort: a miss
            # just means it was already selected, so it is not a failure.
            if self._click_image_in_scaled_arena_region(
                play_subtab_btn, "POST_LOGIN_PLAY_SUBTAB", rel_region=None, confidence=0.75, timeout=1.5
            ):
                bot_logger.log_info("Post-login: Play format sub-tab selected.")
                time.sleep(0.8)
            if not self._click_image_in_scaled_arena_region(hist_btn, "POST_LOGIN_HIST_PLAY", rel_region=None, confidence=0.80, timeout=1.5) and not self._click_image(hist_btn, "POST_LOGIN_HIST_PLAY"):
                return False
            time.sleep(1.0)
            if not self._my_decks_grid_open():
                if not self._click_image_in_scaled_arena_region(decks_btn, "POST_LOGIN_MY_DECKS", rel_region=None, confidence=0.80, timeout=1.5) and not self._click_image(decks_btn, "POST_LOGIN_MY_DECKS"):
                    return False
                time.sleep(1.0)
            else:
                bot_logger.log_info("Post-login: My Decks grid already open; not clicking the header.")

        # Primary attempt uses the planned account folder; if mismatch occurred during login,
        # automatically try other account folders before failing.
        candidate_accounts = [account] + [a for a in all_accounts if a is not account]
        selected_deck = None
        selected_account_name = None
        planned_name = str(account.get("name", "")).strip() or str(account.get("folder", "")).strip()
        any_images_found = False
        for candidate in candidate_accounts:
            candidate_name = str(candidate.get("name", "")).strip() or str(candidate.get("folder", "")).strip()
            deck_image = self._choose_deck_image(candidate, colors, forced_filename)
            if not deck_image:
                continue
            any_images_found = True
            bot_logger.log_info(
                f"Post-login: trying deck image {os.path.basename(deck_image)} from account '{candidate_name}'."
            )
            if self._click_image(deck_image, "POST_LOGIN_DECK"):
                selected_deck = deck_image
                selected_account_name = candidate_name
                break
            bot_logger.log_info(
                f"Post-login: deck image {os.path.basename(deck_image)} from account '{candidate_name}' not found on screen."
            )
        if not selected_deck:
            # Either no account has any deck thumbnail screenshots configured,
            # or none of the configured ones matched what's actually on screen
            # (e.g. a stale screenshot from before a client UI refresh/reskin).
            # Either way, we are on the My Decks grid with nothing usable to
            # match against -- rather than stall forever, just pick whatever
            # deck MTGA lists first for the planned account.
            if any_images_found:
                bot_logger.log_error(
                    "Post-login: no configured deck image matched on screen "
                    "(stale thumbnail screenshots?); selecting the first deck in the list instead."
                )
            if not self._click_first_deck_slot():
                bot_logger.log_error("Post-login: could not confirm the My Decks grid was open; aborting.")
                return False
            selected_deck = "<first deck in list>"
            selected_account_name = planned_name

        if selected_account_name and planned_name and selected_account_name != planned_name:
            bot_logger.log_info(
                f"Post-login: account mismatch detected (planned '{planned_name}', used '{selected_account_name}')."
            )

        time.sleep(1.0)
        if not self._click_image_in_scaled_arena_region(
            play_btn,
            "POST_LOGIN_PLAY_CONFIRM",
            rel_region=(1160, 680, 740, 360),
            confidence=0.80,
            timeout=1.5,
        ) and not self._click_image(play_btn, "POST_LOGIN_PLAY_CONFIRM"):
            return False

        bot_logger.log_info(f"Post-login: deck selected ({os.path.basename(selected_deck)}) and play clicked.")
        return True

    def start_game_from_home_screen(self):
        # Quests-mode switch decision must reflect THIS account's real quest state
        # on landing (done by bot or human), not a stale/empty cache. Before the
        # first queue for each account, read its quests from Home once. If the
        # account already meets the switch criteria, the check below triggers the
        # switch before we ever queue -- and the next account repeats this on its
        # own Home. Self-healing: on failure the absolute count stays unknown and
        # the bot just plays normally. Gated on account_switch_enabled too: this
        # dips to Home and polls for a fresh quests block for up to 8s (see
        # _refresh_quests_from_home), so skip it whenever switching is off,
        # regardless of what mode is configured -- otherwise every bot start
        # pays that ~8s cost for a switch decision that can never fire.
        if (
            self._account_switch_enabled
            and self._account_switch_mode == "quests"
            and not self._home_quest_check_done
            and not self._account_switch_in_progress
            and not self._stop_requested
        ):
            self._home_quest_check_done = True
            try:
                self._refresh_quests_from_home()
            except Exception as e:
                bot_logger.log_error(f"Startup/home quest check error: {e}")
            # Diagnostic: make the switch decision visible in the log on landing.
            completed = self._main_quests_completed_this_session()
            bot_logger.log_info(
                "SWITCH CHECK (account='{}'): enabled={} mode={} | quests completed(session)={} "
                "(active_incomplete={}) need>={} | wins={} need>={} | due={}".format(
                    self._current_account_key(),
                    self._account_switch_enabled,
                    self._account_switch_mode,
                    completed if completed is not None else "unknown",
                    self._last_valid_quest_active_incomplete,
                    self._account_switch_main_quests,
                    self._daily_wins_this_account,
                    self._account_switch_daily_wins,
                    self._account_switch_due(),
                )
            )
        if self._account_switch_in_progress or self._account_switch_due():
            self._account_switch_pending = True
            bot_logger.log_info("Account switch pending; skipping queue click.")
            return
        if self._game_mode == "starter":
            # Every N matches, dip back to Home so MTGA re-fetches quest progress
            # (it is only logged on Home). Bounded + self-healing: on any failure
            # we simply continue the normal re-queue below, so this can never
            # stall the bot. The subsequent navigation naturally resumes from Home.
            if self._matches_since_quest_refresh >= self._quest_refresh_every_n_matches:
                self._matches_since_quest_refresh = 0
                try:
                    self._refresh_quests_from_home()
                except Exception as e:
                    bot_logger.log_error(f"Quest refresh error: {e}")
            # Starter Deck Duel is not re-entered by a single Play click; it needs
            # the full Events > In Progress > banner navigation each time. The
            # navigation is self-limiting when not on the home/Play blade screen.
            self._navigate_starter_deck()
            return
        # Historic mode: the user picks their format and deck themselves in
        # MTGA; the bot just re-queues from Home like Play button, same as a
        # human clicking Play again for the same, already-selected deck.
        current_state = self._get_state_from_log()
        bot_logger.log_info(f"Queue pre-check state={current_state}")
        if current_state == BotState.STORE:
            bot_logger.log_info("Queue pre-check: Store detected, pressing ESC before queue click.")
            try:
                self.input.tap_escape()
                time.sleep(0.6)
            except Exception:
                pass
        target = self.home_play_button_coors
        source = "absolute_click_target"
        arena = self._ensure_arena_region(force_reacquire=False)
        if arena is not None:
            queue_template = os.path.join(self._buttons_dir(), "play_btn.png")
            if os.path.exists(queue_template):
                template_point = self._locate_image_center_in_scaled_arena_region(
                    queue_template,
                    "QUEUE_TEMPLATE_PLAY_BTN",
                    rel_region=(1160, 680, 740, 360),
                    confidence=0.80,
                    timeout=1.0,
                )
                if template_point is not None:
                    target = template_point
                    source = "arena_template_play_btn"
                    bot_logger.log_info(f"Queue template hit: click={target} source={source}")
            try:
                if source == "absolute_click_target":
                    mapped, mapped_source = self._map_abs_point_to_arena(
                        self.home_play_button_coors,
                        label="QUEUE_BUTTON_CONFIG",
                        force_reacquire=False,
                        apply_correction=False,
                    )
                    if mapped_source != "absolute_fallback":
                        target = mapped
                        source = mapped_source
                    else:
                        fallback_rel_x, fallback_rel_y = self._queue_button_rel
                        if 0 <= fallback_rel_x <= 1920 and 0 <= fallback_rel_y <= 1080:
                            target = self._map_base_point_into_arena(arena, (fallback_rel_x, fallback_rel_y))
                            source = "arena_rel_click_target"
            except Exception as e:
                bot_logger.log_error(f"Queue target compute failed; using absolute target. err={e}")
        if arena is None:
            bot_logger.log_info("Queue target: arena_region unavailable, retrying with force_reacquire.")
            arena = self._ensure_arena_region(force_reacquire=True)
            if arena is not None:
                try:
                    mapped, mapped_source = self._map_abs_point_to_arena(
                        self.home_play_button_coors,
                        label="QUEUE_BUTTON_CONFIG",
                        force_reacquire=False,
                        apply_correction=False,
                    )
                    if mapped_source != "absolute_fallback":
                        target = mapped
                        source = mapped_source
                    else:
                        fallback_rel_x, fallback_rel_y = self._queue_button_rel
                        if 0 <= fallback_rel_x <= 1920 and 0 <= fallback_rel_y <= 1080:
                            target = self._map_base_point_into_arena(arena, (fallback_rel_x, fallback_rel_y))
                            source = "arena_rel_click_target"
                except Exception as e:
                    bot_logger.log_error(f"Queue target recompute failed after reacquire. err={e}")
            if arena is None:
                bot_logger.log_error("Queue click ABORTED: arena_region unavailable after retry, refusing absolute desktop click.")
                return
        else:
            bot_logger.log_info(
                "Queue target details: source={} arena={} screen_bounds={} click_target={}".format(
                    source,
                    arena,
                    self.screen_bounds,
                    self.home_play_button_coors,
                )
            )
        bot_logger.log_info("Queue attempt: clicking queue button.")
        bot_logger.log_click(target[0], target[1], "QUEUE_BUTTON")
        runtime_status.touch_input("QUEUE_BUTTON", target)
        self.input.move_abs(target[0], target[1])
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
        runtime_status.set_mode(
            "starting",
            bot_state=str(self._get_state_from_log()),
            my_timer_running=False,
            my_timer_type="",
            my_timer_remaining_sec=None,
            my_timer_elapsed_sec=None,
            my_timer_duration_sec=None,
            my_timer_critical_count=0,
            my_timer_last_critical_at_epoch=0.0,
            my_timer_timeout_seen=False,
            my_timer_timeout_at_epoch=0.0,
        )
        if self._account_play_order:
            bot_logger.log_info(f"Account play order active: {self._account_play_order}")
            bot_logger.log_info(f"Account play order next index: {self._account_cycle_index}")
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
        runtime_status.set_mode("stopped", bot_state=str(self._get_state_from_log()))
        # Prevent any future decisions / restarts from firing after a UI stop.
        if self.__decision_execution_thread is not None:
            try:
                self.__decision_execution_thread.cancel()
            except Exception:
                pass
            self.__decision_execution_thread = None
        self.__decision_delay_key = None
        self.__decision_delay_scheduled_at = 0.0
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

        self.__clear_combat_recovery("Stop requested")
        self.__my_timer_state = {}
        self.__pending_select_n = None
        self.__select_n_in_progress = False
        self.__select_n_in_progress_since = 0.0
        self.__select_n_token_counter += 1

        # Disable any further input actions (timers may still fire briefly).
        self._disable_input()

    def _disable_input(self) -> None:
        """Replace input methods with no-ops to avoid any actions after Stop."""
        if not getattr(self, "input", None):
            return
        def _noop(*_args, **_kwargs):
            return None
        for name in (
            "move_abs",
            "move_rel",
            "left_click",
            "left_down",
            "left_up",
            "tap_enter",
            "tap_shift_enter",
            "tap_tab",
            "tap_delete",
            "type_text",
            "tap_escape",
            "tap_printscreen",
            "tap_win_printscreen",
        ):
            if hasattr(self.input, name):
                try:
                    setattr(self.input, name, _noop)
                except Exception:
                    pass

    def cast(self, card_id: int) -> None:
        # MTGA sometimes emits a card's hover objectId a beat after our scan
        # passes it ("No hover update before bounds"), so a single pass can miss
        # a card that is really in hand. Retry a couple of times after a pause.
        for attempt in range(3):
            if self._stop_requested or self._suppress_selections:
                return
            if self._cast_once(card_id):
                return
            if attempt < 2:
                bot_logger.log_info(
                    f"CAST_RETRY: card {card_id} not hovered on attempt {attempt}; rescanning after pause."
                )
                time.sleep(0.8)

    def _cast_once(self, card_id: int) -> bool:
        bot_logger.set_hover_logging(True)
        try:
            if not self._ensure_options_overlay_closed(context=f"CAST_CARD id={card_id}"):
                return False
            hand_p1, hand_p2 = self._get_hand_scan_points_mapped(force_reacquire=True)
            # Clear any stale hover events from previous scans
            self.log_reader.clear_new_line_flag(self.patterns['hover_id'])

            # Move above start point first to reset any hover states
            reset_pos = (hand_p1[0], hand_p1[1] - 100)
            bot_logger.log_move(
                reset_pos[0],
                reset_pos[1],
                f"RESET_BEFORE_SCAN (target card_id={card_id})",
            )
            self.input.move_abs(reset_pos[0], reset_pos[1])
            time.sleep(0.5)

            # Move to start of hand scan
            bot_logger.log_move(hand_p1[0], hand_p1[1], "START_HAND_SCAN")
            self.input.move_abs(hand_p1[0], hand_p1[1])

            current_hovered_id = None
            start_x = hand_p1[0]
            end_x = hand_p2[0]

            # Ensure we are scanning in the correct direction (left to right usually)
            direction = 1 if end_x > start_x else -1
            total_dx = (end_x - start_x) if end_x != start_x else 1
            start_y = hand_p1[1]
            end_y = hand_p2[1]

            while current_hovered_id != card_id:
                if self._stop_requested or self._suppress_selections or time.time() < self.__group_req_active_until:
                    break
                # Check if we have exceeded the scan area
                current_x = self.input.position().x
                if (direction == 1 and current_x >= end_x) or (direction == -1 and current_x <= end_x):
                    bot_logger.log_error(
                        f"SCAN_FAILED: Card {card_id} not found. Scanned from x={start_x} to x={end_x}, ended at x={current_x}"
                    )
                    current_pos = self.input.position()
                    self._write_hand_select_debug_bundle(
                        reason="cast_scan_failed",
                        card_id=card_id,
                        scan_start=hand_p1,
                        scan_end=hand_p2,
                        current_pos=(current_pos.x, current_pos.y),
                        current_hovered_id=current_hovered_id,
                    )
                    print(f"Scanned entire hand area but did not find card_id: {card_id}")
                    break

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
                    current_pos = self.input.position()
                    self._write_hand_select_debug_bundle(
                        reason="cast_scan_stopped",
                        card_id=card_id,
                        scan_start=hand_p1,
                        scan_end=hand_p2,
                        current_pos=(current_pos.x, current_pos.y),
                        current_hovered_id=current_hovered_id,
                    )
                    break

            clicked = current_hovered_id == card_id
            if clicked:
                click_pos = self.input.position()
                bot_logger.log_click(click_pos.x, click_pos.y, f"CAST_CARD (id={card_id})")
                time.sleep(0.5)
                self.input.left_click(1)
                time.sleep(0.1)
                self.input.left_click(1)
                time.sleep(0.7)

            # Final reset position
            reset_pos = (hand_p1[0], hand_p1[1] - 100)
            bot_logger.log_move(reset_pos[0], reset_pos[1], "RESET_AFTER_CAST")
            self.input.move_abs(reset_pos[0], reset_pos[1])
            return clicked
        finally:
            bot_logger.set_hover_logging(False)

    def all_attack(self) -> bool:
        target, source = self._map_abs_point_to_arena(
            self.main_br_button_coordinates,
            label="ATTACK_ALL",
            force_reacquire=True,
            apply_correction=False,
        )
        bot_logger.log_info(
            f"ATTACK_ALL target: source={source} arena={self._arena_region} raw={self.main_br_button_coordinates} mapped={target}"
        )
        if source == "absolute_no_arena":
            bot_logger.log_error("ATTACK_ALL aborted: arena_region unavailable, refusing absolute desktop click.")
            return False
        bot_logger.log_click(target[0], target[1], "ATTACK_ALL")
        self.input.move_abs(target[0], target[1])
        self.input.left_click(1)
        time.sleep(1)
        self.input.left_click(1)
        self.__last_attack_submit_ts = time.time()
        # Skip the nested target selection while the attack-target flow already
        # runs on another thread: the non-blocking lock would reject it anyway,
        # and entering select_target would consume a freshly re-set flag.
        if self.__attack_target_required and not self.__attack_target_flow_lock.locked():
            time.sleep(0.3)
            self.select_target(-1)
        return True

    def __attack_target_prompt_active(self) -> bool:
        try:
            turn_info = self.updated_game_state.get_turn_info() or {}
        except Exception:
            return False
        if turn_info.get("phase") != "Phase_Combat" or turn_info.get("step") != "Step_DeclareAttack":
            return False
        my_seat = self.__system_seat_id
        if my_seat is None or turn_info.get("decisionPlayer") != my_seat:
            return False
        return True

    def select_target(self, target_id: int) -> None:
        was_attack_target = self.__attack_target_required
        self.__attack_target_required = False
        # A SelectTargetsReq context (e.g. an attack trigger wanting a target
        # during our own declare-attack step) must stay on the spell path even
        # though the declare-attack prompt is technically active.
        spell_target_context = (
            self.__pending_target_select is not None
            or (
                bool(self.__last_target_select_ts)
                and time.time() - self.__last_target_select_ts < 8.0
                # A submit after the select signal means that selection is
                # finished — a dead signal must not suppress the attack flow.
                and self.__last_target_select_ts > float(self.__last_submit_targets_ts or 0.0)
            )
        )
        attack_mode = (
            was_attack_target or self.__attack_target_prompt_active()
        ) and not spell_target_context
        if not attack_mode:
            if was_attack_target:
                # A live spell selection takes precedence right now; restore
                # the flag so the next all_attack/recovery pass runs the
                # attack flow once the spell target is resolved.
                self.__attack_target_required = True
            # Spell-target case: single click on the resolved avatar position;
            # the log-driven schedule handles verification and retries.
            target, source = self._resolve_opponent_avatar_base(force_reacquire=True)
            bot_logger.log_info(
                "SELECT_OPPONENT_AVATAR target: source={} arena={} raw={} mapped={} target_id={}".format(
                    source,
                    self._arena_region,
                    self.opponent_avatar_coors,
                    target,
                    target_id,
                )
            )
            bot_logger.log_click(target[0], target[1], f"SELECT_OPPONENT_AVATAR (target_id={target_id})")
            self.input.move_abs(target[0], target[1])
            time.sleep(0.2)
            self.input.left_click(1)
            time.sleep(0.2)
            return
        if not self.__attack_target_flow_lock.acquire(blocking=False):
            bot_logger.log_info("ATTACK_TARGET flow already running; skipping duplicate invocation.")
            return
        try:
            # Snapshot-and-clear at flow START: a re-sent DeclareAttackersReq
            # arriving mid-flow repopulates the list for the NEXT attempt and
            # must not be wiped by this flow's teardown.
            attacker_ids = list(self.__attack_target_attacker_ids or [])
            self.__attack_target_attacker_ids = []
            self.__run_attack_target_flow(target_id, attacker_ids)
        finally:
            self.__attack_target_flow_lock.release()

    def __run_attack_target_flow(self, target_id: int, attacker_ids: list[int]) -> None:
        # Attack-target case (opponent planeswalker present). Log evidence from
        # a real game: neither the calibrated avatar point nor the avatar grid
        # fan assigned anything — what worked was clicking the ATTACKER on our
        # battlefield and then pressing the attack button. So lead with that
        # recipe; the avatar grid click in between is harmless and covers the
        # case where MTGA shows an explicit recipient chooser.
        deadline = time.time() + 16.0

        def _wait_resolved(seconds: float) -> bool:
            end = time.time() + seconds
            while time.time() < end:
                time.sleep(0.3)
                if self._stop_requested or self._suppress_selections:
                    return True
                if not self.__attack_target_prompt_active():
                    return True
            return False

        def _avatar_grid_click(tag: str) -> None:
            points = self.__get_avatar_retry_points()
            if points:
                x, y, label = points[0]
                self.__click_opponent_avatar_at_screen(x, y, label, tag, fast=True)

        # Hold off combat recovery while this flow owns the mouse — its forced
        # all_attack clicks could land mid-assignment.
        self.__last_attack_submit_ts = time.time()
        if _wait_resolved(0.8):
            return
        attacker_ids = list(attacker_ids or []) or [None]
        for attacker_id in attacker_ids:
            if time.time() > deadline or self._stop_requested or self._suppress_selections:
                break
            self.__last_attack_submit_ts = time.time()
            if attacker_id is not None:
                try:
                    found = self.select_battlefield_permanent(attacker_id, clicks=1)
                except Exception as e:
                    bot_logger.log_error(f"ATTACK_TARGET attacker select failed for {attacker_id}: {e}")
                    found = False
                bot_logger.log_info(
                    f"ATTACK_TARGET attacker-first flow: attacker={attacker_id} found={found}"
                )
                time.sleep(0.3)
            _avatar_grid_click("SELECT_OPPONENT_AVATAR_AFTER_ATTACKER")
            self.__last_attack_submit_ts = time.time()
            if _wait_resolved(0.8):
                return
        # One confirm via the attack button after all recipients are assigned
        # (attack flag is already cleared, so all_attack cannot recurse).
        if self.__attack_target_prompt_active():
            self.all_attack()
            self.__last_attack_submit_ts = time.time()
            if _wait_resolved(1.0):
                return
        # Last resort: fan out over the remaining avatar candidate points.
        points = self.__get_avatar_retry_points()
        attempts = 1  # point 0 was already used by the primary recipe
        while time.time() < deadline:
            if self._stop_requested or self._suppress_selections:
                return
            if not self.__attack_target_prompt_active():
                bot_logger.log_info(
                    f"SELECT_OPPONENT_AVATAR resolved after {attempts} fan retries."
                )
                return
            if attempts >= len(points):
                break
            x, y, label = points[attempts]
            attempts += 1
            self.__last_attack_submit_ts = time.time()
            self.__click_opponent_avatar_at_screen(
                x, y, label, f"SELECT_OPPONENT_AVATAR_RETRY_{attempts}", fast=True
            )
            time.sleep(0.5)
        if self.__attack_target_prompt_active():
            bot_logger.log_info(
                "SELECT_OPPONENT_AVATAR: all attack-target flows exhausted; capturing debug bundle."
            )
            try:
                current_pos = self.input.position()
                self._write_hand_select_debug_bundle(
                    reason="attack_target_flows_exhausted",
                    card_id=target_id,
                    scan_start=(0, 0),
                    scan_end=(0, 0),
                    current_pos=(current_pos.x, current_pos.y),
                    current_hovered_id=None,
                )
            except Exception as e:
                bot_logger.log_error(f"ATTACK_TARGET debug bundle failed: {e}")

    def __cancel_combat_recovery_timer(self) -> None:
        if self.__combat_recovery_timer is None:
            return
        try:
            self.__combat_recovery_timer.cancel()
        except Exception:
            pass
        self.__combat_recovery_timer = None

    def __clear_combat_recovery(self, reason: str | None = None) -> None:
        if reason:
            bot_logger.log_info(f"COMBAT_RECOVERY_CLEAR: {reason}")
        self.__cancel_combat_recovery_timer()
        self.__combat_recovery_key = None
        self.__combat_recovery_attempts = 0
        self.__combat_recovery_deadline_ts = 0.0

    def __clear_pending_select_n_state(self, reason: str) -> bool:
        had_select_n = self.__pending_select_n is not None or self.__select_n_in_progress
        if not had_select_n:
            return False
        self.__pending_select_n = None
        self.__select_n_in_progress = False
        self.__select_n_in_progress_since = 0.0
        bot_logger.log_info(reason)
        bot_logger.log_info("SelectN cleared: decisions may resume.")
        self.__clear_target_wait_if_unblocked()
        return True

    def __purge_selecting_targets_annotations(self) -> None:
        # GRE never sends deletes for PlayerSelectingTargets annotations and
        # the diff merge accumulates them, so without this purge a finished
        # target selection pauses decisions for the rest of the game.
        try:
            removed = self.updated_game_state.remove_annotations_by_type(
                "AnnotationType_PlayerSelectingTargets", self.__system_seat_id
            )
            if removed:
                bot_logger.log_info(
                    f"Purged {removed} stale PlayerSelectingTargets annotation(s) from game state."
                )
        except Exception as e:
            bot_logger.log_error(f"Failed to purge PlayerSelectingTargets annotations: {e}")

    def __clear_pending_target_select_state(self, reason: str) -> bool:
        self.__purge_selecting_targets_annotations()
        had_target_select = self.__pending_target_select is not None
        if not had_target_select:
            return False
        self.__pending_target_select = None
        bot_logger.log_info(reason)
        runtime_status.clear_intentional_wait()
        return True

    def __mark_has_mulled_keep(self, reason: str) -> bool:
        if self.__mulligan_execution_thread is not None:
            try:
                self.__mulligan_execution_thread.cancel()
            except Exception:
                pass
            self.__mulligan_execution_thread = None
        self.__mulligan_decision_armed = False
        if self.__has_mulled_keep:
            return False
        self.__has_mulled_keep = True
        runtime_status.clear_intentional_wait()
        bot_logger.log_info(reason)
        return True

    def __clear_premature_mulligan_keep(self, reason: str) -> bool:
        if not self.__has_mulled_keep:
            return False
        self.__has_mulled_keep = False
        runtime_status.clear_intentional_wait()
        bot_logger.log_info(reason)
        return True

    def __has_local_mulligan_request(self, raw_dict: dict) -> bool:
        try:
            messages = raw_dict.get("greToClientEvent", {}).get("greToClientMessages", [])
            my_seat = self.__system_seat_id
            for message in messages:
                if message.get("type") != "GREMessageType_MulliganReq":
                    continue
                seat_ids = message.get("systemSeatIds") or []
                if my_seat is None or my_seat in seat_ids:
                    return True
        except Exception as e:
            bot_logger.log_error(f"Failed to inspect local mulligan request: {e}")
        return False

    def __has_pending_mulligan_state(self, raw_dict: dict | None = None) -> bool:
        try:
            if raw_dict is not None and self.__has_local_mulligan_request(raw_dict):
                return True
            turn_info = self.updated_game_state.get_turn_info() or {}
            actions = self.updated_game_state.get_actions() or []
            turn_number = turn_info.get("turnNumber")
            phase = turn_info.get("phase")
            step = turn_info.get("step")
            has_live_turn_context = turn_number is not None and (bool(phase) or bool(step))
            has_real_gameplay_actions = any(
                self.__get_action_type(action) in {"ActionType_Play", "ActionType_Cast", "ActionType_Pass"}
                for action in actions
            )
            if has_live_turn_context and has_real_gameplay_actions:
                return False
            my_seat = self.__system_seat_id
            for player in self.updated_game_state.get_players() or []:
                if my_seat is not None and player.get("systemSeatNumber") != my_seat:
                    continue
                pending_type = str(player.get("pendingMessageType") or "")
                if pending_type.startswith("ClientMessageType_Mulligan"):
                    return True
        except Exception as e:
            bot_logger.log_error(f"Failed to inspect pending mulligan state: {e}")
        return False

    def __arm_mulligan_if_needed(self, turn_info_dict: dict | None, raw_dict: dict | None = None) -> bool:
        my_seat = self.__system_seat_id
        if my_seat is None or self.__has_mulled_keep or not turn_info_dict:
            return False
        if turn_info_dict.get("decisionPlayer") != my_seat:
            return False
        if not self.__has_pending_mulligan_state(raw_dict):
            return False
        if self.__mulligan_execution_thread is not None and self.__mulligan_decision_armed:
            runtime_status.set_intentional_wait(float(self.__intro_delay) + 2.0, "mulligan_wait")
            bot_logger.log_info("Mulligan decision already armed; waiting for callback.")
            return True
        if self.__mulligan_execution_thread is not None:
            self.__mulligan_execution_thread.cancel()

        def _mulligan_if_still_mine():
            try:
                self.__mulligan_execution_thread = None
                self.__mulligan_decision_armed = False
                ti = self.updated_game_state.get_turn_info() or {}
                if (
                    ti.get("decisionPlayer") == my_seat
                    and self.__mulligan_decision_callback
                ):
                    runtime_status.clear_intentional_wait()
                    self.__mulligan_decision_callback([])
                else:
                    runtime_status.clear_intentional_wait()
                    bot_logger.log_info(
                        f"Skipping delayed mulligan (decisionPlayer={ti.get('decisionPlayer')}, my_seat={my_seat})"
                    )
            except Exception as e:
                self.__mulligan_execution_thread = None
                self.__mulligan_decision_armed = False
                runtime_status.clear_intentional_wait()
                bot_logger.log_error(f"Error in delayed mulligan callback: {e}")

        self.__mulligan_execution_thread = threading.Timer(self.__intro_delay, _mulligan_if_still_mine)
        self.__mulligan_execution_thread.start()
        self.__mulligan_decision_armed = True
        runtime_status.set_intentional_wait(float(self.__intro_delay) + 2.0, "mulligan_wait")
        bot_logger.log_info("Arming mulligan decision timer.")
        return True

    def __preempt_stack_select_n_for_combat(self, reason: str) -> bool:
        pending = self.__pending_select_n or {}
        if pending.get("mode") != "stack":
            return False
        return self.__clear_pending_select_n_state(reason)

    def __combat_step_ready_for_recovery(self) -> bool:
        turn_info = self.updated_game_state.get_turn_info() or {}
        my_seat = self.__system_seat_id
        if my_seat is None:
            return False
        if turn_info.get("phase") != "Phase_Combat" or turn_info.get("step") != "Step_DeclareAttack":
            return False
        if turn_info.get("decisionPlayer") != my_seat:
            return False
        if self.updated_game_state.get_pending_message_count() > 0:
            return False
        if self.__pending_target_select is not None:
            return False
        if self.__pending_select_n is not None or self.__select_n_in_progress:
            return False
        if self.__should_pause_for_pay_costs():
            return False
        return True

    def __arm_combat_recovery(self, key: str, delay: float = 1.0) -> None:
        if self._stop_requested or self._suppress_selections:
            return
        if key != self.__combat_recovery_key:
            self.__combat_recovery_attempts = 0
        self.__combat_recovery_key = key
        self.__combat_recovery_deadline_ts = time.time() + 6.0
        self.__cancel_combat_recovery_timer()

        def _tick() -> None:
            self.__combat_recovery_timer = None
            if self._stop_requested or self._suppress_selections:
                return
            if self.__combat_recovery_key != key:
                return
            if time.time() > self.__combat_recovery_deadline_ts:
                self.__clear_combat_recovery(f"Combat recovery expired (key={key}).")
                return
            if self.__combat_recovery_attempts >= 2:
                self.__clear_combat_recovery("Combat recovery exhausted attempts.")
                return
            if not self.__combat_step_ready_for_recovery():
                self.__combat_recovery_timer = threading.Timer(0.5, _tick)
                self.__combat_recovery_timer.start()
                return
            if self.__attack_target_flow_lock.locked():
                # The attack-target flow owns the mouse (its battlefield hover
                # scan can exceed the recent-submit window) — defer, and keep
                # the deadline alive so recovery can still act afterwards.
                self.__combat_recovery_deadline_ts = max(
                    self.__combat_recovery_deadline_ts, time.time() + 3.5
                )
                self.__combat_recovery_timer = threading.Timer(0.5, _tick)
                self.__combat_recovery_timer.start()
                return
            if (time.time() - self.__last_attack_submit_ts) < 3.0:
                # A submit just went out (or select_target's retry loop is still
                # clicking and refreshing the timestamp) — defer instead of
                # clearing so recovery can still fire if the prompt turns out to
                # be stuck (e.g. a missed planeswalker attack-target click).
                # Extend the deadline so deferring cannot expire recovery before
                # it had a chance to act; the game-state handler clears it once
                # combat actually advances.
                self.__combat_recovery_deadline_ts = max(
                    self.__combat_recovery_deadline_ts, time.time() + 3.5
                )
                self.__combat_recovery_timer = threading.Timer(0.5, _tick)
                self.__combat_recovery_timer.start()
                return
            attempt = self.__combat_recovery_attempts + 1
            bot_logger.log_info(
                f"COMBAT_RECOVERY_ATTEMPT: {attempt}/2 forcing all_attack (key={key})"
            )
            # Note: no forced submit_selection here. During DeclareAttack the
            # bottom-right button is the attack button itself (all_attack
            # double-clicks it); the submit/okay template search never matches
            # there and its ~15s of image scanning starved the second attempt.
            attack_ok = self.all_attack()
            if not attack_ok:
                bot_logger.log_error(
                    f"COMBAT_RECOVERY_DEFER: no combat click sent because arena_region is unavailable (key={key})."
                )
                self.__combat_recovery_timer = threading.Timer(0.6, _tick)
                self.__combat_recovery_timer.start()
                return
            self.__combat_recovery_attempts = attempt
            if attempt < 2:
                self.__combat_recovery_timer = threading.Timer(1.2, _tick)
                self.__combat_recovery_timer.start()
            else:
                self.__clear_combat_recovery("Combat recovery complete.")

        self.__combat_recovery_timer = threading.Timer(max(0.0, float(delay)), _tick)
        self.__combat_recovery_timer.start()

    def activate_ability(self, card_id: int, ability_id: int) -> None:
        bot_logger.log_info(f"Activating ability: card_id={card_id}, ability_id={ability_id}")
        # Most optional triggers are confirmed via the bottom-right prompt button.
        time.sleep(0.2)
        self.submit_selection(reason="activate_ability", force=True)
    
    def select_hand_card(self, card_id: int, clicks: int = 1) -> bool:
        """Select a card in hand by hovering until objectId matches, then click."""
        bot_logger.set_hover_logging(True)
        try:
            hand_p1, hand_p2 = self._get_hand_scan_points_mapped(force_reacquire=True)
            # Clear any stale hover events from previous scans
            self.log_reader.clear_new_line_flag(self.patterns['hover_id'])

            # Move above start point first to reset any hover states
            reset_pos = (hand_p1[0], hand_p1[1] - 100)
            bot_logger.log_move(reset_pos[0], reset_pos[1], f"RESET_BEFORE_HAND_SELECT (target card_id={card_id})")
            self.input.move_abs(reset_pos[0], reset_pos[1])
            time.sleep(0.3)

            # Move to start of hand scan
            bot_logger.log_move(hand_p1[0], hand_p1[1], "START_HAND_SELECT_SCAN")
            self.input.move_abs(hand_p1[0], hand_p1[1])

            current_hovered_id = None
            start_x = hand_p1[0]
            end_x = hand_p2[0]

            # Ensure we are scanning in the correct direction (left to right usually)
            direction = 1 if end_x > start_x else -1
            total_dx = (end_x - start_x) if end_x != start_x else 1
            start_y = hand_p1[1]
            end_y = hand_p2[1]

            while current_hovered_id != card_id:
                if self._stop_requested or self._suppress_selections or time.time() < self.__group_req_active_until:
                    return False
                current_x = self.input.position().x
                if (direction == 1 and current_x >= end_x) or (direction == -1 and current_x <= end_x):
                    bot_logger.log_error(
                        f"HAND_SELECT_FAILED: Card {card_id} not found. Scanned x={start_x}..{end_x}, end={current_x}"
                    )
                    current_pos = self.input.position()
                    self._write_hand_select_debug_bundle(
                        reason="hand_select_failed",
                        card_id=card_id,
                        scan_start=hand_p1,
                        scan_end=hand_p2,
                        current_pos=(current_pos.x, current_pos.y),
                        current_hovered_id=current_hovered_id,
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
                    current_pos = self.input.position()
                    self._write_hand_select_debug_bundle(
                        reason="hand_select_stopped",
                        card_id=card_id,
                        scan_start=hand_p1,
                        scan_end=hand_p2,
                        current_pos=(current_pos.x, current_pos.y),
                        current_hovered_id=current_hovered_id,
                    )
                    return False

            click_pos = self.input.position()
            bot_logger.log_click(click_pos.x, click_pos.y, f"SELECT_HAND_CARD (id={card_id})")
            for _ in range(max(1, int(clicks))):
                self.input.left_click(1)
                time.sleep(0.1)
            return True
        finally:
            bot_logger.set_hover_logging(False)

    def select_hand_card_offset(self, card_id: int, clicks: int = 1, y_offset: int = -120) -> bool:
        """Select a hand card using a vertical offset scan (useful for SelectN prompts)."""
        bot_logger.set_hover_logging(True)
        try:
            hand_p1, hand_p2 = self._get_hand_scan_points_mapped(force_reacquire=True)
            p1 = (hand_p1[0], hand_p1[1] + y_offset)
            p2 = (hand_p2[0], hand_p2[1] + y_offset)
            if self._arena_region is not None:
                min_y = int(self._arena_region[1])
                max_y = int(self._arena_region[1] + self._arena_region[3])
            else:
                min_y = self.screen_bounds[0][1]
                max_y = self.screen_bounds[1][1]
            p1 = (p1[0], max(min_y, min(max_y, p1[1])))
            p2 = (p2[0], max(min_y, min(max_y, p2[1])))
            return self.__select_object_in_region(
                card_id=card_id,
                p1=p1,
                p2=p2,
                step=self.cast_card_dist,
                clicks=clicks,
                label="HAND_SELECT_FALLBACK",
            )
        finally:
            bot_logger.set_hover_logging(False)

    def select_stack_item(self, card_id: int, clicks: int = 1) -> bool:
        """Select a stack/prompt item by scanning a grid for matching hover objectId."""
        bot_logger.set_hover_logging(True)
        try:
            if self.__select_object_in_region(
                card_id=card_id,
                p1=self.stack_scan_p1,
                p2=self.stack_scan_p2,
                step=self.stack_scan_step,
                clicks=clicks,
                label="STACK_ITEM",
                max_scan_sec=3.0,
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
                max_scan_sec=4.0,
            )
        finally:
            bot_logger.set_hover_logging(False)

    def select_battlefield_permanent(self, card_id: int, clicks: int = 1) -> bool:
        """Select a permanent on our battlefield by scanning the lower arena region for matching hover objectId."""
        bot_logger.set_hover_logging(True)
        scan_p1, scan_p2 = self._get_battlefield_scan_points_mapped(force_reacquire=True)
        try:
            if self.__select_object_in_region(
                card_id=card_id,
                p1=scan_p1,
                p2=scan_p2,
                step=self.battlefield_scan_step,
                clicks=clicks,
                label="BATTLEFIELD_ITEM",
                max_scan_sec=4.0,
            ):
                return True
            current_pos = self.input.position()
            self._write_hand_select_debug_bundle(
                reason="battlefield_select_failed",
                card_id=card_id,
                scan_start=scan_p1,
                scan_end=scan_p2,
                current_pos=(current_pos.x, current_pos.y),
                current_hovered_id=None,
            )
            return False
        finally:
            bot_logger.set_hover_logging(False)

    def _get_opponent_battlefield_scan_points_mapped(
        self,
        *,
        force_reacquire: bool = False,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        p1, _s1 = self._map_abs_point_to_arena(
            self.opponent_battlefield_scan_p1,
            label="OPP_BATTLEFIELD_SCAN_P1",
            force_reacquire=force_reacquire,
            apply_correction=False,
        )
        p2, _s2 = self._map_abs_point_to_arena(
            self.opponent_battlefield_scan_p2,
            label="OPP_BATTLEFIELD_SCAN_P2",
            force_reacquire=False,
            apply_correction=False,
        )
        bot_logger.log_info(
            "OPP_BATTLEFIELD_SCAN mapped: arena={} raw_p1={} raw_p2={} mapped_p1={} mapped_p2={}".format(
                self._arena_region,
                self.opponent_battlefield_scan_p1,
                self.opponent_battlefield_scan_p2,
                p1,
                p2,
            )
        )
        return p1, p2

    def select_opponent_battlefield_permanent(self, card_id: int, clicks: int = 1) -> bool:
        """Select an opponent's permanent by hover-scanning the upper arena band
        for the matching objectId. Mirror of select_battlefield_permanent.

        NOTE: the scan region (opponent_battlefield_scan_p1/p2) is a first
        estimate and needs in-game calibration.
        """
        bot_logger.set_hover_logging(True)
        scan_p1, scan_p2 = self._get_opponent_battlefield_scan_points_mapped(force_reacquire=True)
        try:
            if self.__select_object_in_region(
                card_id=card_id,
                p1=scan_p1,
                p2=scan_p2,
                step=self.battlefield_scan_step,
                clicks=clicks,
                label="OPP_BATTLEFIELD_ITEM",
                max_scan_sec=4.0,
            ):
                return True
            bot_logger.log_error(
                f"Opponent battlefield select failed for card_id={card_id} "
                f"(scan {scan_p1}->{scan_p2}); region may need calibration."
            )
            return False
        finally:
            bot_logger.set_hover_logging(False)

    def __selection_submit_allowed(self) -> bool:
        if self._suppress_selections or self._stop_requested:
            return False
        if self.__pending_select_n:
            ts = self.__pending_select_n.get("ts", 0.0)
            ids = set(self.__pending_select_n.get("ids", []) or [])
            pending_zone = self.updated_game_state.get_zone("ZoneType_Pending")
            pending_ids = set(pending_zone.get("objectInstanceIds", []) or []) if pending_zone else set()
            if pending_ids and ids.intersection(pending_ids):
                return True
            # Keep a short grace window for submit after selection.
            if time.time() - ts < 4.0:
                return True
        if self.__pending_target_select is not None and self.__pending_target_ready_to_submit():
            return True
        return False

    def submit_selection(self, *, reason: str = "unknown", force: bool = False) -> bool:
        if not self.__submit_selection_lock.acquire(blocking=False):
            bot_logger.log_info(f"SubmitSelection skipped (already running). reason={reason}")
            return False
        try:
            if not force and not self.__selection_submit_allowed():
                bot_logger.log_info(f"SubmitSelection skipped (not active). reason={reason}")
                return False
            submit_img = os.path.join(self._buttons_dir(), "submit_btn.png")
            okay_img = os.path.join(self._buttons_dir(), "okay_btn.png")
            if os.path.exists(submit_img):
                if self._click_image_in_scaled_arena_region(
                    submit_img,
                    "SUBMIT_SELECTION_IMG",
                    rel_region=(1320, 720, 600, 320),
                    confidence=0.82,
                    timeout=1.5,
                ) or self._click_image(submit_img, "SUBMIT_SELECTION_IMG", confidence=0.82, timeout=1.5):
                    self.__last_submit_selection_ts = time.time()
                    return True
                # submit_btn not on screen — try okay_btn as fallback (e.g. combat confirm)
                if os.path.exists(okay_img):
                    if self._click_image_in_scaled_arena_region(
                        okay_img,
                        "SUBMIT_OKAY_FALLBACK_IMG",
                        rel_region=(1320, 720, 600, 320),
                        confidence=0.82,
                        timeout=1.5,
                    ) or self._click_image(okay_img, "SUBMIT_OKAY_FALLBACK_IMG", confidence=0.82, timeout=1.5):
                        bot_logger.log_info("SUBMIT_SELECTION: submit_btn not found, clicked okay_btn as fallback")
                        self.__last_submit_selection_ts = time.time()
                        return True
                return False
            target, source = self._map_abs_point_to_arena(
                self.main_br_button_coordinates,
                label="SUBMIT_SELECTION",
                force_reacquire=True,
                apply_correction=False,
            )
            bot_logger.log_info(
                f"SUBMIT_SELECTION target: source={source} arena={self._arena_region} raw={self.main_br_button_coordinates} mapped={target}"
            )
            if source == "absolute_no_arena":
                bot_logger.log_error(
                    f"SUBMIT_SELECTION aborted: arena_region unavailable, refusing absolute desktop click. reason={reason}"
                )
                return False
            bot_logger.log_click(target[0], target[1], "SUBMIT_SELECTION")
            self.input.move_abs(target[0], target[1])
            time.sleep(0.1)
            self.input.left_click(1)
            self.__last_submit_selection_ts = time.time()
            return True
        finally:
            self.__submit_selection_lock.release()

    def resolve(self) -> None:
        if self.__should_pause_for_assign_damage():
            bot_logger.log_info("RESOLVE skipped: assign damage handler is active.")
            return
        turn_info = self.updated_game_state.get_turn_info() or {}
        my_seat = self.__system_seat_id or turn_info.get('decisionPlayer') or 1

        # MTGA's bottom-right "pass/next/resolve/no-blocks" button sometimes shifts vertically during
        # opponent DeclareAttack. Historically we clicked slightly above to compensate, but that can
        # miss depending on UI scale/layout. Use the calibrated button position first, then a small
        # upward fallback only for that specific case.
        base_target, source = self._map_abs_point_to_arena(
            self.main_br_button_coordinates,
            label="RESOLVE",
            force_reacquire=True,
            apply_correction=False,
        )
        positions = [base_target]
        if turn_info.get('step') == 'Step_DeclareAttack' and turn_info.get('activePlayer') != my_seat:
            fallback_y = base_target[1] - 50
            if self._arena_region is not None:
                min_y = int(self._arena_region[1])
            else:
                min_y = self.screen_bounds[0][1]
            positions.append((base_target[0], max(min_y, fallback_y)))

        bot_logger.log_info(
            f"RESOLVE target: source={source} arena={self._arena_region} raw={self.main_br_button_coordinates} positions={positions}"
        )

        for pos in positions:
            bot_logger.log_click(pos[0], pos[1], "RESOLVE")
            runtime_status.touch_input("RESOLVE", pos)
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
        max_scan_sec: float | None = None,
    ) -> bool:
        self.log_reader.clear_new_line_flag(self.patterns['hover_id'])
        x1, y1 = p1
        x2, y2 = p2
        x_min, x_max = (x1, x2) if x1 <= x2 else (x2, x1)
        y_min, y_max = (y1, y2) if y1 <= y2 else (y2, y1)
        step = max(10, int(step))
        start_ts = time.time()

        reset_x = x_min
        reset_y = max(self.screen_bounds[0][1], y_min - 80)
        bot_logger.log_move(reset_x, reset_y, f"RESET_BEFORE_{label} (target card_id={card_id})")
        self.input.move_abs(reset_x, reset_y)
        time.sleep(0.1)

        for y in range(y_min, y_max + 1, step):
            for x in range(x_min, x_max + 1, step):
                if self._stop_requested or self._suppress_selections:
                    bot_logger.log_info(f"{label}_ABORTED: stop/suppress requested")
                    return False
                if max_scan_sec is not None and (time.time() - start_ts) > max_scan_sec:
                    bot_logger.log_error(
                        f"{label}_TIMEOUT: card {card_id} not found within {max_scan_sec:.1f}s"
                    )
                    return False
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
            used_raw = self.mulligan_keep_coors
            target, source = self._map_abs_point_to_arena(
                self.mulligan_keep_coors,
                label="KEEP_HAND_CONFIG",
                force_reacquire=True,
                apply_correction=False,
            )
            arena = self._arena_region
            if arena is not None:
                local_x = int(target[0] - arena[0])
                local_y = int(target[1] - arena[1])
                # Keep button is expected near bottom-center/right, not at extreme bottom-right.
                if not (760 <= local_x <= 1550 and 700 <= local_y <= 980):
                    fallback_target, fallback_source = self._map_abs_point_to_arena(
                        self._default_mulligan_keep_coors,
                        label="KEEP_HAND_DEFAULT",
                        force_reacquire=False,
                        apply_correction=False,
                    )
                    bot_logger.log_error(
                        "KEEP_HAND config appears invalid for mulligan screen: "
                        f"local=({local_x}, {local_y}) raw={self.mulligan_keep_coors}. "
                        f"Using fallback raw={self._default_mulligan_keep_coors} mapped={fallback_target}."
                    )
                    target = fallback_target
                    used_raw = self._default_mulligan_keep_coors
                    source = f"{fallback_source}_fallback_default_keep"
            source = f"{source}_configured_keep"
            bot_logger.log_info(
                f"KEEP_HAND target: source={source} arena={self._arena_region} raw={used_raw} mapped={target}"
            )
            bot_logger.log_click(target[0], target[1], "KEEP_HAND")
            self.input.move_abs(target[0], target[1])
        else:
            target, source = self._map_abs_point_to_arena(
                self.mulligan_mull_coors,
                label="MULLIGAN",
                force_reacquire=True,
                apply_correction=False,
            )
            bot_logger.log_info(
                f"MULLIGAN target: source={source} arena={self._arena_region} raw={self.mulligan_mull_coors} mapped={target}"
            )
            bot_logger.log_click(target[0], target[1], "MULLIGAN")
            self.input.move_abs(target[0], target[1])
        self.input.left_click(1)
        time.sleep(0.08)
        try:
            self._write_keep_click_debug_bundle(
                decision="KEEP_HAND" if keep else "MULLIGAN",
                raw_point=self.mulligan_keep_coors if keep else self.mulligan_mull_coors,
                mapped_point=target,
                source=source,
            )
        except Exception as e:
            bot_logger.log_error(f"Failed to write mulligan click debug bundle: {e}")

    def click_assign_damage_done(self):
        """Click the Done button during damage assignment"""
        runtime_status.set_mode("in_game", bot_state=str(self._get_state_from_log()))
        button_img = os.path.join(self._buttons_dir(), "assign_damage_done.png")
        try:
            if not self._is_assign_damage_step_active():
                bot_logger.log_info("ASSIGN_DAMAGE_DONE aborted: combat damage step no longer active.")
                return
            arena = self._ensure_arena_region(force_reacquire=True)
            template_point = None
            if arena is not None:
                # The Assign Damage Done button lives near the lower arena center; search there first
                # because saved click targets can still be stale legacy desktop coordinates.
                template_region = self._scale_base_region_to_arena(arena, (480, 700, 960, 280))
                if os.path.exists(button_img):
                    template_point = self._locate_image_center(
                        button_img,
                        "ASSIGN_DAMAGE_DONE_IMG_LOCATE",
                        confidence=0.82,
                        timeout=1.0,
                        region=template_region,
                    )
                    if template_point is None:
                        template_point = self._locate_image_center_in_rescaled_region(
                            button_img,
                            "ASSIGN_DAMAGE_DONE_IMG_RESCALED",
                            region=template_region,
                            normalized_size=(960, 280),
                            confidence=0.82,
                            timeout=1.2,
                        )
                    if template_point is not None:
                        bot_logger.log_info(
                            f"ASSIGN_DAMAGE_DONE template located at {template_point} in region={template_region}."
                        )
                        for attempt in range(1, 4):
                            self._click_abs(template_point[0], template_point[1], f"ASSIGN_DAMAGE_DONE_IMG_{attempt}")
                            time.sleep(0.45)
                            if not self._is_assign_damage_step_active():
                                bot_logger.log_info(
                                    f"ASSIGN_DAMAGE_DONE completed after template attempt {attempt}."
                                )
                                return
            target, source = self._map_abs_point_to_arena(
                self.assign_damage_done_coors,
                label="ASSIGN_DAMAGE_DONE",
                force_reacquire=True,
                apply_correction=False,
            )
            if source == "arena_relative_1920_direct":
                local_x = int(target[0] - arena[0]) if arena is not None else int(self.assign_damage_done_coors[0])
                local_y = int(target[1] - arena[1]) if arena is not None else int(self.assign_damage_done_coors[1])
                plausible = 720 <= local_x <= 1220 and 760 <= local_y <= 980
                if not plausible:
                    bot_logger.log_error(
                        "ASSIGN_DAMAGE_DONE config appears implausible for the lower-center done button: "
                        f"local=({local_x}, {local_y}) raw={self.assign_damage_done_coors}. "
                        "Skipping stale coordinate fallback."
                    )
                    self._write_assign_damage_debug_bundle(
                        reason="assign_damage_stale_config",
                        mapped_point=target,
                        source=f"{source}_implausible",
                    )
                    return
            bot_logger.log_info(
                f"ASSIGN_DAMAGE_DONE target: source={source} arena={self._arena_region} raw={self.assign_damage_done_coors} mapped={target}"
            )
            if source == "absolute_no_arena":
                bot_logger.log_error(
                    "ASSIGN_DAMAGE_DONE aborted: arena_region unavailable, refusing absolute desktop click."
                )
                self._write_assign_damage_debug_bundle(
                    reason="assign_damage_no_arena",
                    mapped_point=target,
                    source=source,
                )
                return
            for attempt in range(1, 4):
                self._click_abs(target[0], target[1], f"ASSIGN_DAMAGE_DONE_{attempt}")
                time.sleep(0.45)
                if not self._is_assign_damage_step_active():
                    bot_logger.log_info(
                        f"ASSIGN_DAMAGE_DONE completed after attempt {attempt}."
                    )
                    return
            bot_logger.log_error(
                "ASSIGN_DAMAGE_DONE still active after 3 low-level clicks; writing debug bundle."
            )
            self._write_assign_damage_debug_bundle(
                reason="assign_damage_click_not_accepted",
                mapped_point=target,
                source=source,
            )
        finally:
            self.__clear_assign_damage_state("assign damage click routine finished")

    def _is_assign_damage_step_active(self) -> bool:
        try:
            turn_info = self.updated_game_state.get_turn_info() or {}
            return str(turn_info.get("step") or "") == "Step_CombatDamage"
        except Exception:
            return False

    def __should_pause_for_assign_damage(self) -> bool:
        return bool(self.__assign_damage_in_progress and self._is_assign_damage_step_active())

    def __clear_assign_damage_state(self, reason: str) -> None:
        if self.__assign_damage_execution_thread is not None:
            try:
                self.__assign_damage_execution_thread.cancel()
            except Exception:
                pass
            self.__assign_damage_execution_thread = None
        if self.__assign_damage_in_progress:
            bot_logger.log_info(f"ASSIGN_DAMAGE_CLEAR: {reason}")
        self.__assign_damage_in_progress = False

    def __run_assign_damage_done(self) -> None:
        self.__assign_damage_execution_thread = None
        try:
            self.click_assign_damage_done()
        except Exception as e:
            bot_logger.log_error(f"ASSIGN_DAMAGE_DONE worker failed: {e}")
            self.__clear_assign_damage_state("assign damage worker exception")

    def _write_assign_damage_debug_bundle(
        self,
        *,
        reason: str,
        mapped_point: tuple[int, int],
        source: str,
    ) -> None:
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            debug_dir = Path(bot_logger.ensure_debug_dir(f"assign-damage-{stamp}"))
            turn_info = self.updated_game_state.get_turn_info() or {}
            payload = {
                "reason": reason,
                "source": source,
                "raw_point": list(self.assign_damage_done_coors),
                "mapped_point": [int(mapped_point[0]), int(mapped_point[1])],
                "arena_region": list(self._arena_region) if self._arena_region is not None else None,
                "cached_arena_region": list(self._last_good_arena_region) if self._last_good_arena_region is not None else None,
                "turn_info": turn_info,
                "bot_state": str(self._get_state_from_log()),
            }
            with (debug_dir / "assign_damage_state.json").open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)

            try:
                with open(bot_logger.BOT_LOG_FILE, "r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    start = max(0, size - 120000)
                    handle.seek(start, os.SEEK_SET)
                    tail = handle.read()
                with (debug_dir / "log_tail.txt").open("w", encoding="utf-8") as handle:
                    handle.write(tail)
            except Exception:
                pass

            if self._vision is not None:
                full = self._vision.capture(None)
                self._vision.save_image(full, str(debug_dir / "full_screen.png"))
                if self._arena_region is not None:
                    arena_img = self._vision.capture(self._arena_region)
                    self._vision.save_image(arena_img, str(debug_dir / "arena_region.png"))
                focus_region = (
                    max(self.screen_bounds[0][0], int(mapped_point[0]) - 220),
                    max(self.screen_bounds[0][1], int(mapped_point[1]) - 160),
                    440,
                    320,
                )
                focus_img = self._vision.capture(focus_region)
                self._vision.save_image(focus_img, str(debug_dir / "assign_damage_focus.png"))
        except Exception as exc:
            bot_logger.log_error(f"Failed to write assign-damage debug bundle: {exc}")

    def __handle_inactivity_timeout(self):
        """Handle timeout when no activity for 3 minutes - click next button repeatedly"""
        if self._supervisor_active:
            bot_logger.log_error("TIMEOUT: legacy inactivity resolve disabled while supervisor mode is active.")
            runtime_status.set_recovery_reason("controller_inactivity_timeout")
            runtime_status.set_mode("stuck_suspected", bot_state=str(self._get_state_from_log()))
            self.__inactivity_timer = None
            return
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
        if self._supervisor_active:
            runtime_status.touch_decision()
            return
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

    def __get_running_inactivity_timer_remaining(self) -> float | None:
        remaining_values: list[float] = []
        for timer_state in self.__my_timer_state.values():
            if not bool(timer_state.get("running", False)):
                continue
            if str(timer_state.get("type") or "") != "TimerType_Inactivity":
                continue
            remaining = timer_state.get("remaining_sec")
            try:
                if remaining is not None:
                    remaining_values.append(float(remaining))
            except Exception:
                continue
        if not remaining_values:
            return None
        return min(remaining_values)

    def __should_allow_emergency_concede_now(self) -> tuple[bool, str]:
        turn_info = self.updated_game_state.get_turn_info() or {}
        my_seat = self.__system_seat_id or turn_info.get("decisionPlayer")
        if my_seat is None:
            return False, "local seat unknown"
        if self.__pending_target_select is not None:
            return True, "pending target selection"
        if self.__pending_select_n is not None or self.__select_n_in_progress:
            return True, "pending select-n"
        if self.__should_pause_for_pay_costs():
            return True, "pending pay costs"
        if self.__should_pause_for_assign_damage():
            return True, "assign damage active"
        if turn_info.get("decisionPlayer") == my_seat:
            return True, "local decision player"
        return False, (
            "decisionPlayer={} activePlayer={} priorityPlayer={} mySeat={}".format(
                turn_info.get("decisionPlayer"),
                turn_info.get("activePlayer"),
                turn_info.get("priorityPlayer"),
                my_seat,
            )
        )

    def __schedule_emergency_concede(self, timer_id: int, remaining_sec: float, delay: float) -> None:
        if self.__emergency_concede_timer is not None:
            self.__emergency_concede_timer.cancel()
        self.__emergency_concede_in_progress = False
        bot_logger.log_info(
            f"EMERGENCY_CONCEDE_SCHEDULED: timerId={timer_id} remaining={remaining_sec:.1f}s "
            f"will fire in {delay:.1f}s (at ~{self.__emergency_concede_threshold_sec:.0f}s left)"
        )
        self.__emergency_concede_scheduled_at = time.time()
        self.__emergency_concede_timer = threading.Timer(delay, self.__attempt_emergency_concede)
        self.__emergency_concede_timer.daemon = True
        self.__emergency_concede_timer.start()

    def __cancel_emergency_concede_timer(self, reason: str) -> None:
        if self.__emergency_concede_timer is not None:
            self.__emergency_concede_timer.cancel()
            self.__emergency_concede_timer = None
            bot_logger.log_info(f"EMERGENCY_CONCEDE_CANCELLED: {reason}")
        self.__emergency_concede_in_progress = False

    def __attempt_emergency_concede(self) -> None:
        """Safety-net concede when inactivity timer is critically low and supervisor is absent."""
        self.__emergency_concede_timer = None
        if self._stop_requested:
            return
        live_remaining = self.__get_running_inactivity_timer_remaining()
        if live_remaining is None:
            bot_logger.log_info("EMERGENCY_CONCEDE: cancelled — no running local inactivity timer.")
            return
        if live_remaining > (self.__emergency_concede_threshold_sec + 3.0):
            bot_logger.log_info(
                "EMERGENCY_CONCEDE: cancelled — inactivity timer is no longer critical "
                f"(remaining={live_remaining:.1f}s)."
            )
            return
        allowed_now, reason = self.__should_allow_emergency_concede_now()
        if not allowed_now:
            bot_logger.log_info(
                "EMERGENCY_CONCEDE: deferred — local input not currently required ({})".format(reason)
            )
            self.__emergency_concede_timer = threading.Timer(5.0, self.__attempt_emergency_concede)
            self.__emergency_concede_timer.daemon = True
            self.__emergency_concede_timer.start()
            return
        # Cancel if bot was active at any point after the concede was scheduled — it's clearly not stuck.
        # Only fire if the bot has been continuously idle since scheduling.
        try:
            status = runtime_status.read_status()
            last_decision = float(status.get("last_decision_at_epoch") or 0.0)
            last_input = float(status.get("last_input_at_epoch") or 0.0)
            last_activity = max(last_decision, last_input)
            idle_secs = time.time() - last_activity
            if last_activity > self.__emergency_concede_scheduled_at and idle_secs < 15.0:
                # Bot acted after the concede was scheduled AND is still recently active → alive and playing.
                bot_logger.log_info(
                    f"EMERGENCY_CONCEDE: cancelled — bot was active after scheduling "
                    f"(activity {idle_secs:.1f}s ago, idle={idle_secs:.1f}s)"
                )
                return
            if idle_secs < 8.0:
                bot_logger.log_info(
                    f"EMERGENCY_CONCEDE: deferred — bot recently active (idle={idle_secs:.1f}s), retrying in 5s"
                )
                self.__emergency_concede_timer = threading.Timer(5.0, self.__attempt_emergency_concede)
                self.__emergency_concede_timer.daemon = True
                self.__emergency_concede_timer.start()
                return
        except Exception:
            pass
        try:
            self.__emergency_concede_in_progress = True
            bot_logger.log_info("EMERGENCY_CONCEDE: starting ESC+concede sequence")
            runtime_status.set_mode("stuck_suspected", bot_state=str(self._get_state_from_log()))
            if focus_mtga_window():
                time.sleep(0.3)
            self.input.tap_escape()
            time.sleep(0.8)
            concede_raw = self._loaded_click_targets.get("concede", {})
            concede_xy = (int(concede_raw.get("x", 1714)), int(concede_raw.get("y", 814)))
            target, source = self._map_abs_point_to_arena(
                concede_xy,
                label="EMERGENCY_CONCEDE_BTN",
                force_reacquire=True,
                apply_correction=False,
            )
            if source == "absolute_no_arena":
                bot_logger.log_error("EMERGENCY_CONCEDE: arena_region unavailable, skipping click")
                return
            bot_logger.log_info(f"EMERGENCY_CONCEDE: clicking concede at {target} (source={source})")
            runtime_status.touch_input("EMERGENCY_CONCEDE", target)
            self.__click_concede_and_confirm(target, label="EMERGENCY_CONCEDE")
        except Exception as exc:
            bot_logger.log_error(f"EMERGENCY_CONCEDE: exception: {exc}")
        finally:
            self.__emergency_concede_in_progress = False

    def __force_concede(self) -> None:
        """Unconditional concede — called when ActivePlayer timer expires. No idle guard."""
        if self._stop_requested:
            return
        try:
            bot_logger.log_info("FORCE_CONCEDE: starting ESC+concede sequence (ActivePlayer timer expired)")
            runtime_status.set_mode("stuck_suspected", bot_state=str(self._get_state_from_log()))
            if focus_mtga_window():
                time.sleep(0.3)
            self.input.tap_escape()
            time.sleep(0.8)
            concede_raw = self._loaded_click_targets.get("concede", {})
            concede_xy = (int(concede_raw.get("x", 962)), int(concede_raw.get("y", 631)))
            target, source = self._map_abs_point_to_arena(
                concede_xy,
                label="FORCE_CONCEDE_BTN",
                force_reacquire=True,
                apply_correction=False,
            )
            if source == "absolute_no_arena":
                bot_logger.log_error("FORCE_CONCEDE: arena_region unavailable, skipping click")
                return
            bot_logger.log_info(f"FORCE_CONCEDE: clicking concede at {target} (source={source})")
            runtime_status.touch_input("FORCE_CONCEDE", target)
            self.__click_concede_and_confirm(target, label="FORCE_CONCEDE")
        except Exception as exc:
            bot_logger.log_error(f"FORCE_CONCEDE: exception: {exc}")

    def __click_concede_and_confirm(self, concede_target: tuple, label: str) -> None:
        """Click the Concede button then click the OK confirmation dialog."""
        concede_img = os.path.join(self._buttons_dir(), "concede.png")
        clicked_concede = False
        if os.path.exists(concede_img):
            clicked_concede = self._click_image_in_scaled_arena_region(
                concede_img,
                f"{label}_CONCEDE_IMG",
                rel_region=(640, 500, 640, 220),
                confidence=0.80,
                timeout=1.5,
            )
        if not clicked_concede:
            self.input.move_abs(concede_target[0], concede_target[1])
            time.sleep(0.1)
            self.input.left_click(1)
        time.sleep(1.5)
        okay_img = os.path.join(self._buttons_dir(), "okay_btn.png")
        if os.path.exists(okay_img):
            if self._click_image_in_scaled_arena_region(
                okay_img,
                f"{label}_OKAY_IMG",
                rel_region=(700, 430, 520, 260),
                confidence=0.82,
                timeout=1.5,
            ):
                return
        # Click OK/confirm dialog — falls back to arena center if template search misses
        arena = self._arena_region
        if arena is not None:
            ok_x, ok_y = self._map_base_point_into_arena(arena, (960, 540))
            bot_logger.log_info(f"{label}: clicking confirm OK at ({ok_x}, {ok_y})")
            self.input.move_abs(ok_x, ok_y)
            time.sleep(0.1)
            self.input.left_click(1)

    def dismiss_end_screen(self):
        """Click to dismiss match end screen and return to main menu"""
        if self._stop_requested:
            bot_logger.log_info("Dismiss end screen skipped: stop requested.")
            return
        if focus_mtga_window():
            bot_logger.log_info("Dismiss end screen: focused MTGA window before click.")
            time.sleep(0.25)
        runtime_status.set_mode("post_match", bot_state=str(self._get_state_from_log()))
        self._suppress_selections = False
        arena = self._get_ui_action_arena_region(force_reacquire=True, label="DISMISS_END_SCREEN")
        if arena is not None:
            center_x = int(arena[0] + (arena[2] // 2))
            center_y = int(arena[1] + (arena[3] // 2))
            # The DEFEAT/VICTORY "[Click to Continue]" prompt sits near the bottom
            # of the board, not the middle. Clicking only the arena center lands on
            # the DEFEAT crest and does NOT advance the screen, leaving the bot stuck
            # on the match-end screen forever (no supervisor to recover it). Click
            # the continue prompt too.
            continue_x = center_x
            continue_y = int(arena[1] + (arena[3] * 0.93))
            source = f"arena_center arena={arena}"
        else:
            center_x = (self.screen_bounds[0][0] + self.screen_bounds[1][0]) // 2
            center_y = (self.screen_bounds[0][1] + self.screen_bounds[1][1]) // 2
            continue_x = center_x
            continue_y = int(self.screen_bounds[0][1] + (self.screen_bounds[1][1] - self.screen_bounds[0][1]) * 0.93)
            source = f"screen_bounds_center screen_bounds={self.screen_bounds}"
        bot_logger.log_info(f"Dismiss end screen: clicking {source} center=({center_x}, {center_y}) continue=({continue_x}, {continue_y})")
        bot_logger.log_click(center_x, center_y, "DISMISS_END_SCREEN")
        runtime_status.touch_input("DISMISS_END_SCREEN", (center_x, center_y))
        # Click to advance past the result screen, then VERIFY we actually left it
        # before handing off to the queue loop. A single center click lands on the
        # DEFEAT crest and does not always advance; a late-rendering result screen
        # also swallows the first click. Without a verified dismissal the queue loop
        # spins forever navigating a screen that still shows the result (this
        # stalled farming until an external clicker intervened). Retry the
        # continue-prompt + center clicks until the top-left nav anchor reappears --
        # language-independent, since that anchor is absent on the full-screen
        # result and present on every normal Arena screen.
        advanced = False
        for attempt in range(1, 7):
            if self._stop_requested:
                break
            for (tx, ty) in ((continue_x, continue_y), (center_x, center_y)):
                self.input.move_abs(tx, ty)
                time.sleep(0.25)
                self.input.left_click(1)
                time.sleep(0.4)
            time.sleep(1.1)
            if self._match_end_screen_cleared():
                advanced = True
                bot_logger.log_info(f"Match completed - dismissed end screen (confirmed after {attempt} attempt(s))")
                break
            bot_logger.log_info(f"Dismiss end screen: result screen still up after attempt {attempt}; retrying continue-click.")
        if not advanced:
            bot_logger.log_info("Match completed - dismissed end screen (unconfirmed; queue loop safety net will keep retrying)")
        self._match_end_dismissed = True
        self._matches_since_quest_refresh += 1
        # Count a daily win once per match (the bot knows the result). Used by
        # quest-mode account switching.
        if self.__last_match_won is True and not self._win_counted_this_match:
            self._daily_wins_this_account += 1
            self._win_counted_this_match = True
            bot_logger.log_info(
                f"Daily win counted: {self._daily_wins_this_account} win(s) this account."
            )
            # Estimated gold from the daily-win reward track (the exact amount is
            # not in the log). Configurable via gold_per_win; 0 disables it.
            self._credit_gold(self._gold_per_win, reason="match won")
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

    def _match_end_screen_cleared(self) -> bool:
        """True once we are back on a recognizable Arena screen after a match.

        The full-screen DEFEAT/VICTORY result hides the top-left navigation
        anchor, so a positive anchor match (detect().ok) means the result screen
        is gone and normal navigation can resume. Language-independent -- it keys
        on UI anchors, not result text. Falls back to the player-log HOME state so
        a detector hiccup during a transition still lets the bot proceed."""
        try:
            det = self._arena_region_provider.detect(write_debug_on_fail=False)
            if det is not None and det.ok:
                return True
        except Exception:
            pass
        try:
            if self._get_state_from_log() == BotState.HOME:
                return True
        except Exception:
            pass
        return False

    def reset_for_new_game(self):
        """Reset controller state for a new game - complete fresh start"""
        bot_logger.log_info("Resetting controller state for new game")
        self.__has_mulled_keep = False
        self.__system_seat_id = None
        self.__last_match_won = None
        self._win_counted_this_match = False
        self.__last_seen_match_id = None
        self.__attack_target_required = False
        self.__attack_target_attacker_ids = []
        self._suppress_selections = False
        self.updated_game_state = GameState()
        self.__inst_id_grp_id_dict = {}
        self.__pending_select_n = None
        self.__select_n_in_progress = False
        self.__select_n_in_progress_since = 0.0
        self.__select_n_token_counter += 1
        self.__clear_combat_recovery("Reset for new game")
        self.__last_attack_submit_ts = 0.0
        self.__my_timer_state = {}
        self.__cancel_emergency_concede_timer("new game / reset")
        # Cancel any pending decision timers
        if self.__decision_execution_thread is not None:
            self.__decision_execution_thread.cancel()
            self.__decision_execution_thread = None
        self.__decision_delay_key = None
        self.__decision_delay_scheduled_at = 0.0
        if self.__mulligan_execution_thread is not None:
            self.__mulligan_execution_thread.cancel()
            self.__mulligan_execution_thread = None
        self.__mulligan_decision_armed = False
        # Cancel inactivity timer
        self.stop_inactivity_timer()
        # Reset all cached log data for fresh start
        self.log_reader.reset_all_patterns()
        bot_logger.log_info("Controller state reset complete")

    def __reset_live_game_state(self, reason: str, *, preserve_system_seat_id: int | None = None) -> None:
        preserved_seat = preserve_system_seat_id if preserve_system_seat_id is not None else self.__system_seat_id
        self.__has_mulled_keep = False
        self.__last_match_won = None
        self.__attack_target_required = False
        self.__attack_target_attacker_ids = []
        self._suppress_selections = False
        self.updated_game_state = GameState()
        self.__inst_id_grp_id_dict = {}
        self.__pending_target_select = None
        self.__last_target_select_source_id = None
        self.__last_target_select_ts = 0.0
        self.__last_submit_targets_ts = 0.0
        self.__pending_select_n = None
        self.__select_n_in_progress = False
        self.__select_n_in_progress_since = 0.0
        self.__select_n_token_counter += 1
        self.__pending_pay_costs_ts = 0.0
        self.__clear_combat_recovery(reason)
        self.__last_attack_submit_ts = 0.0
        self.__my_timer_state = {}
        if self.__decision_execution_thread is not None:
            self.__decision_execution_thread.cancel()
            self.__decision_execution_thread = None
        self.__decision_delay_key = None
        self.__decision_delay_scheduled_at = 0.0
        if self.__mulligan_execution_thread is not None:
            self.__mulligan_execution_thread.cancel()
            self.__mulligan_execution_thread = None
        self.__mulligan_decision_armed = False
        self.stop_inactivity_timer()
        self.__system_seat_id = preserved_seat
        runtime_status.update_status(
            turn_info={},
            local_system_seat_id=preserved_seat,
            my_timer_running=False,
            my_timer_type="",
            my_timer_remaining_sec=None,
            my_timer_elapsed_sec=None,
            my_timer_duration_sec=None,
        )
        bot_logger.log_info(reason)

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

    def __log_match_summary(self, line: str) -> None:
        match_id = None
        try:
            start = line.find("{")
            if start != -1:
                payload = json.loads(line[start:])
                match_info = payload.get("matchGameRoomStateChangedEvent", {}).get("gameRoomInfo", {})
                match_id = match_info.get("gameRoomConfig", {}).get("matchId")
        except Exception:
            match_id = None

        result = "unknown"
        if self.__last_match_won is True:
            result = "win"
        elif self.__last_match_won is False:
            result = "loss"

        turn_info = self.updated_game_state.get_turn_info() or {}
        turn = turn_info.get("turnNumber")
        phase = turn_info.get("phase")
        step = turn_info.get("step")

        my_seat = self.__system_seat_id
        my_life = None
        opp_life = None
        try:
            players = self.updated_game_state.get_players() or []
            if my_seat is not None:
                for player in players:
                    if player.get("systemSeatNumber") == my_seat:
                        my_life = player.get("lifeTotal")
                    elif opp_life is None:
                        opp_life = player.get("lifeTotal")
            elif players:
                my_life = players[0].get("lifeTotal")
                if len(players) > 1:
                    opp_life = players[1].get("lifeTotal")
        except Exception:
            pass

        bot_logger.log_info(
            "Match summary: matchId={}, result={}, turn={}, phase={}, step={}, life_me={}, life_opp={}".format(
                match_id or "unknown",
                result,
                turn if turn is not None else "unknown",
                phase or "unknown",
                step or "unknown",
                my_life if my_life is not None else "unknown",
                opp_life if opp_life is not None else "unknown",
            )
        )

    def __log_callback(self, pattern: str, line_containing_pattern: str):
        self._state_tracker.push_line(line_containing_pattern)
        current_state = self._get_state_from_log()
        runtime_status.touch_playerlog_event(state=str(current_state))
        if pattern == self.patterns["game_state"]:
            self.__update_game_state(json.loads(line_containing_pattern))
            if self._queue_spam_thread and self._queue_spam_thread.is_alive():
                self._stop_queue_spam = True
            if self._queue_spam_thread and self._queue_spam_thread.is_alive():
                self._stop_queue_spam = True
        elif pattern == self.patterns["timer_state"]:
            self.__update_game_state(json.loads(line_containing_pattern))
        elif pattern == self.patterns["match_completed"]:
            bot_logger.log_info("Detected match completed event")
            runtime_status.set_mode(
                "post_match",
                bot_state=str(current_state),
                my_timer_running=False,
                my_timer_type="",
                my_timer_remaining_sec=None,
                my_timer_elapsed_sec=None,
                my_timer_duration_sec=None,
            )
            self._suppress_selections = True
            self.__pending_select_n = None
            self.__select_n_in_progress = False
            self.__select_n_in_progress_since = 0.0
            self.__select_n_token_counter += 1
            self.__pending_target_select = None
            self.__my_timer_state = {}
            remaining = self.get_account_switch_remaining_sec()
            if self._account_switch_interval > 0:
                bot_logger.log_info(f"Account switch ETA: {remaining}s remaining.")
            outcome = self.__infer_match_won(line_containing_pattern)
            if outcome is not None:
                self.__last_match_won = outcome
            self.__log_match_summary(line_containing_pattern)
            # A match was played -> reset the anti-storm switch guard.
            self._switches_without_match = 0
            self._match_end_dismissed = False
            self._post_match_ready_ts = None
            # Wait a moment for end screen to fully appear, then dismiss it
            threading.Timer(6.0, self.dismiss_end_screen).start()
            if self._account_switch_due():
                self._account_switch_pending = True
        elif pattern == self.patterns["queue_ready_marker"]:
            self._set_runtime_home_mode("queue_ready")
            self._handle_queue_ready()
        elif pattern == self.patterns["main_nav_loaded"]:
            if self._account_switch_in_progress:
                self._set_runtime_home_mode("account_switch")
            else:
                self._set_runtime_home_mode("home_ready")
            self._handle_main_nav_loaded()
        elif pattern == self.patterns["assign_damage"]:
            # Wait a small delay to ensure UI is ready
            runtime_status.set_mode("in_game", bot_state=str(current_state))
            if not self.__assign_damage_in_progress:
                self.__assign_damage_in_progress = True
                self.__assign_damage_execution_thread = threading.Timer(1.0, self.__run_assign_damage_done)
                self.__assign_damage_execution_thread.start()
                bot_logger.log_info("ASSIGN_DAMAGE_ARMED: scheduled assign damage done handler")
            else:
                bot_logger.log_info("ASSIGN_DAMAGE_ARMED: duplicate AssignDamageReq ignored while handler is active")
        elif pattern == self.patterns["declare_attackers"]:
            runtime_status.set_mode("in_game", bot_state=str(current_state))
            self.__handle_declare_attackers_req(line_containing_pattern)
        elif pattern == self.patterns["declare_blockers"]:
            runtime_status.set_mode("in_game", bot_state=str(current_state))
            self.__handle_declare_blockers_req(line_containing_pattern)
        elif pattern == self.patterns["select_n"]:
            runtime_status.set_mode("in_game", bot_state=str(current_state))
            self.__handle_select_n_req(line_containing_pattern)
        elif pattern == self.patterns["group_req"]:
            runtime_status.set_mode("in_game", bot_state=str(current_state))
            self.__handle_group_req(line_containing_pattern)
        elif pattern == self.patterns["select_targets"]:
            runtime_status.set_mode("in_game", bot_state=str(current_state))
            self.__handle_select_targets_req(line_containing_pattern)
        elif pattern == self.patterns["pay_costs"]:
            runtime_status.set_mode("in_game", bot_state=str(current_state))
            self.__pending_pay_costs_ts = time.time()
            bot_logger.log_info("PayCostsReq detected: attempting auto-pay.")
            self.__handle_pay_costs_req(line_containing_pattern)
        elif pattern == self.patterns["casting_time_options"]:
            runtime_status.set_mode("in_game", bot_state=str(current_state))
            self.__handle_casting_time_options_req(line_containing_pattern)
        elif pattern == self.patterns["client_select_targets_resp"]:
            bot_logger.log_info("CLIENT_EVENT: SelectTargetsResp sent — a target click registered in the MTGA client.")
        elif pattern == self.patterns["client_submit_attackers"]:
            bot_logger.log_info("CLIENT_EVENT: SubmitAttackersReq sent — attack declaration submitted by the client.")
        elif pattern == self.patterns["client_set_settings"]:
            bot_logger.log_info(
                "CLIENT_EVENT: SetSettingsReq sent — if this follows one of our clicks, that click hit the "
                "phase strip / stops UI instead of the intended target (misclick signal)."
            )

    def _main_quests_completed_absolute(self) -> int | None:
        """Daily quests this account has completed RIGHT NOW (by bot or human),
        derived from the current Home quest list: slots - still-incomplete.
        Returns None when no valid Home read has happened yet (so callers don't
        act on an empty/stale cache)."""
        incomplete = self._last_valid_quest_active_incomplete
        if incomplete is None:
            return None
        return max(0, min(self._DAILY_QUEST_SLOTS, self._DAILY_QUEST_SLOTS - int(incomplete)))

    def _main_quests_completed_this_session(self) -> int | None:
        """How many daily quests this account has completed DURING THIS SESSION.

        Uses the absolute count (quests done by anyone, bot or human) minus a
        baseline captured at the first valid Home read this session, so quests
        the human already finished before the bot started don't count toward
        the switch threshold. Returns None until a valid Home read has happened."""
        absolute = self._main_quests_completed_absolute()
        if absolute is None:
            return None
        if self._account_start_quests_completed_absolute is None:
            self._account_start_quests_completed_absolute = absolute
        return max(0, absolute - self._account_start_quests_completed_absolute)

    def set_account_switch_enabled(self, enabled: bool) -> None:
        """Live master on/off from the main UI toggle."""
        self._account_switch_enabled = bool(enabled)
        bot_logger.log_info(f"Account switching {'ENABLED' if self._account_switch_enabled else 'DISABLED'} (UI toggle).")

    def get_account_switch_enabled(self) -> bool:
        return bool(self._account_switch_enabled)

    def get_account_switch_mode(self) -> str:
        return self._account_switch_mode

    def get_account_switch_main_quests(self) -> int:
        return self._account_switch_main_quests

    def get_account_switch_daily_wins(self) -> int:
        return self._account_switch_daily_wins

    def _account_switch_due(self) -> bool:
        # Master switch off -> never switch, whatever the thresholds/accounts are.
        if not self._account_switch_enabled:
            return False
        # Quest mode: switch once the configured number of main quests are
        # completed AND the configured daily wins are reached. A 0 threshold means
        # "no requirement" for that dimension; if both are 0 the mode is disabled.
        if self._account_switch_mode == "quests":
            if self._account_switch_main_quests <= 0 and self._account_switch_daily_wins <= 0:
                return False
            # Anti-storm: if we've already cycled through every configured account
            # without playing a single match, they all meet the criteria -> stop
            # switching and just play, instead of an endless logout/login loop.
            if self._known_account_count and self._switches_without_match >= self._known_account_count:
                return False
            main_req = self._account_switch_main_quests
            if main_req <= 0:
                main_ok = True
            else:
                completed = self._main_quests_completed_this_session()
                if completed is None:
                    # Quest state not read from Home yet -> never switch on a guess.
                    return False
                main_ok = completed >= main_req
            wins_ok = self._daily_wins_this_account >= self._account_switch_daily_wins
            return main_ok and wins_ok
        # Time mode (default): switch every N minutes.
        if self._account_switch_interval <= 0:
            return False
        return (time.time() - self._last_account_switch_ts) >= self._account_switch_interval

    def get_account_switch_remaining_sec(self) -> int:
        """Seconds remaining until next account switch (0 if disabled or due)."""
        if self._account_switch_interval <= 0:
            return 0
        remaining = int(self._account_switch_interval - (time.time() - self._last_account_switch_ts))
        return max(0, remaining)

    def get_account_switch_interval_minutes(self) -> int:
        return int(self._account_switch_interval // 60) if self._account_switch_interval else 0

    def set_account_play_order(self, order: list[str]) -> None:
        self._account_play_order = order or []
        if self._account_play_order:
            bot_logger.log_info(f"Account play order updated: {self._account_play_order}")
        else:
            bot_logger.log_info("Account play order cleared.")

    def set_account_cycle_index(self, index: int) -> None:
        try:
            self._account_cycle_index = int(index)
        except (TypeError, ValueError):
            self._account_cycle_index = 0

    def _replay_recorded_logout(self) -> bool:
        return self._replay_named_record("Account Switch", tag_prefix="LOGOUT", allow_keys={"esc"})

    def _load_logout_click_points_from_record(
        self,
    ) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None:
        path = str(runtime_file("records", "recorded_actions_records.json"))
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        records = data.get("records", [])
        if not records:
            return None
        record = None
        for rec in reversed(records):
            if rec.get("name") in {"Logout", "logout", "Account Switch"}:
                record = rec
                break
        if record is None:
            return None
        actions = record.get("actions", [])
        clicks: list[tuple[int, int]] = []
        for ev in actions:
            if ev.get("type") != "click":
                continue
            try:
                x = int(float(ev.get("x", 0)))
                y = int(float(ev.get("y", 0)))
            except Exception:
                continue
            if x > 0 and y > 0:
                clicks.append((x, y))
        if len(clicks) < 3:
            return None
        # Recorded logout flows are typically:
        # [focus, focus, esc, LOG_OUT_BTN, LOG_OUT_OK_BTN].
        return clicks[0], clicks[-2], clicks[-1]

    def _seed_logout_points_from_record_once(self) -> None:
        points = self._load_logout_click_points_from_record()
        if points is None:
            return
        focus_pt_raw, log_out_pt_raw, log_out_ok_pt_raw = points
        focus_pt = self._convert_record_click_to_1920_relative(focus_pt_raw)
        log_out_pt = self._convert_record_click_to_1920_relative(log_out_pt_raw)
        log_out_ok_pt = self._convert_record_click_to_1920_relative(log_out_ok_pt_raw)
        self.log_out_focus_coors = focus_pt
        self.log_out_btn_coors = log_out_pt
        self.log_out_ok_btn_coors = log_out_ok_pt
        self._loaded_click_targets["log_out_focus"] = {"x": int(focus_pt[0]), "y": int(focus_pt[1])}
        self._loaded_click_targets["log_out_btn"] = {"x": int(log_out_pt[0]), "y": int(log_out_pt[1])}
        self._loaded_click_targets["log_out_ok_btn"] = {"x": int(log_out_ok_pt[0]), "y": int(log_out_ok_pt[1])}
        self._persist_logout_points_to_calibration_config(focus_pt, log_out_pt, log_out_ok_pt)
        bot_logger.log_info(
            "Seeded logout baseline points from record: "
            f"focus_raw={focus_pt_raw} focus={focus_pt}, "
            f"log_out_raw={log_out_pt_raw} log_out={log_out_pt}, "
            f"log_out_ok_raw={log_out_ok_pt_raw} log_out_ok={log_out_ok_pt}"
        )

    def _convert_record_click_to_1920_relative(self, point: tuple[int, int]) -> tuple[int, int]:
        """
        Convert a recorded absolute desktop click into 1920-relative window space when possible.
        If conversion cannot be safely inferred, keep the original point.
        """
        try:
            px = int(point[0])
            py = int(point[1])
        except Exception:
            return point

        ct = self._loaded_click_targets or {}
        queue_cfg = ct.get("queue_button")
        qrel = self._default_points_1920.get("home_play_button_coors")
        if isinstance(queue_cfg, dict) and qrel is not None:
            try:
                qx = int(queue_cfg.get("x"))
                qy = int(queue_cfg.get("y"))
                # Legacy absolute calibration profile: reconstruct old window origin.
                if qx > 1920 or qy > 1080:
                    ox = int(qx - int(qrel[0]))
                    oy = int(qy - int(qrel[1]))
                    rx = int(px - ox)
                    ry = int(py - oy)
                    if 0 <= rx <= 1920 and 0 <= ry <= 1080:
                        return (rx, ry)
            except Exception:
                pass

        # Already normalized.
        if 0 <= px <= 1920 and 0 <= py <= 1080:
            return (px, py)
        return (px, py)

    def _persist_logout_points_to_calibration_config(
        self,
        focus_pt: tuple[int, int],
        log_out_pt: tuple[int, int],
        log_out_ok_pt: tuple[int, int],
    ) -> None:
        config_path = str(runtime_file("config", "calibration_config.json"))
        if not os.path.exists(config_path):
            return
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return
        click_targets = cfg.get("click_targets", {})
        if not isinstance(click_targets, dict):
            click_targets = {}
        current_focus = click_targets.get("log_out_focus")
        current_a = click_targets.get("log_out_btn")
        current_b = click_targets.get("log_out_ok_btn")
        desired_focus = {"x": int(focus_pt[0]), "y": int(focus_pt[1])}
        desired_a = {"x": int(log_out_pt[0]), "y": int(log_out_pt[1])}
        desired_b = {"x": int(log_out_ok_pt[0]), "y": int(log_out_ok_pt[1])}
        if current_focus == desired_focus and current_a == desired_a and current_b == desired_b:
            return
        click_targets["log_out_focus"] = desired_focus
        click_targets["log_out_btn"] = desired_a
        click_targets["log_out_ok_btn"] = desired_b
        cfg["click_targets"] = click_targets
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _replay_named_record(self, name: str, tag_prefix: str = "REPLAY", allow_keys: set[str] | None = None) -> bool:
        path = str(runtime_file("records", "recorded_actions_records.json"))
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
        runtime_status.set_mode("post_match", bot_state=str(self._get_state_from_log()))
        elapsed = time.time() - self._post_match_ready_ts
        if elapsed < self._post_match_delay_sec:
            remaining = self._post_match_delay_sec - elapsed
            bot_logger.log_info(f"Post-match delay active ({remaining:.1f}s remaining).")
            runtime_status.set_intentional_wait(remaining + 0.2, "post_match_delay")
            # Ensure we re-check when the delay elapses to avoid getting stuck at ~0s.
            threading.Timer(max(0.1, remaining + 0.1), self._maybe_post_match_action).start()
            return
        runtime_status.clear_intentional_wait()
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
        runtime_status.clear_intentional_wait()
        runtime_status.set_mode(
            "queueing",
            bot_state=str(self._get_state_from_log()),
            my_timer_running=False,
            my_timer_type="",
            my_timer_remaining_sec=None,
            my_timer_elapsed_sec=None,
            my_timer_duration_sec=None,
            my_timer_critical_count=0,
            my_timer_last_critical_at_epoch=0.0,
            my_timer_timeout_seen=False,
            my_timer_timeout_at_epoch=0.0,
        )
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
                if self._post_match_ready_ts is None:
                    # Switch became due BEFORE any match finished (e.g. the account
                    # already meets the criteria at start). The post-match path
                    # never runs here, so trigger the switch directly -- otherwise
                    # the loop would just stop and nothing would perform it.
                    bot_logger.log_info("Account switch due before a match finished; performing switch now.")
                    threading.Thread(target=self._perform_account_switch, daemon=True).start()
                else:
                    bot_logger.log_info("Account switch due; stopping queue spam; post-match flow will perform it.")
                return
            self.start_game_from_home_screen()
            time.sleep(3.0)

    def _perform_account_switch(self) -> None:
        if self._account_switch_in_progress:
            return
        self._account_switch_in_progress = True
        # Reset quest-mode tracking so the incoming account is measured fresh:
        # re-capture its session baseline and restart its daily-win count.
        self._account_start_quests_completed_absolute = None
        self._daily_wins_this_account = 0
        self._win_counted_this_match = False
        # Re-evaluate the incoming account's quest state fresh from its own Home:
        # forget the previous account's absolute count and re-arm the one-shot
        # Home quest check. Count this switch for the anti-storm guard.
        self._last_valid_quest_active_incomplete = None
        self._home_quest_check_done = False
        self._starter_picker_shortcut_tried = False
        self._switches_without_match += 1
        # Reset post-match marker so the incoming account is treated as "no match
        # played yet": if it already meets the criteria on landing, the queue loop
        # performs the switch immediately instead of waiting for a match that the
        # post-match path would need.
        self._post_match_ready_ts = None
        # Per-account gold tracking is measured fresh for the incoming account.
        # The accumulated per-account totals (_gold_farmed_by_account) are kept:
        # they are keyed by screenName, so each account keeps its own running
        # session total across switches.
        self._account_quest_gold = {}
        self._credited_quest_ids = set()
        # Forget the previous account's screenName so the next quests block we
        # parse (the new account's) is latched as the new owner instead of being
        # rejected as stale.
        self._current_account_screen_name = None
        runtime_status.clear_intentional_wait()
        runtime_status.set_mode("account_switch", bot_state=str(self._get_state_from_log()))
        queued_after_login = False
        try:
            if self._stop_requested:
                bot_logger.log_info("Account switch aborted: stop requested.")
                return
            bot_logger.log_info("Account switch: starting logout/login flow.")
            accounts = self._load_accounts_from_dirs()
            if not accounts:
                bot_logger.log_error("Account switch failed: no account credentials found in account folders.")
                self._account_switch_pending = False
                return
            # Remember how many accounts exist, for the anti-storm guard. Set
            # this BEFORE the calibration check below so a missing button
            # calibration (which aborts every switch attempt) still lets the
            # guard trip instead of leaving _known_account_count at 0 forever
            # and disabling the anti-storm protection permanently.
            self._known_account_count = len(accounts)
            if not self.log_out_btn_coors or not self.log_out_ok_btn_coors:
                bot_logger.log_error("Account switch failed: missing calibrated button(s).")
                self._account_switch_pending = False
                return
            bot_logger.log_info(
                "Accounts loaded: count={} names={}".format(
                    len(accounts), [a.get("name") for a in accounts]
                )
            )
            if self._account_play_order:
                bot_logger.log_info(f"Account play order configured: {self._account_play_order}")

            custom_order = self._resolve_account_play_order(accounts)
            bot_logger.log_info(f"Account play order resolved indices: {custom_order}")
            if custom_order:
                order_len = len(custom_order)
                if order_len == 1:
                    next_pos = 0
                    next_index = custom_order[0]
                else:
                    # Treat account_cycle_index as the NEXT position to use.
                    # If unset/invalid, start at the first entry.
                    pos = self._account_cycle_index
                    if pos < 0 or pos >= order_len:
                        pos = 0
                    next_pos = pos
                    next_index = custom_order[next_pos]
                bot_logger.log_info(f"Account play order (indices): {custom_order}")
                bot_logger.log_info(f"Account play order pos (next): {self._account_cycle_index} -> {next_pos}")
            else:
                # No explicit play order configured: cycle by sorted account list.
                if self._account_cycle_index < 0 or self._account_cycle_index >= len(accounts):
                    self._account_cycle_index = 0
                next_index = self._account_cycle_index
            account = accounts[next_index]
            account_name = str(account.get("name", "")).strip() or str(account.get("folder", "")).strip()

            bot_logger.log_info(f"Switching account to '{account_name}'")
            # Remember which alias we're switching to, so the next quests block we
            # latch (the new account's) can be mapped screenName -> alias.
            self._pending_switch_alias = account_name or None
            if custom_order:
                next_cycle = (next_pos + 1) % len(custom_order)
                bot_logger.log_info(f"Account cycle index (order pos): {self._account_cycle_index} -> {next_cycle}")
            else:
                bot_logger.log_info(f"Account cycle index: {self._account_cycle_index} -> {next_index}")
            self._post_login_action_done = False
            logout_log_offset = self._get_log_size(self._log_path)
            logout_ok = False
            if self._replay_recorded_logout():
                bot_logger.log_info("Recorded logout replay started; waiting for login-screen transition.")
                runtime_status.set_intentional_wait(max(8.0, self._login_delete_delay_sec + 3.0), "logout_transition_wait")
                logout_ok = self._wait_for_logout_to_reach_login_screen(
                    start_offset=logout_log_offset,
                    timeout_sec=max(8.0, self._login_delete_delay_sec + 3.0),
                )
                if not logout_ok:
                    bot_logger.log_error("Recorded logout replay did not reach the login screen; falling back to built-in logout clicks.")
            else:
                bot_logger.log_info("Recorded logout replay unavailable; falling back to built-in macOS-style logout clicks.")

            if not logout_ok:
                logout_log_offset = self._get_log_size(self._log_path)
                self._run_mapped_logout_sequence()
                runtime_status.set_intentional_wait(max(8.0, self._login_delete_delay_sec + 3.0), "logout_transition_wait")
                logout_ok = self._wait_for_logout_to_reach_login_screen(
                    start_offset=logout_log_offset,
                    timeout_sec=max(8.0, self._login_delete_delay_sec + 3.0),
                )
            if not logout_ok:
                home_ready_after_logout = self._playerlog_contains_marker_since(
                    ["MainNav load in"],
                    start_offset=logout_log_offset,
                )
                if home_ready_after_logout:
                    bot_logger.log_error(
                        "Account switch logout did not reach the login screen; MainNav/Home was detected instead. "
                        "Aborting account switch and resuming queue on the current account."
                    )
                    self._write_nav_debug_bundle("logout_failed_home_visible")
                    self._account_switch_pending = False
                    self._last_account_switch_ts = time.time()
                    self._account_switch_in_progress = False
                    runtime_status.clear_intentional_wait()
                    self._set_runtime_home_mode("home_ready")
                    self.start_queueing()
                    queued_after_login = True
                    return
                bot_logger.log_error("Account switch failed: logout did not reach the login screen.")
                self._write_nav_debug_bundle("logout_failed_no_login_screen")
                self._account_switch_pending = False
                return
            if self._stop_requested:
                bot_logger.log_info("Account switch aborted after logout: stop requested.")
                return
            bot_logger.log_info(f"Account switch: waiting {self._login_delete_delay_sec:.2f}s for login screen.")
            runtime_status.set_intentional_wait(self._login_delete_delay_sec + 0.2, "login_screen_settle")
            for _ in range(int(self._login_delete_delay_sec * 10)):
                if self._stop_requested:
                    bot_logger.log_info("Account switch aborted while waiting for login screen.")
                    return
                time.sleep(0.1)

            bot_logger.log_info("Account switch: entering credentials.")
            runtime_status.clear_intentional_wait()
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
                bot_logger.log_info("Account switch: waiting 20s before post-login record.")
                runtime_status.set_intentional_wait(20.2, "post_login_wait")
                for _ in range(200):
                    if self._stop_requested:
                        break
                    time.sleep(0.1)
            if not self._stop_requested and not self._post_login_action_done:
                if self._run_post_login_routine(account, accounts):
                    self._post_login_action_done = True
            if not self._stop_requested and self._post_login_action_done:
                bot_logger.log_info("Post-login routine done; waiting 5s before queueing.")
                runtime_status.set_intentional_wait(5.2, "post_login_queue_delay")
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

            if custom_order:
                # Advance to next position after a successful switch.
                self._account_cycle_index = (next_pos + 1) % len(custom_order)
            else:
                # Advance to next account in default sorted list.
                self._account_cycle_index = (next_index + 1) % len(accounts)
            self._last_account_switch_ts = time.time()
            self._account_switch_pending = False
            if not queued_after_login:
                self._queue_after_login = True
            self._persist_account_cycle_index()
        except Exception as e:
            bot_logger.log_error(f"Account switch failed: {e}")
        finally:
            runtime_status.clear_intentional_wait()
            self._account_switch_in_progress = False

    def _run_mapped_logout_sequence(self) -> None:
        bot_logger.log_info("Account switch: using built-in macOS-style logout sequence.")
        bot_logger.log_info("Account switch: pressing ESC to open options menu.")
        self.input.tap_escape()
        time.sleep(1.0)
        last_scene = self._get_last_scene_name()
        bot_logger.log_info(f"Account switch: last scene before fallback logout = {last_scene or 'unknown'}.")
        if last_scene == "Store":
            bot_logger.log_info("Account switch: Store scene detected; pressing ESC again for options menu.")
            self.input.tap_escape()
            time.sleep(1.0)
        else:
            time.sleep(1.0)

        log_out_target, log_out_source = self._resolve_target_from_queue_anchor_rebase(
            config_key="log_out_btn",
            raw_point=self.log_out_btn_coors,
            label="LOG_OUT_BTN",
            force_reacquire=True,
        )
        bot_logger.log_info(
            "Account switch: clicking LOG_OUT_BTN at {} (source={}).".format(
                log_out_target,
                log_out_source,
            )
        )
        self._click_abs(int(log_out_target[0]), int(log_out_target[1]), "LOG_OUT_BTN")
        time.sleep(0.15)
        self._click_abs(int(log_out_target[0]), int(log_out_target[1]), "LOG_OUT_BTN")
        time.sleep(0.8)
        if self._click_image_in_scaled_arena_region(
            os.path.join(self._buttons_dir(), "log_out_btn.png"),
            "LOG_OUT_BTN_IMG",
            rel_region=None,
            confidence=0.84,
            timeout=1.2,
        ) or self._click_logout_image_if_visible(
            "log_out_btn.png",
            label="LOG_OUT_BTN_IMG_FALLBACK",
            confidence=0.84,
            timeout_sec=1.2,
            region=self._region_around_point(log_out_target, width=520, height=260),
        ):
            time.sleep(0.8)

        log_out_ok_target, log_out_ok_source = self._resolve_target_from_queue_anchor_rebase(
            config_key="log_out_ok_btn",
            raw_point=self.log_out_ok_btn_coors,
            label="LOG_OUT_OK_BTN",
            force_reacquire=True,
        )
        bot_logger.log_info(
            "Account switch: clicking LOG_OUT_OK_BTN at {} (source={}).".format(
                log_out_ok_target,
                log_out_ok_source,
            )
        )
        self._click_abs(int(log_out_ok_target[0]), int(log_out_ok_target[1]), "LOG_OUT_OK_BTN")
        time.sleep(0.15)
        self._click_abs(int(log_out_ok_target[0]), int(log_out_ok_target[1]), "LOG_OUT_OK_BTN")
        time.sleep(0.35)
        self._click_abs(int(log_out_ok_target[0]), int(log_out_ok_target[1]), "LOG_OUT_OK_BTN")
        time.sleep(0.5)
        self._click_image_in_scaled_arena_region(
            os.path.join(self._buttons_dir(), "okay_btn.png"),
            "LOG_OUT_OK_IMG",
            rel_region=None,
            confidence=0.86,
            timeout=0.9,
        ) or self._click_logout_image_if_visible(
            "okay_btn.png",
            label="LOG_OUT_OK_IMG_FALLBACK",
            confidence=0.86,
            timeout_sec=0.9,
            region=self._region_around_point(log_out_ok_target, width=420, height=220),
        )

    def run_mapped_logout_sequence_for_test(self) -> bool:
        """Run only the built-in macOS-style logout sequence (no account/login steps)."""
        try:
            logout_log_offset = self._get_log_size(self._log_path)
            self._run_mapped_logout_sequence()
            return self._wait_for_logout_to_reach_login_screen(
                start_offset=logout_log_offset,
                timeout_sec=max(8.0, self._login_delete_delay_sec + 3.0),
            )
        except Exception as e:
            bot_logger.log_error(f"Built-in logout test sequence failed: {e}")
            return False

    def _resolve_account_play_order(self, accounts: list[dict]) -> list[int]:
        if not self._account_play_order:
            return []
        account_name_to_pos = {}
        for pos, acc in enumerate(accounts):
            raw_name = str(acc.get("name", "")).strip()
            if not raw_name:
                continue
            account_name_to_pos[raw_name.casefold()] = pos

        order = []
        for raw in self._account_play_order:
            name = str(raw).strip()
            if not name:
                continue
            pos = account_name_to_pos.get(name.casefold())
            if pos is None or pos in order:
                continue
            order.append(pos)
        return order

    def _load_accounts_from_dirs(self) -> list[dict]:
        accounts = []
        seen_folders = set()
        try:
            scan_dirs = [self._accounts_base_dir(), self._legacy_accounts_base_dir()]
            for base_dir in scan_dirs:
                for entry in os.listdir(base_dir):
                    full = os.path.join(base_dir, entry)
                    if not os.path.isdir(full):
                        continue
                    entry_key = entry.casefold()
                    if entry_key in seen_folders:
                        continue
                    creds_json = os.path.join(full, "credentials.json")
                    if not os.path.isfile(creds_json):
                        continue
                    try:
                        with open(creds_json, "r", encoding="utf-8") as f:
                            payload = json.load(f)
                    except Exception as e:
                        bot_logger.log_error(f"Failed to read account credentials from {creds_json}: {e}")
                        continue
                    if not isinstance(payload, dict) or not payload:
                        continue
                    first_name = next(iter(payload.keys()))
                    details = payload.get(first_name, {})
                    if not isinstance(details, dict):
                        continue
                    email = str(details.get("email", "")).strip()
                    pw = str(details.get("pw", "")).strip()
                    if not first_name or not email or not pw:
                        continue
                    accounts.append({
                        "name": str(first_name).strip(),
                        "folder": entry,
                        "email": email,
                        "pw": pw,
                    })
                    seen_folders.add(entry_key)
        except Exception as e:
            bot_logger.log_error(f"Failed to scan account folders: {e}")
            return []
        accounts.sort(key=lambda a: str(a.get("name", "")).casefold())
        return accounts

    def _persist_account_cycle_index(self) -> None:
        try:
            config_path = str(runtime_file("config", "calibration_config.json"))
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

    def __handle_modal_choose_last_option(self, n_options: int, source_id=None) -> None:
        """Click the bottom button of a modal "Choose One" overlay.

        Used for ability-resolution choices whose options are prompt-parameter
        indices (no game object to click), e.g. Perforating Artist's "each
        opponent loses 3 life unless they sacrifice/discard". The punitive
        "lose life" option is always the last/bottom plate, so we click it.
        Geometry is base-1920x1080, measured from a 3-option capture; the button
        stack is vertically centered, so the bottom plate shifts predictably with
        the option count (2 options when hand/board is empty).
        """
        try:
            if self._suppress_selections or self._stop_requested:
                bot_logger.log_info("Modal choice ignored: selections suppressed or stop requested.")
                return
            now = time.time()
            if now - self.__last_modal_choice_ts < 2.0:
                bot_logger.log_info("Modal choice ignored: duplicate within 2s window.")
                return
            n = max(1, int(n_options or 1))
            group_center_y = 408
            spacing_y = 107
            center_x = 956
            bottom_y = int(group_center_y + ((n - 1) / 2.0) * spacing_y)
            base_point = (center_x, bottom_y)
            self.__last_modal_choice_ts = now
            # Pause the decision loop while we resolve the modal, so a running
            # hand/board scan does not move the mouse and race the modal click
            # (mirrors the scry/GroupReq handler). Without this the click could
            # land off the "Lose 3 life" plate and the choice would not register.
            self.__group_req_active_until = now + 6.0
            # Snapshot the turn state so retries stop once the modal resolves (the
            # game advances past this step) -- avoids clicking the board after.
            snap_ti = {}
            try:
                snap_ti = dict(self.updated_game_state.get_turn_info() or {})
            except Exception:
                snap_ti = {}
            snap_key = (
                snap_ti.get("turnNumber"), snap_ti.get("phase"),
                snap_ti.get("step"), snap_ti.get("decisionPlayer"),
            )
            bot_logger.log_info(
                f"MODAL_CHOICE: {n} option(s) (source={source_id}) -> clicking bottom "
                f"(lose life) at base={base_point}"
            )

            def _modal_still_open() -> bool:
                # Resolved if the turn state moved on from the modal's step.
                try:
                    ti = self.updated_game_state.get_turn_info() or {}
                except Exception:
                    return True
                cur = (
                    ti.get("turnNumber"), ti.get("phase"),
                    ti.get("step"), ti.get("decisionPlayer"),
                )
                return cur == snap_key

            def _click_bottom(attempt: int = 0) -> None:
                try:
                    if self._suppress_selections or self._stop_requested:
                        return
                    if attempt > 0 and not _modal_still_open():
                        bot_logger.log_info(
                            f"MODAL_CHOICE: modal resolved before retry {attempt}; stopping."
                        )
                        return
                    target, src = self._map_abs_point_to_arena(base_point, label="MODAL_LOSE_LIFE")
                    bot_logger.log_info(
                        f"MODAL_CHOICE click (attempt {attempt}): base={base_point} target={target} src={src}"
                    )
                    bot_logger.log_click(target[0], target[1], "MODAL_LOSE_LIFE")
                    self.input.move_abs(target[0], target[1])
                    time.sleep(0.3)
                    self.input.left_click(1)
                    # Retry a couple of times in case the click landed mid-animation
                    # or did not register; guarded by _modal_still_open so we never
                    # click the board once it resolves.
                    if attempt < 2:
                        threading.Timer(1.4, lambda: _click_bottom(attempt + 1)).start()
                except Exception as e:
                    bot_logger.log_error(f"Modal choice click execution failed: {e}")

            # Let the "Choose One" overlay finish animating in before clicking.
            threading.Timer(1.0, lambda: _click_bottom(0)).start()
        except Exception as e:
            bot_logger.log_error(f"Failed to handle modal choice: {e}")

    def __handle_group_req(self, line: str) -> None:
        """Scry / surveil / other ordered-grouping prompts. No reordering logic
        for now -- just click Done so the bot does not stall. Leaving the cards
        untouched keeps them in their default order (i.e. on top for scry)."""
        try:
            if self._suppress_selections or self._stop_requested:
                bot_logger.log_info("GroupReq ignored: selections suppressed or stop requested.")
                return
            start = line.find("{")
            if start == -1:
                return
            payload = json.loads(line[start:])
            messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
            for message in messages:
                if message.get("type") != "GREMessageType_GroupReq":
                    continue
                if self.__system_seat_id is None:
                    return
                seat_ids = message.get("systemSeatIds") or []
                if self.__system_seat_id not in seat_ids:
                    continue
                context = (message.get("groupReq", {}) or {}).get("context", "")
                now = time.time()
                if now - self.__last_group_req_ts < 2.0:
                    bot_logger.log_info("GroupReq ignored: duplicate within 2s window.")
                    return
                self.__last_group_req_ts = now
                # A running decision/cast hand-scan moves the mouse and would
                # race the Done click. Signal scans to abort and pause new
                # decisions until the scry resolves.
                self.__group_req_active_until = now + 6.0
                bot_logger.log_info(f"GROUP_REQ ({context}): clicking Done (no reordering).")

                def _click_done() -> None:
                    try:
                        if self._suppress_selections or self._stop_requested:
                            return
                        # Locate the orange "Done" button by template first. The
                        # button is visually identical in scry and surveil but can
                        # sit at slightly different heights, so a single fixed pixel
                        # missed on scry (bot stalled with the prompt still open).
                        # Template matching over a generous bottom-centre band works
                        # for both overlays regardless of the exact position.
                        # Scry's Done button sits ~49px higher than surveil's
                        # (game-frame y~876 vs ~925); the band spans both. Template
                        # confirmed to match the real scry overlay at conf ~0.86, so
                        # 0.78 leaves headroom for the button's pulsing glow.
                        done_tpl = os.path.join(self._buttons_dir(), "scry_done.png")
                        if os.path.exists(done_tpl) and self._click_image_in_scaled_arena_region(
                            done_tpl, "SCRY_DONE", rel_region=(700, 820, 520, 240),
                            confidence=0.78, timeout=1.5,
                        ):
                            bot_logger.log_info("GROUP_REQ Done click: matched scry_done.png template.")
                            return
                        # Fallback fixed coordinate (base 1920x1080), measured from
                        # real captures: scry's Done button centres at (960, 878),
                        # surveil's ~47px lower at (960, 925). Pick by context so a
                        # template miss still lands on the right overlay's button.
                        base_point = (960, 878) if "scry" in context.lower() else (960, 925)
                        target, src = self._map_abs_point_to_arena(base_point, label="SCRY_DONE")
                        bot_logger.log_info(
                            f"GROUP_REQ Done click (fallback fixed, context={context}): base={base_point} target={target} src={src}"
                        )
                        # _click_abs does a proper left_down/hold/left_up press,
                        # which Unity registers reliably (a bare left_click can be
                        # dropped).
                        self._click_abs(int(target[0]), int(target[1]), "SCRY_DONE")
                    except Exception as e:
                        bot_logger.log_error(f"GroupReq Done click failed: {e}")

                # Let the scry overlay finish animating in before clicking.
                threading.Timer(0.8, _click_done).start()
                return
        except Exception as e:
            bot_logger.log_error(f"Failed to handle GroupReq: {e}")

    def __handle_select_n_req(self, line: str) -> None:
        try:
            if self._suppress_selections or self._stop_requested:
                bot_logger.log_info("SelectN ignored: selections suppressed or stop requested.")
                return
            stack_count = 0
            try:
                stack_count = self.updated_game_state.get_zone_object_count("ZoneType_Stack")
            except Exception:
                stack_count = 0
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
                # Modal "Choose One" from an ability resolution (e.g. Perforating
                # Artist: "loses 3 life unless sacrifice/discard"). Options are
                # prompt-parameter indices, not game objects, so the object-based
                # selection paths below cannot resolve them and the bot would abort
                # and stall. Policy: click the LAST option, which is the punitive
                # "lose life" plate -- it is never filtered out and always renders
                # at the bottom.
                if (
                    req.get("idType") == "IdType_PromptParameterIndex"
                    and req.get("context") == "SelectionContext_Resolution"
                ):
                    self.__handle_modal_choose_last_option(
                        len(ids), source_id=req.get("sourceId")
                    )
                    self.__clear_pending_select_n_state(
                        "Modal choice: clicked last option (lose life)."
                    )
                    return
                existing_pending = self.__pending_select_n if isinstance(self.__pending_select_n, dict) else None
                same_pending = False
                if existing_pending is not None:
                    try:
                        existing_ids = list(existing_pending.get("ids", []) or [])
                        same_pending = sorted(existing_ids) == sorted(ids)
                    except Exception:
                        same_pending = False
                if same_pending:
                    token = int(existing_pending.get("token", 0) or 0)
                    if token <= 0:
                        self.__select_n_token_counter += 1
                        token = self.__select_n_token_counter
                        existing_pending["token"] = token
                    existing_pending["ids"] = list(ids)
                    existing_pending["ts"] = time.time()
                    self.__pending_select_n = existing_pending
                    bot_logger.log_info(
                        f"SelectN reusing pending request token={token} ids={ids}"
                    )
                else:
                    self.__select_n_token_counter += 1
                    token = self.__select_n_token_counter
                    self.__pending_select_n = {"ids": list(ids), "ts": time.time(), "token": token}
                min_sel = int(req.get("minSel", 1))
                if min_sel < 1:
                    min_sel = 1
                random.shuffle(ids)
                def _clear_pending_select_n(reason: str | None = None) -> None:
                    self.__clear_pending_select_n_state(reason or "SelectN cleared.")

                informational_use_only = bool(message.get("informationalUseOnly"))
                if informational_use_only:
                    _clear_pending_select_n(
                        "SelectN informational-only: no action required."
                    )
                    continue

                context = req.get("context")
                option_context = req.get("optionContext")
                discard_context = False
                try:
                    context_candidates = [
                        context,
                        option_context,
                        req.get("selectionType"),
                        req.get("selectionContext"),
                        req.get("promptType"),
                    ]
                    discard_context = any(
                        isinstance(val, str) and "discard" in val.lower()
                        for val in context_candidates
                    )
                except Exception:
                    discard_context = False
                if discard_context:
                    bot_logger.log_info("SelectN context: discard detected.")
                sacrifice_context = False
                try:
                    sacrifice_context = any(
                        isinstance(val, str) and "sacrif" in val.lower()
                        for val in context_candidates
                    )
                except Exception:
                    sacrifice_context = False
                if sacrifice_context:
                    bot_logger.log_info("SelectN context: sacrifice detected.")
                resolution_context = (
                    context == "SelectionContext_Resolution"
                    or option_context == "OptionContext_Resolution"
                )
                use_stack_selection = False
                use_battlefield_selection = False
                hand_zone = self.updated_game_state.get_zone("ZoneType_Hand", self.__system_seat_id)
                hand_ids = set(hand_zone.get("objectInstanceIds", []) or []) if hand_zone else set()
                ids_in_hand = [cid for cid in ids if cid in hand_ids]
                use_hand_selection = bool(ids_in_hand)
                pending_zone = self.updated_game_state.get_zone("ZoneType_Pending")
                pending_ids = set(pending_zone.get("objectInstanceIds", []) or []) if pending_zone else set()
                stack_zone = self.updated_game_state.get_zone("ZoneType_Stack")
                stack_ids = set(stack_zone.get("objectInstanceIds", []) or []) if stack_zone else set()
                battlefield_zone = self.updated_game_state.get_zone("ZoneType_Battlefield")
                battlefield_zone_ids = set(
                    battlefield_zone.get("objectInstanceIds", []) or []
                ) if battlefield_zone else set()
                game_objects = self.updated_game_state.get_game_objects() or []
                my_battlefield_ids = {
                    int(obj.get("instanceId"))
                    for obj in game_objects
                    if isinstance(obj, dict)
                    and obj.get("zoneId") == (battlefield_zone or {}).get("zoneId")
                    and obj.get("controllerSeatId") == self.__system_seat_id
                    and isinstance(obj.get("instanceId"), int)
                }
                ids_on_my_battlefield = [
                    cid for cid in ids if cid in battlefield_zone_ids and cid in my_battlefield_ids
                ]
                prompt_ids = [cid for cid in ids if cid in pending_ids or cid in stack_ids]
                game_objects_by_id = {
                    int(obj.get("instanceId")): obj
                    for obj in game_objects
                    if isinstance(obj, dict) and isinstance(obj.get("instanceId"), int)
                }
                stack_selection_targets: list[dict[str, int]] = []
                for prompt_id in prompt_ids:
                    hover_id = prompt_id
                    prompt_obj = game_objects_by_id.get(prompt_id) or {}
                    if str(prompt_obj.get("type") or "") == "GameObjectType_Ability":
                        parent_id = prompt_obj.get("parentId")
                        if isinstance(parent_id, int) and parent_id > 0:
                            hover_id = parent_id
                    stack_selection_targets.append(
                        {"prompt_id": int(prompt_id), "hover_id": int(hover_id)}
                    )
                if prompt_ids:
                    use_stack_selection = True
                elif isinstance(option_context, str) and "stack" in option_context.lower():
                    bot_logger.log_info("SelectN stack context detected but prompt ids are not active.")
                if ids_on_my_battlefield:
                    use_battlefield_selection = True
                if resolution_context and stack_count > 0 and not (use_hand_selection or use_stack_selection or use_battlefield_selection):
                    wait_ts = self.__pending_select_n.get("stack_wait_ts") if self.__pending_select_n else None
                    if wait_ts is None and self.__pending_select_n is not None:
                        self.__pending_select_n["stack_wait_ts"] = time.time()
                        wait_ts = self.__pending_select_n.get("stack_wait_ts")
                    if wait_ts is not None and (time.time() - wait_ts) > self.__select_n_stack_wait_timeout_sec:
                        bot_logger.log_info(
                            "SelectN stack wait timeout: aborting selection to avoid stall."
                        )
                        self.__pending_select_n = None
                        self.__select_n_in_progress = False
                        self.__select_n_in_progress_since = 0.0
                        return
                    bot_logger.log_info(
                        f"SelectN delayed: stack has {stack_count} object(s) during resolution."
                    )
                    threading.Timer(0.6, lambda: self.__handle_select_n_req(line)).start()
                    return
                if not ids_in_hand:
                    # Hand zone can be missing in this update (e.g., discard prompts from opponent effects).
                    # Fall back to the provided ids and retry selection after a brief delay.
                    bot_logger.log_info(
                        f"SelectN ids not in hand; attempting selection from prompt list. ids={ids}"
                    )
                    if discard_context:
                        retry = 0
                        if self.__pending_select_n is not None:
                            retry = int(self.__pending_select_n.get("discard_retry", 0))
                        if retry < 1:
                            if self.__pending_select_n is not None:
                                self.__pending_select_n["discard_retry"] = retry + 1
                            bot_logger.log_info(
                                "SelectN discard: hand zone missing, retrying once after delay."
                            )
                            threading.Timer(1.0, lambda: self.__handle_select_n_req(line)).start()
                            return
                    if not use_hand_selection and not use_stack_selection and not use_battlefield_selection:
                        bot_logger.log_info("SelectN aborting: ids not in hand and no prompt candidates found.")
                        _clear_pending_select_n()
                        return
                else:
                    ids = ids_in_hand
                if use_stack_selection and not use_hand_selection:
                    if prompt_ids:
                        ids = prompt_ids
                    bot_logger.log_info(
                        f"SelectN using stack/pending selection for ids={ids}"
                    )
                    if stack_selection_targets and any(
                        target["prompt_id"] != target["hover_id"] for target in stack_selection_targets
                    ):
                        bot_logger.log_info(
                            "SelectN stack hover remap: {}".format(
                                [
                                    f"{target['prompt_id']}->{target['hover_id']}"
                                    for target in stack_selection_targets
                                ]
                            )
                        )
                elif use_battlefield_selection and not use_hand_selection:
                    ids = ids_on_my_battlefield
                    # If this is a sacrifice, keep protected creatures (e.g.
                    # Perforating Artist) last so they are only chosen when nothing
                    # else is available. Order-only change; the id set is unchanged.
                    if sacrifice_context and len(ids) > 1:
                        ids = sorted(
                            ids,
                            key=lambda cid: 1 if RemovalLogic.is_protected_from_sacrifice(
                                (game_objects_by_id.get(cid) or {}).get("grpId")
                            ) else 0,
                        )
                    bot_logger.log_info(
                        f"SelectN using battlefield selection for ids={ids}"
                    )
                if self.__pending_select_n is not None:
                    self.__pending_select_n["mode"] = (
                        "stack"
                        if (use_stack_selection and not use_hand_selection)
                        else ("battlefield" if (use_battlefield_selection and not use_hand_selection) else "hand")
                    )

                def _select_n_valid() -> bool:
                    if self._suppress_selections or self._stop_requested:
                        return False
                    pending = self.__pending_select_n
                    return bool(pending and pending.get("token") == token)

                def _current_prompt_ids() -> set[int]:
                    try:
                        pending_zone_local = self.updated_game_state.get_zone("ZoneType_Pending")
                        pending_ids_local = set(
                            pending_zone_local.get("objectInstanceIds", []) or []
                        ) if pending_zone_local else set()
                    except Exception:
                        pending_ids_local = set()
                    try:
                        stack_zone_local = self.updated_game_state.get_zone("ZoneType_Stack")
                        stack_ids_local = set(
                            stack_zone_local.get("objectInstanceIds", []) or []
                        ) if stack_zone_local else set()
                    except Exception:
                        stack_ids_local = set()
                    return pending_ids_local.union(stack_ids_local)

                def _verify_selection(selected_ids: list[int], attempt: int) -> None:
                    try:
                        if not _select_n_valid():
                            return
                        if use_stack_selection and not use_hand_selection:
                            selected_set = set(selected_ids or [])
                            active_prompt_ids = _current_prompt_ids()
                            if selected_set and not selected_set.intersection(active_prompt_ids):
                                _clear_pending_select_n(
                                    "SelectN stack verify: prompt resolved."
                                )
                                return
                        if use_battlefield_selection and not use_hand_selection:
                            pending_count = self.updated_game_state.get_pending_message_count()
                            if pending_count == 0 and (time.time() - self.__last_submit_selection_ts) > 1.0:
                                _clear_pending_select_n(
                                    "SelectN battlefield verify: prompt resolved."
                                )
                            return
                        if self.__system_seat_id is None:
                            return
                        hand_zone = self.updated_game_state.get_zone("ZoneType_Hand", self.__system_seat_id)
                        if not hand_zone:
                            return
                        hand_ids = set(hand_zone.get("objectInstanceIds", []) or [])
                        still_in_hand = [cid for cid in selected_ids if cid in hand_ids]
                        if not still_in_hand:
                            _clear_pending_select_n()
                            return
                        if discard_context:
                            # Avoid aggressive reselect loops on discard prompts.
                            if time.time() - self.__last_submit_selection_ts > 2.5 and attempt < 2:
                                self.submit_selection(
                                    reason="select_n_discard_verify_retry",
                                    force=True,
                                )
                                if self.__pending_select_n is not None:
                                    self.__pending_select_n["ts"] = time.time()
                                threading.Timer(1.2, _verify_selection, args=(selected_ids, attempt + 1)).start()
                            return
                        pending_count = self.updated_game_state.get_pending_message_count()
                        pending_zone = self.updated_game_state.get_zone("ZoneType_Pending")
                        pending_ids = set(pending_zone.get("objectInstanceIds", []) or []) if pending_zone else set()
                        if pending_ids.intersection(still_in_hand):
                            return
                        if pending_count > 0 and not resolution_context:
                            return
                        if time.time() - self.__last_submit_selection_ts < 2.5:
                            return
                        max_attempts = 3 if resolution_context else 2
                        if attempt < max_attempts:
                            if self.submit_selection(
                                reason="select_n_verify_retry",
                                force=resolution_context,
                            ):
                                if self.__pending_select_n is not None:
                                    self.__pending_select_n["ts"] = time.time()
                                threading.Timer(1.2, _verify_selection, args=(selected_ids, attempt + 1)).start()
                                return
                            if attempt < max_attempts:
                                bot_logger.log_info(
                                    f"SelectN verify: ids still in hand {still_in_hand}, retrying (attempt {attempt + 1})"
                                )
                                _attempt_selection(attempt + 1, delay=0.8)
                    except Exception as e:
                        bot_logger.log_error(f"SelectN verify failed: {e}")

                def _attempt_selection(attempt: int, delay: float) -> None:
                    def _do_selection():
                        try:
                            if self._suppress_selections or self._stop_requested:
                                _clear_pending_select_n()
                                return
                            if not _select_n_valid():
                                return
                            self.__select_n_in_progress = True
                            self.__select_n_in_progress_since = time.time()
                            if attempt == 1:
                                wait_sec = 3.0
                                if discard_context:
                                    wait_sec = 3.5
                                bot_logger.log_info(
                                    f"SelectN delay: waiting {wait_sec:.1f} seconds before selection."
                                )
                                time.sleep(wait_sec)
                                if not _select_n_valid():
                                    bot_logger.log_info(
                                        "SelectN selection canceled after delay: prompt token no longer valid."
                                    )
                                    return
                            try:
                                turn_info = self.updated_game_state.get_turn_info() or {}
                                decision_player = turn_info.get("decisionPlayer")
                            except Exception:
                                decision_player = None
                            pending_count = self.updated_game_state.get_pending_message_count()
                            stack_count_local = 0
                            try:
                                stack_count_local = self.updated_game_state.get_zone_object_count("ZoneType_Stack")
                            except Exception:
                                stack_count_local = 0
                            has_concrete_resolution_selection = (
                                resolution_context
                                and (use_hand_selection or use_stack_selection or use_battlefield_selection)
                            )
                            should_wait_for_stack_resolution = (
                                resolution_context
                                and stack_count_local > 0
                                and not has_concrete_resolution_selection
                            )
                            if (
                                self.__system_seat_id is not None
                                and decision_player is not None
                                and decision_player != self.__system_seat_id
                            ) or pending_count > 0 or should_wait_for_stack_resolution:
                                if attempt < 3:
                                    bot_logger.log_info(
                                        "SelectN delayed: decisionPlayer={}, pendingMessages={}, stackCount={}, concreteSelection={}, retrying (attempt {}).".format(
                                            decision_player,
                                            pending_count,
                                            stack_count_local,
                                            has_concrete_resolution_selection,
                                            attempt + 1,
                                        )
                                    )
                                    _attempt_selection(attempt + 1, delay=0.8)
                                else:
                                    bot_logger.log_info(
                                        "SelectN aborted: decisionPlayer={}, pendingMessages={}, stackCount={}, concreteSelection={}.".format(
                                            decision_player,
                                            pending_count,
                                            stack_count_local,
                                            has_concrete_resolution_selection,
                                        )
                                    )
                                    _clear_pending_select_n()
                                return
                            selected = 0
                            selected_ids: list[int] = []
                            used_hover_ids: set[int] = set()
                            base_clicks = 2 if resolution_context else 1
                            clicks = base_clicks if attempt == 1 else 2
                            ids_to_select = list(ids)
                            stack_targets_to_select = list(stack_selection_targets)
                            if use_stack_selection and not use_hand_selection:
                                active_prompt_ids = _current_prompt_ids()
                                stack_targets_to_select = [
                                    target
                                    for target in stack_targets_to_select
                                    if target.get("prompt_id") in active_prompt_ids
                                ]
                                ids_to_select = [target.get("prompt_id") for target in stack_targets_to_select]
                                if not stack_targets_to_select:
                                    bot_logger.log_info(
                                        "SelectN stack/pending prompt no longer active; aborting stale selection."
                                    )
                                    _clear_pending_select_n()
                                    return
                            for idx, card_id in enumerate(ids_to_select):
                                if not _select_n_valid():
                                    bot_logger.log_info(
                                        "SelectN selection canceled mid-loop: prompt token no longer valid."
                                    )
                                    return
                                if selected >= min_sel:
                                    break
                                selected_ok = False
                                if use_hand_selection:
                                    selected_ok = self.select_hand_card(card_id, clicks=clicks)
                                    if not selected_ok and discard_context:
                                        for y_offset in (-120, -200):
                                            selected_ok = self.select_hand_card_offset(
                                                card_id, clicks=clicks, y_offset=y_offset
                                            )
                                            if selected_ok:
                                                break
                                elif use_stack_selection:
                                    if card_id not in _current_prompt_ids():
                                        bot_logger.log_info(
                                            f"SelectN skipping stale prompt id={card_id} before stack click."
                                        )
                                        continue
                                    hover_id = card_id
                                    if idx < len(stack_targets_to_select):
                                        hover_id = int(stack_targets_to_select[idx].get("hover_id") or card_id)
                                    if hover_id in used_hover_ids:
                                        bot_logger.log_info(
                                            f"SelectN skipping duplicate stack hover target id={hover_id} for prompt id={card_id}."
                                        )
                                        continue
                                    selected_ok = self.select_stack_item(hover_id, clicks=1)
                                    if selected_ok:
                                        used_hover_ids.add(hover_id)
                                elif use_battlefield_selection:
                                    selected_ok = self.select_battlefield_permanent(card_id, clicks=1)
                                if selected_ok:
                                    if not _select_n_valid():
                                        bot_logger.log_info(
                                            "SelectN selection canceled after click: prompt token no longer valid."
                                        )
                                        return
                                    selected += 1
                                    selected_ids.append(card_id)
                                    time.sleep(0.3)
                            if not selected_ids:
                                bot_logger.log_error("SelectN failed to select any cards")
                                _clear_pending_select_n()
                                return
                            if not _select_n_valid():
                                bot_logger.log_info(
                                    "SelectN submit canceled: prompt token no longer valid."
                                )
                                return
                            time.sleep(0.8)
                            bot_logger.log_info("SelectN submitting selection.")
                            self.submit_selection(reason="select_n_initial_submit", force=True)
                            if self.__pending_select_n is not None:
                                self.__pending_select_n["ts"] = time.time()
                            # If the submit click doesn't register, retry submit without reselecting.
                            def _retry_submit_only(retry_idx: int) -> None:
                                if not _select_n_valid():
                                    return
                                if discard_context and retry_idx > 1:
                                    return
                                if retry_idx > 2:
                                    return
                                if self.__pending_select_n is None:
                                    return
                                try:
                                    turn_info = self.updated_game_state.get_turn_info() or {}
                                    decision_player = turn_info.get("decisionPlayer")
                                except Exception:
                                    decision_player = None
                                pending_count = self.updated_game_state.get_pending_message_count()
                                if (
                                    self.__system_seat_id is not None
                                    and decision_player is not None
                                    and decision_player != self.__system_seat_id
                                ) or pending_count > 0:
                                    return
                                if self.submit_selection(
                                    reason=f"select_n_retry_submit_{retry_idx}",
                                    force=True,
                                ):
                                    if self.__pending_select_n is not None:
                                        self.__pending_select_n["ts"] = time.time()
                                threading.Timer(1.2, _retry_submit_only, args=(retry_idx + 1,)).start()

                            threading.Timer(1.2, _retry_submit_only, args=(1,)).start()
                            threading.Timer(1.2, _verify_selection, args=(selected_ids, attempt)).start()
                        except Exception as e:
                            bot_logger.log_error(f"SelectN selection failed: {e}")
                            _clear_pending_select_n("SelectN failed: clearing pending selection.")

                    threading.Timer(delay, _do_selection).start()

                delay = 0.6
                if not ids_in_hand:
                    delay = 1.0
                _attempt_selection(1, delay=delay)
        except Exception as e:
            bot_logger.log_error(f"Failed to handle SelectNReq: {e}")

    def __handle_pay_costs_req(self, line: str) -> None:
        try:
            start = line.find("{")
            if start == -1:
                threading.Timer(0.6, lambda: self.submit_selection(reason="pay_costs_no_payload", force=True)).start()
                return
            payload = json.loads(line[start:])
            messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
            handled_selection = False

            for message in messages:
                if message.get("type") != "GREMessageType_PayCostsReq":
                    continue
                seat_ids = message.get("systemSeatIds") or []
                if self.__system_seat_id is not None and seat_ids and self.__system_seat_id not in seat_ids:
                    continue

                pay_req = message.get("payCostsReq", {}) or {}
                effect_cost = pay_req.get("effectCostReq", {}) or {}
                cost_sel = effect_cost.get("costSelection", {}) or {}
                ids = list(cost_sel.get("ids", []) or [])
                min_sel = int(cost_sel.get("minSel", 0) or 0)
                max_sel = int(cost_sel.get("maxSel", 0) or 0)

                if not ids or min_sel <= 0:
                    continue

                handled_selection = True
                bot_logger.log_info(
                    f"PayCostsReq selection detected: minSel={min_sel} maxSel={max_sel} ids={ids}"
                )

                hand_zone = None
                try:
                    if self.__system_seat_id is not None:
                        hand_zone = self.updated_game_state.get_zone("ZoneType_Hand", self.__system_seat_id)
                except Exception:
                    hand_zone = None
                hand_ids = set(hand_zone.get("objectInstanceIds", []) or []) if hand_zone else set()

                # Classify each candidate by zone so we click the right region.
                # A "sacrifice a creature" cost lists battlefield permanents, not
                # hand cards -- clicking the hand region for those silently fails
                # and stalls the cast (the original bug with Arbiter of Woe).
                game_objects = self.updated_game_state.get_game_objects() or []
                obj_by_id = {
                    o.get("instanceId"): o for o in game_objects if isinstance(o, dict)
                }
                bf_zone_ids = RemovalLogic.battlefield_zone_ids(self.updated_game_state.get_full_state())

                def _on_our_battlefield(cid: int) -> bool:
                    obj = obj_by_id.get(cid)
                    return (
                        bool(obj)
                        and obj.get("zoneId") in bf_zone_ids
                        and obj.get("controllerSeatId") == self.__system_seat_id
                    )

                hand_candidates = [cid for cid in ids if cid in hand_ids]
                bf_candidates = [cid for cid in ids if _on_our_battlefield(cid)]

                # Protected creatures last (never sacrifice unless it's the only
                # legal choice -- e.g. Perforating Artist to Vampire Gourmand), then
                # sacrifice fodder first (creatures whose death is beneficial or who
                # recur -- Infestation Sage, Reassembling Skeleton, Infernal Vessel),
                # then the least valuable creature (keep our bombs).
                def _sac_value(cid: int):
                    obj = obj_by_id.get(cid) or {}
                    protected = 1 if RemovalLogic.is_protected_from_sacrifice(obj.get("grpId")) else 0
                    fodder = 0 if RemovalLogic.is_sacrifice_fodder(obj.get("grpId")) else 1
                    return (
                        protected,
                        fodder,
                        RemovalLogic.effective_toughness(obj) + RemovalLogic._stat(obj.get("power")),
                        RemovalLogic._stat(obj.get("power")),
                    )

                bf_candidates.sort(key=_sac_value)

                need = max(min_sel, 1)
                if bf_candidates:
                    chosen_ids, click_kind = bf_candidates[:need], "battlefield"
                elif hand_candidates:
                    chosen_ids, click_kind = hand_candidates[:need], "hand"
                else:
                    chosen_ids, click_kind = ids[:need], "unknown"

                bot_logger.log_info(
                    f"PayCostsReq paying via {click_kind}: {chosen_ids} (need {need} of {ids})"
                )

                def _do_cost_selection(card_ids: list[int], kind: str) -> None:
                    try:
                        if self._suppress_selections or self._stop_requested:
                            return
                        for cid in card_ids:
                            selected = False
                            if kind == "hand":
                                selected = self.select_hand_card(cid, clicks=1)
                                if not selected:
                                    selected = self.select_hand_card_offset(cid, clicks=1, y_offset=-120)
                                if not selected:
                                    selected = self.select_hand_card_offset(cid, clicks=1, y_offset=-200)
                            elif kind == "battlefield":
                                selected = self.select_battlefield_permanent(cid, clicks=1)
                            else:
                                # Zone unknown: try battlefield then hand.
                                selected = self.select_battlefield_permanent(cid, clicks=1)
                                if not selected:
                                    selected = self.select_hand_card(cid, clicks=1)
                            if not selected:
                                bot_logger.log_error(
                                    f"PayCostsReq selection failed for id={cid} (kind={kind})."
                                )
                            time.sleep(0.25)
                        time.sleep(0.2)
                        self.submit_selection(reason="pay_costs_selection_submit", force=True)
                    except Exception as e:
                        bot_logger.log_error(f"PayCostsReq selection execution failed: {e}")

                threading.Timer(0.6, _do_cost_selection, args=(chosen_ids, click_kind)).start()
                break

            if not handled_selection:
                threading.Timer(0.6, lambda: self.submit_selection(reason="pay_costs_auto_submit", force=True)).start()
        except Exception as e:
            bot_logger.log_error(f"Failed to handle PayCostsReq: {e}")
            threading.Timer(0.6, lambda: self.submit_selection(reason="pay_costs_error_fallback", force=True)).start()

    # Modal "Choose one" spells whose SECOND (right) plate is the mode we want.
    # Valorous Stance (all printings): always take "Destroy target creature with
    # toughness 4 or greater", never the indestructible mode.
    _MODAL_PICK_SECOND_GRPIDS = {72198, 78825, 93566, 94011, 98299}

    def __handle_casting_time_options_req(self, line: str) -> None:
        # Kicker & friends: after clicking a card with optional casting-time
        # costs, MTGA blocks the cast behind a mid-screen "Choose One" dialog
        # (plain version on the left, kicked version to its right). Without a
        # response the bot idles until the priority timer force-resolves it.
        # Simple policy for now: always pick the plain (non-kicked) version.
        try:
            if self._suppress_selections or self._stop_requested:
                bot_logger.log_info("CastingTimeOptionsReq ignored: selections suppressed or stop requested.")
                return
            now = time.time()
            if now - self.__last_casting_time_options_ts < 2.0:
                bot_logger.log_info("CastingTimeOptionsReq ignored: duplicate within 2s window.")
                return

            # Best-effort parse: a truncated/malformed line must not prevent
            # the click — the dialog is blocking either way.
            payload = {}
            try:
                start = line.find("{")
                if start != -1:
                    payload = json.loads(line[start:])
            except Exception as e:
                bot_logger.log_info(f"CastingTimeOptionsReq payload unparsable ({e}); clicking anyway.")
            messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
            option_summaries = []
            is_choose_or_cost = False
            prefer_second_modal = False
            saw_req = False
            saw_our_req = False
            for message in messages:
                if message.get("type") != "GREMessageType_CastingTimeOptionsReq":
                    continue
                saw_req = True
                seat_ids = message.get("systemSeatIds") or []
                if self.__system_seat_id is not None and seat_ids and self.__system_seat_id not in seat_ids:
                    bot_logger.log_info(f"CastingTimeOptionsReq for other seat {seat_ids}; skipping message.")
                    continue
                saw_our_req = True
                req = message.get("castingTimeOptionsReq", {}) or {}
                for option in req.get("castingTimeOptionReq", []) or []:
                    otype = option.get("castingTimeOptionType")
                    if otype == "CastingTimeOptionType_ChooseOrCost":
                        # "Choose an additional cost" -- e.g. Eaten Alive's
                        # "sacrifice a creature or pay {3}{B}". We prefer to
                        # sacrifice (saves mana; fodder creatures are expendable).
                        is_choose_or_cost = True
                    # Modal "Choose one" spells whose SECOND (right) mode is the one
                    # we want -- Valorous Stance: pick "Destroy target creature with
                    # toughness 4+", never "gains indestructible" (which would buff
                    # the enemy). Keyed on grpId.
                    if otype == "CastingTimeOptionType_Modal":
                        try:
                            if int(option.get("grpId") or 0) in self._MODAL_PICK_SECOND_GRPIDS:
                                prefer_second_modal = True
                        except (TypeError, ValueError):
                            pass
                    option_summaries.append(
                        "type={} ctoId={} grpId={} required={}".format(
                            option.get("castingTimeOptionType"),
                            option.get("ctoId"),
                            option.get("grpId"),
                            option.get("isRequired", False),
                        )
                    )
            if saw_req and not saw_our_req:
                # Parsed fine, but every CastingTimeOptionsReq message was for
                # another seat — nothing for us to answer.
                return
            # "Choose an additional cost" (sacrifice-or-pay) renders as two
            # bottom-right buttons (Pay on top, "Sacrifice a creature" below);
            # Kicker-style options render as mid-screen plates. Pick accordingly.
            if is_choose_or_cost:
                label = "CASTING_TIME_OPTION_SACRIFICE"
                base_point = (1775, 978)  # bottom-right "Sacrifice a creature" button
                choice_desc = "sacrifice-a-creature (bottom button)"
            elif prefer_second_modal:
                # Two mode plates side by side (indestructible left, destroy right).
                # Click the RIGHT plate (the "destroy" mode). Measured centre in the
                # 1920x1080 arena frame; mirrors the left plate at (750, 505).
                label = "CASTING_TIME_OPTION_MODAL_SECOND"
                base_point = (1185, 505)
                choice_desc = "second/right modal option (e.g. Valorous Stance destroy)"
            else:
                label = "CASTING_TIME_OPTION_PLAIN"
                base_point = (750, 505)   # leftmost (plain, non-kicked) plate
                choice_desc = "plain (non-kicked) version"
            bot_logger.log_info(
                "CASTING_TIME_OPTIONS detected: options=[{}] — choosing {}.".format(
                    "; ".join(option_summaries) or "unparsed", choice_desc
                )
            )

            # Snapshot the turn state so retries stop once the dialog resolves --
            # avoids clicking the board after it closes (like the modal handler).
            snap_ti = {}
            try:
                snap_ti = dict(self.updated_game_state.get_turn_info() or {})
            except Exception:
                snap_ti = {}
            snap_key = (
                snap_ti.get("turnNumber"), snap_ti.get("phase"),
                snap_ti.get("step"), snap_ti.get("decisionPlayer"),
            )

            # PayCostsReq marker at the moment we schedule the click: if picking
            # "Sacrifice a creature" immediately opens the cost-selection picker
            # (a PayCostsReq newer than this timestamp), the ChooseOrCost dialog
            # is gone and any retry click must not hit the button again -- the
            # picker sits at the same bottom-right screen area and a stray click
            # there could cancel the cost selection instead of confirming it.
            click_scheduled_ts = now

            def _dialog_still_open() -> bool:
                if self.__pending_pay_costs_ts > click_scheduled_ts:
                    return False
                try:
                    ti = self.updated_game_state.get_turn_info() or {}
                except Exception:
                    return True
                return (
                    ti.get("turnNumber"), ti.get("phase"),
                    ti.get("step"), ti.get("decisionPlayer"),
                ) == snap_key

            def _click_option(attempt: int = 0) -> None:
                try:
                    if self._suppress_selections or self._stop_requested:
                        return
                    if attempt > 0 and not _dialog_still_open():
                        bot_logger.log_info(
                            f"CASTING_TIME_OPTION: dialog resolved before retry {attempt}; stopping."
                        )
                        return
                    target, source = self._map_abs_point_to_arena(base_point, label=label)
                    bot_logger.log_info(
                        f"CASTING_TIME_OPTION click (attempt {attempt}): base={base_point} "
                        f"target={target} source={source}"
                    )
                    bot_logger.log_click(target[0], target[1], label)
                    self.input.move_abs(target[0], target[1])
                    time.sleep(0.4)
                    self.input.left_click(1)
                    # Only the ChooseOrCost button gets retries -- the Kicker plate
                    # sits mid-screen and a stray click there could hit the board.
                    if is_choose_or_cost and attempt < 2:
                        threading.Timer(1.4, lambda: _click_option(attempt + 1)).start()
                except Exception as e:
                    bot_logger.log_error(f"CastingTimeOptionsReq click execution failed: {e}")

            # Stamp the dedupe window only once a click is actually scheduled,
            # so a no-click exit above never blocks a legitimate follow-up.
            self.__last_casting_time_options_ts = now
            # Give the overlay a moment to finish animating in.
            threading.Timer(1.0, lambda: _click_option(0)).start()
        except Exception as e:
            bot_logger.log_error(f"Failed to handle CastingTimeOptionsReq: {e}")

    def __handle_select_targets_req(self, line: str) -> None:
        try:
            if self._suppress_selections or self._stop_requested:
                bot_logger.log_info("SelectTargets ignored: selections suppressed or stop requested.")
                return
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
                req = message.get("selectTargetsReq", {}) or {}
                source_id = req.get("sourceId")
                self.__update_pending_target_select(source_id)
                # Two-target pump-fight spells (e.g. Felling Blow) need both a
                # friendly and an enemy creature picked -- handle before the
                # single-target paths.
                if self.__try_handle_fight_targets(req):
                    return
                chooser_pick = self.__pick_chooser_target(req)
                if chooser_pick is not None:
                    chooser_target, is_stack_target = chooser_pick
                    self.__schedule_chooser_target_selection(
                        source_id, chooser_target, reason="SelectTargetsReq(chooser)",
                        is_stack_target=is_stack_target,
                    )
                else:
                    opp_creatures, own_creatures, face_legal = self.__analyze_legal_targets(req)
                    self.__schedule_target_selection(
                        source_id,
                        reason="SelectTargetsReq",
                        legal_creature_ids=opp_creatures,
                        face_legal=face_legal,
                        own_creature_ids=own_creatures,
                    )
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

    @staticmethod
    def __timer_elapsed_remaining(timer: dict) -> tuple[float | None, float | None]:
        duration = timer.get("durationSec")
        duration_sec = None
        if duration is not None:
            try:
                duration_sec = float(duration)
            except Exception:
                duration_sec = None
        elapsed_sec = None
        if "elapsedSec" in timer:
            try:
                elapsed_sec = float(timer.get("elapsedSec", 0) or 0)
            except Exception:
                elapsed_sec = None
        elif "elapsedMs" in timer:
            try:
                elapsed_sec = float(timer.get("elapsedMs", 0) or 0) / 1000.0
            except Exception:
                elapsed_sec = None
        remaining_sec = None
        if duration_sec is not None and elapsed_sec is not None:
            remaining_sec = max(0.0, duration_sec - elapsed_sec)
        return elapsed_sec, remaining_sec

    def __log_my_timer_status(self) -> None:
        if self.__system_seat_id is None:
            return
        try:
            full_state = self.updated_game_state.get_full_state()
        except Exception:
            return
        players = full_state.get("players", []) or []
        my_timer_ids: set[int] = set()
        for player in players:
            if player.get("systemSeatNumber") != self.__system_seat_id:
                continue
            raw_ids = player.get("timerIds", []) or []
            for raw_id in raw_ids:
                if isinstance(raw_id, int):
                    my_timer_ids.add(raw_id)
            break
        if not my_timer_ids:
            return
        timers = full_state.get("timers", []) or []
        current_running: set[int] = set()
        seen_timer_ids: set[int] = set()
        for timer in timers:
            timer_id = timer.get("timerId")
            if not isinstance(timer_id, int) or timer_id not in my_timer_ids:
                continue
            timer_type = str(timer.get("type") or "?")
            if timer_type not in _MY_TIMER_TYPES:
                continue
            seen_timer_ids.add(timer_id)
            running = bool(timer.get("running", False))
            elapsed_sec, remaining_sec = self.__timer_elapsed_remaining(timer)
            duration_sec = timer.get("durationSec")
            if duration_sec is not None:
                try:
                    duration_sec = float(duration_sec)
                except Exception:
                    duration_sec = None
            warning_threshold = timer.get("warningThresholdSec")
            warning_sec = None
            if warning_threshold is not None:
                try:
                    warning_sec = float(warning_threshold)
                except Exception:
                    warning_sec = None

            prev = self.__my_timer_state.get(timer_id, {})
            was_running = bool(prev.get("running", False))
            warned = bool(prev.get("warned", False))
            critical = bool(prev.get("critical", False))
            prev_elapsed = prev.get("elapsed_sec")
            prev_remaining = prev.get("remaining_sec")
            prev_duration = prev.get("duration_sec")
            if elapsed_sec is None and prev_elapsed is not None:
                try:
                    elapsed_sec = float(prev_elapsed)
                except Exception:
                    pass
            if remaining_sec is None and prev_remaining is not None:
                try:
                    remaining_sec = float(prev_remaining)
                except Exception:
                    pass
            if duration_sec is None and prev_duration is not None:
                try:
                    duration_sec = float(prev_duration)
                except Exception:
                    pass
            # If the same timer ID was reused (elapsed reset significantly), treat as a fresh start.
            if (
                running
                and was_running
                and elapsed_sec is not None
                and prev_elapsed is not None
                and elapsed_sec < 5.0
                and prev_elapsed > 10.0
            ):
                was_running = False
                warned = False
                critical = False
            if running:
                current_running.add(timer_id)
                if not was_running:
                    msg = f"MY_TIMER_START: timerId={timer_id} type={timer_type}"
                    if elapsed_sec is not None:
                        msg += f" elapsed={elapsed_sec:.1f}s"
                    if remaining_sec is not None:
                        msg += f" remaining={remaining_sec:.1f}s"
                    bot_logger.log_info(msg)
                    warned = False
                    critical = False
                if (
                    remaining_sec is not None
                    and warning_sec is not None
                    and warning_sec > 0
                    and remaining_sec <= warning_sec
                    and not warned
                ):
                    bot_logger.log_info(
                        f"MY_TIMER_WARNING: timerId={timer_id} type={timer_type} remaining={remaining_sec:.1f}s threshold={warning_sec:.1f}s"
                    )
                    warned = True
                if (
                    timer_type == "TimerType_Inactivity"
                    and not was_running
                    and remaining_sec is not None
                    and remaining_sec > self.__emergency_concede_threshold_sec
                    and not self._stop_requested
                ):
                    delay = remaining_sec - self.__emergency_concede_threshold_sec
                    self.__schedule_emergency_concede(timer_id, remaining_sec, delay)
                if remaining_sec is not None and remaining_sec <= 5.0 and not critical:
                    bot_logger.log_info(
                        f"MY_TIMER_CRITICAL: timerId={timer_id} type={timer_type} remaining={remaining_sec:.1f}s"
                    )
                    if timer_type == "TimerType_Inactivity":
                        runtime_status.bump_counter(
                            "my_timer_critical_count",
                            1,
                            my_timer_running=True,
                            my_timer_type=timer_type,
                            my_timer_remaining_sec=remaining_sec,
                            my_timer_last_critical_at_epoch=time.time(),
                        )
                    # Note: no concede on TimerType_ActivePlayer. That timer is
                    # MTGA's rope (TimerBehavior_TakeControl) — on expiry the
                    # client just auto-passes; the game is NOT lost. Conceding
                    # here threw away winnable games (and even fired during the
                    # next game's mulligan when GRE replayed the old timer
                    # snapshot). Loss protection is handled exclusively by the
                    # TimerType_Inactivity emergency-concede scheduling above.
                    critical = True
                if remaining_sec is not None and remaining_sec > 5.0:
                    critical = False
                self.__my_timer_state[timer_id] = {
                    "running": True,
                    "warned": warned,
                    "critical": critical,
                    "timeout_seen": bool(prev.get("timeout_seen", False)),
                    "type": timer_type,
                    "elapsed_sec": elapsed_sec,
                    "remaining_sec": remaining_sec,
                    "duration_sec": duration_sec,
                }
                timeout_seen = bool(prev.get("timeout_seen", False))
                if (
                    timer_type == "TimerType_Inactivity"
                    and elapsed_sec is not None
                    and duration_sec is not None
                    and elapsed_sec >= max(1.0, duration_sec - 0.5)
                    and not timeout_seen
                ):
                    bot_logger.log_info(
                        f"MY_TIMER_TIMEOUT_OBSERVED: timerId={timer_id} type={timer_type} elapsed={elapsed_sec:.1f}s duration={duration_sec:.1f}s"
                    )
                    runtime_status.update_status(
                        my_timer_timeout_seen=True,
                        my_timer_timeout_at_epoch=time.time(),
                    )
                    timeout_seen = True
                    self.__my_timer_state[timer_id]["timeout_seen"] = True
                runtime_status.update_status(
                    my_timer_running=True,
                    my_timer_type=timer_type,
                    my_timer_remaining_sec=remaining_sec,
                    my_timer_elapsed_sec=elapsed_sec,
                    my_timer_duration_sec=duration_sec,
                )
            else:
                if was_running:
                    bot_logger.log_info(f"MY_TIMER_STOP: timerId={timer_id} type={timer_type}")
                    if timer_type == "TimerType_Inactivity":
                        self.__cancel_emergency_concede_timer(f"timer {timer_id} stopped")
                self.__my_timer_state[timer_id] = {
                    "running": False,
                    "warned": False,
                    "critical": False,
                    "timeout_seen": bool(prev.get("timeout_seen", False)),
                    "type": timer_type,
                    "elapsed_sec": elapsed_sec,
                    "remaining_sec": remaining_sec,
                    "duration_sec": duration_sec,
                }
                runtime_status.update_status(
                    my_timer_running=False,
                    my_timer_type=timer_type,
                    my_timer_remaining_sec=remaining_sec,
                    my_timer_elapsed_sec=elapsed_sec,
                    my_timer_duration_sec=duration_sec,
                )
        for timer_id, prev in list(self.__my_timer_state.items()):
            if not prev.get("running", False):
                continue
            if timer_id in current_running:
                continue
            if timer_id in seen_timer_ids:
                continue
            timer_type = prev.get("type", "?")
            bot_logger.log_info(f"MY_TIMER_STOP: timerId={timer_id} type={timer_type} (not running)")
            if timer_type == "TimerType_Inactivity":
                self.__cancel_emergency_concede_timer(f"timer {timer_id} disappeared from timer state")
            self.__my_timer_state[timer_id] = {
                "running": False,
                "warned": False,
                "critical": False,
                "timeout_seen": bool(prev.get("timeout_seen", False)),
                "type": timer_type,
                "elapsed_sec": None,
                "remaining_sec": None,
                "duration_sec": None,
            }
            runtime_status.update_status(
                my_timer_running=False,
                my_timer_type=timer_type,
                my_timer_remaining_sec=None,
                my_timer_elapsed_sec=None,
                my_timer_duration_sec=None,
            )

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
            self.__target_select_token_counter += 1
            pending = {
                "source_id": source_id,
                "token": self.__target_select_token_counter,
            }
        elif "token" not in pending:
            self.__target_select_token_counter += 1
            pending["token"] = self.__target_select_token_counter
        pending["ts"] = time.time()
        if min_t is not _TARGET_FIELD_UNSET:
            pending["min"] = min_t
        if max_t is not _TARGET_FIELD_UNSET:
            pending["max"] = max_t
        if selected is not _TARGET_FIELD_UNSET:
            pending["selected"] = selected
        self.__pending_target_select = pending

    def __get_effective_decision_delay(self) -> float:
        delay = max(0.0, float(self.__decision_delay or 0.0))
        # During the OPPONENT's turn we only ever pass priority (the bot does not
        # cast reactively; blocking is driven by explicit SelectTargets/DeclareBlock
        # prompts, not by this settle timer). Applying the full 4s delay to every
        # priority window of the opponent's turn stacks into ~20s of apparent freeze
        # across a single combat — which reads as "stuck" and pushed users to press
        # Resolve manually. Pass quickly on the opponent's turn instead.
        try:
            ti = self.updated_game_state.get_turn_info() or {}
            my_seat = self.__system_seat_id
            active_player = ti.get("activePlayer")
            if (
                my_seat is not None
                and active_player is not None
                and int(active_player) != int(my_seat)
            ):
                delay = min(delay, 0.8)
        except Exception:
            pass
        lowest_remaining = None
        for timer_state in self.__my_timer_state.values():
            if not timer_state.get("running", False):
                continue
            if str(timer_state.get("type") or "") != "TimerType_Inactivity":
                continue
            remaining = timer_state.get("remaining_sec")
            if remaining is None:
                continue
            try:
                remaining_value = float(remaining)
            except Exception:
                continue
            if lowest_remaining is None or remaining_value < lowest_remaining:
                lowest_remaining = remaining_value
        if lowest_remaining is None:
            return delay
        if lowest_remaining <= 6.0:
            bot_logger.log_info(
                f"Decision delay bypassed: inactivity rope remaining={lowest_remaining:.1f}s"
            )
            return 0.0
        safe_delay = max(0.0, lowest_remaining - 2.5)
        if safe_delay < delay:
            bot_logger.log_info(
                "Decision delay clamped: requested={}s effective={:.1f}s inactivity_remaining={:.1f}s".format(
                    delay,
                    safe_delay,
                    lowest_remaining,
                )
            )
        return min(delay, safe_delay)

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
        # Small fan of offsets around the calibrated avatar position. Used for
        # cases where calibration is just slightly off.
        return [
            (0, 0),
            (-80, 0),
            (-120, 10),
            (-60, 25),
            (0, 35),
            (-90, 45),
        ]

    def __get_avatar_retry_points(self) -> list[tuple[int, int, str]]:
        # Primary: arena-relative geometric position. The opponent avatar in
        # MTGA sits at ~50% of arena width, ~10% from the top — this works
        # regardless of windowed-mode position because we recompute relative
        # to the detected arena rect. Fallbacks fan out around that anchor,
        # then fall back to the calibrated config point as last resort.
        ordered: list[tuple[int, int, str]] = []
        arena = self._arena_region
        if arena is not None:
            try:
                ax, ay, aw, ah = (int(arena[0]), int(arena[1]), int(arena[2]), int(arena[3]))
                grid_specs = [
                    (0.50, 0.10),  # Primary: top-center.
                    (0.42, 0.10), (0.58, 0.10),
                    (0.50, 0.16), (0.50, 0.22),
                    # Mid-screen chooser plates: when the legal targets are two
                    # similar entities (both players, or player vs planeswalker)
                    # MTGA shows a pick-a-plate dialog near screen center and
                    # avatar clicks assign nothing (log evidence: req with
                    # targets [player1, player2] ignored 9 avatar-area clicks).
                    (0.42, 0.46), (0.58, 0.46), (0.50, 0.52),
                    (0.35, 0.10), (0.65, 0.10),
                    (0.42, 0.16), (0.58, 0.16),
                    (0.35, 0.16), (0.65, 0.16),
                    (0.42, 0.22), (0.58, 0.22),
                    (0.35, 0.22), (0.65, 0.22),
                ]
                for rx, ry in grid_specs:
                    px = ax + int(aw * rx)
                    py = ay + int(ah * ry)
                    ordered.append((px, py, f"arena_grid(rx={rx:.2f},ry={ry:.2f})"))
            except Exception:
                pass

        # Fallback to the calibrated point only if arena detection failed or
        # as a last resort after the geometric grid is exhausted.
        try:
            base_target, _ = self._resolve_opponent_avatar_base(force_reacquire=True)
            bx, by = int(base_target[0]), int(base_target[1])
            ordered.append((bx, by, "calibrated_base"))
            for dx, dy in self.__get_target_click_offsets():
                if dx == 0 and dy == 0:
                    continue
                ordered.append((bx + dx, by + dy, f"calibrated_offset({dx},{dy})"))
        except Exception:
            pass

        # De-duplicate while preserving order.
        seen: set[tuple[int, int]] = set()
        unique: list[tuple[int, int, str]] = []
        for x, y, label in ordered:
            key = (x, y)
            if key in seen:
                continue
            seen.add(key)
            unique.append((x, y, label))
        return unique

    def __click_opponent_avatar_at_screen(self, x: int, y: int, label: str, tag: str, *, fast: bool = False) -> None:
        bot_logger.log_info(
            "OPPONENT_AVATAR click: arena={} raw_base={} target=({}, {}) source={} tag={}".format(
                self._arena_region,
                self.opponent_avatar_coors,
                x,
                y,
                label,
                tag,
            )
        )
        bot_logger.log_click(x, y, tag)
        self.input.move_abs(x, y)
        time.sleep(0.15 if fast else 0.4)
        self.input.left_click(1)
        time.sleep(0.1 if fast else 0.3)

    def __click_opponent_avatar_with_offset(self, offset: tuple[int, int], tag: str) -> None:
        base_target, source = self._resolve_opponent_avatar_base(force_reacquire=True)
        x = int(base_target[0] + offset[0])
        y = int(base_target[1] + offset[1])
        self.__click_opponent_avatar_at_screen(x, y, f"{source}+offset{offset}", tag)

    def __describe_instance(self, instance_id) -> str:
        # Compact description for log lines: "Creature/seat2", "player", ...
        try:
            for obj in self.updated_game_state.get_game_objects() or []:
                if obj.get("instanceId") == instance_id:
                    types = obj.get("cardTypes") or []
                    short = ",".join(t.replace("CardType_", "") for t in types)
                    return f"{short or obj.get('type', '?')}/seat{obj.get('controllerSeatId')}"
        except Exception:
            pass
        try:
            for player in self.updated_game_state.get_players() or []:
                if player.get("systemSeatNumber") == instance_id:
                    return "player"
        except Exception:
            pass
        return "unknown"

    def __write_target_debug_bundle(self, reason: str) -> None:
        # Screenshot + state dump so unresolved target prompts show us the
        # actual UI (e.g. a mid-screen chooser) instead of guessing from logs.
        try:
            current_pos = self.input.position()
            self._write_hand_select_debug_bundle(
                reason=reason,
                card_id=-1,
                scan_start=(0, 0),
                scan_end=(0, 0),
                current_pos=(current_pos.x, current_pos.y),
                current_hovered_id=None,
            )
        except Exception as e:
            bot_logger.log_error(f"Target debug bundle failed: {e}")

    def __analyze_legal_targets(self, req) -> tuple[list[int], list[int], bool]:
        """From a SelectTargetsReq, classify the legal targets into
        (enemy-creature ids, friendly-creature ids, is-a-player/face-legal).

        MTGA only lists *legal* targets, so this is the ground truth for what a
        click may land on. Off-battlefield targets (creature spells on the stack)
        are already routed to the chooser before this runs, so any creature here
        is a battlefield permanent -- clickable on our or the opponent's row.
        When the face is not a legal target, clicking the avatar is illegal and
        would stall, so the caller must pick a creature (ours or theirs)."""
        opp_creatures: list[int] = []
        own_creatures: list[int] = []
        face_legal = False
        try:
            game_objects = self.updated_game_state.get_game_objects() or []
            obj_by_id = {
                o.get("instanceId"): o for o in game_objects if isinstance(o, dict)
            }
            player_seats = {
                p.get("systemSeatNumber")
                for p in (self.updated_game_state.get_players() or [])
            }
            for group in req.get("targets", []) or []:
                for tgt in group.get("targets", []) or []:
                    if tgt.get("legalAction") != "SelectAction_Select":
                        continue
                    tid = tgt.get("targetInstanceId")
                    if tid is None:
                        continue
                    obj = obj_by_id.get(tid)
                    if obj is not None and "CardType_Creature" in (obj.get("cardTypes") or []):
                        if obj.get("controllerSeatId") == self.__system_seat_id:
                            own_creatures.append(int(tid))
                        else:
                            opp_creatures.append(int(tid))
                    elif tid in player_seats and tid != self.__system_seat_id:
                        face_legal = True
                    elif obj is None:
                        # Unknown target (player/planeswalker not in objects): do
                        # not force a creature click when a face click may be valid.
                        face_legal = True
        except Exception as e:
            bot_logger.log_error(f"analyze_legal_targets failed: {e}")
        return opp_creatures, own_creatures, face_legal

    def __best_creature_among(self, candidate_ids) -> int | None:
        """Pick the enemy creature with the highest effective toughness (power as
        tiebreak) from an explicit list of legal instanceIds."""
        if not candidate_ids:
            return None
        game_objects = self.updated_game_state.get_game_objects() or []
        obj_by_id = {
            o.get("instanceId"): o for o in game_objects if isinstance(o, dict)
        }
        best_id = None
        best_key = None
        for cid in candidate_ids:
            obj = obj_by_id.get(cid)
            if obj is None:
                key = (-1, -1)
            else:
                key = (
                    RemovalLogic.effective_toughness(obj),
                    RemovalLogic._stat(obj.get("power")),
                )
            if best_key is None or key > best_key:
                best_key = key
                best_id = int(cid)
        return best_id

    def __resolve_removal_target(self, source_id):
        """Decide whether a spell/ability on the stack should target an enemy
        creature. Returns a creature instanceId, RemovalLogic.FACE_TARGET (-1)
        for lethal burn, or None when it is not creature removal (face path)."""
        try:
            if source_id is None or self.__system_seat_id is None:
                return None
            game_objects = self.updated_game_state.get_game_objects() or []
            grp_id = None
            for obj in game_objects:
                if isinstance(obj, dict) and obj.get("instanceId") == source_id:
                    grp_id = obj.get("grpId")
                    break
            if grp_id is None:
                return None
            profile = RemovalLogic.get_removal_profile(grp_id)
            if not profile:
                return None
            bf_ids = RemovalLogic.battlefield_zone_ids(self.updated_game_state.get_full_state())
            opp_life = RemovalLogic.opponent_life_from_players(
                self.updated_game_state.get_players(), self.__system_seat_id
            )
            target = RemovalLogic.choose_removal_target(
                profile,
                game_objects,
                self.__system_seat_id,
                opponent_life=opp_life,
                battlefield_zone_ids=(bf_ids or None),
            )
            bot_logger.log_info(
                f"REMOVAL resolve: source={source_id} grp={grp_id} profile={profile} "
                f"opp_life={opp_life} -> target={target}"
            )
            return target
        except Exception as e:
            bot_logger.log_error(f"Failed to resolve removal target: {e}")
            return None

    def __grp_id_for_instance(self, instance_id):
        """Map a battlefield/stack instanceId to its grpId (card id)."""
        if instance_id is None:
            return None
        for obj in (self.updated_game_state.get_game_objects() or []):
            if isinstance(obj, dict) and obj.get("instanceId") == instance_id:
                return obj.get("grpId")
        return None

    def __is_self_buff_source(self, source_id) -> bool:
        """True if the spell on the stack is a pump/protect trick to cast on our
        own creature (e.g. Fake Your Own Death). Delegates to the shared profile."""
        try:
            return RemovalLogic.is_self_buff(self.__grp_id_for_instance(source_id))
        except Exception:
            return False

    def __is_my_creature(self, instance_id) -> bool:
        """True if instance_id is a creature WE control (battlefield permanent)."""
        if instance_id is None or self.__system_seat_id is None:
            return False
        for obj in (self.updated_game_state.get_game_objects() or []):
            if isinstance(obj, dict) and obj.get("instanceId") == instance_id:
                return (
                    obj.get("controllerSeatId") == self.__system_seat_id
                    and "CardType_Creature" in (obj.get("cardTypes") or [])
                )
        return False

    def __best_own_attacker_among(self, candidate_ids) -> int | None:
        """Our creature with the highest power (attack) among the legal own
        targets, preferring one that is currently attacking. Rule for self-buff
        tricks: pump our biggest attacker."""
        if not candidate_ids:
            return None
        game_objects = self.updated_game_state.get_game_objects() or []
        obj_by_id = {
            o.get("instanceId"): o for o in game_objects if isinstance(o, dict)
        }
        best_id = None
        best_key = None
        for cid in candidate_ids:
            obj = obj_by_id.get(cid) or {}
            attacking = 1 if obj.get("attackState") == "AttackState_Attacking" else 0
            power = RemovalLogic._stat(obj.get("power"))
            key = (attacking, power)
            if best_key is None or key > best_key:
                best_key = key
                best_id = int(cid)
        return best_id

    def __schedule_creature_target_selection(self, source_id, creature_id, reason, *, friendly=False):
        """Target a creature: click it via hover-scan, then submit. Clicks our own
        battlefield row when `friendly` (a "target creature you control" spell),
        otherwise the opponent's row. Mirrors the avatar flow's pending/submit."""
        now = time.time()
        if self._suppress_selections or self._stop_requested:
            return
        self.__last_target_select_source_id = source_id if source_id is not None else -1
        self.__last_target_select_ts = now
        self.__update_pending_target_select(source_id)
        selection_token = (self.__pending_target_select or {}).get("token")
        side = "friendly" if friendly else "enemy"
        bot_logger.log_info(f"{reason}: targeting {side} creature instanceId={creature_id}")

        def _valid() -> bool:
            if self._suppress_selections or self._stop_requested:
                return False
            pending = self.__pending_target_select or {}
            return pending.get("source_id") == source_id and pending.get("token") == selection_token

        def _attempt_submit() -> None:
            if not _valid():
                return
            if self.__pending_target_ready_to_submit():
                self.__last_submit_targets_ts = time.time()
                self.submit_selection(reason="creature_target_submit")

        def _do_click(attempt: int = 0) -> None:
            if not _valid():
                return
            clicked = False
            try:
                if friendly:
                    clicked = self.select_battlefield_permanent(creature_id, clicks=1)
                else:
                    clicked = self.select_opponent_battlefield_permanent(creature_id, clicks=1)
            except Exception as e:
                bot_logger.log_error(f"Creature target click failed: {e}")
            bot_logger.log_info(
                f"CREATURE_TARGET click: id={creature_id} side={side} found={clicked} attempt={attempt}"
            )
            threading.Timer(0.5, _attempt_submit).start()
            if not clicked and attempt < 2 and _valid():
                threading.Timer(0.9, lambda: _do_click(attempt + 1)).start()

        delay_remaining = self.__get_delay_timer_remaining()
        start_delay = 0.8
        if delay_remaining > 0.05:
            start_delay = delay_remaining + 0.4
        threading.Timer(start_delay, lambda: _do_click(0)).start()

    def __try_handle_fight_targets(self, req) -> bool:
        """Handle a two-target pump-fight spell (e.g. Felling Blow): pick our
        highest-power creature (the group whose legal targets we control) and the
        best enemy creature it can then kill (the other group). Returns True if it
        scheduled the two-target selection, False to fall back to normal handling.
        """
        try:
            groups = req.get("targets", []) or []
            if len(groups) < 2 or self.__system_seat_id is None:
                return False
            source_id = req.get("sourceId")
            if source_id is None:
                return False
            game_objects = self.updated_game_state.get_game_objects() or []
            obj_by_id = {o.get("instanceId"): o for o in game_objects if isinstance(o, dict)}
            grp_id = (obj_by_id.get(source_id) or {}).get("grpId")
            profile = FightLogic.get_fight_profile(grp_id)
            if not profile:
                return False

            def _legal_ids(group):
                return [
                    t.get("targetInstanceId")
                    for t in (group.get("targets", []) or [])
                    if t.get("legalAction") == "SelectAction_Select"
                    and t.get("targetInstanceId") is not None
                ]

            our_ids = None
            enemy_ids = None
            for group in groups:
                ids = _legal_ids(group)
                if not ids:
                    continue
                controllers = {(obj_by_id.get(i) or {}).get("controllerSeatId") for i in ids}
                if controllers == {self.__system_seat_id}:
                    our_ids = ids
                else:
                    enemy_ids = ids
            if not our_ids or not enemy_ids:
                return False

            def _pow(i):
                return RemovalLogic._stat((obj_by_id.get(i) or {}).get("power"))

            def _tough(i):
                return RemovalLogic.effective_toughness(obj_by_id.get(i) or {})

            counter = int(profile.get("counter", 1))
            our_id = max(our_ids, key=_pow)
            damage = _pow(our_id) + counter
            killable = [
                i for i in enemy_ids
                if FightLogic.killable_by_damage(obj_by_id.get(i) or {}, damage)
            ]
            if not killable:
                return False
            enemy_id = max(killable, key=lambda i: (_tough(i), _pow(i)))

            bot_logger.log_info(
                f"FIGHT targets: source={source_id} grp={grp_id} our={our_id} "
                f"(dmg={damage}) enemy={enemy_id} of {enemy_ids}."
            )
            self.__schedule_fight_target_selection(source_id, our_id, enemy_id)
            return True
        except Exception as e:
            bot_logger.log_error(f"Failed to handle fight targets: {e}")
            return False

    def __schedule_fight_target_selection(self, source_id, our_id, enemy_id) -> None:
        """Two-target selection: click our creature (group 1) on our battlefield,
        then the enemy creature (group 2) on the opponent's, then submit."""
        now = time.time()
        if self._suppress_selections or self._stop_requested:
            return
        norm_source = source_id if source_id is not None else -1
        if self.__last_target_select_source_id == norm_source and now - self.__last_target_select_ts < 1.5:
            return
        self.__last_target_select_source_id = norm_source
        self.__last_target_select_ts = now
        self.__update_pending_target_select(source_id)
        bot_logger.log_info(
            f"FIGHT target flow: our creature {our_id} then enemy creature {enemy_id}."
        )

        def _flow() -> None:
            if self._suppress_selections or self._stop_requested:
                return
            try:
                ok1 = self.select_battlefield_permanent(our_id, clicks=1)
            except Exception as e:
                ok1 = False
                bot_logger.log_error(f"Fight our-creature click failed: {e}")
            bot_logger.log_info(f"FIGHT our-creature click: id={our_id} found={ok1}")
            time.sleep(0.6)
            if self._suppress_selections or self._stop_requested:
                return
            try:
                ok2 = self.select_opponent_battlefield_permanent(enemy_id, clicks=1)
            except Exception as e:
                ok2 = False
                bot_logger.log_error(f"Fight enemy-creature click failed: {e}")
            bot_logger.log_info(f"FIGHT enemy-creature click: id={enemy_id} found={ok2}")
            time.sleep(0.6)
            self.__last_submit_targets_ts = time.time()
            self.submit_selection(reason="fight_target_submit", force=True)

        delay_remaining = self.__get_delay_timer_remaining()
        start_delay = 0.8 if delay_remaining <= 0.05 else delay_remaining + 0.4
        threading.Timer(start_delay, _flow).start()

    def _get_chooser_scan_points_mapped(
        self,
        *,
        force_reacquire: bool = False,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        p1, _s1 = self._map_abs_point_to_arena(
            self.chooser_scan_p1, label="CHOOSER_SCAN_P1",
            force_reacquire=force_reacquire, apply_correction=False,
        )
        p2, _s2 = self._map_abs_point_to_arena(
            self.chooser_scan_p2, label="CHOOSER_SCAN_P2",
            force_reacquire=False, apply_correction=False,
        )
        bot_logger.log_info(
            f"CHOOSER_SCAN mapped: arena={self._arena_region} p1={p1} p2={p2}"
        )
        return p1, p2

    def select_chooser_card(self, card_id: int, clicks: int = 1) -> bool:
        """Select a card shown in a central 'choose a card' overlay (e.g. a
        creature in the graveyard for Zombify) by hover-scanning that region.
        NOTE: the region (chooser_scan_p1/p2) is a first estimate and needs
        in-game calibration."""
        bot_logger.set_hover_logging(True)
        scan_p1, scan_p2 = self._get_chooser_scan_points_mapped(force_reacquire=True)
        try:
            return self.__select_object_in_region(
                card_id=card_id, p1=scan_p1, p2=scan_p2,
                step=self.battlefield_scan_step, clicks=clicks,
                label="CHOOSER_ITEM", max_scan_sec=8.0,
            )
        finally:
            bot_logger.set_hover_logging(False)

    def __pick_chooser_target(self, req) -> tuple[int, bool] | None:
        """If a SelectTargetsReq's legal target is a card off the battlefield
        (graveyard/exile/limbo/stack), return (instanceId, is_stack_target);
        otherwise None (normal battlefield/face targeting).

        Off-battlefield targets render in two different places: a spell on the
        stack (e.g. what a counterspell targets) is shown in the stack region
        (stack_scan_p1/p2, already used by select_stack_item), while
        graveyard/exile/limbo targets render in the central chooser overlay
        (chooser_scan_p1/p2). Conflating the two routes counterspell targets to
        the wrong scan region and the click never finds the card."""
        try:
            game_objects = self.updated_game_state.get_game_objects() or []
            obj_by_id = {
                o.get("instanceId"): o for o in game_objects if isinstance(o, dict)
            }
            full_state = self.updated_game_state.get_full_state()
            bf_ids = RemovalLogic.battlefield_zone_ids(full_state)
            stack_ids = CounterLogic.stack_zone_ids(full_state)
            for group in req.get("targets", []) or []:
                for tgt in group.get("targets", []) or []:
                    if tgt.get("legalAction") != "SelectAction_Select":
                        continue
                    tid = tgt.get("targetInstanceId")
                    if tid is None:
                        continue
                    obj = obj_by_id.get(tid)
                    if obj is None:
                        continue  # e.g. a player target -> not a card overlay
                    zid = obj.get("zoneId")
                    if zid is not None and zid not in bf_ids:
                        return int(tid), (zid in stack_ids)
            return None
        except Exception as e:
            bot_logger.log_error(f"pick_chooser_target failed: {e}")
            return None

    def __schedule_chooser_target_selection(self, source_id, target_id, reason, *, is_stack_target=False):
        if self._suppress_selections or self._stop_requested:
            return
        # Dedup: both SelectTargetsReq entry points (raw pattern + game-state
        # dict) can fire for the same request. Skip a duplicate chooser click
        # for the same source within a short window (mirrors the avatar path).
        now = time.time()
        norm_source = source_id if source_id is not None else -1
        if self.__last_target_select_source_id == norm_source and now - self.__last_target_select_ts < 1.0:
            return
        self.__last_target_select_source_id = norm_source
        self.__last_target_select_ts = now
        where = "stack" if is_stack_target else "overlay"
        bot_logger.log_info(f"{reason}: choosing card instanceId={target_id} from {where}")

        def _do(attempt: int = 0) -> None:
            if self._suppress_selections or self._stop_requested:
                return
            found = False
            try:
                if is_stack_target:
                    found = self.select_stack_item(target_id, clicks=1)
                else:
                    found = self.select_chooser_card(target_id, clicks=1)
            except Exception as e:
                bot_logger.log_error(f"Chooser target click failed: {e}")
            bot_logger.log_info(
                f"CHOOSER_TARGET click: id={target_id} where={where} found={found} attempt={attempt}"
            )
            if found:
                time.sleep(0.5)
                self.submit_selection(reason="chooser_target_submit", force=True)
            elif attempt < 2 and not (self._suppress_selections or self._stop_requested):
                threading.Timer(1.0, lambda: _do(attempt + 1)).start()
            else:
                bot_logger.log_error(
                    f"CHOOSER_TARGET: giving up on id={target_id} after {attempt + 1} attempt(s); "
                    "not submitting an unresolved selection."
                )

        delay = self.__get_delay_timer_remaining()
        start_delay = 0.8 if delay <= 0.05 else delay + 0.4
        threading.Timer(start_delay, lambda: _do(0)).start()

    def __schedule_target_selection(
        self,
        source_id: int | None,
        reason: str,
        legal_creature_ids: list[int] | None = None,
        face_legal: bool | None = None,
        own_creature_ids: list[int] | None = None,
    ) -> None:
        now = time.time()
        if self._suppress_selections or self._stop_requested:
            return
        if source_id is None:
            source_id = -1
        if self.__last_target_select_source_id == source_id and now - self.__last_target_select_ts < 1.0:
            return
        removal_target = self.__resolve_removal_target(source_id)
        bot_logger.log_info(
            f"{reason}: target decision -- removal_target={removal_target} "
            f"opp_creatures={legal_creature_ids} own_creatures={own_creature_ids} face_legal={face_legal}"
        )

        # Self-target buff / combat trick (e.g. Fake Your Own Death): it must ONLY
        # ever land on a creature WE control -- never an enemy (that buffs the
        # opponent) and never the avatar. Runs first, before any removal/enemy/face
        # logic. We re-derive our creatures from ALL offered legal targets (both
        # lists) rather than trusting own_creature_ids, because the game-state
        # target path can classify them into the enemy list. If none of the legal
        # targets is ours, we refuse rather than buff an enemy.
        if self.__is_self_buff_source(source_id):
            all_legal = list(legal_creature_ids or []) + list(own_creature_ids or [])
            our_legal = [cid for cid in all_legal if self.__is_my_creature(cid)]
            buff_target = self.__best_own_attacker_among(our_legal)
            if buff_target is not None:
                bot_logger.log_info(
                    f"{reason}: self-buff spell; targeting our highest-attack creature "
                    f"{buff_target} of {our_legal}."
                )
                self.__schedule_creature_target_selection(
                    source_id, buff_target, reason, friendly=True
                )
                return
            bot_logger.log_info(
                f"{reason}: self-buff spell but no friendly creature among legal targets "
                f"{all_legal}; refusing to target an enemy."
            )
            return

        # Reconcile the profile-based result with the prompt's explicit legal
        # targets. This makes creature removal robust even when we have no oracle
        # profile for the card (missing/offline card data): if the face is not a
        # legal target, we must never click the avatar or the game stalls.
        if legal_creature_ids:
            has_creature = (
                removal_target is not None and removal_target != RemovalLogic.FACE_TARGET
            )
            if has_creature and removal_target not in legal_creature_ids:
                replacement = self.__best_creature_among(legal_creature_ids)
                if replacement is not None:
                    bot_logger.log_info(
                        f"{reason}: profile target {removal_target} not offered; "
                        f"using best legal creature {replacement} of {legal_creature_ids}."
                    )
                    removal_target = replacement
            elif not has_creature and not face_legal:
                replacement = self.__best_creature_among(legal_creature_ids)
                if replacement is not None:
                    bot_logger.log_info(
                        f"{reason}: no removal profile and face not a legal target; "
                        f"picking highest-toughness enemy creature {replacement} "
                        f"of {legal_creature_ids}."
                    )
                    removal_target = replacement

        if removal_target is not None and removal_target != RemovalLogic.FACE_TARGET:
            self.__schedule_creature_target_selection(source_id, removal_target, reason)
            return

        # No enemy-creature/face target. If the face is not legal but the spell
        # can target a creature WE control (e.g. Undying Malice), pick our best
        # creature and click our own battlefield -- never the avatar.
        if not face_legal and not legal_creature_ids and own_creature_ids:
            own_target = self.__best_creature_among(own_creature_ids)
            if own_target is not None:
                bot_logger.log_info(
                    f"{reason}: face not legal; targeting our own creature "
                    f"{own_target} of {own_creature_ids}."
                )
                self.__schedule_creature_target_selection(
                    source_id, own_target, reason, friendly=True
                )
                return
        self.__last_target_select_source_id = source_id
        self.__last_target_select_ts = now
        self.__update_pending_target_select(source_id)
        selection_token = (self.__pending_target_select or {}).get("token")
        bot_logger.log_info(f"{reason}: targeting opponent avatar")

        def _target_selection_still_valid() -> bool:
            if self._suppress_selections or self._stop_requested:
                return False
            pending = self.__pending_target_select or {}
            return pending.get("source_id") == source_id and pending.get("token") == selection_token

        def _submit_if_still_valid() -> None:
            if not _target_selection_still_valid():
                return
            self.submit_selection(reason="target_selection_submit")

        def _attempt_submit():
            if not _target_selection_still_valid():
                return False
            if self.__pending_target_ready_to_submit():
                self.__last_submit_targets_ts = time.time()
                threading.Timer(0.3, _submit_if_still_valid).start()
                return True
            return False

        retry_budget_sec = 10.0

        def _retry_if_needed():
            if not _target_selection_still_valid():
                return
            pending = self.__pending_target_select
            if not pending:
                # Submitted or cleared elsewhere — done.
                return
            age = time.time() - self.__last_target_select_ts
            if age >= retry_budget_sec:
                bot_logger.log_info(
                    "Target retry abandoned: budget elapsed ({:.1f}s) attempts={}".format(
                        age, pending.get("attempts", 0)
                    )
                )
                self.__write_target_debug_bundle("spell_target_budget_elapsed")
                return
            # Note: do NOT gate on __get_delay_timer_remaining here — that value
            # comes from the last GameStateMessage snapshot and does not tick
            # forward without a new TimerStateMessage, so it would lock out
            # retries indefinitely. The initial start_delay before _do_click
            # already covers MTGA's pre-target animation window.
            if _attempt_submit():
                return
            points = pending.get("retry_points")
            if not points:
                points = self.__get_avatar_retry_points()
                pending["retry_points"] = points
                self.__pending_target_select = pending
            attempts = int(pending.get("attempts", 0))
            if attempts >= len(points):
                bot_logger.log_info(
                    "Target retry abandoned: candidate points exhausted (attempts={})".format(attempts)
                )
                self.__write_target_debug_bundle("spell_target_points_exhausted")
                return
            x, y, label = points[attempts]
            pending["attempts"] = attempts + 1
            self.__pending_target_select = pending
            bot_logger.log_info(
                "Target still pending, retrying opponent avatar (attempt {}/{}, {})".format(
                    attempts + 1, len(points), label
                )
            )
            self.__click_opponent_avatar_at_screen(
                x, y, label, f"SELECT_OPPONENT_AVATAR_RETRY_{attempts + 1}", fast=True
            )
            threading.Timer(0.5, _attempt_submit).start()
            threading.Timer(0.7, _retry_if_needed).start()

        def _do_click():
            if not _target_selection_still_valid():
                return
            if _attempt_submit():
                return
            pending = self.__pending_target_select or {}
            points = self.__get_avatar_retry_points()
            pending["retry_points"] = points
            pending["attempts"] = 1
            self.__pending_target_select = pending
            if points:
                x, y, label = points[0]
                self.__click_opponent_avatar_at_screen(x, y, label, "SELECT_OPPONENT_AVATAR")
            else:
                self.__click_opponent_avatar_with_offset((0, 0), "SELECT_OPPONENT_AVATAR")
            threading.Timer(0.7, _attempt_submit).start()
            threading.Timer(1.0, _retry_if_needed).start()

        delay_remaining = self.__get_delay_timer_remaining()
        start_delay = 0.8
        if delay_remaining > 0.05:
            start_delay = delay_remaining + 0.4
        threading.Timer(start_delay, _do_click).start()

    def __is_selecting_targets(self) -> bool:
        try:
            has_local_target_annotation = False
            annotations = self.updated_game_state.get_annotations()
            for annotation in annotations:
                types = annotation.get("type", []) or []
                if "AnnotationType_PlayerSelectingTargets" not in types:
                    continue
                affector_id = annotation.get("affectorId")
                if self.__system_seat_id is None or affector_id is None:
                    has_local_target_annotation = True
                    break
                if affector_id == self.__system_seat_id:
                    has_local_target_annotation = True
                    break
            if not has_local_target_annotation:
                return False
            pending_message_count = self.updated_game_state.get_pending_message_count()
            last_signal_ts = max(
                float(self.__last_target_select_ts or 0.0),
                float((self.__pending_target_select or {}).get("ts", 0.0) or 0.0),
            )
            # Without an active SelectTargetsReq context the annotation cannot
            # be answered by waiting, so clear it even while another prompt
            # (e.g. DeclareBlockers) keeps pendingMessageCount above zero.
            no_select_context = self.__pending_target_select is None
            if (pending_message_count <= 0 or no_select_context) and last_signal_ts > 0.0:
                signal_age = time.time() - last_signal_ts
                if signal_age > 8.0:
                    self.__clear_pending_target_select_state(
                        "Target selection auto-clear: stale PlayerSelectingTargets annotation."
                    )
                    return False
            return True
        except Exception:
            return False

    def __clear_target_wait_if_unblocked(self) -> None:
        if self.__pending_target_select is not None:
            return
        if self.__is_selecting_targets():
            return
        runtime_status.clear_intentional_wait()

    def __clear_stale_target_wait_if_safe_pass_window(self) -> bool:
        if not self.__is_safe_stack_pass_window():
            return False
        had_pending = self.__pending_target_select is not None
        selecting = self.__is_selecting_targets()
        if not had_pending and not selecting:
            return False
        if had_pending:
            self.__pending_target_select = None
        if selecting:
            self.__purge_selecting_targets_annotations()
        runtime_status.clear_intentional_wait()
        bot_logger.log_info(
            "Target selection auto-clear: safe own pass window with pendingMessageCount=0."
        )
        return True

    def __get_action_type(self, action: dict | None) -> str | None:
        if not isinstance(action, dict):
            return None
        action_type = action.get("actionType")
        if isinstance(action_type, str):
            return action_type
        nested_action = action.get("action")
        if isinstance(nested_action, dict):
            nested_action_type = nested_action.get("actionType")
            if isinstance(nested_action_type, str):
                return nested_action_type
        return None

    def __has_available_action_type(self, action_type: str) -> bool:
        return any(
            self.__get_action_type(action) == action_type
            for action in (self.updated_game_state.get_actions() or [])
        )

    def __is_safe_stack_pass_window(self) -> bool:
        turn_info = self.updated_game_state.get_turn_info() or {}
        if not turn_info:
            return False
        if turn_info.get("phase") not in ("Phase_Main1", "Phase_Main2"):
            return False
        my_seat = self.__system_seat_id or turn_info.get("decisionPlayer")
        if my_seat is None or turn_info.get("decisionPlayer") != my_seat:
            return False
        if self.updated_game_state.get_pending_message_count() != 0:
            return False
        stack_zone = self.updated_game_state.get_zone("ZoneType_Stack")
        if not stack_zone or not (stack_zone.get("objectInstanceIds", []) or []):
            return False
        return self.__has_available_action_type("ActionType_Pass")

    def __should_pause_for_select_n(self) -> bool:
        if self._suppress_selections:
            self.__pending_select_n = None
            self.__select_n_in_progress = False
            self.__select_n_in_progress_since = 0.0
            self.__clear_target_wait_if_unblocked()
            return False
        pending_ids = set()
        stack_ids = set()
        pending_zone = self.updated_game_state.get_zone("ZoneType_Pending")
        if pending_zone:
            pending_ids = set(pending_zone.get("objectInstanceIds", []) or [])
        stack_zone = self.updated_game_state.get_zone("ZoneType_Stack")
        if stack_zone:
            stack_ids = set(stack_zone.get("objectInstanceIds", []) or [])
        active_prompt_ids = pending_ids.union(stack_ids)

        if self.__select_n_in_progress:
            pending = self.__pending_select_n or {}
            mode = pending.get("mode")
            ids = set(pending.get("ids", []) or [])
            in_progress_age = time.time() - float(self.__select_n_in_progress_since or 0.0)
            if mode == "stack" and self.__combat_recovery_key is not None:
                if self.__preempt_stack_select_n_for_combat(
                    "SelectN auto-clear: combat attackers prompt superseded stack selection."
                ):
                    return False
            if mode == "stack" and self.__is_safe_stack_pass_window():
                self.__clear_pending_select_n_state(
                    "SelectN auto-clear: safe pass window superseded stale stack selection."
                )
                return False
            # Fail-safe for stack/pending prompts: if ids vanished, unblock decisions.
            if mode == "stack" and ids and not ids.intersection(active_prompt_ids):
                if (time.time() - self.__last_submit_selection_ts) > 0.8:
                    self.__clear_pending_select_n_state("SelectN auto-clear: stack prompt resolved.")
                    return False
            # Hard timeout guard to avoid infinite selection stalls.
            if in_progress_age > 20.0:
                self.__clear_pending_select_n_state("SelectN auto-clear: in-progress timeout.")
                return False
            bot_logger.log_info("SelectN in progress: pausing other decisions.")
            return True
        if not self.__pending_select_n:
            return False
        ts = self.__pending_select_n.get("ts", 0.0)
        ids = set(self.__pending_select_n.get("ids", []) or [])
        if self.__pending_select_n.get("mode") == "stack" and self.__is_safe_stack_pass_window():
            self.__clear_pending_select_n_state(
                "SelectN auto-clear: safe pass window superseded stale stack selection."
            )
            return False
        if pending_ids and ids.intersection(pending_ids):
            bot_logger.log_info(
                f"SelectN pause reason: pending ids {sorted(ids.intersection(pending_ids))} still in pending zone."
            )
            return True
        if time.time() - ts < 3.0:
            bot_logger.log_info("SelectN pause reason: pending request younger than 3s.")
            return True
        self.__clear_pending_select_n_state("SelectN auto-clear: pending window elapsed.")
        return False

    def __clear_stale_target_wait_if_own_attack_prompt(self) -> bool:
        # A pending DeclareAttackersReq is our own prompt to answer. Stale
        # target-selection state (e.g. left over from an earlier cast) must not
        # pause the decision loop here, otherwise the AI never declares attacks
        # and only the bounded combat recovery clicks remain.
        if not self.__attack_target_prompt_active():
            return False
        signal_ts = max(
            float(self.__last_target_select_ts or 0.0),
            float((self.__pending_target_select or {}).get("ts", 0.0) or 0.0),
        )
        if signal_ts > 0.0 and time.time() - signal_ts < 8.0:
            # Fresh, genuine target selection — keep pausing.
            return False
        had_pending = self.__pending_target_select is not None
        selecting = self.__is_selecting_targets()
        if not had_pending and not selecting:
            return False
        if had_pending:
            self.__pending_target_select = None
        if selecting:
            self.__purge_selecting_targets_annotations()
        runtime_status.clear_intentional_wait()
        bot_logger.log_info(
            "Target selection auto-clear: own declare-attack prompt supersedes stale target state "
            f"(had_pending={had_pending}, selecting_annotation={selecting})."
        )
        return True

    def __should_pause_for_targets(self) -> bool:
        if self.__should_pause_for_select_n():
            return True
        if self.__clear_stale_target_wait_if_safe_pass_window():
            return False
        if self.__clear_stale_target_wait_if_own_attack_prompt():
            return False
        if self.__pending_target_select is not None:
            if self.__last_submit_targets_ts and time.time() - self.__last_submit_targets_ts < self.__target_submit_cooldown_sec:
                bot_logger.log_info("Target pause reason: submit cooldown active.")
                return True
            if self.__is_selecting_targets():
                bot_logger.log_info("Target pause reason: pending target select + selecting annotation.")
                return True
            self.__clear_pending_target_select_state("Target selection auto-clear: prompt no longer active.")
            return False
        selecting = self.__is_selecting_targets()
        if selecting:
            bot_logger.log_info("Target pause reason: PlayerSelectingTargets annotation without pending select.")
        return selecting

    def __should_pause_for_pay_costs(self) -> bool:
        if not self.__pending_pay_costs_ts:
            return False
        # Treat PayCostsReq as blocking for a short window.
        return (time.time() - self.__pending_pay_costs_ts) < 3.0

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
                    legal_desc = ", ".join(
                        f"{t.get('targetInstanceId')}:{self.__describe_instance(t.get('targetInstanceId'))}"
                        for t in (t0.get("targets") or [])
                        if t.get("targetInstanceId") is not None
                    )
                    bot_logger.log_info(
                        f"SelectTargetsReq details: sourceId={req.get('sourceId')}, min={min_t}, max={max_t}, "
                        f"selected={selected}, targetCount={len(t0.get('targets', []) or [])}, legalTargets=[{legal_desc}]"
                    )
                    self.__update_pending_target_select(
                        req.get("sourceId"),
                        min_t=min_t,
                        max_t=max_t,
                        selected=selected,
                    )
                    pending_token = (self.__pending_target_select or {}).get("token")
                    if self.__pending_target_ready_to_submit():
                        # MTGA already has enough targets selected -- e.g. a spell
                        # with a single legal target (Essence Scatter vs the one
                        # creature spell on the stack) is auto-targeted. Just
                        # confirm; clicking again could toggle the target off.
                        def _submit_if_pending_target_still_matches() -> None:
                            pending = self.__pending_target_select or {}
                            if (
                                pending.get("source_id") == (req.get("sourceId") if req.get("sourceId") is not None else -1)
                                and pending.get("token") == pending_token
                            ):
                                self.submit_selection(reason="target_selection_ready")
                        threading.Timer(0.2, _submit_if_pending_target_still_matches).start()
                        return
                source_id = message.get("selectTargetsReq", {}).get("sourceId")
                # Two-target pump-fight spells (e.g. Felling Blow): pick a friendly
                # creature and an enemy creature. Handle before the single-target
                # paths (mirrors __handle_select_targets_req).
                if self.__try_handle_fight_targets(req):
                    return
                # Off-battlefield targets (a spell on the stack for a counter, a
                # card in graveyard/exile) are shown in the stack region or a
                # central overlay, not on a battlefield row -- route them
                # accordingly. Mirrors __handle_select_targets_req so both entry
                # points behave alike.
                chooser_pick = self.__pick_chooser_target(req)
                if chooser_pick is not None:
                    chooser_target, is_stack_target = chooser_pick
                    self.__schedule_chooser_target_selection(
                        source_id, chooser_target,
                        reason="SelectTargetsReq (from game state, chooser)",
                        is_stack_target=is_stack_target,
                    )
                    return
                opp_creatures, own_creatures, face_legal = self.__analyze_legal_targets(req)
                self.__schedule_target_selection(
                    source_id,
                    reason="SelectTargetsReq (from game state)",
                    legal_creature_ids=opp_creatures,
                    face_legal=face_legal,
                    own_creature_ids=own_creatures,
                )
                return
            for message in messages:
                if message.get("type") != "GREMessageType_GameStateMessage":
                    continue
                annotations = message.get("gameStateMessage", {}).get("annotations", []) or []
                for annotation in annotations:
                    types = annotation.get("type", []) or []
                    if "AnnotationType_PlayerSubmittedTargets" not in types:
                        continue
                    affector_id = annotation.get("affectorId")
                    if affector_id is not None and affector_id != self.__system_seat_id:
                        continue
                    self.__last_submit_targets_ts = time.time()
                    self.__clear_pending_target_select_state(
                        "Target selection cleared: PlayerSubmittedTargets annotation received."
                    )
                    return
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
                    self.__clear_pending_target_select_state("SubmitTargetsResp: success")
        except Exception as e:
            bot_logger.log_error(f"Failed to handle target selection from game state: {e}")

    def __handle_declare_blockers_req(self, line: str) -> None:
        """Defending step. No blocking logic yet -> declare NO blocks by clicking
        the bottom-right combat button (which reads "No Blocks" here). Guiding
        rule: never freeze; better to lose than to stall."""
        try:
            if self._suppress_selections or self._stop_requested:
                return
            # Only act if the block decision is ours.
            start = line.find("{")
            if start != -1:
                try:
                    payload = json.loads(line[start:])
                    messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
                    ours = False
                    for message in messages:
                        if message.get("type") != "GREMessageType_DeclareBlockersReq":
                            continue
                        seat_ids = message.get("systemSeatIds") or []
                        if self.__system_seat_id is None or self.__system_seat_id in seat_ids:
                            ours = True
                    if messages and not ours:
                        return
                except Exception:
                    pass
            now = time.time()
            if now - self.__last_declare_blockers_ts < 2.0:
                return
            self.__last_declare_blockers_ts = now
            bot_logger.log_info("DeclareBlockersReq: declaring NO blocks (no blocking logic yet).")

            def _click_no_blocks() -> None:
                try:
                    if self._suppress_selections or self._stop_requested:
                        return
                    target, source = self._map_abs_point_to_arena(
                        self.main_br_button_coordinates,
                        label="NO_BLOCKS",
                        force_reacquire=True,
                        apply_correction=False,
                    )
                    if source == "absolute_no_arena":
                        bot_logger.log_error("NO_BLOCKS aborted: arena_region unavailable.")
                        return
                    bot_logger.log_click(target[0], target[1], "NO_BLOCKS")
                    self.input.move_abs(target[0], target[1])
                    self.input.left_click(1)
                    time.sleep(0.6)
                    # Second click confirms through any first-strike/submit sub-step.
                    self.input.left_click(1)
                except Exception as e:
                    bot_logger.log_error(f"NO_BLOCKS click failed: {e}")

            threading.Timer(0.8, _click_no_blocks).start()
        except Exception as e:
            bot_logger.log_error(f"Failed to handle DeclareBlockersReq: {e}")

    def __handle_declare_attackers_req(self, line: str) -> None:
        try:
            # A DeclareAttackers prompt means any prior PayCosts prompt has resolved.
            # Clear the short blocking window to avoid stalling on combat submit.
            if self.__pending_pay_costs_ts:
                self.__pending_pay_costs_ts = 0.0
                bot_logger.log_info("DeclareAttackersReq: cleared pending pay-costs pause")
            start = line.find("{")
            if start == -1:
                return
            payload = json.loads(line[start:])
            messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
            request_id = payload.get("requestId")
            for message in messages:
                if message.get("type") != "GREMessageType_DeclareAttackersReq":
                    continue
                seat_ids = message.get("systemSeatIds") or []
                if self.__system_seat_id is not None and seat_ids and self.__system_seat_id not in seat_ids:
                    continue
                # Only handle DeclareAttackersReq when we are the active player (our attack phase).
                turn_info_check = self.updated_game_state.get_turn_info() or {}
                active_player = turn_info_check.get("activePlayer")
                if active_player is not None and self.__system_seat_id is not None and active_player != self.__system_seat_id:
                    bot_logger.log_info(
                        f"DeclareAttackersReq IGNORED: activePlayer={active_player} is not us (seat={self.__system_seat_id}), skipping combat recovery."
                    )
                    continue
                req = message.get("declareAttackersReq", {})
                attackers = req.get("attackers", []) or req.get("qualifiedAttackers", [])
                self.__attack_target_required = False
                self.__attack_target_attacker_ids = [
                    attacker.get("attackerInstanceId")
                    for attacker in attackers
                    if attacker.get("attackerInstanceId") is not None
                ]
                for attacker in attackers:
                    recipients = attacker.get("legalDamageRecipients", []) or []
                    for rec in recipients:
                        if rec.get("type") == "DamageRecType_PlanesWalker":
                            self.__attack_target_required = True
                            bot_logger.log_info(
                                "DeclareAttackersReq: planeswalker target present "
                                f"(attackers={self.__attack_target_attacker_ids})"
                            )
                            break
                    if self.__attack_target_required:
                        break
                turn_info = self.updated_game_state.get_turn_info() or {}
                turn_key = "turn:{}:{}:{}".format(
                    turn_info.get("turnNumber", "?"),
                    turn_info.get("activePlayer", "?"),
                    turn_info.get("step", "?"),
                )
                if turn_key == self.__declare_attackers_turn_key:
                    self.__declare_attackers_cycle_count += 1
                else:
                    self.__declare_attackers_turn_key = turn_key
                    self.__declare_attackers_cycle_count = 1
                if self.__declare_attackers_cycle_count > self.__declare_attackers_cycle_limit:
                    bot_logger.log_error(
                        f"DeclareAttackersReq LOOP DETECTED: {self.__declare_attackers_cycle_count} cycles "
                        f"on {turn_key}, aborting attack and passing priority."
                    )
                    self.__clear_combat_recovery("DeclareAttackers loop limit reached.")
                    self.submit_selection(reason="declare_attackers_loop_break", force=True)
                    return
                fallback_key = "combat:{}:{}:{}".format(
                    turn_info.get("turnNumber", "?"),
                    turn_info.get("activePlayer", "?"),
                    turn_info.get("decisionPlayer", "?"),
                )
                recovery_key = f"req:{request_id}" if request_id is not None else fallback_key
                bot_logger.log_info(
                    "COMBAT_RECOVERY_ARMED: key={} canSubmitAttackers={} cycle={}/{}".format(
                        recovery_key,
                        req.get("canSubmitAttackers"),
                        self.__declare_attackers_cycle_count,
                        self.__declare_attackers_cycle_limit,
                    )
                )
                self.__preempt_stack_select_n_for_combat(
                    "DeclareAttackersReq: preempted stale stack SelectN prompt."
                )
                self.__arm_combat_recovery(recovery_key, delay=1.0)
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

    def __infer_local_timeout_from_raw_dict(self, raw_dict: dict) -> bool:
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
                timeout_seen = False
                for result in results:
                    if result.get("result") == "ResultType_WinLoss" and "winningTeamId" in result:
                        winning_team_id = result.get("winningTeamId")
                    if result.get("reason") == "ResultReason_Timeout":
                        timeout_seen = True
                if not timeout_seen or winning_team_id is None:
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
                    continue

                return winning_team_id != my_team_id
        except Exception as e:
            bot_logger.log_error(f"Failed to infer timeout loss from game state: {e}")
        return False

    def __infer_keep_from_raw_dict(self, raw_dict: dict) -> bool:
        try:
            messages = raw_dict.get("greToClientEvent", {}).get("greToClientMessages", [])
            for message in messages:
                if message.get("type") != "GREMessageType_EdictalMessage":
                    continue
                edict_message = (message.get("edictalMessage", {}) or {}).get("edictMessage", {}) or {}
                if edict_message.get("type") != "ClientMessageType_MulliganResp":
                    continue
                seat_id = edict_message.get("systemSeatId")
                if self.__system_seat_id is not None and seat_id is not None and seat_id != self.__system_seat_id:
                    continue
                decision = ((edict_message.get("mulliganResp", {}) or {}).get("decision") or "")
                if decision == "MulliganOption_AcceptHand":
                    return True
        except Exception as e:
            bot_logger.log_error(f"Failed to infer mulligan keep from raw dict: {e}")
        return False

    def __extract_match_id_from_raw_dict(self, raw_dict: dict) -> str | None:
        try:
            messages = raw_dict.get("greToClientEvent", {}).get("greToClientMessages", [])
            for message in messages:
                if message.get("type") != "GREMessageType_GameStateMessage":
                    continue
                game_info = (message.get("gameStateMessage", {}) or {}).get("gameInfo", {}) or {}
                match_id = str(game_info.get("matchID") or "").strip()
                if match_id:
                    return match_id
        except Exception as e:
            bot_logger.log_error(f"Failed to extract matchID from raw dict: {e}")
        return None

    def __should_reset_for_fresh_game_baseline(self, raw_dict: dict) -> bool:
        try:
            current_state = self.updated_game_state.get_full_state() or {}
            current_turn_info = self.updated_game_state.get_turn_info() or {}
            current_game_state_id = int(current_state.get("gameStateId") or 0)
            has_live_turn_context = any(
                current_turn_info.get(key) is not None
                for key in ("turnNumber", "phase", "step", "activePlayer", "priorityPlayer", "decisionPlayer")
            )
            if not has_live_turn_context and current_game_state_id <= 0:
                return False

            has_local_mulligan_req = self.__has_local_mulligan_request(raw_dict)
            messages = raw_dict.get("greToClientEvent", {}).get("greToClientMessages", [])
            for message in messages:
                if message.get("type") != "GREMessageType_GameStateMessage":
                    continue
                game_state_msg = message.get("gameStateMessage", {}) or {}
                incoming_game_state_id = int(game_state_msg.get("gameStateId") or 0)
                prev_game_state_id = int(game_state_msg.get("prevGameStateId") or 0)
                game_info = game_state_msg.get("gameInfo", {}) or {}
                stage = str(game_info.get("stage") or "")
                has_mulligan_pending = any(
                    str((player or {}).get("pendingMessageType") or "").startswith("ClientMessageType_Mulligan")
                    for player in (game_state_msg.get("players", []) or [])
                )
                has_sparse_turn_info = isinstance(game_state_msg.get("turnInfo"), dict) and not any(
                    game_state_msg.get("turnInfo", {}).get(key)
                    for key in ("turnNumber", "phase", "step")
                )
                game_state_regressed = incoming_game_state_id > 0 and current_game_state_id > 0 and incoming_game_state_id < current_game_state_id
                looks_like_fresh_baseline = (
                    stage == "GameStage_Start"
                    or (incoming_game_state_id > 0 and incoming_game_state_id <= 2 and prev_game_state_id <= 1)
                    or game_state_regressed
                )
                if looks_like_fresh_baseline and (has_local_mulligan_req or has_mulligan_pending or has_sparse_turn_info):
                    return True
        except Exception as e:
            bot_logger.log_error(f"Failed to detect fresh game baseline reset: {e}")
        return False

    def __infer_keep_from_live_game_state(self) -> bool:
        try:
            turn_info = self.updated_game_state.get_turn_info() or {}
            actions = self.updated_game_state.get_actions() or []
            if not turn_info:
                return False
            turn_number = turn_info.get("turnNumber")
            active_player = turn_info.get("activePlayer")
            priority_player = turn_info.get("priorityPlayer")
            decision_player = turn_info.get("decisionPlayer")
            phase = turn_info.get("phase")
            step = turn_info.get("step")
            has_live_priority = any(v is not None for v in (active_player, priority_player, decision_player))
            has_turn_progress = turn_number is not None and (bool(phase) or bool(step))
            has_playable_actions = any(
                self.__get_action_type(action) in {"ActionType_Play", "ActionType_Cast", "ActionType_Pass"}
                for action in actions
            )
            has_playable_state = has_playable_actions or bool(phase) or bool(step)
            return bool(has_live_priority and has_turn_progress and has_playable_state)
        except Exception as e:
            bot_logger.log_error(f"Failed to infer keep from live game state: {e}")
            return False

    def __update_inst_id__grp_id_dict(self, object_dict_arr):
        for object_dict in object_dict_arr:
            if object_dict['instanceId'] not in self.__inst_id_grp_id_dict.keys():
                self.__inst_id_grp_id_dict[object_dict['instanceId']] = object_dict['grpId']

    def __update_game_state(self, raw_dict: [str, str or int]):
        # Derive the local player's systemSeatId from incoming messages (if present)
        system_seat_id = Controller.__get_system_seat_id_from_raw_dict(raw_dict)
        if system_seat_id is not None and system_seat_id != self.__system_seat_id:
            self.__system_seat_id = system_seat_id
            self.__my_timer_state = {}
            bot_logger.log_info(f"Detected local systemSeatId={self.__system_seat_id}")
        if self.__system_seat_id is not None:
            runtime_status.update_status(local_system_seat_id=self.__system_seat_id)

        incoming_match_id = self.__extract_match_id_from_raw_dict(raw_dict)
        if (
            incoming_match_id
            and self.__last_seen_match_id
            and incoming_match_id != self.__last_seen_match_id
        ):
            self.__reset_live_game_state(
                f"Fresh match detected: {self.__last_seen_match_id} -> {incoming_match_id}. Resetting stale local game state.",
                preserve_system_seat_id=self.__system_seat_id,
            )
        elif self.__should_reset_for_fresh_game_baseline(raw_dict):
            self.__reset_live_game_state(
                "Fresh game baseline detected from early gameState/mulligan signals. Resetting stale local game state.",
                preserve_system_seat_id=self.__system_seat_id,
            )
        if incoming_match_id:
            self.__last_seen_match_id = incoming_match_id

        outcome = self.__infer_match_won_from_raw_dict(raw_dict)
        if outcome is not None:
            self.__last_match_won = outcome
        if self.__infer_local_timeout_from_raw_dict(raw_dict):
            bot_logger.log_info("MY_TIMER_TIMEOUT_RESULT_OBSERVED: local match loss reason=ResultReason_Timeout")
            runtime_status.update_status(
                my_timer_timeout_seen=True,
                my_timer_timeout_at_epoch=time.time(),
            )

        game_state = Controller.__get_game_state_from_raw_dict(raw_dict, fallback_seat_id=self.__system_seat_id or 1)
        self.updated_game_state.update(game_state)
        keep_observed = self.__infer_keep_from_raw_dict(raw_dict)
        if self.__has_local_mulligan_request(raw_dict) and not keep_observed:
            self.__clear_premature_mulligan_keep("Local MulliganReq observed: clearing premature keep state.")
        if keep_observed:
            self.__mark_has_mulled_keep("Mulligan keep observed from ClientMessageType_MulliganResp.")
        elif not self.__has_mulled_keep and not self.__has_pending_mulligan_state(raw_dict) and self.__infer_keep_from_live_game_state():
            self.__mark_has_mulled_keep("Mulligan keep inferred from live gameplay state.")
        # Log all parsed game state data to bot.log
        bot_logger.log_game_state_update(self.updated_game_state.get_full_state())
        self.__log_my_timer_status()

        self.__handle_target_selection_from_raw_dict(raw_dict)

        # Check for successful actions in the log update
        if self.__action_success_callback:
            # Pass to avoid log spam, as requested by user.
            # The original implementation here was checking GameStateMessage actions
            # which caused false positives for every action in the list.
            pass

        turn_info_dict = self.updated_game_state.get_turn_info()
        runtime_status.touch_playerlog_event(state=str(self._get_state_from_log()), turn_info=turn_info_dict)
        runtime_status.set_mode("in_game", bot_state=str(self._get_state_from_log()), turn_info=turn_info_dict or {})
        if self.__assign_damage_in_progress and not self._is_assign_damage_step_active():
            self.__clear_assign_damage_state("left Step_CombatDamage")
        is_complete = self.updated_game_state.is_complete()
        pending_count = self.updated_game_state.get_pending_message_count()
        stack_count = self.updated_game_state.get_zone_object_count("ZoneType_Stack")
        my_seat = self.__system_seat_id
        is_my_combat_declare = (
            bool(turn_info_dict)
            and my_seat is not None
            and turn_info_dict.get("phase") == "Phase_Combat"
            and turn_info_dict.get("step") == "Step_DeclareAttack"
            and turn_info_dict.get("decisionPlayer") == my_seat
        )
        if not is_my_combat_declare and self.__combat_recovery_key is not None:
            self.__clear_combat_recovery("Left Step_DeclareAttack or lost priority")

        # Log controller state
        bot_logger.log_controller_event(
            f"is_complete={is_complete}",
            f"decisionPlayer={turn_info_dict.get('decisionPlayer') if turn_info_dict else None}, has_mulled_keep={self.__has_mulled_keep}"
        )

        if self.__arm_mulligan_if_needed(turn_info_dict, raw_dict):
            return

        if time.time() < self.__group_req_active_until:
            if self.__decision_execution_thread is not None:
                self.__decision_execution_thread.cancel()
                self.__decision_execution_thread = None
                self.__decision_delay_key = None
                self.__decision_delay_scheduled_at = 0.0
            runtime_status.set_intentional_wait(2.0, "scry_wait")
            bot_logger.log_info("Pausing decision while scry/group prompt is active")
            return

        if self.__should_pause_for_assign_damage():
            if self.__decision_execution_thread is not None:
                self.__decision_execution_thread.cancel()
                self.__decision_execution_thread = None
                self.__decision_delay_key = None
                self.__decision_delay_scheduled_at = 0.0
            runtime_status.set_intentional_wait(3.0, "assign_damage_wait")
            runtime_status.touch_decision()
            bot_logger.log_info("Pausing decision while assign damage handler is pending")
            return

        if stack_count > 0 and turn_info_dict and turn_info_dict.get("phase") in ("Phase_Main1", "Phase_Main2"):
            my_seat = self.__system_seat_id or turn_info_dict.get("decisionPlayer")
            if my_seat is not None and turn_info_dict.get("decisionPlayer") == my_seat and pending_count == 0:
                has_pass = self.__has_available_action_type("ActionType_Pass")
                if has_pass:
                    bot_logger.log_info(
                        "Stack present but safe to resolve: decisionPlayer=me, pendingMessageCount=0, pass available."
                    )
                else:
                    # If we have available actions, proceed anyway instead of deferring forever.
                    action_count = len(self.updated_game_state.get_actions() or [])
                    if action_count > 0:
                        bot_logger.log_info(
                            f"Stack present, no pass action, but {action_count} actions available. Proceeding with decision."
                        )
                    else:
                        if self.__decision_execution_thread is not None:
                            self.__decision_execution_thread.cancel()
                            self.__decision_execution_thread = None
                            self.__decision_delay_key = None
                            self.__decision_delay_scheduled_at = 0.0
                        runtime_status.set_intentional_wait(2.0, "stack_resolution_wait")
                        bot_logger.log_info(f"Deferring decision: stack has {stack_count} object(s)")
                        return
            else:
                if self.__decision_execution_thread is not None:
                    self.__decision_execution_thread.cancel()
                    self.__decision_execution_thread = None
                    self.__decision_delay_key = None
                    self.__decision_delay_scheduled_at = 0.0
                runtime_status.set_intentional_wait(2.0, "stack_resolution_wait")
                bot_logger.log_info(f"Deferring decision: stack has {stack_count} object(s)")
                return

        if pending_count > 0:
            if self.__decision_execution_thread is not None:
                self.__decision_execution_thread.cancel()
                self.__decision_execution_thread = None
                self.__decision_delay_key = None
                self.__decision_delay_scheduled_at = 0.0
            runtime_status.set_intentional_wait(1.2, "pending_message_wait")
            bot_logger.log_info(f"Deferring decision: pendingMessageCount={pending_count}")
            return

        if self.__should_pause_for_pay_costs():
            if self.__decision_execution_thread is not None:
                self.__decision_execution_thread.cancel()
                self.__decision_execution_thread = None
                self.__decision_delay_key = None
                self.__decision_delay_scheduled_at = 0.0
            runtime_status.set_intentional_wait(2.0, "pay_costs_wait")
            bot_logger.log_info("Pausing decision while pay costs prompt is active")
            my_seat = self.__system_seat_id
            if (
                my_seat is not None
                and turn_info_dict
                and turn_info_dict.get("decisionPlayer") == my_seat
                and self.__has_mulled_keep
            ):
                def _retry_after_pay_costs_pause():
                    try:
                        ti = self.updated_game_state.get_turn_info() or {}
                        if self.__should_pause_for_pay_costs():
                            self.__decision_execution_thread = threading.Timer(0.5, _retry_after_pay_costs_pause)
                            self.__decision_execution_thread.start()
                            return
                        if self.__should_pause_for_targets():
                            self.__decision_execution_thread = threading.Timer(0.5, _retry_after_pay_costs_pause)
                            self.__decision_execution_thread.start()
                            return
                        if (
                            self.__decision_callback
                            and self.__has_mulled_keep
                            and ti.get("decisionPlayer") == my_seat
                        ):
                            bot_logger.log_info("Retrying decision after pay costs pause")
                            self.__decision_callback(self.updated_game_state)
                    except Exception as e:
                        bot_logger.log_error(f"Error in pay-costs pause retry: {e}")

                self.__decision_execution_thread = threading.Timer(0.5, _retry_after_pay_costs_pause)
                self.__decision_execution_thread.start()
            return

        if self.__should_pause_for_targets():
            if self.__decision_execution_thread is not None:
                self.__decision_execution_thread.cancel()
                self.__decision_execution_thread = None
                self.__decision_delay_key = None
                self.__decision_delay_scheduled_at = 0.0
            runtime_status.set_intentional_wait(15.0, "target_selection_wait")
            runtime_status.touch_decision()
            bot_logger.log_info("Pausing decision while target selection is pending")
            # Let target selection resolve before scheduling new decisions.
            return

        if is_complete:
            self.__update_inst_id__grp_id_dict(self.updated_game_state.get_game_objects())
            my_seat = self.__system_seat_id
            if my_seat is None:
                bot_logger.log_info("Skipping decision (local systemSeatId unknown)")
            elif turn_info_dict['decisionPlayer'] == my_seat and self.__has_mulled_keep:
                delay_key = (
                    int(turn_info_dict.get("turnNumber", -1) or -1),
                    str(turn_info_dict.get("phase") or ""),
                    str(turn_info_dict.get("step") or ""),
                    int(turn_info_dict.get("activePlayer", -1) or -1),
                    int(turn_info_dict.get("decisionPlayer", -1) or -1),
                )
                effective_delay = self.__get_effective_decision_delay()
                existing_alive = (
                    self.__decision_execution_thread is not None
                    and getattr(self.__decision_execution_thread, "is_alive", lambda: False)()
                )
                if existing_alive and self.__decision_delay_key == delay_key:
                    if effective_delay <= 0.05:
                        bot_logger.log_info(
                            "Decision delay override: canceling existing timer because inactivity rope is low."
                        )
                        self.__decision_execution_thread.cancel()
                        self.__decision_execution_thread = None
                        self.__decision_delay_key = None
                        self.__decision_delay_scheduled_at = 0.0
                    else:
                        elapsed = max(
                            0.0,
                            time.time() - float(self.__decision_delay_scheduled_at or 0.0),
                        )
                        remaining_delay = max(0.2, float(effective_delay) - elapsed)
                        runtime_status.set_intentional_wait(
                            max(2.0, remaining_delay + 2.5),
                            "decision_delay_wait",
                        )
                        bot_logger.log_info(
                            "Decision delay already armed for current priority window; keeping existing timer (remaining={:.1f}s).".format(
                                remaining_delay
                            )
                        )
                        return
                if self.__decision_execution_thread is not None:
                    self.__decision_execution_thread.cancel()
                    self.__decision_execution_thread = None
                    self.__decision_delay_key = None
                    self.__decision_delay_scheduled_at = 0.0

                def _decision_if_still_my_priority():
                    try:
                        self.__decision_execution_thread = None
                        self.__decision_delay_key = None
                        self.__decision_delay_scheduled_at = 0.0
                        ti = self.updated_game_state.get_turn_info() or {}
                        if self.__should_pause_for_targets():
                            bot_logger.log_info("Deferring decision; target selection still pending")
                            runtime_status.set_intentional_wait(15.0, "target_selection_wait")
                            runtime_status.touch_decision()
                            self.__decision_execution_thread = threading.Timer(0.5, _decision_if_still_my_priority)
                            self.__decision_delay_key = delay_key
                            self.__decision_delay_scheduled_at = time.time()
                            self.__decision_execution_thread.start()
                            return
                        still_my_priority = (ti.get('decisionPlayer') == my_seat)
                        runtime_status.clear_intentional_wait()
                        bot_logger.log_info(
                            "Decision delay fired: turn={} phase={} step={} still_my_priority={}".format(
                                ti.get("turnNumber"),
                                ti.get("phase"),
                                ti.get("step"),
                                still_my_priority,
                            )
                        )
                        if still_my_priority and self.__decision_callback and self.__has_mulled_keep:
                            self.__decision_callback(self.updated_game_state)
                        else:
                            bot_logger.log_info(
                                f"Skipping delayed decision (decisionPlayer={ti.get('decisionPlayer')}, my_seat={my_seat})"
                            )
                    except Exception as e:
                        runtime_status.clear_intentional_wait()
                        bot_logger.log_error(f"Error in delayed decision callback: {e}")

                if effective_delay <= 0.05:
                    runtime_status.clear_intentional_wait()
                    bot_logger.log_info(
                        "Executing decision immediately: turn={} phase={} step={} reason=low_inactivity_timer".format(
                            delay_key[0],
                            delay_key[1],
                            delay_key[2],
                        )
                    )
                    _decision_if_still_my_priority()
                    return

                runtime_status.set_intentional_wait(
                    max(2.0, float(effective_delay) + 2.5),
                    "decision_delay_wait",
                )
                self.__decision_delay_key = delay_key
                self.__decision_delay_scheduled_at = time.time()
                bot_logger.log_info(
                    "Arming delayed decision: turn={} phase={} step={} delay={}s effective_delay={:.1f}s".format(
                        delay_key[0],
                        delay_key[1],
                        delay_key[2],
                        self.__decision_delay,
                        effective_delay,
                    )
                )
                self.__decision_execution_thread = threading.Timer(effective_delay, _decision_if_still_my_priority)
                self.__decision_execution_thread.start()
                return

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
            elif message['type'] == "GREMessageType_TimerStateMessage":
                timer_state = message.get('timerStateMessage', {}) or {}
                timers = timer_state.get('timers', []) or []
                if timers:
                    timer_game_state = GameState({'timers': timers})
                    return_game_state.update(timer_game_state)
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
