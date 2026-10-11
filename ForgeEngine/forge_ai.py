"""The bot's AI with Forge consulted first; the bot's own move on any problem."""
from __future__ import annotations

from typing import Callable

from AI.AIInterface import AIKernel
from ForgeEngine.bridge import Bridge, Config, log, probe_safely, shared


class ForgeAI(AIKernel):
    """Wraps the bot's AI. With the engine switched off it is the bot's AI, unchanged.

    The mulligan stays with the bot: Forge's advisor has no mulligan decision.
    """

    def __init__(self, base, enabled: Callable[[], bool], bridge: Bridge | None = None,
                 config: Callable[[], Config] = Config.from_env):
        self.base = base
        self.enabled = enabled
        self.bridge = bridge or shared()
        self.config = config

    def reset(self):
        reset = getattr(self.base, "reset", None)
        if callable(reset):
            reset()

    def generate_keep(self, card_list) -> bool:
        return self.base.generate_keep(card_list)

    def generate_move(self, game_state, inst_id_grp_id_dict):
        own = self.base.generate_move(game_state, inst_id_grp_id_dict)
        try:
            if not self.enabled():
                return own
            config = self.config()
            if config.off():
                return own
            return self.bridge.choose_move(
                own,
                lambda state, ids: probe_safely(self.base, state, ids),
                game_state,
                inst_id_grp_id_dict,
                config,
            )
        except Exception as exc:
            log(f"main decision failed ({exc}); using the bot's move")
            return own

    def __getattr__(self, name):
        # Anything else the game loop reads off its AI is the bot's.
        if name == "base":
            raise AttributeError(name)
        return getattr(self.base, name)
