from AI.AIInterface import AIKernel
from Controller.Utilities.GameState import GameState
from Controller.Utilities.GameStateInterface import GameStateSecondary
import AI.Utilities.CardInfo as CardInfo
import AI.Utilities.RemovalLogic as RemovalLogic
import AI.Utilities.CardPolicy as CardPolicy
import AI.Utilities.CounterLogic as CounterLogic
import AI.Utilities.FightLogic as FightLogic
import AI.Utilities.LifegainLogic as LifegainLogic
import AI.Utilities.LegendRule as LegendRule
import traceback
from datetime import datetime


class DummyAI(AIKernel):

    def __init__(self):
        self.__current_turn_num = 0
        self.__has_land_been_played_this_turn = False
        self.__known_battlefield_zones = set()
        # AI debug lines go into the shared bot.log; without this assignment
        # _debug silently dropped every message (open() raised AttributeError).
        try:
            from runtime_paths import runtime_file
            self.__bot_log_file = str(runtime_file("logs", "bot.log"))
        except Exception:
            self.__bot_log_file = "bot.log"

    def reset(self):
        """Reset AI state for a new game"""
        self._debug("Resetting AI state for new game")
        self.__current_turn_num = 0
        self.__has_land_been_played_this_turn = False
        self.__known_battlefield_zones = set()
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

                    # 1) Offline and exact: the action's own mana-ability id
                    # (duals expose one Activate_Mana action per color).
                    ability_color = CardInfo.get_mana_color_from_ability(action.get('abilityGrpId'))
                    if ability_color:
                        mana_sources[instance_id].add(ability_color)
                        mana_colors.add(ability_color)
                        continue

                    # 2) Fallback: produced colors by grpId (local map + Scryfall)
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

        # 3) Wildcard fallback: MTGA only offers ActionType_Activate_Mana for
        # real mana sources. A source whose colors we cannot resolve (unknown
        # ability id like 1039, Scryfall miss) must still count as usable
        # mana of any color — treating it as nothing made the AI pass every
        # turn with dual/utility lands in play.
        wildcard = {"white", "blue", "black", "red", "green"}
        for instance_id, colors in mana_sources.items():
            if not colors:
                mana_sources[instance_id] = set(wildcard)
                mana_colors.update(wildcard)
                self._debug(f"Unknown mana colors for instId={instance_id}; counting as wildcard source")

        total_sources = len(mana_sources)
        sources = [set(colors) for colors in mana_sources.values() if colors]
        self._debug(f"Mana sources: {total_sources}, colors available: {mana_colors}")
        return mana_colors, total_sources, sources

    def _can_cast_with_mana_cost(self, action_mana_cost, available_colors, total_mana, sources):
        """Check if we can pay a mana cost from the action's manaCost field."""
        return self._can_cast_with_mana_costs(action_mana_cost, available_colors, total_mana, sources)

    @staticmethod
    def _mana_cost_total(action_mana_cost):
        """Return total mana symbols to pay from an action manaCost list."""
        if not action_mana_cost:
            return 0
        total = 0
        for entry in action_mana_cost:
            try:
                total += int(entry.get('count', 0) or 0)
            except Exception:
                continue
        return total

    def _can_cast_with_mana_costs(self, combined_mana_cost, available_colors, total_mana, sources):
        """Check if we can pay combined mana costs (multiple spells)."""
        if not combined_mana_cost:
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
        for cost_entry in combined_mana_cost:
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

    def _find_named_activation(
        self,
        action_list,
        inst_id_grp_id_dict,
        available_colors,
        total_mana,
        sources,
        *,
        card_name,
        fallback_mana_cost,
        explicit_cost_fail_msg,
        fallback_cost_fail_msg,
    ):
        """Find a payable activation action for `card_name`.

        Shared by _find_phoenix_chick_activation and
        _find_reassembling_skeleton_activation, which differ only in which card
        they look for and what mana cost to assume when MTGA's action omits
        manaCost. Both are direct self-activations with no target and no
        in-resolution chooser, so they are safe to fire once payable.
        """
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
            if not card_info or card_info.get('name') != card_name:
                continue

            action_mana_cost = action.get('manaCost', [])
            if action_mana_cost:
                if not self._can_cast_with_mana_cost(action_mana_cost, available_colors, total_mana, sources):
                    self._debug(explicit_cost_fail_msg)
                    continue
            else:
                if not self._can_cast_with_mana_cost(fallback_mana_cost, available_colors, total_mana, sources):
                    self._debug(fallback_cost_fail_msg)
                    continue

            ability_grp_id = action.get('abilityGrpId', 0)
            return instance_id, ability_grp_id
        return None

    def _find_phoenix_chick_activation(self, action_list, inst_id_grp_id_dict, available_colors, total_mana, sources):
        """Find a Phoenix Chick activation action we can pay for."""
        return self._find_named_activation(
            action_list, inst_id_grp_id_dict, available_colors, total_mana, sources,
            card_name='Phoenix Chick',
            fallback_mana_cost=[{'color': ['ManaColor_Red'], 'count': 2}],
            explicit_cost_fail_msg="Phoenix Chick activation available but mana cost not payable",
            fallback_cost_fail_msg="Phoenix Chick activation available but RR not payable",
        )

    @staticmethod
    def _is_priority_removal_target(target_id, game_objects) -> bool:
        """True if this removal target is worth answering NOW rather than
        developing the board.

        The generic picker maximises mana spent and breaks ties by card type
        (Creature 5 > Instant 4), so a 1-mana Burst Lightning always lost to a
        1-mana creature: the bot logged 'Removal Burst Lightning target=305'
        every turn and never fired it, while that very target (Perforating
        Artist) drained us with its repeatable ability. Treat a target as worth
        it when it is a known repeatable engine, or simply a real beater.
        """
        if target_id is None:
            return False
        try:
            target_id = int(target_id)
        except Exception:
            return False
        if target_id < 0:
            return False  # face/player damage is not a "threat answer"
        obj = None
        for candidate in game_objects or []:
            if isinstance(candidate, dict) and candidate.get("instanceId") == target_id:
                obj = candidate
                break
        if not obj:
            return False
        # Repeatable engines the bot already flags as too valuable to sacrifice
        # (e.g. Perforating Artist) are exactly what removal is for.
        if RemovalLogic.is_protected_from_sacrifice(obj.get("grpId")):
            return True
        return RemovalLogic._stat(obj.get("power")) >= 3

    def _find_reassembling_skeleton_activation(self, action_list, inst_id_grp_id_dict, available_colors, total_mana, sources):
        """Find a payable Reassembling Skeleton return-from-graveyard activation.

        Reassembling Skeleton has '1B: Return this card from your graveyard to
        the battlefield tapped.' MTGA offers this as an ActionType_Activate for
        the graveyard instance whenever we have priority and can pay. It returns
        itself with no target and no in-resolution chooser, so it is safe to
        activate directly (unlike a cast with an unclickable chooser). This is
        the key line for the sacrifice deck: the skeleton is sac fodder we bring
        back every time it is sacrificed (e.g. to Vampire Gourmand)."""
        fallback_cost_fail_msg = "Reassembling Skeleton return available but mana cost not payable"
        return self._find_named_activation(
            action_list, inst_id_grp_id_dict, available_colors, total_mana, sources,
            card_name='Reassembling Skeleton',
            # Fallback to the printed activation cost 1B when MTGA omits manaCost.
            fallback_mana_cost=[
                {'color': ['ManaColor_Black'], 'count': 1},
                {'color': ['ManaColor_Generic'], 'count': 1},
            ],
            explicit_cost_fail_msg=fallback_cost_fail_msg,
            fallback_cost_fail_msg=fallback_cost_fail_msg,
        )

    def _get_convoke_sources(self, game_state: GameState, my_seat: int):
        """Return convoke sources from untapped creatures we control."""
        color_map = {
            'W': 'white',
            'U': 'blue',
            'B': 'black',
            'R': 'red',
            'G': 'green'
        }
        sources = []
        colors = set()
        try:
            for obj in game_state.get_game_objects():
                if obj.get("controllerSeatId") != my_seat:
                    continue
                if "CardType_Creature" not in (obj.get("cardTypes") or []):
                    continue
                if obj.get("isTapped"):
                    continue
                grp_id = obj.get("grpId")
                if grp_id is None:
                    continue
                card_info = CardInfo.get_card_info(grp_id)
                card_colors = card_info.get("colors", []) if card_info else []
                source_colors = {color_map.get(c, c) for c in card_colors if c in color_map}
                sources.append(set(source_colors))
                colors.update(source_colors)
        except Exception:
            return set(), []
        return colors, sources

    def _select_cast_action_max_mana(self, cast_actions, available_colors, total_mana, sources):
        """Select a cast action that maximizes mana usage this turn.

        Strategy:
        1) Maximize total CMC spent (<= total_mana).
        2) If multiple ways spend the same total, prefer fewer spells.
        3) If still tied, prefer plans containing higher CMC spells.
        """
        if not cast_actions:
            return None, None

        actions = [a for a in cast_actions if a[0] <= total_mana]
        if not actions:
            return None, None

        cmc_suffix = [0] * (len(actions) + 1)
        for i in range(len(actions) - 1, -1, -1):
            cmc_suffix[i] = cmc_suffix[i + 1] + actions[i][0]

        best = None  # (spent, count, max_cmc, payoffs, indices)

        def _better(a, b):
            if b is None:
                return True
            if a[0] != b[0]:
                return a[0] > b[0]
            if a[1] != b[1]:
                return a[1] < b[1]
            # Same mana spent on the same number of cards: prefer the plan that
            # gets a lifegain payoff onto the board. Deliberately ranked BELOW
            # mana efficiency -- this only breaks ties, it never trades tempo for
            # the engine.
            if a[3] != b[3]:
                return a[3] > b[3]
            return a[2] > b[2]

        def _dfs(idx, spent, count, max_cmc, payoffs, indices, combined_costs):
            nonlocal best
            if spent > total_mana:
                return
            if best and spent + cmc_suffix[idx] < best[0]:
                return
            if count > 0 and not self._can_cast_with_mana_costs(
                list(combined_costs), available_colors, total_mana, sources
            ):
                return
            if count > 0:
                cand = (spent, count, max_cmc, payoffs, list(indices))
                if _better(cand, best):
                    best = cand
            if idx >= len(actions):
                return

            paid_cost, _instance_id, _card_name, _mana_cost_str, action_mana_cost, _uses_convoke, _type_priority, _nominal_cmc, _is_discounted, _priority_removal, _lifegain_payoff = actions[idx]
            safe_action_mana_cost = list(action_mana_cost) if isinstance(action_mana_cost, list) else []
            _dfs(
                idx + 1,
                spent + paid_cost,
                count + 1,
                max(max_cmc, paid_cost),
                payoffs + (1 if _lifegain_payoff else 0),
                indices + [idx],
                combined_costs + safe_action_mana_cost,
            )
            _dfs(idx + 1, spent, count, max_cmc, payoffs, indices, combined_costs)

        _dfs(0, 0, 0, 0, 0, [], [])

        if not best:
            return None, None

        best_spent, plan_count, plan_max, plan_payoffs, plan_indices = best
        if plan_count == 1:
            chosen_index = plan_indices[0]
        else:
            # The AI returns ONE cast per decision and is re-invoked for the rest of
            # the plan, so this is really "which of the planned spells goes first".
            # Lifegain payoffs lead: a counter is only earned by lifegain that
            # happens while the creature is already out, so every spell resolved
            # ahead of it is a trigger it can never get back.
            chosen_index = max(
                plan_indices, key=lambda i: (actions[i][10], actions[i][0], actions[i][6])
            )

        chosen = actions[chosen_index]
        score = (best_spent, -plan_count, plan_max)
        self._debug(
            f"Mana plan: total_mana={total_mana}, spent={best_spent}, "
            f"count={plan_count}, max_cmc={plan_max}, payoffs={plan_payoffs}, "
            f"chosen={chosen[2]}"
        )
        return chosen, score

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

    @staticmethod
    def _card_type_priority(card_types: list[str] | None) -> int:
        if not card_types:
            return 0
        if 'Creature' in card_types:
            return 5
        if 'Instant' in card_types:
            return 4
        if 'Sorcery' in card_types:
            return 3
        if 'Enchantment' in card_types:
            return 2
        return 1

    def _find_counter_cast(
        self,
        action_list,
        game_state,
        inst_id_grp_id_dict,
        my_seat,
        available_colors,
        total_mana,
        sources,
    ):
        """If the opponent has a spell on the stack we can counter and we hold a
        payable counterspell, return that counter's instanceId. Reactive: works
        on either player's turn as long as we have priority."""
        try:
            full_state = game_state.get_full_state() or {}
            stack_ids = CounterLogic.stack_zone_ids(full_state)
            if not stack_ids:
                return None
            game_objects = game_state.get_game_objects() or []
            if not CounterLogic.opponent_spells_on_stack(game_objects, my_seat, stack_ids):
                return None

            for action_wrapper in action_list:
                action = action_wrapper.get('action', {})
                if action.get('actionType') != 'ActionType_Cast':
                    continue
                instance_id = action.get('instanceId')
                grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                profile = CounterLogic.get_counter_profile(grp_id)
                if not profile:
                    continue
                target = CounterLogic.find_counterable_spell(
                    profile, game_objects, my_seat, stack_ids, full_state
                )
                if target is None:
                    continue
                action_mana_cost = action.get('manaCost', [])
                if not self._can_cast_with_mana_costs(
                    action_mana_cost, available_colors, total_mana, sources
                ):
                    self._debug(
                        f"Counter available but not payable (grpId={grp_id})."
                    )
                    continue
                card_info = CardInfo.get_card_info(grp_id) or {}
                self._debug(
                    f"COUNTER: casting {card_info.get('name', grp_id)} "
                    f"(instanceId={instance_id}) to counter spell {target} (profile={profile})."
                )
                return instance_id
        except Exception as e:
            self._debug(f"ERROR in _find_counter_cast: {e}")
        return None

    def generate_move(self, game_state: GameStateSecondary, inst_id_grp_id_dict) -> dict[str, list[int]]:
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

            has_land_play_action = any(
                (action_wrapper.get('action', {}) or {}).get('actionType') == 'ActionType_Play'
                for action_wrapper in action_list
            )
            if has_land_play_action and self.__has_land_been_played_this_turn:
                self.__has_land_been_played_this_turn = False
                self._debug(
                    "ActionType_Play still available; clearing stale land-play flag for this turn"
                )

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

            # Reactive counterspell: if the opponent has a counterable spell on
            # the stack and we hold a payable counter, cast it now. This runs on
            # either player's turn (we just need priority), so it is checked
            # before the proactive/own-turn block below.
            counter_instance_id = self._find_counter_cast(
                action_list, game_state, inst_id_grp_id_dict, my_seat,
                available_colors, total_mana, sources,
            )
            if counter_instance_id is not None:
                return {'cast': [counter_instance_id]}

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
                    # Snapshot sorcery availability for debug, regardless of chosen priority.
                    sorcery_in_actions = 0
                    sorcery_names = []
                    for action_wrapper in action_list:
                        action = action_wrapper.get('action', {})
                        if action.get('actionType') != 'ActionType_Cast':
                            continue
                        instance_id = action.get('instanceId')
                        grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                        card_info = CardInfo.get_card_info(grp_id)
                        if not card_info:
                            continue
                        card_types = card_info.get('types', [])
                        if 'Sorcery' in card_types:
                            sorcery_in_actions += 1
                            sorcery_names.append(card_info.get('name', f'Card#{instance_id}'))

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

                    # Second: cast any spell to maximize mana usage (category-agnostic)
                    cast_actions = []
                    convoke_colors, convoke_sources = self._get_convoke_sources(game_state, my_seat)
                    # Targeted-removal context (shared with the Controller's
                    # target selection via RemovalLogic).
                    removal_game_objects = game_state.get_game_objects() or []
                    try:
                        removal_opp_life = RemovalLogic.opponent_life_from_players(
                            game_state.get_players(), my_seat
                        )
                    except Exception:
                        removal_opp_life = None
                    # Do we control a creature on the battlefield? Self-buff tricks
                    # (e.g. Fake Your Own Death) must not be cast with nothing of ours
                    # to buff -- otherwise the only legal target is an enemy, which we
                    # must never buff.
                    # A buff aura / pump trick needs a creature ON OUR BATTLEFIELD to
                    # enchant; a creature card sitting in our hand or graveyard is not a
                    # legal target. The zone filter must therefore be REQUIRED, never
                    # bypassed: an earlier version fell back to "any zone" whenever the
                    # battlefield zones could not be determined (`not _own_bf or ...`),
                    # which counted a creature in HAND as a board creature -- so a 4-mana
                    # aura (e.g. Angelic Destiny) got cast with an empty board, MTGA then
                    # offered only enemy creatures as targets, the self-buff handler
                    # refused (correctly, never buff an enemy), and the decision loop
                    # re-cast the same aura every rope, bleeding the whole game.
                    #
                    # battlefield_zone_ids() only finds a zone once MTGA re-declares its
                    # ZoneType (observed: a match can go many turns with the battlefield
                    # type declared only once, so it can return empty most of the game).
                    # Inferring the zone from lands instead is unsafe: removal_game_objects
                    # holds objects from every zone, so a land sitting in hand/graveyard/deck
                    # would tag that zone as "battlefield" too, letting a hand creature slip
                    # through -- reintroducing the exact bug this guard exists to prevent.
                    # Instead, cache every battlefield zoneId ever reported and keep using
                    # the cached set once known zones become momentarily unavailable.
                    _current_bf_ids = RemovalLogic.battlefield_zone_ids(game_state.get_full_state())
                    if _current_bf_ids:
                        self.__known_battlefield_zones.update(_current_bf_ids)
                    _own_bf = self.__known_battlefield_zones
                    # Reuse the cache (instead of this turn's possibly-empty lookup) for
                    # the removal/fight target filters below too, so they don't lose all
                    # targets on the turns battlefield_zone_ids() comes back empty.
                    removal_bf_ids = _own_bf or None
                    # zoneId alone is not enough: gameObjects is merged across diffs
                    # and only pruned on an explicit diffDeletedInstanceIds, so a
                    # creature that died keeps zoneId=<battlefield> forever. The zone's
                    # own objectInstanceIds list is replaced on every diff, so it is the
                    # authoritative "what is actually on the board" answer. Without this
                    # the bot cast removal at creatures that had been dead for minutes
                    # and then hung hunting them on screen.
                    removal_live_ids = RemovalLogic.battlefield_instance_ids(
                        game_state.get_full_state(), _own_bf or None
                    )
                    have_own_creature = bool(_own_bf) and any(
                        isinstance(o, dict)
                        and o.get('controllerSeatId') == my_seat
                        and 'CardType_Creature' in (o.get('cardTypes') or [])
                        and o.get('zoneId') in _own_bf
                        and (removal_live_ids is None or o.get('instanceId') in removal_live_ids)
                        for o in removal_game_objects
                    )
                    allow_sorcery = phase in ['Phase_Main1', 'Phase_Main2']
                    sorcery_found = 0
                    sorcery_castable = 0
                    sorcery_blocked_phase = 0
                    sorcery_blocked_mana = 0

                    for action_wrapper in action_list:
                        action = action_wrapper.get('action', {})
                        if action.get('actionType') != 'ActionType_Cast':
                            continue
                        instance_id = action.get('instanceId')
                        action_mana_cost = action.get('manaCost', [])
                        grp_id = action.get('grpId') or inst_id_grp_id_dict.get(instance_id)
                        card_info = CardInfo.get_card_info(grp_id)

                        if not card_info:
                            self._debug(f"No card info for grpId={grp_id}")
                            continue

                        card_types = card_info.get('types', [])
                        is_sorcery = 'Sorcery' in card_types
                        if is_sorcery and not allow_sorcery:
                            sorcery_found += 1
                            sorcery_blocked_phase += 1
                            continue

                        card_name = card_info.get('name', f'Card#{instance_id}')
                        # Do not cast cards whose in-resolution card chooser
                        # (e.g. return-from-graveyard) is not implemented yet;
                        # the bot cannot click it and would stall the game.
                        if CardPolicy.is_unsupported_to_cast(grp_id):
                            self._debug(
                                f"Skipping {card_name}: in-resolution chooser not implemented yet."
                            )
                            continue
                        # A second copy of a legend we already control is legal,
                        # so MTGA offers it -- and then blocks the cast behind a
                        # client-side "Are You Sure?" that no GRE message
                        # announces. Observed live: the cast never lands, three
                        # sweeps burn the rope, and the card ends up suppressed
                        # as unreachable. Confirming would be no better (CR
                        # 704.5j bins one of the two right after), so never pick
                        # it in the first place.
                        _legend_dupe = LegendRule.duplicate_legend_in_play(
                            cast_instance_id=instance_id,
                            game_objects=removal_game_objects,
                            my_seat=my_seat,
                            battlefield_zone_ids=_own_bf,
                            live_instance_ids=removal_live_ids,
                        )
                        if _legend_dupe is not None:
                            self._debug(
                                f"LEGEND_RULE: skipping {card_name} (inst={instance_id}); "
                                f"we already control the same legend (inst={_legend_dupe}). "
                                "Casting it would only bin one of the two."
                            )
                            continue
                        mana_cost_str = card_info.get('manaCost', '')
                        nominal_cmc = CardInfo.calculate_cmc(mana_cost_str)
                        paid_cost = self._mana_cost_total(action_mana_cost)
                        is_discounted = paid_cost < nominal_cmc

                        uses_convoke = CardInfo.card_has_convoke(grp_id) if grp_id else False
                        eff_colors = set(available_colors)
                        eff_sources = list(sources)
                        eff_total_mana = total_mana
                        if uses_convoke:
                            eff_colors.update(convoke_colors)
                            eff_sources = list(sources) + list(convoke_sources)
                            eff_total_mana = total_mana + len(convoke_sources)

                        # Check if we can pay the mana cost (color + total)
                        if self._can_cast_with_mana_costs(action_mana_cost, eff_colors, eff_total_mana, eff_sources):
                            # Self-buff trick (e.g. Fake Your Own Death) with no
                            # creature of ours to buff: skip. Its only legal target
                            # would be an enemy, which we must never buff.
                            if RemovalLogic.is_self_buff(grp_id) and not have_own_creature:
                                self._debug(
                                    f"Self-buff {card_name}: we control no creature to target; skipping cast."
                                )
                                continue
                            # Targeted removal: only cast it if it has a valid
                            # target (kills a creature, or is lethal to the face).
                            priority_removal = False
                            removal_profile = RemovalLogic.get_removal_profile(grp_id)
                            if removal_profile is not None:
                                # Mana still free once this spell is paid for -- what
                                # a ward would have to come out of. Without it the
                                # bot happily aims removal at a warded creature it
                                # cannot pay for, the client raises a confirm we then
                                # decline, and the spell fizzles back to hand for the
                                # decision loop to try again, forever.
                                # Convoke creatures can pay for this spell, but not
                                # for the ward triggered after it resolves onto the
                                # stack. Price ward only from real mana sources.
                                _ward_budget = max(0, total_mana - paid_cost)
                                _rm_target = RemovalLogic.choose_removal_target(
                                    removal_profile,
                                    removal_game_objects,
                                    my_seat,
                                    opponent_life=removal_opp_life,
                                    battlefield_zone_ids=removal_bf_ids,
                                    live_instance_ids=removal_live_ids,
                                    ward_budget=_ward_budget,
                                    ward_sources=list(sources),
                                    source_grp_id=grp_id,
                                )
                                if _rm_target is None:
                                    # A non-permanent removal spell (instant/sorcery)
                                    # is wasted without a target, so skip it. But a
                                    # creature/permanent whose removal is just an
                                    # activated or triggered ability (e.g. Fanatical
                                    # Firebrand's sacrifice) still has board value --
                                    # cast it as a creature and keep the ability for
                                    # later. Casting the permanent needs no target.
                                    is_spell_removal = (
                                        'Instant' in card_types or 'Sorcery' in card_types
                                    )
                                    if is_spell_removal:
                                        self._debug(
                                            f"Removal {card_name} has no killable target; skipping cast."
                                        )
                                        continue
                                    self._debug(
                                        f"Removal {card_name} has no target; casting as creature/permanent for board presence."
                                    )
                                else:
                                    priority_removal = self._is_priority_removal_target(
                                        _rm_target, removal_game_objects
                                    )
                                    self._debug(
                                        f"Removal {card_name} target={_rm_target} (profile={removal_profile}, "
                                        f"priority={priority_removal}, ward_budget={_ward_budget})."
                                    )
                            # Pump-fight (e.g. Felling Blow): only cast it if we
                            # have a creature to buff AND an enemy it can then
                            # kill (our best power + counter >= enemy toughness).
                            fight_profile = FightLogic.get_fight_profile(grp_id)
                            if fight_profile is not None:
                                _fight = FightLogic.choose_fight_pairing(
                                    fight_profile,
                                    removal_game_objects,
                                    my_seat,
                                    battlefield_zone_ids=removal_bf_ids,
                                )
                                if _fight is None:
                                    self._debug(
                                        f"Fight {card_name} has no killable pairing; skipping cast."
                                    )
                                    continue
                                self._debug(
                                    f"Fight {card_name} pairing our={_fight[0]} enemy={_fight[1]} (profile={fight_profile})."
                                )
                            type_priority = self._card_type_priority(card_types)
                            # "Whenever you gain life, put a +1/+1 counter on this
                            # creature" -- the deck's engine. Every turn one of these
                            # is not on the board is lifegain triggers (and counters)
                            # lost for good, so it gets deployed ahead of other
                            # creatures in the same mana plan.
                            lifegain_payoff = LifegainLogic.is_lifegain_payoff(grp_id)
                            cast_actions.append(
                                (
                                    paid_cost,
                                    instance_id,
                                    card_name,
                                    mana_cost_str,
                                    action_mana_cost,
                                    uses_convoke,
                                    type_priority,
                                    nominal_cmc,
                                    is_discounted,
                                    priority_removal,
                                    lifegain_payoff,
                                )
                            )
                            self._debug(
                                f"Can cast: {card_name} (cost={mana_cost_str}, paid={paid_cost}, cmc={nominal_cmc}, discounted={is_discounted}, convoke={uses_convoke}, lifegain_payoff={lifegain_payoff})"
                            )
                            if is_sorcery:
                                sorcery_found += 1
                                sorcery_castable += 1
                        else:
                            self._debug(
                                f"Cannot cast {card_name} (cost={mana_cost_str}, colors={available_colors}, "
                                f"mana={total_mana})"
                            )
                            if is_sorcery:
                                sorcery_found += 1
                                sorcery_blocked_mana += 1

                    if cast_actions:
                        # Answer a real threat before developing the board. The
                        # max-mana plan below breaks ties by card type (Creature 5 >
                        # Instant 4), so a 1-mana Burst Lightning always lost to a
                        # 1-mana creature and the bot never removed the Perforating
                        # Artist that was draining it. Take the cheapest removal that
                        # answers the threat, so the rest of the mana still develops.
                        priority_removals = [a for a in cast_actions if a[9]]
                        if priority_removals:
                            chosen = min(priority_removals, key=lambda a: (a[0], a[7]))
                            self._debug(
                                f"Priority removal: casting {chosen[2]} (instanceId={chosen[1]}, "
                                f"cost={chosen[3]}) to answer a threat before developing."
                            )
                            return {'cast': [chosen[1]]}

                        # If a high-mana-value spell is heavily discounted by effects, prioritize it.
                        discounted_bombs = [a for a in cast_actions if a[8] and a[7] >= 6]
                        if discounted_bombs:
                            chosen = max(discounted_bombs, key=lambda a: (a[7], a[6], -a[0]))
                            self._debug(
                                f"Discount priority: choosing {chosen[2]} (paid={chosen[0]}, cmc={chosen[7]})"
                            )
                            move = {'cast': [chosen[1]]}
                            return move

                        non_convoke_actions = [a for a in cast_actions if not a[5]]
                        convoke_actions = [a for a in cast_actions if a[5]]

                        chosen = None
                        chosen_score = None
                        if non_convoke_actions:
                            chosen, chosen_score = self._select_cast_action_max_mana(
                                non_convoke_actions, available_colors, total_mana, sources
                            )
                        if convoke_actions:
                            convoke_best = max(convoke_actions, key=lambda a: (a[0], a[6]))
                            convoke_score = (convoke_best[0], -1, convoke_best[0])
                            if chosen_score is None or convoke_score > chosen_score:
                                chosen = convoke_best
                                chosen_score = convoke_score

                        if chosen:
                            paid_cost, instance_id, card_name, mana_cost, _action_mana_cost, _uses_convoke, _type_priority, nominal_cmc, _is_discounted, _priority_removal, _lifegain_payoff = chosen
                            self._debug(
                                f"CASTING: {card_name} (instanceId={instance_id}, cost={mana_cost}, paid={paid_cost}, cmc={nominal_cmc})"
                            )
                            if sorcery_found:
                                self._debug(
                                    f"Sorcery debug: found={sorcery_found}, castable={sorcery_castable}, "
                                    f"blocked_phase={sorcery_blocked_phase}, blocked_mana={sorcery_blocked_mana}"
                                )
                            move = {'cast': [instance_id]}
                            return move

                    # Nothing better to cast this window: bring Reassembling
                    # Skeleton back from the graveyard with the leftover mana so
                    # it is available as sacrifice fodder again. Checked after
                    # casts so a real spell always takes mana priority.
                    skeleton_activation = self._find_reassembling_skeleton_activation(
                        action_list, inst_id_grp_id_dict, available_colors, total_mana, sources
                    )
                    if skeleton_activation:
                        inst_id, ability_grp_id = skeleton_activation
                        self._debug(
                            f"Reassembling Skeleton return-from-graveyard: instanceId={inst_id}, abilityGrpId={ability_grp_id}"
                        )
                        return {'activate_ability': [inst_id, ability_grp_id]}

                    if sorcery_found:
                        self._debug(
                            f"Sorcery debug: no spell cast. found={sorcery_found}, castable={sorcery_castable}, "
                            f"blocked_phase={sorcery_blocked_phase}, blocked_mana={sorcery_blocked_mana}"
                        )

            self._debug(f"Returning default move: {move}")
            return move

        except Exception as e:
            self._debug(f"CRITICAL ERROR in generate_move: {e}\n{traceback.format_exc()}")
            return {'resolve': []}
