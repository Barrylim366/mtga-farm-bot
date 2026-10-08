"""Runs the bot's AI, with Forge hooked in, over a recorded Player.log -- no game client, no clicks.

    python -m forge_bridge.dryrun [Player.log ...] [--verbose]

For every moment in the log where the bot would have been asked for a main-phase move or for blocks,
this calls the same hooked code the live bot runs (the bot's own AI class, the real advisor process)
and reports which move would have been made and where it came from. It is the closest thing to a
live session that can be checked without one.

The bot's state is rebuilt from the log by this project's tracker, so it is cleaner than what the
bot holds in a real match; the bot's card data comes from its own cache if it has one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Optional
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="*", type=Path)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--online", action="store_true", help="let the bot look unknown cards up on Scryfall, as it does live")
    args = parser.parse_args(argv)

    # keep the dry run's log lines and caches away from a bot that may be running
    os.environ.setdefault("MTGA_RUNTIME_DIR", str(ROOT / "forge_advisor" / "run" / "dryrun-runtime"))
    sys.path.insert(0, str(ROOT))

    from forge_bridge.carddb import find_data_dir

    # The bot refuses to cast a card it has no data for. Live, it exports Arena's card list when it
    # starts; do the same here (its own exporter, reading the client's local database) if that has
    # not happened in this runtime directory yet.
    bot_cards = Path(os.environ["MTGA_RUNTIME_DIR"]) / "cache" / "cards.json"
    data_dir = find_data_dir()
    if not bot_cards.exists() and data_dir is not None:
        import subprocess

        subprocess.run(
            [sys.executable, str(BOT / "tools" / "mtga_cards_export.py"), "--data-dir", str(data_dir)],
            cwd=str(BOT), env=dict(os.environ), stdout=subprocess.DEVNULL, check=False,
        )

    from AI.DummyAI import DummyAI  # type: ignore
    from Controller.Utilities.GameState import GameState  # type: ignore

    from forge_bridge import forge_ai, snapshot
    from forge_bridge.carddb import CardDb, default_log_path
    from forge_bridge.client import AdvisorClient
    from forge_bridge.replay import log_events
    from forge_bridge.tracker import ArenaTracker

    cards = CardDb.load()
    client = AdvisorClient()
    client.start()
    outcomes: Counter = Counter()
    blocks: Counter = Counter()

    def bot_state(tracker: ArenaTracker) -> GameState:
        # Arena leaves decisionPlayer/priorityPlayer out of many states. The bot merges states field by
        # field, so it still holds the last values it saw; while a request to us is pending, that is us.
        turn_info = dict(tracker.turn_info)
        turn_info.setdefault("decisionPlayer", tracker.my_seat)
        turn_info.setdefault("priorityPlayer", tracker.my_seat)
        return GameState({
            "gameStateId": tracker.game_state_id,
            "turnInfo": turn_info,
            "zones": list(tracker.zones.values()),
            "gameObjects": list(tracker.objects.values()),
            "players": list(tracker.players.values()),
            "actions": [{"seatId": tracker.my_seat, "action": a} for a in tracker.my_actions()],
            "annotations": [],
            "timers": [],
        })

    def name(tracker: ArenaTracker, move: dict) -> str:
        kind = next(iter(move), "?")
        if kind == "cast" and move[kind]:
            obj = tracker.objects.get(move[kind][0]) or {}
            return f"cast {cards.name(obj.get('grpId')) or move[kind][0]}"
        return kind

    # the error a real connection failure raises, which the bot's card lookups are written to absorb
    network = mock.patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("offline dry run"))
    if not args.online:
        network.start()
    try:
        for log in args.logs or [default_log_path()]:
            tracker = ArenaTracker()
            bridge = forge_ai.Bridge(client=client, cards=cards, tracker=tracker)
            ai_class = forge_ai.make_forge_ai_class(DummyAI, bridge)
            ai = ai_class()
            plain = DummyAI()
            choose_blocks = forge_ai.wrap_choose_blocks(
                lambda *a, **k: {"assignments": [], "detail": [], "reason": "bot"}, bridge
            )
            for source, item in log_events(log):
                if source != "gre":
                    continue
                for message in tracker.feed_line(item):  # type: ignore[arg-type]
                    kind = message.get("type")
                    turn_info = tracker.turn_info or {}
                    if kind == "GREMessageType_ConnectResp":
                        ai.reset()
                        plain.reset()
                    elif kind == "GREMessageType_ActionsAvailableReq":
                        if turn_info.get("activePlayer") != tracker.my_seat or turn_info.get("phase") not in (
                            "Phase_Main1", "Phase_Main2"
                        ):
                            continue
                        ids = {i: o.get("grpId") for i, o in tracker.objects.items()}
                        bot_move = plain.generate_move(bot_state(tracker), ids)
                        final = ai.generate_move(bot_state(tracker), ids)
                        # by card name: which of two identical lands gets played is not a different move
                        if name(tracker, final) == name(tracker, bot_move):
                            outcomes["same move as the bot alone"] += 1
                        else:
                            outcomes["Forge changed the move"] += 1
                        if args.verbose:
                            mark = "=" if name(tracker, final) == name(tracker, bot_move) else ">"
                            print(
                                f"turn {turn_info.get('turnNumber'):>2} {turn_info.get('phase')[6:]:<5} {mark} "
                                f"{name(tracker, final):<34} (bot alone: {name(tracker, bot_move)})"
                            )
                    elif kind == "GREMessageType_DeclareBlockersReq":
                        request = snapshot.block_request(tracker)
                        legal = [
                            {"blockerInstanceId": int(b), "attackerInstanceIds": a}
                            for b, a in request["legalBlocks"].items()
                        ]
                        result = choose_blocks(
                            legal, [], tracker.my_seat, 20,
                            attacking_instance_ids={a["id"] for a in request["attackers"]},
                        )
                        blocks["from Forge" if result.get("reason") == "forge" else "from the bot"] += 1
                        if args.verbose:
                            print(f"turn {turn_info.get('turnNumber'):>2} block   {result.get('assignments')} ({result.get('reason')})")
    finally:
        if not args.online:
            network.stop()
        client.close()

    print(json.dumps({"mainPhaseMoves": dict(outcomes), "blockDecisions": dict(blocks)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
