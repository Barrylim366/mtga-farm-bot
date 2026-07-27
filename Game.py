from Controller.ControllerInterface import ControllerSecondary
from AI.AIInterface import AIKernel
from Controller.Utilities.GameState import GameState
import AI.Utilities.CardInfo as CardInfo
import time
import os
import traceback
import threading
import bot_logger
import debug_recorder
import click_recorder
import runtime_status


class Game:

    # If the AI is handed the exact same decision context and returns the
    # exact same move this many times in a row, the executed click(s) are not
    # actually reaching the game (e.g. a cast whose confirmation never fires) --
    # retrying again would just repeat the same silent failure forever. See
    # STUCK_ACTION_RETRY_LIMIT below.
    _STUCK_MOVE_RETRY_LIMIT = 3

    def __init__(self, controller: ControllerSecondary, ai: AIKernel, data_dir_prompt=None):
        self.ai = ai
        self.controller = controller
        self.last_logged_turn = -1
        self.game_started = False
        self.starting_hand_logged = False
        self._stop_requested = False
        # Key of the match whose end was already handled -- see _match_end_key.
        # Guards against a match-end being signalled more than once: without it,
        # each duplicate schedules another restart timer, spawning parallel
        # queue/switch loops that race and break account rotation + switch criteria.
        # Keyed per MATCH rather than a bare flag, so the suppression lasts as long
        # as the match does (a bare flag re-armed on the restart timer would let a
        # duplicate ~10s later through) without ever latching permanently.
        # Guarded by _match_end_lock: the signal arrives on the log-monitor thread
        # while decision threads write the state the key is built from.
        self._handled_match_end_key: str | None = None
        self._match_end_lock = threading.Lock()
        # Counts restarts, i.e. identifies the match slot we are in. Only used as
        # the dedup key's fallback when no matchId is available -- unlike a
        # timestamp that stays None until the bot first acts, this always differs
        # between consecutive matches, so back-to-back matches that both end before
        # the bot ever acts can never share a key (which would swallow the second
        # match's end and leave the bot idle with no restart scheduled).
        self._match_slot = 0
        self._timers: list[threading.Timer] = []
        self._last_action_delay_turn = -1
        # Tracks (turn, phase, step, decisionPlayer, move_name, move_payload) of
        # the last move actually executed, plus how many times in a row it has
        # repeated -- see _STUCK_MOVE_RETRY_LIMIT.
        self._last_move_signature = None
        self._last_move_repeat_count = 0
        # Epoch when the current match actually started (first mulligan / inferred
        # live state). Used only to report match duration in the [MATCH_END]
        # marker; None until a game is under way.
        self._match_started_ts: float | None = None
        # Optional callback (no args -> path string or "") the UI can supply to
        # ask the user to locate their MTGA install when auto-detection in
        # _refresh_card_data() fails. Without card data the bot has no removal/
        # counter/fight information and effectively only plays lands, with no
        # obvious error pointing at the missing install path as the cause.
        self._data_dir_prompt = data_dir_prompt

        # Initialize bot.log with centralized logger
        bot_logger.init_bot_log()

    def start(self):
        self._debug("Game.start() called")
        self._stop_requested = False
        with self._match_end_lock:
            self._handled_match_end_key = None
        try:
            debug_recorder.start_session()
        except Exception as e:
            self._debug(f"Decision recorder start failed: {e}")
        try:
            click_recorder.start_session()
        except Exception as e:
            self._debug(f"Click recorder start failed: {e}")
        runtime_status.set_mode("starting")
        self._refresh_card_data()
        # Eagerly load the curated Starter Deck Duel card DB (oracle text, types,
        # keywords) and pre-resolve removal/counter profiles. ~0.2s paid once at
        # startup so the first in-game decision has this data ready offline.
        try:
            summary = CardInfo.warm_up_starter_data()
            self._debug(
                "Starter card DB warmed: {cards} cards, "
                "{removal} removal / {counter} counter profiles.".format(**summary)
            )
        except Exception as e:
            self._debug(f"Starter card DB warm-up failed: {e}")
        try:
            CardInfo.refresh_missing_cards()
            self._debug("Card data refresh: missing cards resolved via Scryfall (if any).")
        except Exception as e:
            self._debug(f"Card data refresh failed: {e}")
        # Read the account's quests from scratch, ignoring anything the log already
        # holds: it can belong to another account, or predate a quest the user
        # re-rolled/finished by hand in MTGA. Bounded (see prime_quests_for_new_session)
        # and done BEFORE start_game() so the first queue already knows the right
        # deck colours. The cache is then reused locally instead of re-parsing the
        # player.log on every queue cycle.
        try:
            # Clear the previous run's stop flag first: the priming below polls and
            # clicks, and would otherwise bail out on its first tick. start_game()
            # calls this again (it is idempotent).
            if hasattr(self.controller, "begin_session"):
                self.controller.begin_session()
            if hasattr(self.controller, "prime_quests_for_new_session"):
                self.controller.prime_quests_for_new_session()
            elif hasattr(self.controller, "refresh_quests_cache"):
                self.controller.refresh_quests_cache()
        except Exception as e:
            self._debug(f"Quest cache refresh failed: {e}")
        self.controller.start_game()
        self.controller.set_mulligan_decision_callback(self.mulligan_decision_method)
        self.controller.set_decision_callback(self.decision_method)
        self.controller.set_action_success_callback(self.on_action_success)
        self.controller.set_match_end_callback(self.on_match_end)
        self._debug("All callbacks registered")

    def _mark_match_started(self) -> None:
        """A new match is confirmed live: first mulligan, or an inferred live state
        when we attached mid-game."""
        self._match_started_ts = time.time()

    def _match_end_key(self) -> str:
        """Identity of the match that is ending, used to collapse duplicate
        match-end signals.

        The matchId is the real identity and it is set when the match is JOINED, so
        it is available even for a match that ended before the bot ever acted. When
        it is missing, fall back to the match slot (the restart counter), which still
        collapses duplicates inside one match but always differs between consecutive
        matches.

        Deliberately never a value that can repeat across matches: a key that does
        can swallow a real match end, and then no restart is scheduled and the bot
        sits idle forever. One redundant restart is the far cheaper failure --
        start_queueing already refuses to spawn a second queue loop."""
        match_id = ""
        try:
            if hasattr(self.controller, "get_current_match_id"):
                match_id = str(self.controller.get_current_match_id() or "").strip()
        except Exception:
            match_id = ""
        return f"id:{match_id}" if match_id else f"slot:{self._match_slot}"

    def on_match_end(self, won: bool | None = None) -> bool:
        """Called when a match ends - wait 10 seconds then start a new game.

        Returns True if this call claimed the match end (i.e. it was the first
        signal for this match), False if it was a suppressed duplicate. Callers
        that record per-match data -- the UI's session win/loss counters -- must
        only count when this returns True."""
        # Idempotency: the match-end can be signalled more than once for the same
        # match. Handle only the first; duplicates must NOT re-record the match or
        # schedule a second restart (which would spawn a parallel queue/switch loop
        # -- the cause of broken rotation and duplicated switch attempts).
        # Check-and-set under the lock so two concurrent signals can't both claim it.
        key = self._match_end_key()
        with self._match_end_lock:
            if key is not None and key == self._handled_match_end_key:
                self._debug(f"Duplicate match-end signal ignored ({key}).")
                return False
            self._handled_match_end_key = key
        # Finalize the debug recording first, independently of the restart
        # logic below -- otherwise stopping the bot at match end (a common case)
        # would skip the match.json footer and leave the last match unfinalized.
        try:
            result = None if won is None else ("win" if won else "loss")
            debug_recorder.end_match(result)
        except Exception as e:
            self._debug(f"Decision recorder end_match failed: {e}")
        # Emit one structured match-end marker (result/turns/duration) for the
        # session watchdog to turn into a per-match record. Done before the
        # stop-requested check so the final match of a session is still recorded.
        # Never let recording failures touch the restart path.
        try:
            if won is True:
                result = "won"
            elif won is False:
                result = "lost"
            else:
                result = "unknown"
            if self._match_started_ts:
                duration_sec = max(0.0, time.time() - self._match_started_ts)
            else:
                duration_sec = 0.0
            bot_logger.log_match_end(result, self.last_logged_turn, duration_sec)
        except Exception as e:
            self._debug(f"Match-end marker failed: {e}")
        if self._stop_requested:
            self._debug("Match ended but stop requested - not restarting")
            return True
        runtime_status.set_mode("match_end")
        self._debug("Match ended - scheduling restart in 10 seconds")
        # Stop inactivity timer since match ended
        if hasattr(self.controller, 'stop_inactivity_timer'):
            self.controller.stop_inactivity_timer()
        restart_timer = threading.Timer(10.0, self._restart_game)
        self._timers.append(restart_timer)
        restart_timer.start()
        return True

    def _restart_game(self):
        """Reset state and start a new game"""
        if self._stop_requested:
            self._debug("Stop requested - skipping restart")
            return
        runtime_status.set_mode("restarting")
        self._debug("Restarting game...")

        # NB: _handled_match_end_key is deliberately NOT cleared here -- clearing it
        # on the restart timer would reopen the duplicate-signal window ~10s after
        # the match. Instead the key rotates on its own: a new matchId when the next
        # match is joined, and the slot counter below as the fallback.
        self._match_slot += 1

        # Reset Game state
        self.last_logged_turn = -1
        self.game_started = False
        self.starting_hand_logged = False
        self._last_action_delay_turn = -1
        self._match_started_ts = None

        # Reset AI state
        if hasattr(self.ai, 'reset'):
            self.ai.reset()

        # Reset Controller state
        if hasattr(self.controller, 'reset_for_new_game'):
            self.controller.reset_for_new_game()

        # Defer queueing to controller if it's handling post-match delay or account switching.
        try:
            if hasattr(self.controller, "should_defer_post_match_actions") and self.controller.should_defer_post_match_actions():
                self._debug("Controller requested post-match defer; skipping immediate queue spam")
                return
        except Exception:
            pass

        # Start queueing loop (keeps clicking until queue is accepted)
        self._debug("Starting queue spam to enter next match")
        self.controller.start_queueing()
        self._debug("Queue spam started")

    def stop(self):
        """Stop the game cleanly (cancel timers, stop log monitor, prevent restarts)."""
        self._stop_requested = True
        self.game_started = False
        runtime_status.set_mode("stopped")

        # Finalize any in-progress debug recording so a mid-match stop still
        # writes the match.json footer (no-op if already finalized).
        try:
            debug_recorder.end_match(None)
        except Exception:
            pass

        for timer in list(self._timers):
            try:
                timer.cancel()
            except Exception:
                pass
        self._timers.clear()

        if hasattr(self.controller, 'stop_inactivity_timer'):
            try:
                self.controller.stop_inactivity_timer()
            except Exception:
                pass
        if hasattr(self.controller, 'end_game'):
            try:
                self.controller.end_game()
            except Exception:
                pass

    def mulligan_decision_method(self, card_list):
        if self._stop_requested:
            return
        self._debug(f"Mulligan decision called with {len(card_list)} cards")
        runtime_status.set_mode("in_game")
        if not self.game_started:
            self._mark_match_started()
        self.game_started = True  # Mark game as started after mulligan
        keep = self.ai.generate_keep(card_list)
        bot_logger.log_mulligan_decision(keep, len(card_list))
        try:
            debug_recorder.record(
                self.controller.get_game_state(),
                self._recorder_seat(),
                self._recorder_match_id(),
                "mulligan",
                "keep" if keep else "mulligan",
                {"card_count": len(card_list)},
            )
        except Exception as e:
            self._debug(f"Decision recorder (mulligan) failed: {e}")
        self.controller.keep(keep)

    def _debug(self, message):
        """Debug log - detailed technical information"""
        bot_logger.log_info(message)

    def _recorder_seat(self):
        try:
            if hasattr(self.controller, "get_system_seat_id"):
                return self.controller.get_system_seat_id()
        except Exception:
            pass
        return None

    def _recorder_match_id(self):
        try:
            if hasattr(self.controller, "get_current_match_id"):
                return self.controller.get_current_match_id()
        except Exception:
            pass
        return None

    def _refresh_card_data(self):
        """Refresh cards.json from local MTGA data (Linux/macOS/Windows Steam paths).

        Deliberately no Scryfall bulk download here. The local export already
        contains every Arena grpId, so a bulk merge can only ever skip cards it
        already has -- it added exactly 0 entries while costing a blocking
        ~600 MB download plus a full in-memory parse on the first start of each
        day (Scryfall regenerates the bulk file daily). Cards the export misses,
        and the oracle text/keywords it never carries, are resolved lazily and
        cached per card instead: CardInfo.get_card_info()'s single-card Scryfall
        fallback, CardInfo.refresh_missing_cards() at startup for IDs that
        fallback failed on, and CardInfo.get_oracle_text_from_scryfall() on demand.
        """
        try:
            import sys
            import subprocess
            if getattr(sys, "frozen", False):
                self._debug("Card data refresh: frozen build detected, skipping local exporter subprocess.")
                try:
                    CardInfo.reload_cards_from_disk()
                    self._debug("Card data refresh: cards.json reloaded into memory.")
                except Exception as e:
                    self._debug(f"Card data refresh: reload failed: {e}")
                return
            base_candidates = [
                os.path.expanduser("~/.local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"),
                os.path.expanduser("~/.steam/steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"),
                os.path.expanduser("~/.steam/root/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"),
                os.path.expanduser("~/Library/Application Support/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"),
                os.path.expanduser(
                    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"
                ),
                r"C:\\Program Files (x86)\\Steam\\steamapps\\common\\MTGA\\MTGA_Data\\Downloads\\Raw",
                r"C:\\Program Files\\Wizards of the Coast\\MTGA\\MTGA_Data\\Downloads\\Raw",
                r"C:\\Program Files\\Steam\\steamapps\\common\\MTGA\\MTGA_Data\\Downloads\\Raw",
            ]
            data_dir = next((p for p in base_candidates if os.path.isdir(p)), "")
            if not data_dir and self._data_dir_prompt is not None:
                self._debug(
                    "Card data refresh: MTGA data dir not found via auto-detect; "
                    "asking the user to locate it."
                )
                try:
                    prompted = self._data_dir_prompt() or ""
                except Exception as e:
                    self._debug(f"Card data refresh: data dir prompt failed: {e}")
                    prompted = ""
                if prompted and os.path.isdir(prompted):
                    data_dir = prompted
            if not data_dir:
                self._debug("Card data refresh: MTGA data dir not found, skipping export.")
                return
            self._debug(f"Card data refresh: exporting from {data_dir}")
            result = subprocess.run(
                [sys.executable, os.path.join("tools", "mtga_cards_export.py"), "--data-dir", data_dir],
                cwd=os.path.dirname(__file__),
                timeout=30,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.stdout:
                self._debug(f"Card data refresh: exporter stdout: {result.stdout.strip()}")
            if result.stderr:
                self._debug(f"Card data refresh: exporter stderr: {result.stderr.strip()}")
            try:
                CardInfo.reload_cards_from_disk()
                self._debug("Card data refresh: cards.json reloaded into memory.")
            except Exception as e:
                self._debug(f"Card data refresh: reload failed: {e}")
        except Exception as e:
            self._debug(f"Card data refresh failed: {e}")

    def _get_card_id_str(self, instance_id):
        """Get card ID string (instanceId, grpId) for logging"""
        try:
            grp_id = self.controller.get_inst_id_grp_id_dict().get(instance_id)
            if grp_id:
                return f"(inst={instance_id}, grp={grp_id})"
        except Exception:
            pass
        return f"(inst={instance_id})"

    def on_action_success(self, action_dict):
        """Callback from Controller - only debug logging"""
        if self._stop_requested:
            return
        try:
            action_type = action_dict.get('actionType', 'Unknown')
            instance_id = action_dict.get('instanceId')
            self._debug(f"on_action_success: type={action_type}, instanceId={instance_id}")
        except Exception as e:
            self._debug(f"ERROR in on_action_success: {e}")

    def _infer_game_started_from_live_state(self, current_game_state: GameState) -> bool:
        """Allow direct/supervised starts that attach after mulligan into a live game."""
        try:
            turn_info = current_game_state.get_turn_info() or {}
            actions = current_game_state.get_actions() or []
            active_player = turn_info.get('activePlayer')
            priority_player = turn_info.get('priorityPlayer')
            decision_player = turn_info.get('decisionPlayer')
            turn_num = int(turn_info.get('turnNumber', 0) or 0)
            phase = turn_info.get('phase')
            step = turn_info.get('step')

            if not turn_info:
                return False

            has_live_priority = any(v is not None for v in (active_player, priority_player, decision_player))
            has_playable_state = bool(actions) or bool(phase) or bool(step)
            if not (has_live_priority and has_playable_state):
                return False

            if not self.game_started:
                self._mark_match_started()
            self.game_started = True
            if turn_num > 1:
                self.starting_hand_logged = True
            self._debug(
                "Inferred game_started from live gameplay state: "
                f"turn={turn_num}, phase={phase}, step={step}, "
                f"active={active_player}, priority={priority_player}, decision={decision_player}, "
                f"actions={len(actions)}"
            )
            return True
        except Exception as e:
            self._debug(f"Failed to infer live game_started state: {e}")
            return False

    def decision_method(self, current_game_state: GameState):
        if self._stop_requested:
            return
        runtime_status.clear_intentional_wait()
        # Don't do anything before game has started
        if not self.game_started:
            if not self._infer_game_started_from_live_state(current_game_state):
                self._debug("decision_method called but game not started yet, ignoring")
                return
        delay_wait_active = False

        # Reset inactivity timer since we're making a decision
        if hasattr(self.controller, 'reset_inactivity_timer'):
            self.controller.reset_inactivity_timer()

        # Initialized before the try so the except handler can still record the
        # snapshot when generate_move() itself throws (the most valuable case).
        snapshot_handle = None
        try:
            self._debug("=" * 50)
            self._debug("decision_method called")

            turn_info = current_game_state.get_turn_info()
            if not turn_info:
                self._debug("ERROR: turn_info is None!")
                return

            turn_num = turn_info.get('turnNumber', -1)
            active_player = turn_info.get('activePlayer', -1)
            phase = turn_info.get('phase', 'Unknown')
            step = turn_info.get('step', 'Unknown')
            decision_player = turn_info.get('decisionPlayer', -1)
            priority_player = turn_info.get('priorityPlayer', -1)

            self._debug(f"Turn info: turn={turn_num}, active={active_player}, phase={phase}, step={step}")
            self._debug(f"Decision player={decision_player}, priority={priority_player}")
            runtime_status.set_mode(
                "in_game",
                bot_state="BotState.IN_GAME",
                turn_info={
                    "turnNumber": turn_num,
                    "phase": phase,
                    "step": step,
                    "activePlayer": active_player,
                    "priorityPlayer": priority_player,
                    "decisionPlayer": decision_player,
                },
            )

            if active_player == decision_player and turn_num != self._last_action_delay_turn:
                self._debug("Active turn delay: waiting 2 seconds before actions")
                runtime_status.set_intentional_wait(2.2, "active_turn_delay")
                delay_wait_active = True
                try:
                    time.sleep(2.0)
                except Exception:
                    pass
                self._last_action_delay_turn = turn_num

            if turn_num != self.last_logged_turn and active_player == 1:
                self.last_logged_turn = turn_num

                if not self.starting_hand_logged:
                    self.starting_hand_logged = True
                    try:
                        inst_id_grp_id_dict = self.controller.get_inst_id_grp_id_dict()
                        self._debug(f"Logging starting hand: {len(inst_id_grp_id_dict)} cards")
                    except Exception as e:
                        self._debug(f"Error logging starting hand: {e}")

                if turn_num > 1:
                    try:
                        action_list = current_game_state.get_actions()
                        if action_list:
                            mana_sources = set()
                            for aw in action_list:
                                action = aw.get('action', {})
                                if action.get('actionType') == 'ActionType_Activate_Mana':
                                    inst_id = action.get('instanceId')
                                    if inst_id:
                                        mana_sources.add(inst_id)
                    except Exception as e:
                        self._debug(f"Could not get mana info: {e}")

            # Get actions
            try:
                action_list = current_game_state.get_actions()
                self._debug(f"Available actions: {len(action_list) if action_list else 0}")
                if action_list:
                    for i, action_wrapper in enumerate(action_list[:5]):
                        action = action_wrapper.get('action', {})
                        self._debug(f"  Action {i}: type={action.get('actionType')}, instanceId={action.get('instanceId')}")
            except Exception as e:
                self._debug(f"ERROR getting actions: {e}")
                action_list = []

            # Snapshot the exact state the AI is about to reason over, so the
            # recorded board matches the move decided below (capture only deep-
            # copies a pruned subset; no I/O on this thread).
            try:
                snapshot_handle = debug_recorder.capture(
                    current_game_state, self._recorder_seat(), self._recorder_match_id(), "main"
                )
            except Exception as e:
                self._debug(f"Decision recorder capture failed: {e}")

            # Generate move
            self._debug("Calling AI.generate_move()")
            move = self.ai.generate_move(current_game_state, self.controller.get_inst_id_grp_id_dict())
            self._debug(f"AI returned move: {move}")

            if not move:
                self._debug("ERROR: AI returned empty move!")
                try:
                    debug_recorder.attach_move(snapshot_handle, None, None)
                except Exception:
                    pass
                return

            move_name = list(move.keys())[0]
            move_payload = move.get(move_name)
            move_signature = (
                turn_num, phase, step, decision_player, move_name,
                tuple(move_payload) if isinstance(move_payload, list) else move_payload,
            )
            if move_signature == self._last_move_signature:
                self._last_move_repeat_count += 1
            else:
                self._last_move_signature = move_signature
                self._last_move_repeat_count = 1

            # 'resolve'/auto_pass repeating is normal (e.g. passing priority
            # while the opponent acts) -- only an active move (cast, attack,
            # ...) repeating unchanged means the click(s) it drives are not
            # actually landing in the game. 'select_target' is exempt too: the
            # AI always returns the same sentinel payload ([-1], "target
            # opponent avatar") for it regardless of which prompt triggered
            # it, so an identical signature can legitimately recur across
            # several different real target requests in one phase/step; it
            # also already has its own retry-and-give-up handling in
            # Controller.__schedule_target_selection.
            _BREAKER_EXEMPT_MOVES = ('resolve', 'auto_pass', 'unconditional_auto_pass', 'select_target')
            if (
                move_name not in _BREAKER_EXEMPT_MOVES
                and self._last_move_repeat_count >= self._STUCK_MOVE_RETRY_LIMIT
            ):
                bot_logger.log_error(
                    f"STUCK_ACTION_RETRY_LIMIT: move {move_name}={move_payload} repeated "
                    f"{self._last_move_repeat_count}x with no game-state progress "
                    f"(turn={turn_num} {phase}/{step}); forcing resolve() to break the loop."
                )
                move = {'resolve': []}
                move_name = 'resolve'
                self._last_move_signature = (turn_num, phase, step, decision_player, 'resolve', ())
                self._last_move_repeat_count = 1

            runtime_status.touch_decision(
                move_name=move_name,
                turn_info={
                    "turnNumber": turn_num,
                    "phase": phase,
                    "step": step,
                    "activePlayer": active_player,
                    "priorityPlayer": priority_player,
                    "decisionPlayer": decision_player,
                },
            )
            bot_logger.log_decision(move_name, move.get(move_name))
            try:
                debug_recorder.attach_move(snapshot_handle, move_name, move.get(move_name))
                snapshot_handle = None  # attached; don't let the except re-record it
            except Exception:
                pass
            self._debug(f"Executing move: {move_name}")

            # Execute move
            if move_name == 'cast':
                inst_id = int(move[move_name][0])
                card_id_str = self._get_card_id_str(inst_id)
                self._debug(f"Casting card with instanceId={inst_id}")
                grp_id = self.controller.get_inst_id_grp_id_dict().get(inst_id)
                card_info = CardInfo.get_card_info(grp_id) if grp_id else None
                if card_info:
                    types = card_info.get('types', [])
                    if 'Land' in types:
                        self._debug(f"Play Land {card_id_str}")
                    elif 'Creature' in types:
                        self._debug(f"Cast Creature {card_id_str}")
                    else:
                        self._debug(f"Cast {card_id_str}")
                else:
                    self._debug(f"Cast {card_id_str}")
                self.controller.cast(inst_id)
            elif move_name == 'attack':
                self._debug(f"Attacking with {move[move_name][0]}")
                self.controller.attack(move[move_name][0])
            elif move_name == 'all_attack':
                self._debug("Executing all_attack")
                self.controller.all_attack()
            elif move_name == 'block':
                self._debug(f"Blocking: {move[move_name]}")
                self.controller.block(move[move_name][0], move[move_name][1])
            elif move_name == 'all_block':
                self._debug("Executing all_block")
                self.controller.all_block()
            elif move_name == 'select_target':
                self._debug(f"Selecting target: {move[move_name][0]}")
                self.controller.select_target(move[move_name][0])
            elif move_name == 'activate_ability':
                self._debug(f"Activating ability: {move[move_name]}")
                self.controller.activate_ability(move[move_name][0], move[move_name][1])
            elif move_name == 'resolve':
                self._debug("Resolving priority")
                self.controller.resolve()
            elif move_name == 'auto_pass':
                self._debug("Auto passing")
                self.controller.auto_pass()
            elif move_name == 'unconditional_auto_pass':
                self._debug("Unconditional auto pass")
                self.controller.unconditional_auto_pass()
            else:
                self._debug(f"WARNING: Unknown move type: {move_name}")

        except Exception as e:
            self._debug(f"CRITICAL ERROR in decision_method: {e}")
            self._debug(traceback.format_exc())
            # Preserve the snapshot that led to the crash (only if not yet
            # attached above) -- the crash case is the most useful to debug.
            try:
                debug_recorder.attach_move(snapshot_handle, "exception", str(e))
            except Exception:
                pass
        finally:
            if delay_wait_active:
                runtime_status.clear_intentional_wait()
