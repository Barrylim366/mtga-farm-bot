from Controller.Utilities.GameStateInterface import GameStateSecondary


class AIKernel:
    """
    Informal Interface
    Defines kernel methods for the AI type.
    """

    def generate_keep(self, card_list) -> bool:
        """ Returns a true or false value to determine whether a hand should be taken """
        pass

    def generate_move(self, game_state: GameStateSecondary, inst_id_grp_id_dict):
        """
        Takes in the game state of a game and returns a Dict of representing a valid move

        Parameters:
            game_state GameStateSecondary: a valid game state of a game
        Requires:
            game_state is of a valid form
        Returns:
            [valid_move: [int,...]]
        """
        pass

