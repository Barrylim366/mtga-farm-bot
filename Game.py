from Controller.ControllerInterface import ControllerSecondary
from AI.AIInterface import AIKernel
from Controller.Utilities.GameState import GameState
import AI.Utilities.CardInfo as CardInfo
from datetime import datetime


class Game:

    def __init__(self, controller: ControllerSecondary, ai: AIKernel):
        self.ai = ai
        self.controller = controller
        self.log_file = "bot.log"
        self.last_turn_num = -1
        self.last_active_player = -1
        self.logged_actions = set() # To prevent duplicate logging of the same action ID
        # Clear log file on start
        with open(self.log_file, 'w') as f:
            f.write("")

    def start(self):
        self.controller.start_game()
        self.controller.set_mulligan_decision_callback(self.mulligan_decision_method)
        self.controller.set_decision_callback(self.decision_method)
        self.controller.set_action_success_callback(self.on_action_success)

    def mulligan_decision_method(self, card_list):
        keep = self.ai.generate_keep(card_list)
        self.controller.keep(keep)

    def _log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_file, 'a') as f:
            f.write(f"[{timestamp}] {message}\n")

    def on_action_success(self, action_dict):
        """
        Callback from Controller when an action is confirmed in the log.
        """
        action_type = action_dict.get('actionType', 'Unknown')
        instance_id = action_dict.get('instanceId')
        
        # Unique key for this action: (ActionType, InstanceID)
        # Using instance_id is generally safe for deduplication within a game.
        action_key = (action_type, instance_id)
        
        if action_key in self.logged_actions:
            return
            
        self.logged_actions.add(action_key)
        
        if action_type == 'ActionType_Cast':
             action_desc = "spell"
             if instance_id:
                 grp_id = self.controller.get_inst_id_grp_id_dict().get(instance_id)
                 card_info = CardInfo.get_card_info(grp_id)
                 if card_info:
                     types = card_info.get('types', [])
                     if 'Creature' in types:
                         action_desc = "creature"
                     elif 'Instant' in types or 'Sorcery' in types:
                         action_desc = "spell"
                     elif 'Enchantment' in types:
                         action_desc = "enchantment"
                     elif 'Artifact' in types:
                         action_desc = "artifact"
                     elif 'Planeswalker' in types:
                         action_desc = "planeswalker"
             self._log(f"Casted {action_desc} successfully")
             
        elif action_type == 'ActionType_Play':
             self._log("Played Land successfully")

    def decision_method(self, current_game_state: GameState):
        # Logging Turn Info
        turn_info = current_game_state.get_turn_info()
        turn_num = turn_info['turnNumber']
        active_player = turn_info['activePlayer']

        if (turn_num != self.last_turn_num or active_player != self.last_active_player) and active_player == 1:
            self.last_turn_num = turn_num
            self.last_active_player = active_player
            self._log(f"Turn {turn_num} (my turn):")

        move = self.ai.generate_move(current_game_state, self.controller.get_inst_id_grp_id_dict())
        print(move)
        move_name = list(move.keys())[0]

        # Log Action
        if move_name == 'cast':
            inst_id = int(move[move_name][0])
            # Logging is now handled by on_action_success for successful casts
            self.controller.cast(inst_id)
        elif move_name == 'attack':
            self._log("Attacking...")
            self.controller.attack(move[move_name][0])
        elif move_name == 'all_attack':
            self._log("Attacking all...")
            self.controller.all_attack()
        elif move_name == 'block':
            self._log("Blocking...")
            self.controller.block(move[move_name][0], move[move_name][1])
        elif move_name == 'all_block':
             self._log("Blocking all...")
             self.controller.all_block()
        elif move_name == 'select_target':
            self._log("Selecting target...")
            self.controller.select_target(move[move_name][0])
        elif move_name == 'activate_ability':
            self._log("Activating ability...")
            self.controller.activate_ability(move[move_name][0], move[move_name][1])
        elif move_name == 'resolve':
            self.controller.resolve()
            # self._log("Resolved priority") # Optional to reduce spam
        elif move_name == 'auto_pass':
            self.controller.auto_pass()
            self._log("Auto passed")
        elif move_name == 'unconditional_auto_pass':
            self.controller.unconditional_auto_pass()
            self._log("Unconditionally auto passed")
        else:
            print("Move that was generated was not valid... This should never be reached")
