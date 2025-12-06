from AI.AIInterface import AIKernel
from AI.Utilities.ManaPool import ManaPool
from Controller.Utilities.GameState import GameState
import AI.Utilities.CardInfo as CardInfo

class DummyAI(AIKernel):

    def __init__(self):
        self.__current_turn_num = 0
        self.__has_land_been_played_this_turn = False
        self.__mana_pool = ManaPool()

    def generate_keep(self, card_list) -> bool:
        return True

    def __new_turn_check(self, current_game_state: 'GameState'):
        turn_info = current_game_state.get_turn_info()
        new_turn_num = turn_info['turnNumber']
        if self.__current_turn_num < new_turn_num:
            self.__current_turn_num = new_turn_num
            self.__has_land_been_played_this_turn = False
            self.__mana_pool.reset_mana()

    def generate_move(self, game_state: GameState, inst_id_grp_id_dict):
        move = {'resolve': []}
        self.__new_turn_check(game_state)
        turn_info = game_state.get_turn_info()
        action_list = game_state.get_actions()
        if len(action_list) > 0:
            if turn_info['activePlayer'] == 1 and turn_info['decisionPlayer'] == 1 and turn_info['priorityPlayer'] == 1:
                if turn_info['phase'] == 'Phase_Combat' and turn_info['step'] == 'Step_DeclareAttack':
                    move = {'all_attack': []}
                elif turn_info['phase'] == 'Phase_Main1' or turn_info['phase'] == 'Phase_Main2':
                    for action_wrapper in action_list:
                        action = action_wrapper['action']
                        if action['actionType'] == 'ActionType_Play' and not self.__has_land_been_played_this_turn:
                            move = {'cast': [action['instanceId']]}
                            self.__has_land_been_played_this_turn = True
                            self.__mana_pool.add_mana('red', 1)
                            break
                        elif action['actionType'] == 'ActionType_Cast' and \
                                self.__mana_pool.has_mana(action['manaCost']):
                            card_info = CardInfo.get_card_info(inst_id_grp_id_dict[action['instanceId']])
                            if 'Creature' in card_info['type_line']:
                                move = {'cast': [action['instanceId']]}
                                self.__mana_pool.use_mana(action['manaCost'])
                                break
        return move
