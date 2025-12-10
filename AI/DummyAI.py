from AI.AIInterface import AIKernel
from AI.Utilities.ManaPool import ManaPool
from Controller.Utilities.GameState import GameState
import AI.Utilities.CardInfo as CardInfo
from datetime import datetime
import traceback


class DummyAI(AIKernel):

    def __init__(self):
        self.__current_turn_num = 0
        self.__has_land_been_played_this_turn = False
        self.__mana_pool = ManaPool()
        self.__bot_log_file = "bot.log"

    def _debug(self, message):
        """Debug log for AI decisions"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            with open(self.__bot_log_file, 'a') as f:
                f.write(f"[{timestamp}] [AI] {message}\n")
        except Exception:
            pass

    def get_mana_pool(self):
        """Public method to access mana pool for logging"""
        return self.__mana_pool

    def generate_keep(self, card_list) -> bool:
        self._debug("generate_keep called - keeping hand")
        return True

    def __new_turn_check(self, current_game_state: 'GameState'):
        try:
            turn_info = current_game_state.get_turn_info()
            if not turn_info:
                self._debug("WARNING: turn_info is None in __new_turn_check")
                return

            new_turn_num = turn_info.get('turnNumber', 0)
            if self.__current_turn_num < new_turn_num:
                self.__current_turn_num = new_turn_num
                self.__has_land_been_played_this_turn = False
                self._debug(f"New turn {new_turn_num} - resetting land played flag")
                self._debug(f"Mana before reset: {self.__mana_pool.get_available_mana()}")
                self.__mana_pool.reset_mana()
                self._debug(f"Mana after reset: {self.__mana_pool.get_available_mana()}")
        except Exception as e:
            self._debug(f"ERROR in __new_turn_check: {e}\n{traceback.format_exc()}")

    def generate_move(self, game_state: GameState, inst_id_grp_id_dict):
        move = {'resolve': []}

        try:
            self._debug(f"generate_move called - mana pool: {self.__mana_pool.get_available_mana()}")
            self.__new_turn_check(game_state)

            turn_info = game_state.get_turn_info()
            if not turn_info:
                self._debug("ERROR: turn_info is None!")
                return move

            # Safely get actions
            try:
                action_list = game_state.get_actions()
            except Exception as e:
                self._debug(f"ERROR getting actions: {e}")
                action_list = []

            if not action_list:
                self._debug("No actions available")
                return move

            self._debug(f"Actions available: {len(action_list)}")

            active_player = turn_info.get('activePlayer', 0)
            decision_player = turn_info.get('decisionPlayer', 0)
            priority_player = turn_info.get('priorityPlayer', 0)
            phase = turn_info.get('phase', '')
            step = turn_info.get('step', '')

            self._debug(f"State: active={active_player}, decision={decision_player}, priority={priority_player}, phase={phase}, step={step}")

            # Only act if it's our turn to decide
            if active_player == 1 and decision_player == 1 and priority_player == 1:

                # Combat phase - attack
                if phase == 'Phase_Combat' and step == 'Step_DeclareAttack':
                    self._debug("Combat phase - declaring all attackers")
                    move = {'all_attack': []}
                    return move

                # Main phases - play lands and cast spells
                elif phase in ['Phase_Main1', 'Phase_Main2']:

                    # First: try to play a land
                    if not self.__has_land_been_played_this_turn:
                        for action_wrapper in action_list:
                            action = action_wrapper.get('action', {})
                            if action.get('actionType') == 'ActionType_Play':
                                instance_id = action.get('instanceId')
                                land_grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                                mana_color = CardInfo.get_land_mana_color(land_grp_id)

                                self._debug(f"Playing land: instanceId={instance_id}, grpId={land_grp_id}, mana_color={mana_color}")

                                move = {'cast': [instance_id]}
                                self.__has_land_been_played_this_turn = True

                                if mana_color:
                                    self.__mana_pool.add_mana(mana_color, 1)
                                    self._debug(f"Added {mana_color} mana, pool now: {self.__mana_pool.get_available_mana()}")

                                return move

                    # Second: try to cast a creature
                    for action_wrapper in action_list:
                        action = action_wrapper.get('action', {})
                        if action.get('actionType') == 'ActionType_Cast':
                            instance_id = action.get('instanceId')
                            grp_id = inst_id_grp_id_dict.get(instance_id)
                            card_info = CardInfo.get_card_info(grp_id)

                            if not card_info:
                                self._debug(f"No card info for grpId={grp_id}")
                                continue

                            card_types = card_info.get('types', [])
                            card_name = card_info.get('name', f'Card#{instance_id}')

                            # Check mana cost
                            mana_cost_str = card_info.get('manaCost', '')
                            cmc = CardInfo.calculate_cmc(mana_cost_str)
                            avail_mana_dict = self.__mana_pool.get_available_mana()
                            total_avail = sum(avail_mana_dict.values())

                            self._debug(f"Checking: {card_name} (types={card_types}, cmc={cmc}, avail_mana={total_avail})")

                            if 'Creature' in card_types:
                                if cmc <= total_avail:
                                    self._debug(f"CASTING: {card_name} (instanceId={instance_id})")
                                    # Deduct mana spent on this creature
                                    self.__mana_pool.spend_mana(cmc)
                                    self._debug(f"Spent {cmc} mana, pool now: {self.__mana_pool.get_available_mana()}")
                                    move = {'cast': [instance_id]}
                                    return move
                                else:
                                    self._debug(f"Not enough mana for {card_name} (need {cmc}, have {total_avail})")

            self._debug(f"Returning default move: {move}")
            return move

        except Exception as e:
            self._debug(f"CRITICAL ERROR in generate_move: {e}\n{traceback.format_exc()}")
            return {'resolve': []}
