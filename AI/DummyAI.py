from AI.AIInterface import AIKernel
from AI.Utilities.ManaPool import ManaPool
from Controller.Utilities.GameState import GameState
import AI.Utilities.CardInfo as CardInfo
from datetime import datetime

class DummyAI(AIKernel):

    def __init__(self):
        self.__current_turn_num = 0
        self.__has_land_been_played_this_turn = False
        self.__mana_pool = ManaPool()
        self.__log_file = "bot.log"

    def _log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        with open(self.__log_file, 'a') as f:
            f.write(f"[{timestamp}] [AI] {message}\n")

    def generate_keep(self, card_list) -> bool:
        return True

    def __new_turn_check(self, current_game_state: 'GameState'):
        turn_info = current_game_state.get_turn_info()
        new_turn_num = turn_info['turnNumber']
        if self.__current_turn_num < new_turn_num:
            self.__current_turn_num = new_turn_num
            self.__has_land_been_played_this_turn = False
            self._log(f"New turn {new_turn_num}, resetting mana. Total mana: {self.__mana_pool.get_total_mana()}")
            self.__mana_pool.reset_mana()
            self._log(f"After reset, available mana: {self.__mana_pool.get_available_mana()}")

    def generate_move(self, game_state: GameState, inst_id_grp_id_dict):
        move = {'resolve': []}
        self._log(f"generate_move called. Current pool: {self.__mana_pool.get_available_mana()}, id(self)={id(self)}")
        self.__new_turn_check(game_state)
        turn_info = game_state.get_turn_info()
        action_list = game_state.get_actions()
        if len(action_list) > 0:
            if turn_info['activePlayer'] == 1 and turn_info['decisionPlayer'] == 1 and turn_info['priorityPlayer'] == 1:
                if turn_info['phase'] == 'Phase_Combat' and turn_info['step'] == 'Step_DeclareAttack':
                    move = {'all_attack': []}
                elif turn_info['phase'] == 'Phase_Main1' or turn_info['phase'] == 'Phase_Main2':
                    # First pass: play a land if possible
                    for action_wrapper in action_list:
                        action = action_wrapper['action']
                        if action['actionType'] == 'ActionType_Play' and not self.__has_land_been_played_this_turn:
                            move = {'cast': [action['instanceId']]}
                            self.__has_land_been_played_this_turn = True
                            # Get the correct mana color from the land card
                            # grpId might be in action directly or we need to look it up via instanceId
                            instance_id = action['instanceId']
                            land_grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                            mana_color = CardInfo.get_land_mana_color(land_grp_id)
                            if mana_color:
                                self.__mana_pool.add_mana(mana_color, 1)
                            self._log(f"Playing land instanceId={instance_id}, grpId={land_grp_id}, mana_color={mana_color}")
                            return move

                    # Second pass: cast a creature if available
                    # MTGA already tells us which spells are castable via ActionType_Cast
                    for action_wrapper in action_list:
                        action = action_wrapper['action']
                        if action['actionType'] == 'ActionType_Cast':
                            instance_id = action['instanceId']
                            grp_id = inst_id_grp_id_dict.get(instance_id)
                            card_info = CardInfo.get_card_info(grp_id)
                            card_types = card_info.get('types', []) if card_info else []
                            
                            # Check mana
                            mana_cost_str = card_info.get('manaCost', '') if card_info else ''
                            cmc = CardInfo.calculate_cmc(mana_cost_str)
                            avail_mana_dict = self.__mana_pool.get_available_mana()
                            total_avail = sum(avail_mana_dict.values())

                            self._log(f"Checking cast: instanceId={instance_id}, grpId={grp_id}, types={card_types}, cmc={cmc}, avail={total_avail}")
                            if card_info and 'Creature' in card_types:
                                if cmc <= total_avail:
                                    move = {'cast': [instance_id]}
                                    self._log(f"Casting creature instanceId={instance_id}")
                                    return move
                                else:
                                    self._log(f"Not casting creature instanceId={instance_id} due to insufficient mana (cmc={cmc}, avail={total_avail})")
        return move
