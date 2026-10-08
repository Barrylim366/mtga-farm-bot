"""Puts Forge's AI in front of mtga-farm-bot's own decision code, without editing the bot.

`install()` swaps two things inside the already-imported bot modules:

  * `AI.DummyAI.DummyAI` becomes a subclass whose `generate_move` asks Forge first, and
  * `AI.Utilities.CombatLogic.choose_blocks` is wrapped the same way.

The bot's original answer is always computed and is what gets used whenever Forge has no answer:
the advisor is still starting, timed out, crashed, or picked something the bot cannot carry out.
Forge can therefore only ever replace a move with another move Arena is offering.

Settings (environment variables, read on every decision):

  MTGA_FORGE           on (default) | shadow (log what Forge would do, play the bot's move) | off
  MTGA_FORGE_BLOCKS    on (default) | off
  MTGA_FORGE_ATTACK    select (default: attack with the creatures Forge picks) | shadow (log only, attack
                       with everything) | gate (all or nothing, decided before combat)
  MTGA_FORGE_INSTANTS  on (default: Forge may cast instants and flash cards outside our main phases) | off
  MTGA_FORGE_FALLBACK  bot (default: no Forge play -> the bot's own choice) | pass (-> pass priority)
  MTGA_FORGE_TIMEOUT   seconds the advisor gets per decision (default 4)
  MTGA_PLAYER_LOG      path to Player.log, when it is not in the default place
"""
from __future__ import annotations

import collections
import itertools
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from forge_bridge import snapshot
from forge_bridge.carddb import CardDb, default_log_path
from forge_bridge.client import ADVISOR_DIR, AdvisorClient
from forge_bridge.tracker import ArenaTracker, LogFollower

DECISION_LOG = ADVISOR_DIR / "run" / "decisions.jsonl"
_RESTART_COOLDOWN_SEC = 60.0


# What the moves window shows: (sequence number, heading, explanation), newest last. Appended from the
# bot's decision threads and read from the UI thread, which a deque allows without a lock.
FEED: "collections.deque[tuple[int, str, str]]" = collections.deque(maxlen=400)
_feed_count = itertools.count(1)


def publish(heading: str, explanation: str = "") -> None:
    FEED.append((next(_feed_count), heading, explanation))


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip().lower()


class Config:
    def __init__(self) -> None:
        self.mode = _env("MTGA_FORGE", "on")
        self.blocks = _env("MTGA_FORGE_BLOCKS", "on") not in ("off", "0", "false", "no")
        self.attack = _env("MTGA_FORGE_ATTACK", "select")
        self.instants = _env("MTGA_FORGE_INSTANTS", "on") not in ("off", "0", "false", "no")
        self.fallback = _env("MTGA_FORGE_FALLBACK", "bot")
        try:
            self.timeout = float(os.environ.get("MTGA_FORGE_TIMEOUT") or 4.0)
        except ValueError:
            self.timeout = 4.0

    @property
    def off(self) -> bool:
        return self.mode in ("off", "0", "false", "no")

    @property
    def acting(self) -> bool:
        return self.mode in ("on", "1", "true", "yes")


def _log(message: str) -> None:
    """Into the bot's own log when running inside the bot, so Forge's choices sit next to the bot's."""
    try:
        import bot_logger  # type: ignore

        bot_logger.log_info(f"FORGE {message}")
    except Exception:
        print(f"[forge] {message}")


class FilteredState:
    """The bot's game state with every cast/play action but one hidden.

    Handing this to the bot's own `generate_move` asks it one question: "would you cast exactly this
    card right now?" That reuses all of its safety rules (cards whose prompts it cannot click, a second
    copy of a legend, removal with no target, whether it can pay) without copying any of them here.
    """

    def __init__(self, inner: Any, instance_id: int, as_own_main_for: Optional[int] = None) -> None:
        self._inner = inner
        self._instance_id = instance_id
        # The bot's AI only ever casts in its own main phase. Outside it (the opponent's turn, a combat
        # step) the same question is asked as if it were, which keeps every one of its checks in play;
        # Arena only offers cards that can legally be cast right now, so timing needs no second opinion.
        self._as_own_main_for = as_own_main_for

    def get_turn_info(self) -> Any:
        turn_info = self._inner.get_turn_info()
        if self._as_own_main_for is None or not turn_info:
            return turn_info
        seat = self._as_own_main_for
        pretend = {k: v for k, v in turn_info.items() if k != "step"}
        pretend.update(activePlayer=seat, decisionPlayer=seat, priorityPlayer=seat, phase="Phase_Main1")
        return pretend

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def get_actions(self) -> list:
        kept = []
        for wrapper in self._inner.get_actions() or []:
            action = wrapper.get("action") or {}
            kind = action.get("actionType")
            if kind == "ActionType_Activate_Mana":
                kept.append(wrapper)
            elif kind in ("ActionType_Cast", "ActionType_Play") and action.get("instanceId") == self._instance_id:
                kept.append(wrapper)
        return kept


class Bridge:
    """The advisor process plus our own reading of Player.log, shared by every hook."""

    _instance: Optional["Bridge"] = None

    def __init__(
        self,
        client: Optional[AdvisorClient] = None,
        cards: Optional[CardDb] = None,
        tracker: Optional[ArenaTracker] = None,
        follower: Optional[LogFollower] = None,
    ) -> None:
        self.lock = threading.RLock()
        self.client = client
        self.cards = cards
        self.tracker = tracker or ArenaTracker()
        self.follower = follower
        self.ready = client is not None and cards is not None
        self._starting = False
        self._last_start = 0.0
        self._asked_attack: Optional[tuple] = None
        # the attack worked out for the current turn: {"key", "legal", "decision", "attackers", "why"}
        self.attack_plan: Optional[dict] = None

    @classmethod
    def instance(cls) -> "Bridge":
        if cls._instance is None:
            cls._instance = Bridge()
        return cls._instance

    # ------------------------------------------------------------------ lifecycle
    def start_async(self) -> None:
        """Start the advisor without blocking the caller; loading Forge's cards takes about ten seconds."""
        with self.lock:
            if self._starting or (time.time() - self._last_start) < _RESTART_COOLDOWN_SEC:
                return
            self._starting = True
            self._last_start = time.time()
        threading.Thread(target=self._start, name="forge-advisor-start", daemon=True).start()

    def _start(self) -> None:
        try:
            cards = self.cards or CardDb.load()
            client = self.client or AdvisorClient()
            info = client.start()
            log_path = os.environ.get("MTGA_PLAYER_LOG") or str(default_log_path())
            follower = LogFollower(log_path, self.tracker)
            follower.poll()
            with self.lock:
                self.cards, self.client, self.follower = cards, client, follower
                self.ready = True
            _log(f"advisor ready: {info.get('cards')} Forge cards, {len(cards)} Arena cards, {info.get('startupMs')} ms")
        except Exception as exc:
            _log(f"advisor failed to start ({exc}); the bot's own decisions are used")
        finally:
            self._starting = False

    def _usable(self) -> bool:
        if not self.ready or self.client is None:
            return False
        if not self.client.running:
            # crashed or was killed after a hang: bring it back for a later decision
            self.ready = False
            self.start_async()
            return False
        return True

    def sync(self) -> None:
        if self.follower is not None:
            self.follower.poll()

    def _decide(self, kind: str, state: dict, config: Config, **extra: Any) -> dict:
        assert self.client is not None
        return self.client.decide(kind, state, timeout=config.timeout, **extra)

    def _record(self, entry: dict) -> None:
        try:
            entry["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(DECISION_LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _name(self, instance_id: Optional[int]) -> str:
        obj = self.tracker.objects.get(instance_id) if instance_id is not None else None
        name = self.cards.name(obj.get("grpId")) if (obj and self.cards) else None
        return f"{name or '?'}#{instance_id}"

    # ------------------------------------------------------------------ main phase
    def choose_move(
        self,
        base: dict,
        bot_generate: Callable[[Any, Any], dict],
        game_state: Any,
        ids: Any,
        config: Config,
    ) -> dict:
        """The move to make: Forge's when it has one the bot can carry out, otherwise `base`."""
        turn_info = game_state.get_turn_info() or {}
        me = turn_info.get("decisionPlayer")
        phase = turn_info.get("phase")
        move_name = next(iter(base), None)
        own_main = bool(me) and turn_info.get("activePlayer") == me and phase in ("Phase_Main1", "Phase_Main2")
        if not own_main:
            return self._choose_instant(base, bot_generate, game_state, ids, config)
        if turn_info.get("priorityPlayer") not in (None, 0, me) or move_name not in ("cast", "resolve"):
            # targets, activations and the attack itself stay with the bot's own flows
            return base

        offered: dict[int, str] = {}
        for wrapper in game_state.get_actions() or []:
            action = wrapper.get("action") or {}
            if action.get("actionType") in ("ActionType_Cast", "ActionType_Play") and "instanceId" in action:
                offered[action["instanceId"]] = action["actionType"]

        with self.lock:
            if not self._usable():
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
                # the log has moved on from the state the bot is deciding on (or something is on the stack)
                return base
            state = snapshot.build_state(tracker, self.cards)  # type: ignore[arg-type]
            if state is None:
                return base
            request = snapshot.main_request(tracker)
            # both views must agree the action exists: ours for Forge, the bot's for carrying it out
            request["castable"] = [i for i in request["castable"] if offered.get(i) == "ActionType_Cast"]
            request["playable"] = [i for i in request["playable"] if offered.get(i) == "ActionType_Play"]

            decision: Optional[dict] = None
            diag: dict = {}
            if request["castable"] or request["playable"]:
                reply = self._decide("main", state, config, **request)
                decision, diag = reply["decision"], reply.get("diag") or {}
                if decision.get("action") == "none" and state["phase"] == "MAIN1":
                    # Forge's AI keeps most permanents for the second main phase. Arena does not stop
                    # there unless an attack happened, so anything held would simply never be cast.
                    later = self._decide("main", dict(state, phase="MAIN2"), config, **request)["decision"]
                    if later.get("action") != "none":
                        decision = dict(later, heldForAfterCombat=True)

            final = base
            note = ""
            if decision is None:
                note = "nothing to choose from"
            elif decision.get("action") in ("play", "cast"):
                instance_id = decision["id"]
                wanted = {"cast": [instance_id]}
                if base == wanted or bot_generate(FilteredState(game_state, instance_id), ids) == wanted:
                    final = wanted
                else:
                    note = "bot would not cast that card; keeping the bot's move"
            elif config.fallback == "pass":
                final = {"resolve": []}
                note = "no play"
            else:
                note = "no play; bot decides"

            if (
                config.attack == "gate"
                and phase == "Phase_Main1"
                and next(iter(final), None) == "resolve"
            ):
                final, attack_note = self._gate_attack(final, state, config)
                note = (note + "; " + attack_note).strip("; ")

            forge_text = "none"
            if decision and decision.get("action") != "none":
                forge_text = f"{decision['action']} {decision.get('card')}"
                targets = (decision.get("targets") or {}).get("names")
                if targets:
                    forge_text += " -> " + ", ".join(targets)
            _log(
                "main turn={} {}: forge={} bot={} -> {}{}".format(
                    turn_info.get("turnNumber"), state["phase"], forge_text, base,
                    final if config.acting else f"{base} (shadow)", f" ({note})" if note else "",
                )
            )
            self._publish_main(turn_info, state, decision, base, final, note, config)
            self._record({
                "kind": "main", "turn": turn_info.get("turnNumber"), "phase": state["phase"],
                "forge": decision, "bot": base, "final": final if config.acting else base,
                "note": note, "diag": {k: v for k, v in diag.items() if v},
            })
            return final if config.acting else base

    def _choose_instant(self, base, bot_generate, game_state, ids, config) -> dict:
        """Outside our main phases: cast an instant or flash card if Forge would, where the bot would only pass.

        Covers the opponent's turn and our own combat and end steps, with nothing on the stack. A
        spell on the stack is left to the bot (its counterspell logic): the stack is not rebuilt in Forge.
        """
        if not config.instants or next(iter(base), None) != "resolve":
            return base
        offered = set()
        for wrapper in game_state.get_actions() or []:
            action = wrapper.get("action") or {}
            if action.get("actionType") == "ActionType_Cast" and "instanceId" in action:
                offered.add(action["instanceId"])
        if not offered:
            return base
        turn_info = game_state.get_turn_info() or {}
        with self.lock:
            if not self._usable():
                return base
            self.sync()
            tracker = self.tracker
            me = tracker.my_seat
            ours = tracker.turn_info or {}
            if (
                not tracker.in_sync
                or me is None
                or turn_info.get("priorityPlayer") not in (None, 0, me)
                or ours.get("priorityPlayer") not in (None, me)
                or ours.get("turnNumber") != turn_info.get("turnNumber")
                or ours.get("phase") != turn_info.get("phase")
                or not tracker.stack_is_empty()
            ):
                return base
            phase = snapshot.forge_phase(ours)
            if phase is None or phase.startswith("MAIN"):
                return base
            state = snapshot.build_state(tracker, self.cards)  # type: ignore[arg-type]
            if state is None:
                return base
            castable = [i for i in snapshot.main_request(tracker)["castable"] if i in offered]
            if not castable:
                return base
            reply = self._decide(
                "main", state, config, castable=castable, playable=[], combat=snapshot.combat_request(tracker)
            )
            decision = reply["decision"]
            wants_cast = decision.get("action") == "cast"
            final, note = base, ""
            if wants_cast:
                wanted = {"cast": [decision["id"]]}
                if bot_generate(FilteredState(game_state, decision["id"], as_own_main_for=me), ids) == wanted:
                    final = wanted
                else:
                    note = "bot would not cast that card"
            if not wants_cast:
                return base
            played = final if config.acting else base
            whose = "our" if ours.get("activePlayer") == me else "the opponent's"
            step = phase.replace("_", " ").lower()
            targets = (decision.get("targets") or {}).get("names") or []
            _log("instant turn={} {} {}: forge=cast {}{} bot={} -> {}{}".format(
                turn_info.get("turnNumber"), whose, step, decision.get("card"),
                " -> " + ", ".join(targets) if targets else "", base,
                final if config.acting else f"{base} (shadow)", f" ({note})" if note else "",
            ))
            if not config.acting:
                why = [f"Shadow mode: the bot passed. Forge would have cast {decision.get('card')} during {whose} {step}."]
            elif final == base:
                why = [f"Forge wanted to cast {decision.get('card')} during {whose} {step}, "
                       "but the bot could not carry that out, so it passed."]
            else:
                why = [f"Cast at instant speed, during {whose} {step}. Chosen by Forge's AI."]
            if targets:
                why.append("Forge's intended target: " + ", ".join(targets) + " (the bot picks the actual target).")
            publish(
                "Turn {} · {} — {}".format(
                    turn_info.get("turnNumber"), (whose + " " + step).capitalize(), self._move_text(played)
                ),
                "\n".join(why),
            )
            self._record({
                "kind": "instant", "turn": turn_info.get("turnNumber"), "phase": phase,
                "forge": decision, "bot": base, "final": played, "note": note,
            })
            return played

    def _move_text(self, move: dict) -> str:
        kind = next(iter(move), "")
        if kind == "cast" and move[kind]:
            obj = self.tracker.objects.get(move[kind][0]) or {}
            name = (self.cards.name(obj.get("grpId")) if self.cards else None) or f"card {move[kind][0]}"
            is_land = "CardType_Land" in (obj.get("cardTypes") or [])
            return ("Play " if is_land else "Cast ") + name
        return {"resolve": "Pass", "auto_pass": "Pass the turn without attacking"}.get(kind, kind or "?")

    def _publish_main(self, turn_info, state, decision, base, final, note, config) -> None:
        played = final if config.acting else base
        heading = "Turn {} · {} — {}".format(
            turn_info.get("turnNumber"), "Main 1" if state["phase"] == "MAIN1" else "Main 2", self._move_text(played)
        )
        why = []
        forge_play = decision is not None and decision.get("action") in ("play", "cast")
        forge_text = ""
        if forge_play:
            forge_text = ("Play " if decision["action"] == "play" else "Cast ") + str(decision.get("card"))
        if not config.acting:
            why.append("Shadow mode: this is the bot's own choice.")
            why.append(f"Forge would have chosen: {forge_text or 'nothing'}.")
        elif forge_play and played == {"cast": [decision["id"]]}:
            why.append("Chosen by Forge's AI from the plays Arena offered.")
            if self._move_text(base) != self._move_text(played):
                why.append(f"The bot alone would have gone with: {self._move_text(base)}.")
        elif forge_play:
            why.append(f"Forge wanted {forge_text}, but the bot could not carry that out, so the bot's own choice was used.")
        elif decision is None:
            why.append("Nothing for Forge to choose between; the bot's own choice.")
        else:
            why.append("Forge found no play worth making here; " + (
                "passing." if config.fallback == "pass" else "the bot's own choice was used."))
        if forge_play:
            targets = (decision.get("targets") or {}).get("names")
            if targets:
                why.append("Forge's intended target: " + ", ".join(targets) + " (the bot picks the actual target).")
            if decision.get("heldForAfterCombat"):
                why.append("Forge would normally hold this until after combat; cast now so it is not lost.")
        skipped = (decision or {}).get("passedOver") or []
        if skipped:
            why.append("Forge's first idea was not possible here: " + "; ".join(
                f"{s.get('card')} ({'ability' if s.get('kind') == 'activate' else s.get('kind')})" for s in skipped[:3]) + ".")
        if "skipping attack" in note or "attacking (" in note:
            why.append("Attack check: " + note.split("; ")[-1] + ".")
        publish(heading, "\n".join(why))

    def _gate_attack(self, final: dict, state: dict, config: Config) -> tuple[dict, str]:
        """In the first main phase with nothing left to cast: attack as usual, or pass the turn instead?

        The bot can attack with everything or not at all, so the two are played out and compared.
        Passing the turn is the only way it has of not attacking.
        """
        try:
            decision = self._decide("attack", state, config, evaluate=True)["decision"]
        except Exception as exc:
            return final, f"attack check failed: {exc}"
        scores = decision.get("scores") or {}
        if not decision.get("candidates") or "all" not in scores or "none" not in scores:
            return final, ""
        if scores["none"] > scores["all"]:
            return {"auto_pass": []}, f"skipping attack (none {scores['none']} > all {scores['all']})"
        return final, f"attacking (all {scores['all']} >= none {scores['none']})"

    # ------------------------------------------------------------------ combat
    def choose_blocks(self, legal_blockers: list, attacking_ids: Optional[set], config: Config) -> Optional[list]:
        """Forge's blocks as (blocker, attacker) pairs, or None when it has no answer."""
        with self.lock:
            if not self._usable():
                return None
            self.sync()
            tracker = self.tracker
            if not tracker.in_sync or (tracker.turn_info or {}).get("activePlayer") == tracker.my_seat:
                return None
            state = snapshot.build_state(tracker, self.cards)  # type: ignore[arg-type]
            if state is None:
                return None
            request = snapshot.block_request(tracker)
            if not request["attackers"] and attacking_ids:
                request["attackers"] = [{"id": i} for i in sorted(attacking_ids)]
            # the prompt the bot is answering is the authority on what may block what
            request["legalBlocks"] = {
                str(entry["blockerInstanceId"]): list(entry.get("attackerInstanceIds") or [])
                for entry in legal_blockers or []
                if isinstance(entry, dict) and "blockerInstanceId" in entry
            }
            if not request["attackers"]:
                return None
            reply = self._decide("block", state, config, **request)
            decision = reply["decision"]
            pairs = [(b["blocker"], b["attacker"]) for b in decision.get("blocks") or []]
            self._record({
                "kind": "block", "turn": (tracker.turn_info or {}).get("turnNumber"),
                "forge": decision, "diag": {k: v for k, v in (reply.get("diag") or {}).items() if v},
            })
            _log("blocks: {}".format("; ".join(decision.get("names") or []) or "none"))
            names = decision.get("names") or []
            attackers = ", ".join(self._name(a["id"]).split("#")[0] for a in request["attackers"])
            explanation = [f"Attacking: {attackers}." if attackers else ""]
            explanation.append(
                "Chosen by Forge's blocking AI, limited to the blocks Arena allows."
                if config.acting else "Shadow mode: Forge's suggestion only; the bot used its own blocks."
            )
            if not names:
                explanation.append("It judged that no block was worth making (no good trade, and the damage is survivable).")
            publish(
                "Turn {} · Block — {}".format(
                    (tracker.turn_info or {}).get("turnNumber"), "; ".join(names) if names else "No blocks"
                ),
                "\n".join(line for line in explanation if line),
            )
            return pairs

    def plan_attack(self, config: Config) -> Optional[dict]:
        """The attack for this turn, worked out once: which of the legal attackers to send.

        Forge's AI names its attackers; that choice, attacking with everything, and not attacking are
        each played out through the opponent's next turn, and the best-scoring one is the plan. Forge's
        own pick wins a tie.
        """
        with self.lock:
            if not self._usable():
                return None
            self.sync()
            tracker = self.tracker
            key = (tracker.game_info.get("matchID"), (tracker.turn_info or {}).get("turnNumber"))
            if self.attack_plan is not None and self.attack_plan.get("key") == key:
                return self.attack_plan
            legal = snapshot.attack_request(tracker)["legalAttackers"]
            if not legal:
                return None
            self.shadow_attack(legal, config)
            return self.attack_plan if (self.attack_plan or {}).get("key") == key else None

    def shadow_attack(self, legal_attackers: list, config: Config) -> None:
        """Work out and log Forge's attack for this turn. Does not click anything."""
        with self.lock:
            if not self._usable():
                return
            self.sync()
            tracker = self.tracker
            key = (tracker.game_info.get("matchID"), (tracker.turn_info or {}).get("turnNumber"))
            if key == self._asked_attack or not tracker.in_sync:
                return
            self._asked_attack = key
            state = snapshot.build_state(tracker, self.cards)  # type: ignore[arg-type]
            if state is None:
                return
            legal = []
            for entry in legal_attackers or []:
                attacker = entry.get("attackerInstanceId") if isinstance(entry, dict) else entry
                if attacker is not None:
                    legal.append(attacker)
            if not legal:
                return
            reply = self._decide("attack", state, config, legalAttackers=legal, evaluate=True)
            decision = reply["decision"]
            _log(
                "attack turn={}: forge would attack with [{}] of {} legal; playout scores {}".format(
                    key[1], ", ".join(decision.get("names") or []) or "nobody", len(legal),
                    json.dumps(decision.get("scores") or {}),
                )
            )
            self._record({"kind": "attack", "turn": key[1], "forge": decision, "legal": legal})
            scores = decision.get("scores") or {}
            picked = [a["id"] for a in decision.get("attackers") or []]
            options = {"pick": picked, "all": list(legal), "none": []}
            best = "pick"
            for name in ("all", "none"):
                if name in scores and "pick" in scores and scores[name] > scores[best]:
                    best = name
            self.attack_plan = {
                "key": key, "legal": list(legal), "decision": decision, "attackers": options[best],
                "choice": best, "names": {a["id"]: n for a, n in zip(decision.get("attackers") or [], decision.get("names") or [])},
            }
            if config.acting and config.attack == "select":
                # the attack hook reports what was actually declared
                return
            explanation = [
                "The bot attacks with everything in this mode.",
                "Forge would attack with: {}.".format(", ".join(decision.get("names") or []) or "nobody"),
            ]
            if "all" in scores and "none" in scores:
                if abs(scores["all"]) > 10**9 or abs(scores["none"]) > 10**9:
                    def outcome(value):
                        return "a win" if value > 10**9 else "a loss" if value < -10**9 else f"a board worth {value}"
                    explanation.append(
                        "Played out through the opponent's next turn: attacking with everything leads to "
                        f"{outcome(scores['all'])}, holding back to {outcome(scores['none'])}."
                    )
                else:
                    better = "attacking" if scores["all"] >= scores["none"] else "holding back"
                    explanation.append(
                        f"Played out through the opponent's next turn: all-in scores {scores['all']}, "
                        f"holding back {scores['none']} (higher is better for us), so {better} looks better."
                    )
            publish(f"Turn {key[1]} · Attack — All creatures attack", "\n".join(explanation))


def make_forge_ai_class(base_class: type, bridge: Optional[Bridge] = None) -> type:
    """A subclass of the bot's AI that consults Forge and falls back to the bot on any problem."""

    class ForgeAI(base_class):  # type: ignore[misc, valid-type]
        def generate_move(self, game_state, inst_id_grp_id_dict):
            base = base_class.generate_move(self, game_state, inst_id_grp_id_dict)
            config = Config()
            if config.off:
                return base
            try:
                return (bridge or Bridge.instance()).choose_move(
                    base,
                    lambda state, ids: base_class.generate_move(self, state, ids),
                    game_state,
                    inst_id_grp_id_dict,
                    config,
                )
            except Exception as exc:
                _log(f"main decision failed ({exc}); using the bot's move")
                return base

    return ForgeAI


def wrap_choose_blocks(original: Callable[..., dict], bridge: Optional[Bridge] = None) -> Callable[..., dict]:
    def choose_blocks(legal_blockers, game_objects, my_seat, my_life, live_instance_ids=None, attacking_instance_ids=None):
        base = original(
            legal_blockers, game_objects, my_seat, my_life,
            live_instance_ids=live_instance_ids, attacking_instance_ids=attacking_instance_ids,
        )
        config = Config()
        if config.off or not config.blocks:
            return base
        try:
            pairs = (bridge or Bridge.instance()).choose_blocks(legal_blockers, attacking_instance_ids, config)
        except Exception as exc:
            _log(f"block decision failed ({exc}); using the bot's blocks")
            return base
        if pairs is None or not config.acting:
            return base
        result = dict(base)
        result["bot_assignments"] = base.get("assignments")
        result["assignments"] = pairs
        result["detail"] = [
            {"blocker": blocker, "attacker": attacker, "outcome": "forge", "pass": "forge"}
            for blocker, attacker in pairs
        ]
        result["reason"] = "forge"
        return result

    return choose_blocks


def wrap_choose_attackers(original: Callable[..., dict], bridge: Optional[Bridge] = None) -> Callable[..., dict]:
    def choose_attackers(legal_attackers, *args, **kwargs):
        config = Config()
        if not config.off:
            # in the background: this runs inside the bot's declare-attackers handler, whose timing must not change
            threading.Thread(
                target=_quietly, args=((bridge or Bridge.instance()).shadow_attack, legal_attackers, config),
                name="forge-attack-shadow", daemon=True,
            ).start()
        return original(legal_attackers, *args, **kwargs)

    return choose_attackers


def _quietly(function: Callable, *args: Any) -> None:
    try:
        function(*args)
    except Exception as exc:
        _log(f"attack shadow failed ({exc})")


def install() -> bool:
    """Hook Forge into the bot. Call with the bot's directory on sys.path, before the bot's entry point runs."""
    import AI.DummyAI as dummy_module  # type: ignore
    import AI.Utilities.CombatLogic as combat_logic  # type: ignore

    if getattr(dummy_module, "_forge_installed", False):
        return True
    dummy_module.DummyAI = make_forge_ai_class(dummy_module.DummyAI)
    combat_logic.choose_blocks = wrap_choose_blocks(combat_logic.choose_blocks)
    combat_logic.choose_attackers = wrap_choose_attackers(combat_logic.choose_attackers)
    dummy_module._forge_installed = True
    Bridge.instance().start_async()
    # separate from the play decisions: reads deck colours off the My Decks grid when picking a deck
    from forge_bridge import deck_hook, pointer

    deck_hook.install()
    # a slower, self-checking cursor sweep for clicking permanents on the battlefield
    pointer.install()
    # Forge's opinion on scry and surveil prompts, shown beside what the bot does
    from forge_bridge import scry_hook

    scry_hook.install()
    # attack with the creatures Forge picks rather than always with everything
    from forge_bridge import attack_hook

    attack_hook.install()
    return True
