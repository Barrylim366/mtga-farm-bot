"""Replays a Player.log through the Forge advisor and compares its choices with what was actually played.

    python -m forge_bridge.replay                      # the current Player.log
    python -m forge_bridge.replay path/to/Player.log --verbose

Nothing here touches the game client: it only reads a log that already exists. It is the way to see
how much of a real match Forge understands (cards it knows, positions it loads cleanly) and what it
would have done at each decision.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Iterator, Optional

from forge_bridge import snapshot
from forge_bridge.carddb import CardDb, default_log_path
from forge_bridge.client import AdvisorClient, AdvisorError
from forge_bridge.tracker import ArenaTracker

_ATTACKING = ("AttackState_Attacking", "AttackState_Declared")
_BLOCKING = ("BlockState_Blocking", "BlockState_Declared")


def log_events(path: Path) -> Iterator[tuple[str, object]]:
    """('gre', line) for each server line and ('client', payload) for each message our client sent.

    Server messages are one JSON object per line; the client's own are pretty-printed over many lines.
    """
    block: Optional[list[str]] = None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if block is not None:
                block.append(line)
                if line.rstrip("\r\n") == "}":
                    try:
                        payload = json.loads("".join(block)).get("payload")
                    except ValueError:
                        payload = None
                    block = None
                    if isinstance(payload, dict):
                        yield "client", payload
                continue
            if line.rstrip("\r\n") == "{":
                block = [line]
            elif "greToClientEvent" in line:
                yield "gre", line


class Replay:
    def __init__(self, client: AdvisorClient, cards: CardDb, verbose: bool = False, timeout: float = 15.0):
        self.client = client
        self.cards = cards
        self.verbose = verbose
        self.timeout = timeout
        self.tracker = ArenaTracker()
        self.records: list[dict] = []
        self.open: dict[str, dict] = {}
        self.await_state: Optional[str] = None
        self.game = 0
        self.asked: set[tuple] = set()

    # ------------------------------------------------------------------ naming
    def name(self, instance_id: Optional[int]) -> str:
        obj = self.tracker.objects.get(instance_id) if instance_id is not None else None
        if obj is None:
            return f"#{instance_id}"
        label = self.cards.name(obj.get("grpId")) or f"grp{obj.get('grpId')}"
        power, toughness = snapshot._value(obj.get("power")), snapshot._value(obj.get("toughness"))
        if power is not None and toughness is not None and "CardType_Creature" in (obj.get("cardTypes") or []):
            label += f" {power}/{toughness}"
        return label

    # ------------------------------------------------------------------ asking Forge
    def ask(self, kind: str, extra: dict) -> None:
        state = snapshot.build_state(self.tracker, self.cards)
        if state is None:
            return
        turn = (self.tracker.turn_info or {}).get("turnNumber")
        if kind != "main":
            # the GRE repeats a declare request after every creature we toggle; the decision is the first one
            if (kind, self.game, turn) in self.asked:
                return
            self.asked.add((kind, self.game, turn))
        record = {"kind": kind, "turn": turn, "gameStateId": self.tracker.game_state_id, "phase": state["phase"]}
        started = time.time()
        try:
            reply = self.client.decide(kind, state, timeout=self.timeout, **extra)
            record.update(ok=True, ms=reply.get("ms"), decision=reply["decision"], diag=reply["diag"])
            if kind == "main" and state["phase"] == "MAIN1" and reply["decision"].get("action") == "none":
                # Forge's AI keeps most permanents for after combat; show what it is holding
                later = self.client.decide(kind, dict(state, phase="MAIN2"), timeout=self.timeout, **extra)
                record["afterCombat"] = later["decision"]
        except AdvisorError as exc:
            record.update(ok=False, error=str(exc), ms=int((time.time() - started) * 1000))
            if not self.client.running:
                self.client.start()
        record["request"] = extra
        self.records.append(record)
        self.open[kind] = record

    def on_request(self, message: dict) -> None:
        kind = message.get("type")
        tracker = self.tracker
        turn_info = tracker.turn_info or {}
        if kind == "GREMessageType_DeclareAttackersReq":
            self.ask("attack", dict(snapshot.attack_request(tracker), evaluate=True))
        elif kind == "GREMessageType_DeclareBlockersReq":
            self.ask("block", snapshot.block_request(tracker))
        elif kind == "GREMessageType_ActionsAvailableReq":
            if (
                turn_info.get("activePlayer") == tracker.my_seat
                and turn_info.get("phase") in ("Phase_Main1", "Phase_Main2")
                and tracker.stack_is_empty()
            ):
                self.ask("main", snapshot.main_request(tracker))

    # ------------------------------------------------------------------ what really happened
    def on_client(self, payload: dict) -> None:
        kind = payload.get("type")
        if kind == "ClientMessageType_PerformActionResp":
            record = self.open.pop("main", None)
            if record is None:
                return
            actions = (payload.get("performActionResp") or {}).get("actions") or []
            action = actions[0] if actions else {}
            record["actual"] = {
                "action": {
                    "ActionType_Play": "play",
                    "ActionType_Cast": "cast",
                    "ActionType_Pass": "none",
                }.get(action.get("actionType"), action.get("actionType")),
                "id": action.get("instanceId"),
                "card": self.name(action.get("instanceId")) if action.get("instanceId") else None,
                "name": self.cards.name((self.tracker.objects.get(action.get("instanceId")) or {}).get("grpId")),
            }
            self.finish(record)
        elif kind == "ClientMessageType_SubmitAttackersReq":
            self.await_state = "attack"
        elif kind == "ClientMessageType_SubmitBlockersReq":
            self.await_state = "block"

    def on_state(self) -> None:
        """The first state after a submit shows what was declared."""
        if self.await_state is None:
            return
        kind, self.await_state = self.await_state, None
        record = self.open.pop(kind, None)
        if record is None:
            return
        me = self.tracker.my_seat
        mine = [o for o in self.tracker.zone_objects("ZoneType_Battlefield") if o.get("controllerSeatId") == me]
        if kind == "attack":
            record["actual"] = {"attackers": sorted(o["instanceId"] for o in mine if o.get("attackState") in _ATTACKING)}
        else:
            record["actual"] = {
                "blocks": sorted(
                    (o["instanceId"], attacker)
                    for o in mine if o.get("blockState") in _BLOCKING
                    for attacker in (o.get("blockInfo") or {}).get("attackerIds") or []
                )
            }
        self.finish(record)

    def finish(self, record: dict) -> None:
        kind, decision, actual = record["kind"], record.get("decision"), record.get("actual") or {}
        if not record.get("ok") or decision is None:
            record["agrees"] = None
        elif kind == "attack":
            record["agrees"] = sorted(a["id"] for a in decision["attackers"]) == actual.get("attackers")
        elif kind == "block":
            record["agrees"] = sorted((b["blocker"], b["attacker"]) for b in decision["blocks"]) == actual.get("blocks")
        else:
            # by name: which of two identical Mountains gets played is not a difference
            record["agrees"] = (decision.get("action"), decision.get("card")) == (actual.get("action"), actual.get("name"))
            later = record.get("afterCombat") or {}
            record["agreesAfterCombat"] = (
                (later.get("action"), later.get("card")) == (actual.get("action"), actual.get("name")) if later else None
            )
        if self.verbose:
            self.print_record(record)

    def print_record(self, record: dict) -> None:
        kind, decision, actual = record["kind"], record.get("decision") or {}, record.get("actual") or {}
        head = f"turn {record['turn']:>2} {kind:<6}"
        if not record.get("ok"):
            print(f"{head} FORGE ERROR: {record.get('error')}")
            return
        if kind == "attack":
            forge = ", ".join(decision.get("names") or []) or "no attack"
            real = ", ".join(self.name(i) for i in actual.get("attackers") or []) or "no attack"
        elif kind == "block":
            forge = "; ".join(decision.get("names") or []) or "no blocks"
            real = "; ".join(f"{self.name(b)} blocks {self.name(a)}" for b, a in actual.get("blocks") or []) or "no blocks"
        else:
            forge = f"{decision.get('action')} {decision.get('card') or ''}".strip()
            targets = (decision.get("targets") or {}).get("names")
            if targets:
                forge += " -> " + ", ".join(targets)
            forge = f"[{record['phase']}] {forge}"
            later = record.get("afterCombat") or {}
            if later.get("action") not in (None, "none"):
                forge += f"  (after combat: {later.get('action')} {later.get('card')})"
            real = f"{actual.get('action')} {actual.get('card') or ''}".strip()
        mark = {True: "=", False: "x", None: "?"}[record.get("agrees")]
        print(f"{head} {mark} forge: {forge}\n{'':>15}actual: {real}   ({record.get('ms')} ms)")
        diag = record.get("diag") or {}
        for key in ("dropped", "standIns", "lost", "notes"):
            if diag.get(key):
                print(f"{'':>15}{key}: {json.dumps(diag[key], ensure_ascii=False)}")
        if decision.get("scores"):
            print(f"{'':>15}playout scores: {json.dumps(decision['scores'])}")
        for key in ("notLegalInArena", "missingAttackers", "passedOver", "evaluateError"):
            if decision.get(key):
                print(f"{'':>15}{key}: {json.dumps(decision[key], ensure_ascii=False)}")

    # ------------------------------------------------------------------ driving
    def run(self, path: Path, limit: Optional[int] = None) -> None:
        for source, item in log_events(path):
            if limit is not None and len(self.records) >= limit:
                break
            if source == "client":
                self.on_client(item)  # type: ignore[arg-type]
                continue
            messages = self.tracker.feed_line(item)  # type: ignore[arg-type]
            saw_state = False
            for message in messages:
                kind = message.get("type", "")
                if kind in ("GREMessageType_GameStateMessage", "GREMessageType_QueuedGameStateMessage"):
                    saw_state = True
                    if (message.get("gameStateMessage") or {}).get("type") == "GameStateType_Full":
                        self.game += 1
                        if self.verbose:
                            print("--- new game ---")
            if saw_state:
                self.on_state()
            for message in messages:
                if message.get("type", "").endswith("Req"):
                    self.on_request(message)

    def summary(self) -> dict:
        done = [r for r in self.records if r.get("ok")]
        failed = [r for r in self.records if not r.get("ok")]
        times = [r["ms"] for r in done if isinstance(r.get("ms"), int)]
        by_kind: dict[str, dict] = {}
        for kind in ("main", "attack", "block"):
            rows = [r for r in done if r["kind"] == kind]
            compared = [r for r in rows if r.get("agrees") is not None]
            by_kind[kind] = {
                "decisions": len(rows),
                "compared": len(compared),
                "sameAsPlayed": sum(1 for r in compared if r["agrees"]),
            }
            if kind == "main":
                by_kind[kind]["sameButAfterCombat"] = sum(1 for r in compared if r.get("agreesAfterCombat"))
        unknown = Counter()
        stand_ins = Counter()
        lost = Counter()
        clean = 0
        for r in done:
            diag = r.get("diag") or {}
            if not (diag.get("dropped") or diag.get("standIns") or diag.get("lost")):
                clean += 1
            for d in diag.get("dropped") or []:
                unknown[f"{d.get('name')} ({d.get('zone')})"] += 1
            for d in diag.get("standIns") or []:
                stand_ins[str(d.get("name"))] += 1
            for d in diag.get("lost") or []:
                lost[str(d.get("name"))] += 1
        return {
            "decisions": len(self.records),
            "answered": len(done),
            "failed": len(failed),
            "errors": Counter(str(r.get("error"))[:120] for r in failed).most_common(8),
            "loadedCleanly": clean,
            "ms": {
                "median": int(statistics.median(times)) if times else None,
                "p95": int(sorted(times)[int(len(times) * 0.95) - 1]) if len(times) >= 20 else (max(times) if times else None),
                "max": max(times) if times else None,
            },
            "byKind": by_kind,
            "cardsForgeDoesNotKnow": unknown.most_common(30),
            "standIns": stand_ins.most_common(30),
            "lostOnLoad": lost.most_common(30),
            "stateGaps": self.tracker.gaps,
        }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="*", type=Path, help="Player.log files (default: the client's current one)")
    parser.add_argument("--verbose", "-v", action="store_true", help="print every decision next to what was played")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many decisions")
    parser.add_argument("--json", type=Path, default=None, help="write every decision record to this file")
    args = parser.parse_args(argv)

    logs = args.logs or [default_log_path()]
    cards = CardDb.load()
    client = AdvisorClient()
    info = client.start()
    print(f"advisor ready: {info.get('cards')} Forge cards, {len(cards)} Arena cards", file=sys.stderr)
    replay = Replay(client, cards, verbose=args.verbose)
    try:
        for log in logs:
            if args.verbose:
                print(f"=== {log}")
            replay.tracker = ArenaTracker()
            replay.run(log, args.limit)
    finally:
        client.close()
    print(json.dumps(replay.summary(), indent=2, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(replay.records, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
