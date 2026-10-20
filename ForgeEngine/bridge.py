"""The bridge between the bot and Forge: the advisor process plus our own reading of Player.log.

The bot's own answer is always computed first and is what gets used whenever
Forge has no answer: the advisor is still starting, timed out, crashed, or
picked something the bot cannot carry out. Forge can therefore only ever
replace a move with another move Arena is offering.

Switches beside the "Enable Forge Engine" checkbox (environment variables,
read on every decision; all default to the first value):

    MTGA_FORGE           on | shadow (log what Forge would do, play the bot's move)
    MTGA_FORGE_BLOCKS    on | off
    MTGA_FORGE_MITIGATE  35 | off   share of our life (percent) one attack may take before
                                    cheap creatures are put in its way
    MTGA_FORGE_ATTACK    select | shadow (log only, attack with everything)
    MTGA_FORGE_LANDS     on | off   pick the land by trying each against the hand
    MTGA_FORGE_INSTANTS  on | off   instants and flash outside our main phases
    MTGA_FORGE_ABILITIES on | off   abilities of our permanents
    MTGA_FORGE_TARGETS   abilities | all | off
    MTGA_FORGE_SCRY      on | advise | off
    MTGA_FORGE_TIMEOUT   4          seconds per decision
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import bot_logger
from Controller.Utilities.GameState import GameState
from ForgeEngine import paths, snapshot
from ForgeEngine.carddb import CardDb, default_log_path, find_data_dir
from ForgeEngine.client import AdvisorClient, AdvisorError
from ForgeEngine.tracker import ArenaTracker, LogFollower

RESTART_COOLDOWN_SEC = 60.0
# A ceiling on abilities per turn, for the free one Forge and Arena would both
# allow for ever.
MAX_ABILITIES_PER_TURN = 6
# How long after a move its targets still count for the prompt that shows up.
INTENT_SEC = 25.0

_OFF = ("off", "0", "false", "no")
_ON = ("on", "1", "true", "yes")


def log(message: str) -> None:
    bot_logger.log_info(f"FORGE {message}")


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "")
    return (value if value.strip() else default).strip().lower()


@dataclass
class Config:
    mode: str = "on"
    blocks: bool = True
    mitigate: float | None = 0.35
    attack: str = "select"
    instants: bool = True
    lands: bool = True
    abilities: bool = True
    targets: str = "abilities"
    scry: str = "on"
    timeout: float = 4.0
    free_forge: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        wanted = _env("MTGA_FORGE_MITIGATE", "35")
        if wanted in _OFF:
            mitigate = None
        else:
            try:
                mitigate = min(95.0, max(5.0, float(wanted.rstrip("%")))) / 100.0
            except ValueError:
                mitigate = 0.35
        try:
            timeout = float(_env("MTGA_FORGE_TIMEOUT", "4"))
        except ValueError:
            timeout = 4.0
        return cls(
            mode=_env("MTGA_FORGE", "on"),
            blocks=_env("MTGA_FORGE_BLOCKS", "on") not in _OFF,
            mitigate=mitigate,
            attack=_env("MTGA_FORGE_ATTACK", "select"),
            instants=_env("MTGA_FORGE_INSTANTS", "on") not in _OFF,
            lands=_env("MTGA_FORGE_LANDS", "on") not in _OFF,
            abilities=_env("MTGA_FORGE_ABILITIES", "on") not in _OFF,
            targets=_env("MTGA_FORGE_TARGETS", "abilities"),
            scry=_env("MTGA_FORGE_SCRY", "on"),
            timeout=timeout,
            free_forge=_env("MTGA_FORGE_FREE", "off") in _ON,
        )

    def off(self) -> bool:
        return self.mode in _OFF

    def acting(self) -> bool:
        return self.mode in _ON


@dataclass
class Intent:
    """The targets Forge chose for the move just made, for the prompt that follows."""

    action: str | None
    id: int | None
    grp_id: int | None
    cards: list
    players: list
    names: list
    made: float = field(default_factory=time.monotonic)


@dataclass
class AttackPlan:
    key: tuple
    legal: list
    decision: dict
    attackers: list
    choice: str  # pick | all | none
    names: list


def move_name(move: dict) -> str:
    return next(iter(move), "resolve") if isinstance(move, dict) else "resolve"


def move_text(move: dict) -> str:
    name = move_name(move)
    return f"{name}{move.get(name, [])}" if isinstance(move, dict) else str(move)


def filtered_state(game_state, instance_id: int, as_own_main_for: int | None = None) -> GameState:
    """The bot's game state with every cast/play action but one hidden.

    Handing this to the bot's own generate_move asks it one question: "would you
    cast exactly this card right now?" That reuses all of its safety rules
    (prompts it cannot click, a second legend, removal with no target, whether
    it can pay) without copying any of them here. Outside our main phase the
    question is asked as if it were -- Arena only offers cards that can legally
    be cast right now, so timing needs no second opinion.
    """
    full = dict(game_state.get_full_state())
    kept = []
    for wrapper in full.get("actions") or []:
        action = (wrapper or {}).get("action") or {}
        kind = action.get("actionType")
        if kind == "ActionType_Activate_Mana":
            kept.append(wrapper)
        elif kind in ("ActionType_Cast", "ActionType_Play") and action.get("instanceId") == instance_id:
            kept.append(wrapper)
    full["actions"] = kept
    turn_info = full.get("turnInfo")
    if as_own_main_for is not None and isinstance(turn_info, dict) and turn_info:
        turn_info = dict(turn_info)
        turn_info.pop("step", None)
        for key in ("activePlayer", "decisionPlayer", "priorityPlayer"):
            turn_info[key] = as_own_main_for
        turn_info["phase"] = "Phase_Main1"
        full["turnInfo"] = turn_info
    return GameState(full)


class Bridge:
    """The advisor process plus our own reading of Player.log, shared by every hook."""

    def __init__(self, client: AdvisorClient | None = None, cards: CardDb | None = None,
                 tracker: ArenaTracker | None = None, restarts: bool = True):
        self._lock = threading.RLock()
        self.client = client
        self.cards = cards
        self.tracker = tracker or ArenaTracker()
        self.follower: LogFollower | None = None
        self.ready = client is not None and cards is not None
        self.restarts = restarts
        self._starting = False
        self._last_start: float | None = None
        self.log_path: str | None = None
        self.data_dir_hint: str | None = None
        self.attack_plan: AttackPlan | None = None
        self._asked_attack = None
        self.intent: Intent | None = None
        self._ability_turn = None
        self._abilities_used = 0
        self._abilities_failed: set = set()

    # -- lifecycle ------------------------------------------------------------

    def configure(self, log_path: str | None = None, data_dir_hint: str | None = None) -> None:
        with self._lock:
            if log_path:
                self.log_path = log_path
            if data_dir_hint:
                self.data_dir_hint = data_dir_hint

    def progress(self) -> tuple[bool, bool]:
        """(ready to be asked, being started right now)."""
        with self._lock:
            return self.ready, self._starting

    def start_async(self) -> None:
        """Start the advisor without blocking; loading Forge's cards takes ~10 s."""
        if not self.restarts:
            return
        with self._lock:
            now = time.monotonic()
            if self._starting or (self._last_start is not None and now - self._last_start < RESTART_COOLDOWN_SEC):
                return
            self._starting = True
            self._last_start = now

        def run() -> None:
            try:
                self._start()
            except Exception as exc:
                log(f"advisor failed to start ({exc}); the bot's own decisions are used")
            finally:
                with self._lock:
                    self._starting = False

        threading.Thread(target=run, name="forge-advisor-start", daemon=True).start()

    def _start(self) -> None:
        if not paths.installed():
            raise AdvisorError("the Forge engine is not installed")
        cards = self.cards
        if cards is None:
            cards = CardDb.load(find_data_dir(self.data_dir_hint, self.log_path))
        client = self.client or AdvisorClient()
        info = client.start()
        log_path = os.environ.get("MTGA_PLAYER_LOG") or self.log_path or default_log_path()
        with self._lock:
            follower = LogFollower(log_path)
            # A fresh tracker: a restart mid-game catches up from the top of the log.
            self.tracker = ArenaTracker()
            follower.poll(self.tracker)
            self.cards, self.client, self.follower = cards, client, follower
            self.ready = True
        log(f"advisor ready: {info.get('cards')} Forge cards, {len(cards)} Arena cards, {info.get('startupMs')} ms")

    def close(self) -> None:
        with self._lock:
            client, self.client = self.client, None
            self.ready = False
            self._last_start = None
        if client is not None:
            client.close()

    def usable(self) -> bool:
        """Whether the advisor can be asked right now; one that died is brought back."""
        with self._lock:
            if not self.ready or self.client is None or self.cards is None:
                return False
            if not self.client.running():
                self.ready = False
                self.start_async()
                return False
            return True

    def sync(self) -> None:
        with self._lock:
            if self.follower is not None:
                self.follower.poll(self.tracker)

    def decide(self, kind: str, state: dict, config: Config, extra: dict | None = None) -> dict:
        client = self.client
        if client is None:
            raise AdvisorError("advisor is not running")
        return client.decide(kind, state, config.timeout, extra)

    @staticmethod
    def decision(reply: dict) -> dict:
        decision = reply.get("decision")
        if decision is None:
            raise AdvisorError("reply carries no decision")
        return decision

    def record(self, entry: dict) -> None:
        from runtime_paths import _running_under_test_runner

        if _running_under_test_runner():
            return
        entry = dict(entry)
        entry["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            target = paths.decision_log()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def name(self, instance_id) -> str:
        obj = self.tracker.object(instance_id) or {}
        card = self.cards.name(obj.get("grpId")) if self.cards else None
        return card or f"card {instance_id}"

    def turn_key(self) -> tuple:
        return (self.tracker.game_info.get("matchID"), self.tracker.turn_info.get("turnNumber"))

    # -- abilities and targets ------------------------------------------------

    def _ability_turn_state(self):
        key = self.turn_key()
        if self._ability_turn != key:
            self._ability_turn = key
            self._abilities_used = 0
            self._abilities_failed = set()

    def ability_result(self, instance_id: int, started: bool) -> None:
        with self._lock:
            self._ability_turn_state()
            if not started:
                self._abilities_failed.add(instance_id)

    def _usable_abilities(self, entries: list, offered: list, config: Config) -> list:
        """Our abilities that the bot's view offers too, not yet failed this turn."""
        if not config.abilities:
            return []
        self._ability_turn_state()
        if self._abilities_used >= MAX_ABILITIES_PER_TURN:
            return []
        offered_by_id = {}
        for instance_id, ability in offered:
            offered_by_id[instance_id] = ability
        return [
            entry
            for entry in entries
            if offered_by_id.get(entry.get("id"), object()) == entry.get("abilityGrpId")
            and entry.get("id") not in self._abilities_failed
        ]

    def _remember_intent(self, decision: dict, config: Config) -> None:
        """Keep Forge's targets for the prompt that follows, when they are to be used."""
        self.intent = None
        targets = decision.get("targets") or {}
        if config.targets in _OFF or not (targets.get("cards") or targets.get("players")):
            return
        decision_id = decision.get("id")
        obj = self.tracker.object(decision_id) or {}
        types = obj.get("cardTypes") or []
        wanted = (
            decision.get("action") == "activate"
            or config.targets in ("all", "abilities", "spells")
            or "CardType_Artifact" in types
            or "CardType_Enchantment" in types
            or "CardType_Instant" in types
            or "CardType_Sorcery" in types
        )
        if wanted:
            self.intent = Intent(
                action=decision.get("action"),
                id=decision_id,
                grp_id=obj.get("grpId"),
                cards=list(targets.get("cards") or []),
                players=list(targets.get("players") or []),
                names=list(targets.get("names") or []),
            )

    def intent_for(self, source_id) -> Intent | None:
        """Forge's targets for the spell or ability now asking, or None."""
        with self._lock:
            intent = self.intent
            if intent is None:
                return None
            age = time.monotonic() - intent.made
            if age > INTENT_SEC:
                return None
            self.sync()
            source = self.tracker.object(source_id)
            if source is None:
                # The prompt can arrive before the state that shows its source.
                return intent if age < 10.0 else None
            same = (
                intent.id is not None and intent.id in (source.get("instanceId"), source.get("parentId"))
            ) or (
                intent.grp_id is not None and intent.grp_id in (source.get("grpId"), source.get("objectSourceGrpId"))
            )
            return intent if same else None

    @staticmethod
    def _free_forge_safe(instance_id: int, game_state, ids: dict, me, offered) -> bool:
        """Fail closed: approve only a known, supported, offered, non-duplicate-legend card."""
        grp_id = ids.get(instance_id)
        if grp_id is None or instance_id not in offered:
            return False
        try:
            import AI.Utilities.CardPolicy as CardPolicy
            from AI.Utilities import LegendRule, RemovalLogic
            is_unsupported, has_data = CardPolicy.is_unsupported_to_cast_local(grp_id)
            if not has_data or is_unsupported:
                return False
            full_state = game_state.get_full_state() or {}
            bf_ids = RemovalLogic.battlefield_zone_ids(full_state)
            live_ids = RemovalLogic.battlefield_instance_ids(full_state, bf_ids)
            is_dupe_legend = LegendRule.duplicate_legend_in_play(
                cast_instance_id=instance_id,
                game_objects=full_state.get("gameObjects", []) or [],
                my_seat=me,
                battlefield_zone_ids=bf_ids,
                live_instance_ids=live_ids,
            ) is not None
            return not is_dupe_legend
        except Exception as e:
            bot_logger.log_info(f"Free Forge safety check exception for grp_id={grp_id}: {e}")
            return False

    # -- main phase -----------------------------------------------------------

    def choose_move(self, base: dict, bot_generate: Callable, game_state, ids: dict, config: Config) -> dict:
        """The move to make: Forge's when it has one the bot can carry out, else base."""
        turn_info = game_state.get_turn_info() or {}
        me = turn_info.get("decisionPlayer") or None
        phase = turn_info.get("phase") or ""
        own_main = me is not None and turn_info.get("activePlayer") == me and phase in ("Phase_Main1", "Phase_Main2")
        if not own_main:
            return self.choose_instant(base, bot_generate, game_state, ids, config)
        priority = turn_info.get("priorityPlayer")
        priority_is_ours = priority in (None, 0, me)
        if not priority_is_ours or move_name(base) not in ("cast", "resolve"):
            # Targets, activations and the attack itself stay with the bot.
            return base

        offered: dict = {}
        offered_abilities: list = []
        for wrapper in game_state.get_actions() or []:
            action = (wrapper or {}).get("action") or {}
            instance_id = action.get("instanceId")
            if not isinstance(instance_id, int):
                continue
            kind = action.get("actionType")
            if kind in ("ActionType_Cast", "ActionType_Play"):
                offered[instance_id] = kind
            elif kind == "ActionType_Activate":
                offered_abilities.append((instance_id, action.get("abilityGrpId")))

        with self._lock:
            if not self.usable():
                return base
            self.sync()
            tracker = self.tracker
            ours = tracker.turn_info or {}
            if (
                not tracker.in_sync
                or tracker.my_seat != me
                or ours.get("phase") != phase
                or ours.get("activePlayer") != me
                or ours.get("turnNumber") != turn_info.get("turnNumber")
                or not tracker.stack_is_empty()
            ):
                return base
            if self.cards is None:
                return base
            position = snapshot.build_state(tracker, self.cards)
            if position is None:
                return base
            request = snapshot.main_request(tracker)
            # Both views must agree the action exists: ours for Forge, the
            # bot's for carrying it out.
            castable = [i for i in request["castable"] if offered.get(i) == "ActionType_Cast"]
            playable = [i for i in request["playable"] if offered.get(i) == "ActionType_Play"]
            usable = self._usable_abilities(request["activatable"], offered_abilities, config)
            request.update({"castable": castable, "playable": playable, "activatable": usable})
            if not config.lands:
                request["landChooser"] = False

            decision = None
            diag: dict = {}
            if castable or playable or usable:
                reply = self.decide("main", position, config, request)
                chosen = self.decision(reply)
                diag = reply.get("diag") or {}
                if chosen.get("action") == "none" and position.get("phase") == "MAIN1":
                    # Forge's AI keeps most permanents for the second main
                    # phase. Arena does not stop there unless an attack
                    # happened, so anything held would never be cast.
                    after_combat = dict(position)
                    after_combat["phase"] = "MAIN2"
                    later = self.decision(self.decide("main", after_combat, config, request))
                    if later.get("action") != "none":
                        later["heldForAfterCombat"] = True
                        chosen = later
                decision = chosen

            chosen_move = base
            note = ""
            action = (decision or {}).get("action") or ""
            if decision is None:
                note = "nothing to choose from"
            elif action in ("play", "cast"):
                instance_id = decision.get("id")
                if not isinstance(instance_id, int):
                    return base
                wanted = {"cast": [instance_id]}
                if base == wanted or bot_generate(filtered_state(game_state, instance_id), ids) == wanted:
                    chosen_move = wanted
                elif config.free_forge:
                    if self._free_forge_safe(instance_id, game_state, ids, me, offered):
                        chosen_move = wanted
                        note = "approved by Forge (free forge; legal in Arena and safe)"
                    else:
                        note = "bot would not cast that card; keeping the bot's move"
                else:
                    note = "bot would not cast that card; keeping the bot's move"
            elif action == "activate":
                ability = next((e for e in usable if e.get("id") == decision.get("id")), None)
                if ability is None:
                    note = "Forge chose an ability that was not offered"
                else:
                    chosen_move = {"activate_ability": [decision.get("id"), ability.get("abilityGrpId")]}
            else:
                note = "no play; bot decides"

            self.intent = None
            forge_move = None
            if decision is not None and action in ("play", "cast"):
                forge_move = {"cast": [decision.get("id")]}
            elif decision is not None and action == "activate" and move_name(chosen_move) == "activate_ability":
                forge_move = chosen_move
            if config.acting() and decision is not None and forge_move == chosen_move:
                self._remember_intent(decision, config)
                if action == "activate":
                    self._ability_turn_state()
                    self._abilities_used += 1

            forge_text = "none"
            if decision is not None and action != "none":
                forge_text = f"{action} {decision.get('card')}"
                target_names = (decision.get("targets") or {}).get("names") or []
                if target_names:
                    forge_text += " -> " + ", ".join(str(n) for n in target_names)
            played = chosen_move if config.acting() else base
            outcome = move_text(played) + ("" if config.acting() else " (shadow)")
            log(
                f"main turn={turn_info.get('turnNumber')} {position.get('phase')}: "
                f"forge={forge_text} bot={move_text(base)} -> {outcome}" + (f" ({note})" if note else "")
            )
            self.record({
                "kind": "main", "turn": turn_info.get("turnNumber"), "phase": position.get("phase"),
                "offered": list(offered.keys()),
                "forge": decision, "bot": base, "final": played, "note": note,
                "diag": {k: v for k, v in diag.items() if v},
            })
            return played

    def choose_instant(self, base: dict, bot_generate: Callable, game_state, ids: dict, config: Config) -> dict:
        """Outside our main phases: cast an instant or use an ability where the bot would pass.

        Covers the opponent's turn and our combat and end steps, with nothing on
        the stack. A spell on the stack is left to the bot (its counterspell
        logic): the stack is not rebuilt in Forge.
        """
        if not config.instants or move_name(base) != "resolve":
            return base
        offered = set()
        offered_abilities = []
        for wrapper in game_state.get_actions() or []:
            action = (wrapper or {}).get("action") or {}
            instance_id = action.get("instanceId")
            if not isinstance(instance_id, int):
                continue
            if action.get("actionType") == "ActionType_Cast":
                offered.add(instance_id)
            elif action.get("actionType") == "ActionType_Activate":
                offered_abilities.append((instance_id, action.get("abilityGrpId")))
        if not offered and not (offered_abilities and config.abilities):
            return base
        turn_info = game_state.get_turn_info() or {}

        with self._lock:
            if not self.usable():
                return base
            self.sync()
            tracker = self.tracker
            me = tracker.my_seat
            ours = dict(tracker.turn_info or {})
            bot_priority = turn_info.get("priorityPlayer")
            our_priority = ours.get("priorityPlayer")
            if (
                not tracker.in_sync
                or me is None
                or bot_priority not in (None, 0, me)
                or our_priority not in (None, me)
                or ours.get("turnNumber") != turn_info.get("turnNumber")
                or ours.get("phase") != turn_info.get("phase")
                or not tracker.stack_is_empty()
            ):
                return base
            phase = snapshot.forge_phase(ours)
            if phase is None or phase.startswith("MAIN"):
                return base
            if self.cards is None:
                return base
            position = snapshot.build_state(tracker, self.cards)
            if position is None:
                return base
            request = snapshot.main_request(tracker)
            castable = [i for i in request["castable"] if i in offered]
            usable = self._usable_abilities(request["activatable"], offered_abilities, config)
            if not castable and not usable:
                return base
            extra = {
                "castable": castable,
                "playable": [],
                "activatable": usable,
                "combat": snapshot.combat_request(tracker),
            }
            decision = self.decision(self.decide("main", position, config, extra))
            wants = decision.get("action") or ""
            chosen_move = base
            note = ""
            if wants == "cast":
                instance_id = decision.get("id")
                if not isinstance(instance_id, int):
                    return base
                wanted = {"cast": [instance_id]}
                if bot_generate(filtered_state(game_state, instance_id, me), ids) == wanted:
                    chosen_move = wanted
                elif config.free_forge:
                    if self._free_forge_safe(instance_id, game_state, ids, me, offered):
                        chosen_move = wanted
                        note = "approved by Forge (free forge; instant-speed legal in Arena and safe)"
                    else:
                        note = "bot would not cast that card"
                else:
                    note = "bot would not cast that card"
            elif wants == "activate":
                ability = next((e for e in usable if e.get("id") == decision.get("id")), None)
                if ability is None:
                    return base
                chosen_move = {"activate_ability": [decision.get("id"), ability.get("abilityGrpId")]}
            else:
                return base
            played = chosen_move if config.acting() else base
            self.intent = None
            if config.acting() and chosen_move != base:
                self._remember_intent(decision, config)
                if wants == "activate":
                    self._ability_turn_state()
                    self._abilities_used += 1
            whose = "our" if ours.get("activePlayer") == me else "the opponent's"
            outcome = move_text(played) + ("" if config.acting() else " (shadow)")
            log(
                f"instant turn={turn_info.get('turnNumber')} {whose} {phase.replace('_', ' ').lower()}: "
                f"forge={wants} {decision.get('card')} bot={move_text(base)} -> {outcome}"
                + (f" ({note})" if note else "")
            )
            self.record({
                "kind": "instant", "turn": turn_info.get("turnNumber"), "phase": phase,
                "offered": list(offered),
                "forge": decision, "bot": base, "final": played, "note": note,
            })
            return played

    # -- combat ---------------------------------------------------------------

    def choose_blocks(self, legal_blockers: list, attacking_ids, config: Config) -> list | None:
        """Forge's blocks as (blocker, attacker) pairs, or None when it has no answer."""
        with self._lock:
            if not self.usable():
                return None
            self.sync()
            tracker = self.tracker
            if not tracker.in_sync or (
                tracker.my_seat is not None and tracker.turn_info.get("activePlayer") == tracker.my_seat
            ):
                return None
            if self.cards is None:
                return None
            position = snapshot.build_state(tracker, self.cards)
            if position is None:
                return None
            request = snapshot.block_request(tracker)
            if not request["attackers"] and attacking_ids:
                request["attackers"] = [{"id": i} for i in sorted(attacking_ids)]
            # The prompt the bot answers is the authority on what may block what.
            legal = {}
            for entry in legal_blockers or []:
                if isinstance(entry, dict) and entry.get("blockerInstanceId") is not None:
                    legal[str(entry["blockerInstanceId"])] = list(entry.get("attackerInstanceIds") or [])
            request["legalBlocks"] = legal
            if not request["attackers"]:
                return None
            if config.mitigate is not None:
                request["mitigate"] = {"share": config.mitigate}
            reply = self.decide("block", position, config, request)
            decision = self.decision(reply)
            pairs = []
            for block in decision.get("blocks") or []:
                blocker, attacker = block.get("blocker"), block.get("attacker")
                if isinstance(blocker, int) and isinstance(attacker, int):
                    pairs.append((blocker, attacker))
            names = [str(n) for n in decision.get("names") or []]
            log(f"blocks: {'; '.join(names) if names else 'none'}" + ("" if config.acting() else " (shadow)"))
            control = decision.get("mitigation") or {}
            for given in control.get("blocks") or []:
                log(
                    f"blocks: damage control gives up {given.get('blockerName')} to stop "
                    f"{given.get('attackerName')} (saves {given.get('saves')})"
                )
            self.record({
                "kind": "block", "turn": tracker.turn_info.get("turnNumber"), "forge": decision,
                "diag": {k: v for k, v in (reply.get("diag") or {}).items() if v},
            })
            return pairs

    def plan_attack(self, config: Config) -> AttackPlan | None:
        """The attack for this turn, worked out once: which legal attackers to send.

        Forge's AI names its attackers; that choice, attacking with everything,
        and not attacking are each played out through the opponent's next turn,
        and the best-scoring one is the plan. Forge's own pick wins a tie.
        """
        with self._lock:
            if not self.usable():
                return None
            self.sync()
            key = self.turn_key()
            if self.attack_plan is not None and self.attack_plan.key == key:
                return self.attack_plan
            if self._asked_attack == key or not self.tracker.in_sync:
                return None
            legal = snapshot.attack_request(self.tracker)
            if not legal:
                return None
            if self.cards is None:
                return None
            position = snapshot.build_state(self.tracker, self.cards)
            if position is None:
                return None
            self._asked_attack = key
            decision = self.decision(
                self.decide("attack", position, config, {"legalAttackers": legal, "evaluate": True})
            )
            names = [str(n) for n in decision.get("names") or []]
            scores = decision.get("scores") or {}
            picked = [a.get("id") for a in decision.get("attackers") or [] if isinstance(a.get("id"), int)]
            best = "pick"
            for name in ("all", "none"):
                candidate, leading = scores.get(name), scores.get(best)
                if isinstance(candidate, (int, float)) and isinstance(scores.get("pick"), (int, float)) \
                        and isinstance(leading, (int, float)) and candidate > leading:
                    best = name
            attackers = list(legal) if best == "all" else [] if best == "none" else picked
            log(
                f"attack turn={key[1]}: forge would attack with [{', '.join(names) or 'nobody'}] of "
                f"{len(legal)} legal; playout scores {json.dumps(scores)}; plan={best}"
            )
            self.record({"kind": "attack", "turn": key[1], "forge": decision, "legal": legal, "plan": best})
            self.attack_plan = AttackPlan(key, list(legal), decision, attackers, best, names)
            return self.attack_plan


_SHARED: Bridge | None = None
_SHARED_LOCK = threading.Lock()


def shared() -> Bridge:
    """The one bridge the running bot uses."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = Bridge()
        return _SHARED


def probe_safely(ai, game_state, ids):
    """Ask the bot's AI a hypothetical question without letting it change its memory."""
    saved = copy.deepcopy(ai.__dict__)
    try:
        return ai.generate_move(game_state, ids)
    finally:
        ai.__dict__.clear()
        ai.__dict__.update(saved)
