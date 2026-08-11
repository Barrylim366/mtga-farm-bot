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

    def get_pending_message_count(self) -> int:
        try:
            return int(self.get_full_state().get("pendingMessageCount", 0) or 0)
        except Exception:
            return 0

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

    def get_zone_object_count(self, zone_type: str, owner_seat_id: int = None) -> int:
        try:
            zone = self.get_zone(zone_type, owner_seat_id)
            if not zone:
                return 0
            objects = zone.get("objectInstanceIds", []) or []
            return len(objects)
        except Exception:
            return 0

    def get_annotations(self) -> List[Dict]:
        return self.get_full_state()['annotations']

    def remove_annotations_by_type(self, annotation_type: str, affector_id: int = None) -> int:
        """Remove merged annotations of the given type (optionally limited to an
        affector seat or annotations without affector). GRE never sends deletes
        for transient annotations like PlayerSelectingTargets, so callers must
        purge them once the corresponding interaction is finished."""
        annotations = self.game_dict.get("annotations") or []
        kept = []
        removed = 0
        for annotation in annotations:
            if not isinstance(annotation, dict):
                kept.append(annotation)
                continue
            types = annotation.get("type", []) or []
            matches_affector = affector_id is None or annotation.get("affectorId") in (None, affector_id)
            if annotation_type in types and matches_affector:
                removed += 1
                continue
            kept.append(annotation)
        if removed:
            self.game_dict["annotations"] = kept
        return removed

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

    @staticmethod
    def __merge_list_by_key(
        existing_items: List[Dict],
        updated_items: List[Dict],
        *,
        key_name: str,
        deleted_ids: List[int] | None = None,
    ) -> List[Dict]:
        merged: Dict[int, Dict] = {}
        order: List[int] = []

        def _add_item(item: Dict) -> None:
            if not isinstance(item, dict):
                return
            item_key = item.get(key_name)
            if item_key is None:
                return
            if item_key not in merged:
                merged[item_key] = dict(item)
                order.append(item_key)
            else:
                existing = merged[item_key]
                if isinstance(existing, dict):
                    for field, value in item.items():
                        existing[field] = value
                else:
                    merged[item_key] = dict(item)

        for item in existing_items or []:
            _add_item(item)
        for item in updated_items or []:
            _add_item(item)

        for deleted_id in deleted_ids or []:
            if deleted_id in merged:
                merged.pop(deleted_id, None)
            order = [item_key for item_key in order if item_key != deleted_id]

        return [merged[item_key] for item_key in order if item_key in merged]

    def __turn_number_advanced(self, updated_full_state: Dict) -> bool:
        try:
            new_turn = (updated_full_state.get("turnInfo") or {}).get("turnNumber")
            old_turn = (self.game_dict.get("turnInfo") or {}).get("turnNumber")
            if new_turn is None or old_turn is None:
                return False
            return int(new_turn) > int(old_turn)
        except (TypeError, ValueError):
            return False

    def __clear_marked_damage(self) -> None:
        """Drop `damage` from every merged game object.

        Marked damage wears off in each turn's cleanup step, but the GRE never
        says so: it sends `damage` when a creature is dealt some and then simply
        stops sending the field once it is gone -- in a full match log the field
        appears ~100 times and is *never* sent as 0, while the same objects
        reappear in later diffs without it hundreds of times. Since a merge only
        overwrites the keys it is handed, damage would otherwise stick for the
        rest of the game and every `effective_toughness` reading would drift
        further below the truth (removal targeting, fight logic and blocking all
        read it). Same family as the stale zoneId / attackState problem.
        """
        game_objects = self.game_dict.get("gameObjects") or []
        self.game_dict["gameObjects"] = [
            {key: value for key, value in obj.items() if key != "damage"}
            if isinstance(obj, dict) and "damage" in obj
            else obj
            for obj in game_objects
        ]

    def update(self, updated_state: 'GameStateSecondary') -> None:
        updated_full_state = updated_state.get_full_state()
        if updated_full_state.get("type") == "GameStateType_Full":
            # A full snapshot starts a fresh baseline for the current match/game state.
            self.game_dict = dict(updated_full_state)
            return
        # Before anything is merged, so that damage carried by *this* diff wins
        # and so the clear still happens on a turn-change diff that mentions no
        # game objects at all.
        if self.__turn_number_advanced(updated_full_state):
            self.__clear_marked_damage()
        previous_lists = {
            "zones": list(self.game_dict.get("zones", []) or []),
            "gameObjects": list(self.game_dict.get("gameObjects", []) or []),
            "players": list(self.game_dict.get("players", []) or []),
            "timers": list(self.game_dict.get("timers", []) or []),
            "annotations": list(self.game_dict.get("annotations", []) or []),
            "persistentAnnotations": list(self.game_dict.get("persistentAnnotations", []) or []),
        }
        self.__update_dict(self.game_dict, updated_full_state)

        deleted_instance_ids = updated_full_state.get("diffDeletedInstanceIds", []) or []
        deleted_annotation_ids = updated_full_state.get("diffDeletedAnnotationIds", []) or []
        deleted_persistent_annotation_ids = updated_full_state.get("diffDeletedPersistentAnnotationIds", []) or []

        if "zones" in updated_full_state:
            self.game_dict["zones"] = self.__merge_list_by_key(
                previous_lists["zones"],
                updated_full_state.get("zones", []) or [],
                key_name="zoneId",
            )
        if "gameObjects" in updated_full_state or deleted_instance_ids:
            self.game_dict["gameObjects"] = self.__merge_list_by_key(
                previous_lists["gameObjects"],
                updated_full_state.get("gameObjects", []) or [],
                key_name="instanceId",
                deleted_ids=deleted_instance_ids,
            )
        if "players" in updated_full_state:
            self.game_dict["players"] = self.__merge_list_by_key(
                previous_lists["players"],
                updated_full_state.get("players", []) or [],
                key_name="systemSeatNumber",
            )
        if "timers" in updated_full_state:
            self.game_dict["timers"] = self.__merge_list_by_key(
                previous_lists["timers"],
                updated_full_state.get("timers", []) or [],
                key_name="timerId",
            )
        if "annotations" in updated_full_state or deleted_annotation_ids:
            self.game_dict["annotations"] = self.__merge_list_by_key(
                previous_lists["annotations"],
                updated_full_state.get("annotations", []) or [],
                key_name="id",
                deleted_ids=deleted_annotation_ids,
            )
        if "persistentAnnotations" in updated_full_state or deleted_persistent_annotation_ids:
            self.game_dict["persistentAnnotations"] = self.__merge_list_by_key(
                previous_lists["persistentAnnotations"],
                updated_full_state.get("persistentAnnotations", []) or [],
                key_name="id",
                deleted_ids=deleted_persistent_annotation_ids,
            )
