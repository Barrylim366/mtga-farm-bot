from AI.AIInterface import AIKernel
from Controller.Utilities.GameState import GameState
import AI.Utilities.CardInfo as CardInfo
from datetime import datetime
import traceback


class DummyAI(AIKernel):

    def __init__(self):
        self.__current_turn_num = 0
        self.__has_land_been_played_this_turn = False
        self.__bot_log_file = "bot.log"

    def reset(self):
        """Reset AI state for a new game"""
        self._debug("Resetting AI state for new game")
        self.__current_turn_num = 0
        self.__has_land_been_played_this_turn = False
        self._debug("AI state reset complete")

    def _debug(self, message):
        """Debug log for AI decisions"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            with open(self.__bot_log_file, 'a') as f:
                f.write(f"[{timestamp}] [AI] {message}\n")
        except Exception:
            pass

    def _get_available_mana_colors(self, action_list, inst_id_grp_id_dict):
        """Get available mana colors and total sources from ActionType_Activate_Mana actions.

        Returns:
            - mana_colors: set of available colors (e.g., {'black', 'green', 'blue'})
            - total_sources: number of unique mana sources (for CMC check)

        Note: For dual lands, we count them as providing BOTH colors but only 1 source.
        Uses Scryfall to get the produced mana colors for all lands."""
        mana_colors = set()
        mana_sources = {}  # instanceId -> set of colors

        for action_wrapper in action_list:
            action = action_wrapper.get('action', {})
            if action.get('actionType') == 'ActionType_Activate_Mana':
                instance_id = action.get('instanceId')

                if instance_id:
                    if instance_id not in mana_sources:
                        mana_sources[instance_id] = set()

                    # Use Scryfall to get produced mana colors for ALL lands
                    grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                    if grp_id:
                        produced_colors = CardInfo.get_land_produced_colors(grp_id)
                        if produced_colors:
                            mana_sources[instance_id].update(produced_colors)
                            mana_colors.update(produced_colors)
                            self._debug(f"Scryfall: instId={instance_id}, grpId={grp_id} produces {produced_colors}")
                        else:
                            self._debug(f"No Scryfall data for land: instId={instance_id}, grpId={grp_id}")
                    else:
                        self._debug(f"No grpId for mana source: instId={instance_id}")

        total_sources = len(mana_sources)
        self._debug(f"Mana sources: {total_sources}, colors available: {mana_colors}")
        return mana_colors, total_sources

    def _can_cast_with_mana_cost(self, action_mana_cost, available_colors, total_mana):
        """Check if we can pay a mana cost from the action's manaCost field.

        Parameters:
            action_mana_cost: List like [{'color': ['ManaColor_Red'], 'count': 1}, ...]
            available_colors: Set of available color strings
            total_mana: Total number of mana sources
        Returns:
            True if we can pay the cost
        """
        if not action_mana_cost:
            return True

        color_map = {
            'ManaColor_White': 'white',
            'ManaColor_Blue': 'blue',
            'ManaColor_Black': 'black',
            'ManaColor_Red': 'red',
            'ManaColor_Green': 'green',
            'ManaColor_Generic': 'generic'
        }

        total_needed = 0
        for cost_entry in action_mana_cost:
            colors = cost_entry.get('color', [])
            count = cost_entry.get('count', 0)
            total_needed += count

            if not colors:
                continue

            mana_color = color_map.get(colors[0], 'generic')

            # Generic mana can be paid by any source
            if mana_color == 'generic':
                continue

            # Check if we have this color available
            if mana_color not in available_colors:
                return False

        # Check total mana
        return total_mana >= total_needed

    def generate_keep(self, card_list) -> bool:
        self._debug("generate_keep called - keeping hand")
        return True

    def __new_turn_check(self, current_game_state: 'GameState'):
        """Check if it's a new turn and reset land played flag"""
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
        except Exception as e:
            self._debug(f"ERROR in __new_turn_check: {e}\n{traceback.format_exc()}")

    def generate_move(self, game_state: GameState, inst_id_grp_id_dict):
        move = {'resolve': []}

        try:
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

            # Get available mana colors and total sources
            available_colors, total_mana = self._get_available_mana_colors(action_list, inst_id_grp_id_dict)
            self._debug(f"Actions available: {len(action_list)}")

            active_player = turn_info.get('activePlayer', 0)
            decision_player = turn_info.get('decisionPlayer', 0)
            priority_player = turn_info.get('priorityPlayer', 0)
            phase = turn_info.get('phase', '')
            step = turn_info.get('step', '')

            self._debug(f"State: active={active_player}, decision={decision_player}, priority={priority_player}, phase={phase}, step={step}")

            # Determine which seat we're acting for.
            # The controller is expected to only call the AI when it's our priority/decision.
            my_seat = decision_player or 1

            # If we somehow got called without priority, just pass/resolve.
            if priority_player and priority_player != my_seat:
                self._debug(f"Not our priority (priority={priority_player}, my_seat={my_seat})")
                self._debug(f"Returning default move: {move}")
                return move

            # Only do proactive actions (play land / cast / attack) on our active turn.
            if active_player == my_seat and decision_player == my_seat:

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

                                self._debug(f"Playing land: instanceId={instance_id}, grpId={land_grp_id}")

                                move = {'cast': [instance_id]}
                                self.__has_land_been_played_this_turn = True
                                return move

                    # Second: try to cast a creature
                    # Use the manaCost from the action to check color requirements
                    cast_actions = []
                    for action_wrapper in action_list:
                        action = action_wrapper.get('action', {})
                        if action.get('actionType') == 'ActionType_Cast':
                            instance_id = action.get('instanceId')
                            action_mana_cost = action.get('manaCost', [])
                            grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                            card_info = CardInfo.get_card_info(grp_id)

                            if not card_info:
                                self._debug(f"No card info for grpId={grp_id}")
                                continue

                            card_types = card_info.get('types', [])
                            if 'Creature' not in card_types:
                                continue

                            card_name = card_info.get('name', f'Card#{instance_id}')
                            mana_cost_str = card_info.get('manaCost', '')
                            cmc = CardInfo.calculate_cmc(mana_cost_str)

                            # Check if we can pay the mana cost (color + total)
                            if self._can_cast_with_mana_cost(action_mana_cost, available_colors, total_mana):
                                cast_actions.append((cmc, instance_id, card_name, mana_cost_str))
                                self._debug(f"Can cast: {card_name} (cost={mana_cost_str}, cmc={cmc})")
                            else:
                                self._debug(f"Cannot cast {card_name} (cost={mana_cost_str}, colors={available_colors}, mana={total_mana})")

                    # Cast the cheapest creature we can afford
                    if cast_actions:
                        cast_actions.sort(key=lambda x: x[0])  # Sort by CMC
                        cmc, instance_id, card_name, mana_cost = cast_actions[0]
                        self._debug(f"CASTING: {card_name} (instanceId={instance_id}, cost={mana_cost})")
                        move = {'cast': [instance_id]}
                        return move

            self._debug(f"Returning default move: {move}")
            return move

        except Exception as e:
            self._debug(f"CRITICAL ERROR in generate_move: {e}\n{traceback.format_exc()}")
            return {'resolve': []}
