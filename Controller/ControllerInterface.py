class ControllerKernel:
    """
    Informal Interface
    Defines kernel methods for the Controller type.
    """

    def start_game_from_home_screen(self) -> None:
        """ Starts a game from the home screen """

    def start_monitor(self) -> None:
        """
        Starts the monitor of the log
        Requires:
            The decision callback to be set
        """

    pass

    def get_game_state(self) -> 'GameStateSecondary':
        """ 
        Returns a tuple representing the game's state
        Returns:
            A GameState object containing the current state of the game
        """

    pass

    def attack(self, card_id: int = -1) -> None:
        """
        Makes a specified card attack

        Parameters:
            card_id (int): the id of the card that should attack. If card = -1 then all should attack
        Requires:
            specified card can attack
        Ensures:
            Specified card attacks
        """
    pass

    def block(self, attacker_id: int, blocker_id: int) -> None:
        """
        Makes the blocker card block the attacker

        Parameters:
            attacker_id (int): The id of an opponents card that is attacking
            blocker_id (int): The id of a player's card that should block the specified attacking card
        Requires:
            game_wave == ('opponent_attacking')
            number of cards that are able to block > 0
        Ensures:
            Specified attacker is attempted to be blocked by blocker card
        """
        pass

    def cast(self, card_id: int) -> None:
        """
        Makes the blocker card block the attacker

        Parameters:
            card_id (int): The id of a card in the player's hand
        Requires:
            The requirements to cast the card are met
        Ensures:
            Specified card is cast
        """
        pass

    def select_target(self, target_id: int) -> None:
        """
        Selects a target for spell or ability specified by target id.

        Parameters:
            target_id (int): The id of the spell or abilities target
        Requires:
            The target id is on the battlefield Must occur after an ability is used or a spell is cast that needs
            a target specified (MTGA example: the player is specifically prompted for a target after a spell is cast)
        Ensures:
            Specified card with a corresponding id to target_id is selected as the target
        """

    def activate_ability(self, card_id: int, ability_id: int) -> None:
        """
        Activates a specified card's specified ability

        Parameters:
            card_id (int): The id of a card in the player's hand
            ability_id (int): The id of an ability that is to be activated
        Requires:
            All prerequisites for the ability are filled
        Ensures:
            Specified card's ability is used
        """
        pass

    def resolve(self) -> None:
        """ Passes No attacks and or resolves a decision in the game. Basically a "do nothing" method """
        pass

    def auto_pass(self) -> None:
        """ Auto passes a turn and forfeits priority until player regains it through opponent """
        pass

    def unconditional_auto_pass(self) -> None:
        """ Auto passes a turn unconditionally. Cannot regain priority for the turn """
        pass

    def all_attack(self) -> None:
        """ Attacks with all cards that can attack """
        pass

    def set_decision_callback(self, method) -> None:
        """
        Sets the decision callback for the controller which calls method every single time a decision is required from the player. In other words, everytime the game is
        paused waiting on a player decision method is called

        Parameters:
            method: the callback method to be called
        Requires:
            Method must take a single parameter of the current game state
        """
        pass

    def set_mulligan_decision_callback(self, method) -> None:
        """
        Sets the callback method for the controller which calls it everytime a mulligan decision is made.
        Parameters
            method: the callback method to be called
        Requires:
            method must take a parameter that is the list of card ids
        """
        pass

    def keep(self, keep: bool):
        """
        Parameters
            keep: whether or not to keep the hand given
        Requires:
            the player must be being prompted for a mulligan decision
        """

class ControllerSecondary(ControllerKernel):
    """
    Informal Interface
    Defines secondary methods for the Controller type.
    """
    def start_game(self) -> None:
        """ Starts log monitor and starts the game """