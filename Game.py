from Controller.ControllerInterface import ControllerSecondary
from AI.AIInterface import AIKernel
from Controller.Utilities.GameState import GameState
import AI.Utilities.CardInfo as CardInfo
from datetime import datetime
import time
import os
import traceback
import threading
import bot_logger


class Game:

    def __init__(self, controller: ControllerSecondary, ai: AIKernel):
        self.ai = ai
        self.controller = controller
        self.human_log_file = "human.log"
        self.bot_log_file = "bot.log"
        self.last_logged_turn = -1
        self.game_started = False
        self.starting_hand_logged = False
        self._stop_requested = False
        self._timers: list[threading.Timer] = []
        self._last_action_delay_turn = -1

        # Clear log files on start
        with open(self.human_log_file, 'w') as f:
            f.write("=== MTGA Bot Session Started ===\n")
            f.write(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 35 + "\n\n")
        # Initialize bot.log with centralized logger
        bot_logger.init_bot_log()

    def start(self):
        self._debug("Game.start() called")
        self._stop_requested = False
        self._refresh_card_data()
        try:
            CardInfo.refresh_missing_cards()
            self._debug("Card data refresh: missing cards resolved via Scryfall (if any).")
        except Exception as e:
            self._debug(f"Card data refresh failed: {e}")
        self.controller.start_game()
        self.controller.set_mulligan_decision_callback(self.mulligan_decision_method)
        self.controller.set_decision_callback(self.decision_method)
        self.controller.set_action_success_callback(self.on_action_success)
        self.controller.set_match_end_callback(self.on_match_end)
        self._debug("All callbacks registered")

    def on_match_end(self, won: bool | None = None):
        """Called when a match ends - wait 20 seconds then start a new game"""
        if self._stop_requested:
            self._debug("Match ended but stop requested - not restarting")
            return
        self._debug("Match ended - scheduling restart in 10 seconds")
        self._human_log("\n=== MATCH ENDED ===")
        if won is True:
            self._human_log("Result: WIN")
        elif won is False:
            self._human_log("Result: LOSS")
        self._human_log("Restarting in 10 seconds...\n")
        # Stop inactivity timer since match ended
        if hasattr(self.controller, 'stop_inactivity_timer'):
            self.controller.stop_inactivity_timer()
        restart_timer = threading.Timer(10.0, self._restart_game)
        self._timers.append(restart_timer)
        restart_timer.start()

    def _restart_game(self):
        """Reset state and start a new game"""
        if self._stop_requested:
            self._debug("Stop requested - skipping restart")
            return
        self._debug("Restarting game...")
        self._human_log("=== STARTING NEW GAME ===\n")

        # Reset Game state
        self.last_logged_turn = -1
        self.game_started = False
        self.starting_hand_logged = False
        self._last_action_delay_turn = -1

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
        self._human_log("Keeping hand")
        self.game_started = True  # Mark game as started after mulligan
        keep = self.ai.generate_keep(card_list)
        bot_logger.log_mulligan_decision(keep, len(card_list))
        self.controller.keep(keep)

    def _human_log(self, message):
        """Human-readable log - clean, simple messages"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        with open(self.human_log_file, 'a') as f:
            f.write(f"[{timestamp}] {message}\n")

    def _debug(self, message):
        """Debug log - detailed technical information"""
        bot_logger.log_info(message)

    def _refresh_card_data(self):
        """Refresh cards.json from local MTGA data (Linux/Windows Steam path)."""
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
                try:
                    CardInfo.refresh_cards_from_scryfall_bulk_if_needed()
                    self._debug("Card data refresh: Scryfall bulk delta check done.")
                except Exception as e:
                    self._debug(f"Card data refresh: Scryfall bulk delta failed: {e}")
                return
            base_candidates = [
                os.path.expanduser("~/.local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"),
                os.path.expanduser("~/.steam/steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"),
                os.path.expanduser("~/.steam/root/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"),
                os.path.expanduser(
                    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"
                ),
                r"C:\\Program Files (x86)\\Steam\\steamapps\\common\\MTGA\\MTGA_Data\\Downloads\\Raw",
                r"C:\\Program Files\\Steam\\steamapps\\common\\MTGA\\MTGA_Data\\Downloads\\Raw",
            ]
            data_dir = next((p for p in base_candidates if os.path.isdir(p)), "")
            if not data_dir:
                self._debug("Card data refresh: MTGA data dir not found, skipping export.")
                return
            self._debug(f"Card data refresh: exporting from {data_dir}")
            result = subprocess.run(
                [sys.executable, "mtga_cards_export.py", "--data-dir", data_dir],
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
            try:
                CardInfo.refresh_cards_from_scryfall_bulk_if_needed()
                self._debug("Card data refresh: Scryfall bulk delta check done.")
            except Exception as e:
                self._debug(f"Card data refresh: Scryfall bulk delta failed: {e}")
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

    def decision_method(self, current_game_state: GameState):
        if self._stop_requested:
            return
        # Don't do anything before game has started
        if not self.game_started:
            self._debug("decision_method called but game not started yet, ignoring")
            return

        # Reset inactivity timer since we're making a decision
        if hasattr(self.controller, 'reset_inactivity_timer'):
            self.controller.reset_inactivity_timer()

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

            if active_player == decision_player and turn_num != self._last_action_delay_turn:
                self._debug("Active turn delay: waiting 2 seconds before actions")
                time.sleep(2.0)
                self._last_action_delay_turn = turn_num

            # Log new turn in human log (only once per turn, only for MY turns)
            if turn_num != self.last_logged_turn and active_player == 1:
                self.last_logged_turn = turn_num

                # Log starting hand on first turn (when dict is populated)
                if not self.starting_hand_logged:
                    self.starting_hand_logged = True
                    self._human_log("Starting hand:")
                    try:
                        inst_id_grp_id_dict = self.controller.get_inst_id_grp_id_dict()
                        self._debug(f"Logging starting hand: {len(inst_id_grp_id_dict)} cards")
                        for inst_id, grp_id in inst_id_grp_id_dict.items():
                            card_info = CardInfo.get_card_info(grp_id)
                            if card_info:
                                types = card_info.get('types', [])
                                type_str = '/'.join(types) if types else 'Unknown'
                                self._human_log(f"  - {type_str} (inst={inst_id}, grp={grp_id})")
                            else:
                                self._human_log(f"  - Unknown (inst={inst_id}, grp={grp_id})")
                    except Exception as e:
                        self._debug(f"Error logging starting hand: {e}")

                self._human_log(f"\n--- Turn {turn_num} (ME) ---")

                # Count available mana from ActionType_Activate_Mana actions (unique lands)
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
                            if len(mana_sources) > 0:
                                self._human_log(f"Available Mana: {len(mana_sources)}")
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

            # Generate move
            self._debug("Calling AI.generate_move()")
            move = self.ai.generate_move(current_game_state, self.controller.get_inst_id_grp_id_dict())
            self._debug(f"AI returned move: {move}")

            if not move:
                self._debug("ERROR: AI returned empty move!")
                return

            move_name = list(move.keys())[0]
            bot_logger.log_decision(move_name, move.get(move_name))
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
                        self._human_log(f"  -> Play Land {card_id_str}")
                    elif 'Creature' in types:
                        self._human_log(f"  -> Cast Creature {card_id_str}")
                    else:
                        self._human_log(f"  -> Cast {card_id_str}")
                else:
                    self._human_log(f"  -> Cast {card_id_str}")
                self.controller.cast(inst_id)
            elif move_name == 'attack':
                self._debug(f"Attacking with {move[move_name][0]}")
                self.controller.attack(move[move_name][0])
            elif move_name == 'all_attack':
                self._human_log("  -> Attack All")
                self._debug("Executing all_attack")
                self.controller.all_attack()
            elif move_name == 'block':
                self._debug(f"Blocking: {move[move_name]}")
                self.controller.block(move[move_name][0], move[move_name][1])
            elif move_name == 'all_block':
                self._human_log("  -> Block All")
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
            self._human_log(f"  [ERROR] Bot encountered an error: {e}")
