from Controller.ControllerInterface import ControllerSecondary
from AI.AIInterface import AIKernel
from Controller.Utilities.GameState import GameState
import AI.Utilities.CardInfo as CardInfo
from datetime import datetime
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

        # Clear log files on start
        with open(self.human_log_file, 'w') as f:
            f.write("=== MTGA Bot Session Started ===\n")
            f.write(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 35 + "\n\n")
        # Initialize bot.log with centralized logger
        bot_logger.init_bot_log()

    def start(self):
        self._debug("Game.start() called")
        self.controller.start_game()
        self.controller.set_mulligan_decision_callback(self.mulligan_decision_method)
        self.controller.set_decision_callback(self.decision_method)
        self.controller.set_action_success_callback(self.on_action_success)
        self.controller.set_match_end_callback(self.on_match_end)
        self._debug("All callbacks registered")

    def on_match_end(self):
        """Called when a match ends - wait 20 seconds then start a new game"""
        self._debug("Match ended - scheduling restart in 20 seconds")
        self._human_log("\n=== MATCH ENDED ===")
        self._human_log("Restarting in 20 seconds...\n")
        # Stop inactivity timer since match ended
        if hasattr(self.controller, 'stop_inactivity_timer'):
            self.controller.stop_inactivity_timer()
        threading.Timer(20.0, self._restart_game).start()

    def _restart_game(self):
        """Reset state and start a new game"""
        self._debug("Restarting game...")
        self._human_log("=== STARTING NEW GAME ===\n")

        # Reset Game state
        self.last_logged_turn = -1
        self.game_started = False
        self.starting_hand_logged = False

        # Reset AI state
        if hasattr(self.ai, 'reset'):
            self.ai.reset()

        # Reset Controller state
        if hasattr(self.controller, 'reset_for_new_game'):
            self.controller.reset_for_new_game()

        # Start new game from home screen
        self._debug("Clicking queue button to start new game")
        self.controller.start_game_from_home_screen()
        self._debug("New game queued")

        # Schedule retry queue click after 30 seconds if game hasn't started
        threading.Timer(30.0, self._retry_queue_if_needed).start()

    def _retry_queue_if_needed(self):
        """Retry clicking queue button if game hasn't started yet"""
        if not self.game_started:
            self._debug("Game not started after 30s - retrying queue button")
            self._human_log("Retrying queue...")
            self.controller.start_game_from_home_screen()
            # Schedule next retry
            threading.Timer(30.0, self._retry_queue_if_needed).start()

    def mulligan_decision_method(self, card_list):
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
        try:
            action_type = action_dict.get('actionType', 'Unknown')
            instance_id = action_dict.get('instanceId')
            self._debug(f"on_action_success: type={action_type}, instanceId={instance_id}")
        except Exception as e:
            self._debug(f"ERROR in on_action_success: {e}")

    def decision_method(self, current_game_state: GameState):
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
