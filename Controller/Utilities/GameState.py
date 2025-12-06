from typing import Dict, List
from Controller.Utilities.GameStateInterface import GameStateSecondary


class GameState(GameStateSecondary):
    def __init__(self, game_dict: [str, str or int] = {}):
        self.game_dict = game_dict
        self.game_dict_expected_keys = ["turnInfo", "timers", "gameObjects", "players", "annotations", "actions",
                                        "zones"]
        self.ti_dict_expected_keys = ["phase", "phase", "turnNumber", "activePlayer", "priorityPlayer",
                                      "decisionPlayer", "nextPhase", "nextStep"]

    def __str__(self):
        return str(self.game_dict)

    def get_full_state(self) -> Dict[str, str or int]:
        return dict(self.game_dict)

    def get_turn_info(self) -> Dict[str, str or int]:
        turn_info_dict = None
        full_state_dict = self.get_full_state()
        if 'turnInfo' in full_state_dict.keys():
            turn_info_dict = full_state_dict['turnInfo']
        return turn_info_dict

    def get_game_info(self) -> Dict[str, str or int]:
        return self.get_full_state()['gameInfo']

    def get_zone(self, zone_type: str, owner_seat_id: int = None) -> Dict[str, str or int]:
        zones = self.get_full_state()['zones']
        matching_zones = []
        zone_to_return = None
        for zone in zones:
            if zone['type'] == zone_type:
                matching_zones.append(zone)
        if len(matching_zones) > 1:
            for zone in matching_zones:
                if zone['ownerSeatId'] == owner_seat_id:
                    zone_to_return = zone
        elif len(matching_zones) == 1:
            zone_to_return = matching_zones[0]
        return zone_to_return

    def get_annotations(self) -> List[Dict]:
        return self.get_full_state()['annotations']

    def get_actions(self) -> List[Dict]:
        return self.get_full_state()['actions']

    def get_players(self) -> List[Dict]:
        return self.get_full_state()['players']

    def get_game_objects(self) -> List[Dict[str, str or int]]:
        return self.get_full_state()['gameObjects']

    def is_complete(self):
        is_complete = True
        current_keys = self.game_dict.keys()
        for expected_key in self.game_dict_expected_keys:
            if expected_key not in current_keys:
                is_complete = False
                return is_complete
        turn_info_keys = self.game_dict['turnInfo'].keys()
        for expected_ti_key in self.ti_dict_expected_keys:
            if expected_ti_key not in turn_info_keys:
                is_complete = False
                return is_complete
        return is_complete

    def __update_dict(self, dict_to_update: [str, str or int], dict_with_update: [str, str or int]):
        for key in dict_with_update:
            if key in dict_to_update.keys():
                item_to_update = dict_to_update[key]
                item_with_update = dict_with_update[key]
                if isinstance(item_with_update, dict):
                    if isinstance(item_to_update, dict):
                        self.__update_dict(item_to_update, item_with_update)
                    else:
                        temp_dict = {}
                        self.__update_dict(temp_dict, item_with_update)
                        dict_to_update[key] = temp_dict
                elif isinstance(item_with_update, int) or isinstance(item_with_update, str) or isinstance(
                        item_with_update, list):
                    dict_to_update[key] = dict_with_update[key]
                else:
                    print("Uh oh something went wrong... :(")
            else:
                dict_to_update[key] = dict_with_update[key]

    def update(self, updated_state: 'GameStateSecondary') -> None:
        self.__update_dict(self.game_dict, updated_state.get_full_state())
