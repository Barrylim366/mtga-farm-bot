import json
import re
import threading
import time

from Controller.ControllerInterface import ControllerSecondary
from Controller.MTGAController.LogReader import LogReader
from Controller.Utilities.GameState import GameState
from Controller.Utilities.input_controller import InputControllerError, create_input_controller
import bot_logger


class Controller(ControllerSecondary):

    def __init__(self, log_path, screen_bounds=((0, 0), (1600, 900)), click_targets=None, input_backend: str | None = None):
        self.__decision_callback = None
        self.__mulligan_decision_callback = None
        self.__action_success_callback = None
        self.__decision_execution_thread = None
        self.__mulligan_execution_thread = None
        self.__inactivity_timer = None
        self.__inactivity_timeout = 180  # 3 minutes in seconds
        self.__has_mulled_keep = False
        self.__intro_delay = 15
        self.__decision_delay = 4
        self.screen_bounds = screen_bounds
        self.patterns = {
            'game_state': '"type": "GREMessageType_GameStateMessage"',
            'hover_id': 'objectId',
            'match_completed': 'MatchGameRoomStateType_MatchCompleted',
            'assign_damage': '"type": "GREMessageType_AssignDamageReq"'
        }
        self.log_reader = LogReader(self.patterns.values(), log_path=log_path, callback=self.__log_callback)
        try:
            self.input = create_input_controller(input_backend)
        except InputControllerError as e:
            raise RuntimeError(f"Failed to initialize input backend {input_backend!r}: {e}") from e
        try:
            self.input.configure_screen_bounds(self.screen_bounds)
        except Exception as e:
            raise RuntimeError(f"Failed to configure input backend with screen bounds: {e}") from e
        self.cast_speed = 0.01
        # Height of the mouse when cards are scanned for casting
        self.cast_height = 30
        # Offset of the resolve button from the bottom right
        self.main_br_button_offset = (165, 136)
        self.mulligan_keep_coors = (1101, 870)
        self.mulligan_mull_coors = (801, 870)
        self.player_button_coors = (1699, 996)
        self.home_play_button_coors = (1699, 996)
        self.assign_damage_done_coors = (1280, 720)
        self.cast_card_dist = 10
        self.main_br_button_coordinates = (
            self.screen_bounds[1][0] - self.main_br_button_offset[0],
            self.screen_bounds[1][1] - self.main_br_button_offset[1],
        )
        
        self.hand_scan_p1 = (self.screen_bounds[0][0], self.screen_bounds[1][1] - 30)
        self.hand_scan_p2 = (self.screen_bounds[1][0], self.screen_bounds[1][1] - 30)
        
        if click_targets:
            if "keep_hand" in click_targets:
                self.mulligan_keep_coors = (click_targets["keep_hand"]["x"], click_targets["keep_hand"]["y"])
            if "queue_button" in click_targets:
                self.home_play_button_coors = (click_targets["queue_button"]["x"], click_targets["queue_button"]["y"])
                self.player_button_coors = (click_targets["queue_button"]["x"], click_targets["queue_button"]["y"])
            if "next" in click_targets:
                self.main_br_button_coordinates = (click_targets["next"]["x"], click_targets["next"]["y"])
            if "assign_damage_done" in click_targets:
                self.assign_damage_done_coors = (click_targets["assign_damage_done"]["x"], click_targets["assign_damage_done"]["y"])
            if "hand_scan_points" in click_targets:
                self.hand_scan_p1 = (click_targets["hand_scan_points"]["p1"]["x"], click_targets["hand_scan_points"]["p1"]["y"])
                self.hand_scan_p2 = (click_targets["hand_scan_points"]["p2"]["x"], click_targets["hand_scan_points"]["p2"]["y"])

        self.updated_game_state = GameState()
        self.__inst_id_grp_id_dict = {}
        self.__match_end_callback = None
        self.__last_match_won: bool | None = None
        # MTGA system seat id for the local player (can be 1 or 2)
        self.__system_seat_id = None

    def start_game_from_home_screen(self):
        bot_logger.log_click(self.home_play_button_coors[0], self.home_play_button_coors[1], "QUEUE_BUTTON")
        self.input.move_abs(self.home_play_button_coors[0], self.home_play_button_coors[1])
        self.input.left_down()
        time.sleep(0.2)
        self.input.left_up()
        time.sleep(1)
        self.input.left_down()
        time.sleep(0.2)
        self.input.left_up()

    def start_monitor(self) -> None:
        self.log_reader.start_log_monitor()

    def start_game(self) -> None:
        self.start_monitor()
        self.start_game_from_home_screen()

    def set_decision_callback(self, method) -> None:
        self.__decision_callback = method

    def set_mulligan_decision_callback(self, method) -> None:
        self.__mulligan_decision_callback = method

    def set_action_success_callback(self, method) -> None:
        self.__action_success_callback = method

    def set_match_end_callback(self, method) -> None:
        self.__match_end_callback = method

    def end_game(self) -> None:
        self.log_reader.stop_log_monitor()

    def cast(self, card_id: int) -> None:
        # Clear any stale hover events from previous scans
        self.log_reader.clear_new_line_flag(self.patterns['hover_id'])

        # Move above start point first to reset any hover states
        reset_pos = (self.hand_scan_p1[0], self.hand_scan_p1[1] - 100)
        bot_logger.log_move(reset_pos[0], reset_pos[1], f"RESET_BEFORE_SCAN (target card_id={card_id})")
        self.input.move_abs(reset_pos[0], reset_pos[1])
        time.sleep(0.5)

        # Move to start of hand scan
        bot_logger.log_move(self.hand_scan_p1[0], self.hand_scan_p1[1], "START_HAND_SCAN")
        self.input.move_abs(self.hand_scan_p1[0], self.hand_scan_p1[1])

        current_hovered_id = None
        start_x = self.hand_scan_p1[0]
        end_x = self.hand_scan_p2[0]

        # Ensure we are scanning in the correct direction (left to right usually)
        direction = 1 if end_x > start_x else -1
        total_dx = (end_x - start_x) if end_x != start_x else 1
        start_y = self.hand_scan_p1[1]
        end_y = self.hand_scan_p2[1]

        while current_hovered_id != card_id:
            # Check if we have exceeded the scan area
            current_x = self.input.position().x
            if (direction == 1 and current_x >= end_x) or (direction == -1 and current_x <= end_x):
                bot_logger.log_error(f"SCAN_FAILED: Card {card_id} not found. Scanned from x={start_x} to x={end_x}, ended at x={current_x}")
                print(f"Scanned entire hand area but did not find card_id: {card_id}")
                break

            # Move slightly to find next card
            # We move until we get a log update, or until we move a certain distance?
            # Original code waited for log update. We should do the same but with bounds check.

            # Inner loop: move until log updates or bounds hit
            while not self.log_reader.has_new_line(self.patterns['hover_id']):
                step_dx = self.cast_card_dist * direction
                pos = self.input.position()
                next_x = pos.x + step_dx
                # Follow a (potentially sloped) scan line from p1 -> p2 to better match fanned hands.
                t = (next_x - start_x) / total_dx
                if t < 0:
                    t = 0
                elif t > 1:
                    t = 1
                desired_y = int(round(start_y + t * (end_y - start_y)))
                dy = desired_y - pos.y
                self.input.move_rel(step_dx, dy)
                time.sleep(self.cast_speed)

                # Check bounds inside inner loop too
                current_x = self.input.position().x
                if (direction == 1 and current_x >= end_x) or (direction == -1 and current_x <= end_x):
                    break

            if self.log_reader.has_new_line(self.patterns['hover_id']):
                parsed = self.__parse_object_id_line(
                    self.log_reader.get_latest_line_containing_pattern(self.patterns['hover_id'])
                )
                if parsed is None:
                    continue
                current_hovered_id = parsed
                bot_logger.log_hover(current_hovered_id)
                print(str(current_hovered_id) + '|' + str(card_id))
            else:
                 # Break outer loop if we hit bounds without finding new log line
                 bot_logger.log_error(
                     f"SCAN_STOPPED: No hover update before bounds (target={card_id}, start=({start_x},{start_y}), end=({end_x},{end_y}))"
                 )
                 break

        if current_hovered_id == card_id:
            click_pos = self.input.position()
            bot_logger.log_click(click_pos.x, click_pos.y, f"CAST_CARD (id={card_id})")
            time.sleep(0.5)
            self.input.left_click(1)
            time.sleep(0.1)
            self.input.left_click(1)
            time.sleep(0.7)

        # Reset position
        reset_pos = (self.hand_scan_p1[0], self.hand_scan_p1[1] - 100)
        bot_logger.log_move(reset_pos[0], reset_pos[1], "RESET_AFTER_CAST")
        self.input.move_abs(reset_pos[0], reset_pos[1])

    def all_attack(self) -> None:
        bot_logger.log_click(self.main_br_button_coordinates[0], self.main_br_button_coordinates[1], "ATTACK_ALL")
        self.input.move_abs(self.main_br_button_coordinates[0], self.main_br_button_coordinates[1])
        self.input.left_click(1)
        time.sleep(1)
        self.input.left_click(1)

    def resolve(self) -> None:
        turn_info = self.updated_game_state.get_turn_info() or {}
        my_seat = self.__system_seat_id or turn_info.get('decisionPlayer') or 1

        # MTGA's bottom-right "pass/next/resolve/no-blocks" button sometimes shifts vertically during
        # opponent DeclareAttack. Historically we clicked slightly above to compensate, but that can
        # miss depending on UI scale/layout. Use the calibrated button position first, then a small
        # upward fallback only for that specific case.
        positions = [self.main_br_button_coordinates]
        if turn_info.get('step') == 'Step_DeclareAttack' and turn_info.get('activePlayer') != my_seat:
            fallback_y = self.main_br_button_coordinates[1] - 50
            min_y = self.screen_bounds[0][1]
            positions.append((self.main_br_button_coordinates[0], max(min_y, fallback_y)))

        for pos in positions:
            bot_logger.log_click(pos[0], pos[1], "RESOLVE")
            self.input.move_abs(pos[0], pos[1])
            self.input.left_click(1)
            time.sleep(0.05)

    def auto_pass(self) -> None:
        self.input.tap_enter()
        time.sleep(0.4)

    def unconditional_auto_pass(self) -> None:
        self.input.tap_shift_enter()
        time.sleep(0.4)

    def get_game_state(self) -> 'GameStateSecondary':
        return self.updated_game_state

    def keep(self, keep: bool):
        if keep:
            bot_logger.log_click(self.mulligan_keep_coors[0], self.mulligan_keep_coors[1], "KEEP_HAND")
            self.input.move_abs(self.mulligan_keep_coors[0], self.mulligan_keep_coors[1])
        else:
            bot_logger.log_click(self.mulligan_mull_coors[0], self.mulligan_mull_coors[1], "MULLIGAN")
            self.input.move_abs(self.mulligan_mull_coors[0], self.mulligan_mull_coors[1])
        self.input.left_click(1)

    def click_assign_damage_done(self):
        """Click the Done button during damage assignment"""
        bot_logger.log_click(self.assign_damage_done_coors[0], self.assign_damage_done_coors[1], "ASSIGN_DAMAGE_DONE")
        self.input.move_abs(self.assign_damage_done_coors[0], self.assign_damage_done_coors[1])
        time.sleep(0.5)
        self.input.left_click(1)
        time.sleep(0.2)
        # Maybe click twice to be sure? The user said "click", usually one is enough but delays help.
        # Just one click for now as per other methods.
        bot_logger.log_info("Clicked Assign Damage Done button")

    def __handle_inactivity_timeout(self):
        """Handle timeout when no activity for 3 minutes - click next button repeatedly"""
        bot_logger.log_info("TIMEOUT: No activity for 3 minutes - clicking next button")
        self.resolve()  # Click the "next" button
        # Reschedule timer to keep clicking until turn ends
        self.__inactivity_timer = threading.Timer(
            5.0,  # Click every 5 seconds until something happens
            self.__handle_inactivity_timeout
        )
        self.__inactivity_timer.start()

    def reset_inactivity_timer(self):
        """Reset the inactivity timer - called when a decision is made"""
        if self.__inactivity_timer is not None:
            self.__inactivity_timer.cancel()
            self.__inactivity_timer = None
        # Start fresh 3-minute timer
        self.__inactivity_timer = threading.Timer(
            self.__inactivity_timeout,
            self.__handle_inactivity_timeout
        )
        self.__inactivity_timer.start()
        bot_logger.log_info("Inactivity timer reset (3 minutes)")

    def stop_inactivity_timer(self):
        """Stop the inactivity timer completely"""
        if self.__inactivity_timer is not None:
            self.__inactivity_timer.cancel()
            self.__inactivity_timer = None

    def dismiss_end_screen(self):
        """Click to dismiss match end screen and return to main menu"""
        # Click in center of screen to dismiss end screen
        center_x = (self.screen_bounds[0][0] + self.screen_bounds[1][0]) // 2
        center_y = (self.screen_bounds[0][1] + self.screen_bounds[1][1]) // 2
        bot_logger.log_click(center_x, center_y, "DISMISS_END_SCREEN")
        self.input.move_abs(center_x, center_y)
        time.sleep(0.5)
        self.input.left_click(1)
        time.sleep(1)
        # Click again in case first click wasn't enough
        self.input.left_click(1)
        bot_logger.log_info("Match completed - dismissed end screen")

        # Call match end callback to trigger restart
        if self.__match_end_callback:
            try:
                self.__match_end_callback(self.__last_match_won)
            except TypeError:
                # Backwards compatible: callback may not accept args
                self.__match_end_callback()

    def reset_for_new_game(self):
        """Reset controller state for a new game - complete fresh start"""
        bot_logger.log_info("Resetting controller state for new game")
        self.__has_mulled_keep = False
        self.__system_seat_id = None
        self.__last_match_won = None
        self.updated_game_state = GameState()
        self.__inst_id_grp_id_dict = {}
        # Cancel any pending decision timers
        if self.__decision_execution_thread is not None:
            self.__decision_execution_thread.cancel()
            self.__decision_execution_thread = None
        if self.__mulligan_execution_thread is not None:
            self.__mulligan_execution_thread.cancel()
            self.__mulligan_execution_thread = None
        # Cancel inactivity timer
        self.stop_inactivity_timer()
        # Reset all cached log data for fresh start
        self.log_reader.reset_all_patterns()
        bot_logger.log_info("Controller state reset complete")

    def get_inst_id_grp_id_dict(self):
        return self.__inst_id_grp_id_dict

    @staticmethod
    def __parse_object_id_line(line):
        """
        Extracts `objectId` from log lines.
        MTGA logs sometimes emit full JSON lines, sometimes fragments like `"objectId": 123`.
        """
        if not line:
            return None
        m = re.search(r'"objectId"\s*:\s*(\d+)', line)
        if m:
            return int(m.group(1))
        try:
            start = line.find("{")
            if start != -1:
                payload = json.loads(line[start:])
                # Look for the first objectId key in nested dicts
                stack = [payload]
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        if "objectId" in cur and isinstance(cur["objectId"], int):
                            return cur["objectId"]
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
        except Exception:
            return None
        return None

    def __log_callback(self, pattern: str, line_containing_pattern: str):
        if pattern == self.patterns["game_state"]:
            self.__update_game_state(json.loads(line_containing_pattern))
        elif pattern == self.patterns["match_completed"]:
            bot_logger.log_info("Detected match completed event")
            self.__last_match_won = self.__infer_match_won(line_containing_pattern)
            # Wait a moment for end screen to fully appear, then dismiss it
            threading.Timer(6.0, self.dismiss_end_screen).start()
        elif pattern == self.patterns["assign_damage"]:
            # Wait a small delay to ensure UI is ready
            threading.Timer(1.0, self.click_assign_damage_done).start()

    @staticmethod
    def __infer_match_won(line: str) -> bool | None:
        """
        Best-effort inference from a single log line. Returns True/False/None if unknown.
        MTGA log formats vary by version; we try JSON parsing and fallback to keyword matching.
        """
        try:
            start = line.find("{")
            if start != -1:
                payload = json.loads(line[start:])
                stack = [payload]
                strings: list[str] = []
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
                    elif isinstance(cur, str):
                        strings.append(cur)

                joined = " ".join(strings).lower()
                if re.search(r"\b(victory|win|won)\b", joined):
                    if re.search(r"\b(defeat|loss|lose|lost)\b", joined):
                        return None
                    return True
                if re.search(r"\b(defeat|loss|lose|lost)\b", joined):
                    return False
        except Exception:
            pass

        lower = line.lower()
        has_win = re.search(r"\b(victory|win|won)\b", lower) is not None
        has_loss = re.search(r"\b(defeat|loss|lose|lost)\b", lower) is not None
        if has_win and not has_loss:
            return True
        if has_loss and not has_win:
            return False
        return None

    def __update_inst_id__grp_id_dict(self, object_dict_arr):
        for object_dict in object_dict_arr:
            if object_dict['instanceId'] not in self.__inst_id_grp_id_dict.keys():
                self.__inst_id_grp_id_dict[object_dict['instanceId']] = object_dict['grpId']

    def __update_game_state(self, raw_dict: [str, str or int]):
        # Derive the local player's systemSeatId from incoming messages (if present)
        system_seat_id = Controller.__get_system_seat_id_from_raw_dict(raw_dict)
        if system_seat_id is not None and system_seat_id != self.__system_seat_id:
            self.__system_seat_id = system_seat_id
            bot_logger.log_info(f"Detected local systemSeatId={self.__system_seat_id}")

        game_state = Controller.__get_game_state_from_raw_dict(raw_dict, fallback_seat_id=self.__system_seat_id or 1)
        self.updated_game_state.update(game_state)
        print(self.updated_game_state)

        # Log all parsed game state data to bot.log
        bot_logger.log_game_state_update(self.updated_game_state.get_full_state())

        # Check for successful actions in the log update
        if self.__action_success_callback:
            # Pass to avoid log spam, as requested by user.
            # The original implementation here was checking GameStateMessage actions
            # which caused false positives for every action in the list.
            pass

        turn_info_dict = self.updated_game_state.get_turn_info()
        is_complete = self.updated_game_state.is_complete()

        # Log controller state
        bot_logger.log_controller_event(
            f"is_complete={is_complete}",
            f"decisionPlayer={turn_info_dict.get('decisionPlayer') if turn_info_dict else None}, has_mulled_keep={self.__has_mulled_keep}"
        )

        if is_complete:
            self.__update_inst_id__grp_id_dict(self.updated_game_state.get_game_objects())
            my_seat = self.__system_seat_id or 1
            if turn_info_dict['decisionPlayer'] == my_seat and self.__has_mulled_keep:
                # Cancel any existing timer before starting a new one
                if self.__decision_execution_thread is not None:
                    self.__decision_execution_thread.cancel()

                def _decision_if_still_my_priority():
                    try:
                        ti = self.updated_game_state.get_turn_info() or {}
                        still_my_priority = (ti.get('decisionPlayer') == my_seat)
                        if still_my_priority and self.__decision_callback and self.__has_mulled_keep:
                            self.__decision_callback(self.updated_game_state)
                        else:
                            bot_logger.log_info(
                                f"Skipping delayed decision (decisionPlayer={ti.get('decisionPlayer')}, my_seat={my_seat})"
                            )
                    except Exception as e:
                        bot_logger.log_error(f"Error in delayed decision callback: {e}")

                self.__decision_execution_thread = threading.Timer(self.__decision_delay, _decision_if_still_my_priority)
                self.__decision_execution_thread.start()

        # Start mulligan timer if we haven't made a mulligan decision yet
        # This needs to trigger regardless of is_complete to handle game restarts
        my_seat = self.__system_seat_id or 1
        if not self.__has_mulled_keep and turn_info_dict and turn_info_dict.get('decisionPlayer') == my_seat:
            if self.__mulligan_execution_thread is not None:
                self.__mulligan_execution_thread.cancel()

            def _mulligan_if_still_mine():
                try:
                    ti = self.updated_game_state.get_turn_info() or {}
                    if ti.get('decisionPlayer') == my_seat and self.__mulligan_decision_callback:
                        self.__mulligan_decision_callback([])
                    else:
                        bot_logger.log_info(
                            f"Skipping delayed mulligan (decisionPlayer={ti.get('decisionPlayer')}, my_seat={my_seat})"
                        )
                        # Allow rescheduling if needed
                        self.__has_mulled_keep = False
                except Exception as e:
                    bot_logger.log_error(f"Error in delayed mulligan callback: {e}")
                    self.__has_mulled_keep = False

            self.__mulligan_execution_thread = threading.Timer(self.__intro_delay, _mulligan_if_still_mine)
            self.__mulligan_execution_thread.start()
            self.__has_mulled_keep = True
            print('making mull decision')

    @staticmethod
    def __get_system_seat_id_from_raw_dict(raw_dict: [str, str or int]):
        try:
            temp_dict = raw_dict.get('greToClientEvent', {})
            messages = temp_dict.get('greToClientMessages', [])
            for message in messages:
                seat_ids = message.get('systemSeatIds')
                if isinstance(seat_ids, list) and len(seat_ids) > 0 and isinstance(seat_ids[0], int):
                    return seat_ids[0]
        except Exception:
            return None
        return None

    @staticmethod
    def __get_game_state_from_raw_dict(raw_dict: [str, str or int], fallback_seat_id: int = 1):
        temp_dict = raw_dict['greToClientEvent']
        temp_arr = temp_dict['greToClientMessages']
        return_game_state = GameState({})
        for message in temp_arr:
            if message['type'] == "GREMessageType_GameStateMessage":
                raw_game_state_dict = message['gameStateMessage']
                game_state_dict = {}
                for key in GameState.GAME_STATE_KEYS:
                    if key in raw_game_state_dict:
                        game_state_dict[key] = raw_game_state_dict[key]
                generated_game_state = GameState(game_state_dict)
                return_game_state.update(generated_game_state)
            # Also parse ActionsAvailableReq for available actions
            elif message['type'] == "GREMessageType_ActionsAvailableReq":
                req = message.get('actionsAvailableReq', {})
                active_actions = req.get('actions', [])
                bot_logger.log_actions_available(active_actions)
                # Wrap each action in the expected format with seatId
                seat_ids = message.get('systemSeatIds') or []
                seat_id = seat_ids[0] if isinstance(seat_ids, list) and len(seat_ids) > 0 else fallback_seat_id
                wrapped_actions = [{'seatId': seat_id, 'action': action} for action in active_actions]
                if wrapped_actions:
                    actions_state = GameState({'actions': wrapped_actions})
                    return_game_state.update(actions_state)
        return return_game_state
