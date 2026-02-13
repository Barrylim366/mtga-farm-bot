from typing import Dict, List


class GameStateKernel:
    """
    Informal Interface
    Defines methods for the GameState type
    """

    GAME_STATE_KEYS = ['turnInfo', 'timers', 'gameObjects', 'players', 'annotations', 'actions', 'zones']

    def get_full_state(self) -> Dict[str, str or int]:
        """
        Returns:
             a dictionary object with the game state fully constructed like a JSON object
        Ensures:
            Format of dict is as follows:
            {
                "turnInfo": {
                    "phase": str,
                    "step": str,
                    "turnNumber": int,
                    "activePlayer": int,
                    "priorityPlayer": int,
                    "decisionPlayer": int,
                    "nextPhase": str,
                    "nextStep": str
                },
                "timers": [
                    {
                      "timerId": int,
                      "type": str,
                      "durationSec": int,
                      "behavior": str,
                      "warningThresholdSec": int
                    },
                    ...
                ],
                "gameObjects": [
                    {
                      "instanceId": int,
                      "grpId": int,
                      "type": str,
                      "zoneId": int,
                      "visibility": str,
                      "ownerSeatId": int,
                      "controllerSeatId": int,
                      "superTypes": List[str],
                      "cardTypes": List[str],
                      "subtypes": List[str],
                      "name": int,
                      "abilities": List[int],
                      "overlayGrpId": int
                    },
                    ...
                ],
                "players": [
                    {
                      "lifeTotal": int,
                      "systemSeatNumber": int,
                      "maxHandSize": int,
                      "turnNumber": int,
                      "teamId": int,
                      "timerIds": [
                        int,
                        ...
                      ],
                      "controllerSeatId": int,
                      "controllerType": str,
                      "startingLifeTotal": int
                    }
                ],
                "annotations": [
                    {
                        "id": 188,
                        "affectedIds": List[int],
                        "type": List[str],
                        "details": [
                            {
                              "key": str,
                              "type": str,
                              "valueInt32": List[int]
                            },
                            ...
                          ]
                    },
                    ...
                ],
                "actions": [
                    {
                        "seatId": int,
                        "action": {
                            "actionType": str,
                            "instanceId": int,
                            "manaCost": [
                              {
                                "color": List[str],
                                "count": int
                              },
                              ...
                            ]
                        }
                    },
                    ...
                ],
                "zones": [
                    {
                      "zoneId": int,
                      "type": str,
                      "visibility": str,
                      "ownerSeatId": int,
                      "objectInstanceIds": List[int]
                      "viewers": List[int]
                    },
                ],

            }
        """
        pass


class GameStateSecondary(GameStateKernel):
    """
    Informal Interface
    Defines secondary methods for the GameState type
    """

    def get_zone(self, zone_type: str, owner_seat_id: int = None) -> Dict[str, str or int]:
        """
        Gets specified zone information

        Parameters:
            zone_type (str): Which zone to pull information from. Must be one of the following: Revealed,
                Suppressed, Pending, Command, Stack, Battlefield, Exile, Limbo, Hand, Library, Graveyard, Sideboard
            owner_seat_id (int):  Which player owns the zone (this parameter is not required for zones that have no
                ownership)
        Returns:
            A properly formatted dictionary with all the information necessary needed for a zone
        """

    def get_actions(self) -> List[Dict]:
        """
        Gets all possible actions that are currently available

        Returns:
            A properly formatted dictionary with all the information necessary for actions
        """
        pass

    def get_turn_info(self) -> Dict[str, str or int]:
        """
        Gets all possible actions that are currently available

        Returns:
            A properly formatted dictionary with all the information necessary for actions
        """
        pass

    def get_players(self) -> List[Dict]:
        """
        Get player info

        Returns:
            A properly formatted dictionary with all the information necessary for the players
        """
        pass

    def get_annotations(self) -> List[Dict[str, str or int]]:
        """
        Get annotation info

        Returns:
            A properly formatted dictionary with all the information necessary for annotations on permanents
        """
        pass

    def get_game_info(self) -> Dict[str, str or int]:
        """
        Gets game info

        Returns:
            A properly formatted dictionary with all the information necessary for information on the game
        """
        pass

    def get_game_objects(self) -> List[Dict[str, str or int]]:
        """
        Returns:
             A list of properly formatted dictionaries with all gameObject info
        """
        pass

    def clear(self) -> None:
        """
        Clears a gamestate back to its default value
        """
        pass

    def update(self, updated_state: 'GameStateSecondary') -> None:
        """
        Updates self to the updated state. If self does not contain an index that updated_state does then the index
        is added to state.
        """
        pass

    def diff(self, state: 'GameStateSecondary') -> Dict[str, str or int]:
        """
        Returns the difference between the two states in a new state object
        """
        pass
