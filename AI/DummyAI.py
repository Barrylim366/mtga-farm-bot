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
            - sources: list of sets of colors per mana source

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
        sources = [set(colors) for colors in mana_sources.values() if colors]
        self._debug(f"Mana sources: {total_sources}, colors available: {mana_colors}")
        return mana_colors, total_sources, sources

    def _can_cast_with_mana_cost(self, action_mana_cost, available_colors, total_mana, sources):
        """Check if we can pay a mana cost from the action's manaCost field.

        Parameters:
            action_mana_cost: List like [{'color': ['ManaColor_Red'], 'count': 1}, ...]
            available_colors: Set of available color strings
            total_mana: Total number of mana sources
            sources: List of sets of colors per mana source
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
        colored_requirements = []
        for cost_entry in action_mana_cost:
            colors = cost_entry.get('color', [])
            count = cost_entry.get('count', 0)
            total_needed += count

            if not colors:
                continue

            color_options = {color_map.get(c, 'generic') for c in colors}
            if 'generic' in color_options:
                continue
            for _ in range(count):
                colored_requirements.append(color_options)

        if total_mana < total_needed:
            return False

        # Fast fail if a required color isn't available at all.
        for req in colored_requirements:
            if not (req & available_colors):
                return False

        if not colored_requirements:
            return True

        sources_list = [set(s) for s in sources]
        if len(colored_requirements) > len(sources_list):
            return False

        # Precompute candidates for each requirement.
        reqs = list(colored_requirements)
        candidates = [set(i for i, s in enumerate(sources_list) if s & req) for req in reqs]

        def _search(remaining_reqs, remaining_sources, cand_lists):
            if not remaining_reqs:
                return True
            min_idx = min(range(len(remaining_reqs)), key=lambda i: len(cand_lists[i]))
            if not cand_lists[min_idx]:
                return False
            for src_idx in list(cand_lists[min_idx]):
                if src_idx not in remaining_sources:
                    continue
                new_sources = set(remaining_sources)
                new_sources.remove(src_idx)
                new_reqs = [r for i, r in enumerate(remaining_reqs) if i != min_idx]
                new_cands = []
                for i, _r in enumerate(remaining_reqs):
                    if i == min_idx:
                        continue
                    new_cands.append({s for s in cand_lists[i] if s != src_idx})
                if _search(new_reqs, new_sources, new_cands):
                    return True
            return False

        colored_ok = _search(reqs, set(range(len(sources_list))), candidates)
        if not colored_ok:
            return False

        colored_needed = len(colored_requirements)
        remaining_sources = total_mana - colored_needed
        generic_needed = total_needed - colored_needed
        return remaining_sources >= generic_needed

    def _choose_land_to_play(self, action_list, inst_id_grp_id_dict, available_colors, total_mana, sources):
        """Choose a land that maximizes post-land castability for creatures."""
        land_actions = []
        for action_wrapper in action_list:
            action = action_wrapper.get('action', {})
            if action.get('actionType') == 'ActionType_Play':
                land_actions.append(action)

        if not land_actions:
            return None

        creature_actions = []
        for action_wrapper in action_list:
            action = action_wrapper.get('action', {})
            if action.get('actionType') != 'ActionType_Cast':
                continue
            instance_id = action.get('instanceId')
            grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
            card_info = CardInfo.get_card_info(grp_id)
            if not card_info:
                continue
            if 'Creature' not in card_info.get('types', []):
                continue
            creature_actions.append((instance_id, action.get('manaCost', []), card_info))

        def _score_land(action):
            instance_id = action.get('instanceId')
            grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
            produced_colors = CardInfo.get_land_produced_colors(grp_id) or set()

            sim_colors = set(available_colors)
            sim_colors.update(produced_colors)
            sim_total_mana = total_mana + 1
            sim_sources = list(sources) + [set(produced_colors)] if produced_colors else list(sources)

            castable = []
            for _, mana_cost, card_info in creature_actions:
                if self._can_cast_with_mana_cost(mana_cost, sim_colors, sim_total_mana, sim_sources):
                    mana_cost_str = card_info.get('manaCost', '')
                    cmc = CardInfo.calculate_cmc(mana_cost_str)
                    castable.append((cmc, card_info.get('name', '')))

            castable_count = len(castable)
            best_cmc = min((cmc for cmc, _ in castable), default=999)
            new_colors = len(set(produced_colors) - set(available_colors))

            # Prefer enabling any casts, then more options, then lower CMC, then new colors.
            return (1 if castable_count > 0 else 0, castable_count, -best_cmc, new_colors)

        best_action = max(land_actions, key=_score_land)
        return best_action.get('instanceId')

    def _needs_attack_target_selection(self, action_list):
        """Detect attack target selection actions (e.g., planeswalker present)."""
        for action_wrapper in action_list:
            action = action_wrapper.get('action', {})
            action_type = action.get('actionType', '')
            if not action_type:
                continue
            if "AttackTarget" in action_type or "SelectAttackTarget" in action_type:
                return True
            if "Target" in action_type and ("Attack" in action_type or "Combat" in action_type):
                return True
        return False

    def _needs_spell_target_selection(self, action_list):
        """Detect non-combat target selection prompts (spells/abilities)."""
        for action_wrapper in action_list:
            action = action_wrapper.get('action', {})
            action_type = action.get('actionType', '')
            if not action_type:
                continue
            if "Target" in action_type and "Attack" not in action_type and "Combat" not in action_type:
                return True
        return False

    def _find_phoenix_chick_activation(self, action_list, inst_id_grp_id_dict, available_colors, total_mana, sources):
        """Find a Phoenix Chick activation action we can pay for."""
        for action_wrapper in action_list:
            action = action_wrapper.get('action', {})
            action_type = action.get('actionType', '')
            if not action_type:
                continue
            if not action_type.startswith('ActionType_Activate'):
                continue
            if action_type == 'ActionType_Activate_Mana':
                continue

            instance_id = action.get('instanceId')
            if instance_id is None:
                continue
            grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
            card_info = CardInfo.get_card_info(grp_id) if grp_id else None
            if not card_info or card_info.get('name') != 'Phoenix Chick':
                continue

            action_mana_cost = action.get('manaCost', [])
            if action_mana_cost:
                if not self._can_cast_with_mana_cost(action_mana_cost, available_colors, total_mana, sources):
                    self._debug("Phoenix Chick activation available but mana cost not payable")
                    continue
            else:
                rr_cost = [{'color': ['ManaColor_Red'], 'count': 2}]
                if not self._can_cast_with_mana_cost(rr_cost, available_colors, total_mana, sources):
                    self._debug("Phoenix Chick activation available but RR not payable")
                    continue

            ability_grp_id = action.get('abilityGrpId', 0)
            return instance_id, ability_grp_id
        return None

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
            available_colors, total_mana, sources = self._get_available_mana_colors(action_list, inst_id_grp_id_dict)
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
                phoenix_activation = self._find_phoenix_chick_activation(
                    action_list, inst_id_grp_id_dict, available_colors, total_mana, sources
                )
                if phoenix_activation:
                    inst_id, ability_grp_id = phoenix_activation
                    self._debug(f"Phoenix Chick activation: instanceId={inst_id}, abilityGrpId={ability_grp_id}")
                    return {'activate_ability': [inst_id, ability_grp_id]}

                # If a spell/ability target is required, always target opponent avatar.
                if self._needs_spell_target_selection(action_list):
                    self._debug("Spell target selection required - targeting opponent player")
                    return {'select_target': [-1]}

                # Combat phase - attack
                if phase == 'Phase_Combat' and step == 'Step_DeclareAttack':
                    if self._needs_attack_target_selection(action_list):
                        self._debug("Attack target selection required - targeting opponent player")
                        move = {'select_target': [-1]}
                        return move
                    self._debug("Combat phase - declaring all attackers")
                    move = {'all_attack': []}
                    return move

                # Main phases - play lands and cast spells
                elif phase in ['Phase_Main1', 'Phase_Main2']:

                    # First: try to play a land
                    if not self.__has_land_been_played_this_turn:
                        land_instance_id = self._choose_land_to_play(
                            action_list, inst_id_grp_id_dict, available_colors, total_mana, sources
                        )
                        if land_instance_id is not None:
                            land_grp_id = inst_id_grp_id_dict.get(land_instance_id)
                            self._debug(f"Playing land: instanceId={land_instance_id}, grpId={land_grp_id}")
                            move = {'cast': [land_instance_id]}
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
                            if self._can_cast_with_mana_cost(action_mana_cost, available_colors, total_mana, sources):
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

                    # Third: cast non-creature spells (Instant/Sorcery) face
                    spell_actions = []
                    allow_sorcery = phase in ['Phase_Main1', 'Phase_Main2']
                    for action_wrapper in action_list:
                        action = action_wrapper.get('action', {})
                        if action.get('actionType') != 'ActionType_Cast':
                            continue
                        instance_id = action.get('instanceId')
                        action_mana_cost = action.get('manaCost', [])
                        grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                        card_info = CardInfo.get_card_info(grp_id)
                        if not card_info:
                            continue
                        card_types = card_info.get('types', [])
                        if 'Creature' in card_types:
                            continue
                        is_instant = 'Instant' in card_types
                        is_sorcery = 'Sorcery' in card_types
                        if not is_instant and not is_sorcery:
                            continue
                        if is_sorcery and not allow_sorcery:
                            continue

                        card_name = card_info.get('name', f'Card#{instance_id}')
                        mana_cost_str = card_info.get('manaCost', '')
                        cmc = CardInfo.calculate_cmc(mana_cost_str)
                        if self._can_cast_with_mana_cost(action_mana_cost, available_colors, total_mana, sources):
                            spell_actions.append((cmc, instance_id, card_name, mana_cost_str))
                            self._debug(f"Can cast spell: {card_name} (cost={mana_cost_str}, cmc={cmc})")

                    if spell_actions:
                        spell_actions.sort(key=lambda x: x[0])
                        cmc, instance_id, card_name, mana_cost = spell_actions[0]
                        self._debug(f"CASTING SPELL: {card_name} (instanceId={instance_id}, cost={mana_cost})")
                        move = {'cast': [instance_id]}
                        return move

                    # Last: cast enchantments in main phase
                    if phase in ['Phase_Main1', 'Phase_Main2']:
                        enchant_actions = []
                        for action_wrapper in action_list:
                            action = action_wrapper.get('action', {})
                            if action.get('actionType') != 'ActionType_Cast':
                                continue
                            instance_id = action.get('instanceId')
                            action_mana_cost = action.get('manaCost', [])
                            grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                            card_info = CardInfo.get_card_info(grp_id)
                            if not card_info:
                                continue
                            card_types = card_info.get('types', [])
                            if 'Enchantment' not in card_types:
                                continue

                            card_name = card_info.get('name', f'Card#{instance_id}')
                            mana_cost_str = card_info.get('manaCost', '')
                            cmc = CardInfo.calculate_cmc(mana_cost_str)
                            if self._can_cast_with_mana_cost(action_mana_cost, available_colors, total_mana, sources):
                                enchant_actions.append((cmc, instance_id, card_name, mana_cost_str))
                                self._debug(f"Can cast enchantment: {card_name} (cost={mana_cost_str}, cmc={cmc})")

                        if enchant_actions:
                            enchant_actions.sort(key=lambda x: x[0])
                            cmc, instance_id, card_name, mana_cost = enchant_actions[0]
                            self._debug(f"CASTING ENCHANTMENT: {card_name} (instanceId={instance_id}, cost={mana_cost})")
                            move = {'cast': [instance_id]}
                            return move

            self._debug(f"Returning default move: {move}")
            return move

        except Exception as e:
            self._debug(f"CRITICAL ERROR in generate_move: {e}\n{traceback.format_exc()}")
            return {'resolve': []}
