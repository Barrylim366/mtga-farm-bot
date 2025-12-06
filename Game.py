from Controller.ControllerInterface import ControllerSecondary
from AI.AIInterface import AIKernel
from Controller.Utilities.GameState import GameState


class Game:

    def __init__(self, controller: ControllerSecondary, ai: AIKernel):
        self.ai = ai
        self.controller = controller

    def start(self):
        self.controller.start_game()
        self.controller.set_mulligan_decision_callback(self.mulligan_decision_method)
        self.controller.set_decision_callback(self.decision_method)

    def mulligan_decision_method(self, card_list):
        keep = self.ai.generate_keep(card_list)
        self.controller.keep(keep)

    def decision_method(self, current_game_state: GameState):
        move = self.ai.generate_move(current_game_state, self.controller.get_inst_id_grp_id_dict())
        print(move)
        move_name = list(move.keys())[0]
        if move_name == 'attack':
            self.controller.attack(move[move_name][0])
        elif move_name == 'all_attack':
            self.controller.all_attack()
        elif move_name == 'cast':
            self.controller.cast(int(move[move_name][0]))
        elif move_name == 'block':
            self.controller.block(move[move_name][0], move[move_name][1])
        elif move_name == 'select_target':
            self.controller.select_target(move[move_name][0])
        elif move_name == 'activate_ability':
            self.controller.activate_ability(move[move_name][0], move[move_name][1])
        elif move_name == 'resolve':
            self.controller.resolve()
        elif move_name == 'auto_pass':
            self.controller.auto_pass()
        elif move_name == 'unconditional_auto_pass':
            self.controller.unconditional_auto_pass()
        elif move_name == 'all_block':
            self.controller.all_block()
        else:
            print("Move that was generated was not valid... This should never be reached")
