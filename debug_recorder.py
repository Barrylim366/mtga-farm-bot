"""Decision-snapshot recorder for post-mortem debugging.

Records one structured snapshot per bot *decision* (not per GRE update) so a
human or an AI agent can reconstruct, after the fact, exactly what board state
the bot saw and which move it chose. Output lands under
``runtime/debug/matches/<utc>_<matchId8>/`` as:

- ``snapshots.jsonl`` -- one JSON record per decision (append-only)
- ``board.txt``       -- the same records rendered human-readable
- ``match.json``      -- per-match header/footer (result, unresolved grpIds)

Design constraints (see the module that drives it, Game.decision_method):

- The capture step runs on the decision thread but only performs a *pruned
  deep copy* of the live GameState -- it must never hold references into the
  live ``game_dict`` because the log-monitor thread mutates it in place.
- All I/O and card-name resolution happen off-thread on a daemon writer, so the
  in-game priority timer never waits on disk or lookups.
- Card names are resolved with ``CardInfo.get_card_info_local`` only (offline,
  never blocking on Scryfall).

Env flags:
- ``MTGA_DEBUG_SNAPSHOTS=0`` disables recording entirely (default: on).
- ``MTGA_DEBUG_FULL_STATE=1`` also stores the full raw get_full_state() dump in
  each record under ``raw_state`` (default: off).
"""
from __future__ import annotations

import copy
import json
import os
import queue
import shutil
import threading
import time
from datetime import datetime, timezone

import bot_logger

try:
    import AI.Utilities.CardInfo as CardInfo
except Exception:  # pragma: no cover - CardInfo should always import
    CardInfo = None

# Keep at most this many match directories around (oldest pruned first).
_MAX_MATCH_DIRS = 30
# Drop records rather than block the decision thread if the writer falls behind.
_QUEUE_MAXSIZE = 500


def _enabled() -> bool:
    return os.environ.get("MTGA_DEBUG_SNAPSHOTS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _full_state_enabled() -> bool:
    return os.environ.get("MTGA_DEBUG_FULL_STATE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _stat(value) -> int:
    """Normalize a GRE stat value ({"value": N} or plain int) to an int."""
    if isinstance(value, dict):
        try:
            return int(value.get("value", 0) or 0)
        except Exception:
            return 0
    try:
        return int(value or 0)
    except Exception:
        return 0


class _Snapshot:
    """Handle returned by capture(); attach_move() completes it."""

    __slots__ = ("record", "match_id")

    def __init__(self, record: dict, match_id):
        self.record = record
        self.match_id = match_id


class DecisionRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._writer: threading.Thread | None = None
        self._started = False
        self._seq = 0
        # Current match context (writer-thread only: rotate/write/finalize).
        self._match_id = None
        self._match_dir = None
        self._match_started = None
        self._unresolved: set[int] = set()
        self._drop_warned = False

    # -- lifecycle ---------------------------------------------------------
    def start_session(self) -> None:
        if not _enabled():
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            self._writer = threading.Thread(
                target=self._run_writer, name="DecisionRecorder", daemon=True
            )
            self._writer.start()

    def _run_writer(self) -> None:
        while True:
            try:
                item = self._queue.get()
            except Exception:
                continue
            if item is None:  # shutdown sentinel (unused today, future-proof)
                return
            try:
                self._handle_item(item)
            except Exception as e:  # never let the writer thread die
                try:
                    bot_logger.log_error(f"DecisionRecorder writer failed: {e}")
                except Exception:
                    pass

    # -- capture / attach (called on the decision thread) ------------------
    def capture(self, game_state, my_seat, match_id, decision_kind, extra=None):
        """Deep-copy the pruned decision-relevant state. Cheap, no I/O.

        Returns a handle (or None if disabled / on error). Pass the handle to
        attach_move() once the chosen move is known.
        """
        if not self._started or game_state is None:
            return None
        try:
            full_state = game_state.get_full_state() or {}
            record = _prune_state(full_state, my_seat)
            record["decision_kind"] = decision_kind
            if extra:
                record["extra"] = copy.deepcopy(extra)
            if _full_state_enabled():
                # get_full_state() is a shallow copy of a dict the log thread
                # mutates in place; a deepcopy here can hit "changed size during
                # iteration". Isolate it so a failure only drops raw_state, not
                # the whole (pruned, race-free) record.
                try:
                    record["raw_state"] = copy.deepcopy(full_state)
                except Exception:
                    record["raw_state"] = None
            return _Snapshot(record, match_id)
        except Exception as e:
            try:
                bot_logger.log_error(f"DecisionRecorder.capture failed: {e}")
            except Exception:
                pass
            return None

    def attach_move(self, handle, move_name, move_data) -> None:
        """Complete a captured snapshot with the chosen move and enqueue it."""
        if handle is None:
            return
        try:
            handle.record["move"] = (
                None if move_name is None else {"name": move_name, "data": copy.deepcopy(move_data)}
            )
            self._enqueue(handle)
        except Exception as e:
            try:
                bot_logger.log_error(f"DecisionRecorder.attach_move failed: {e}")
            except Exception:
                pass

    def record(self, game_state, my_seat, match_id, decision_kind, move_name, move_data, extra=None) -> None:
        """One-shot capture+attach for callsites where the move is already known."""
        handle = self.capture(game_state, my_seat, match_id, decision_kind, extra=extra)
        self.attach_move(handle, move_name, move_data)

    def end_match(self, result=None) -> None:
        if not self._started:
            return
        self._enqueue(("match_end", result))

    def current_seq(self) -> int:
        """Last snapshot sequence number assigned. Used as a join key so a click
        record can be correlated back to the decision that triggered it."""
        with self._lock:
            return self._seq

    # -- internals ---------------------------------------------------------
    def _enqueue(self, item) -> None:
        with self._lock:
            if isinstance(item, _Snapshot):
                self._seq += 1
                item.record["seq"] = self._seq
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            if not self._drop_warned:
                self._drop_warned = True
                try:
                    bot_logger.log_error("DecisionRecorder queue full; dropping snapshot(s).")
                except Exception:
                    pass

    def _handle_item(self, item) -> None:
        if isinstance(item, tuple) and item and item[0] == "match_end":
            self._finalize_match(item[1] if len(item) > 1 else None)
            return
        if isinstance(item, _Snapshot):
            self._write_snapshot(item)

    def _rotate_if_needed(self, match_id) -> None:
        if match_id == self._match_id and self._match_dir:
            return
        # New match: finalize the previous one first (best effort) then open a
        # fresh directory.
        if self._match_dir:
            self._finalize_match(None)
        self._match_id = match_id
        self._unresolved = set()
        self._drop_warned = False
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        self._match_started = stamp
        suffix = (str(match_id)[:8] if match_id else "nomatchid") + f"-{int(time.time())}"
        try:
            base = bot_logger.ensure_debug_dir("matches")
            self._match_dir = os.path.join(base, f"{stamp}_{suffix}")
            os.makedirs(self._match_dir, exist_ok=True)
            self._prune_old_match_dirs(base)
            self._write_match_json({"matchId": match_id, "started": stamp}, "w")
        except Exception as e:
            self._match_dir = None
            try:
                bot_logger.log_error(f"DecisionRecorder could not open match dir: {e}")
            except Exception:
                pass

    def _prune_old_match_dirs(self, base) -> None:
        try:
            entries = [
                os.path.join(base, d)
                for d in os.listdir(base)
                if os.path.isdir(os.path.join(base, d))
            ]
            if len(entries) <= _MAX_MATCH_DIRS:
                return
            entries.sort(key=lambda p: os.path.getmtime(p))
            for old in entries[: len(entries) - _MAX_MATCH_DIRS]:
                shutil.rmtree(old, ignore_errors=True)
        except Exception:
            pass

    def _write_snapshot(self, snap: _Snapshot) -> None:
        self._rotate_if_needed(snap.match_id)
        if not self._match_dir:
            return
        record = snap.record
        _resolve_names(record, self._unresolved)
        try:
            with open(os.path.join(self._match_dir, "snapshots.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            try:
                bot_logger.log_error(f"DecisionRecorder snapshot write failed: {e}")
            except Exception:
                pass
        try:
            with open(os.path.join(self._match_dir, "board.txt"), "a", encoding="utf-8") as f:
                f.write(_render_board(record) + "\n")
        except Exception:
            pass

    def _finalize_match(self, result) -> None:
        if not self._match_dir:
            return
        try:
            self._write_match_json(
                {
                    "matchId": self._match_id,
                    "started": self._match_started,
                    "result": result,
                    "ended": datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S"),
                    "unresolved_grpids": sorted(self._unresolved),
                },
                "w",
            )
        except Exception:
            pass
        self._match_dir = None
        self._match_id = None

    def _write_match_json(self, data: dict, mode: str) -> None:
        if not self._match_dir:
            return
        try:
            with open(os.path.join(self._match_dir, "match.json"), mode, encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# -- pure helpers (no shared state) ---------------------------------------
def _prune_state(full_state: dict, my_seat) -> dict:
    """Extract the decision-relevant subset as a self-contained deep copy."""
    turn_info = full_state.get("turnInfo") or {}
    zones = full_state.get("zones") or []
    objects = full_state.get("gameObjects") or []
    players = full_state.get("players") or []
    actions = full_state.get("actions") or []

    obj_by_id = {}
    for obj in objects:
        if isinstance(obj, dict) and obj.get("instanceId") is not None:
            obj_by_id[obj["instanceId"]] = obj

    def zone_ids(zone_type, owner=None):
        matches = [z for z in zones if isinstance(z, dict) and z.get("type") == zone_type]
        if owner is not None and len(matches) > 1:
            matches = [z for z in matches if z.get("ownerSeatId") == owner]
        ids = []
        for z in matches:
            ids.extend(z.get("objectInstanceIds", []) or [])
        return ids

    def describe_permanent(obj):
        return {
            "iid": obj.get("instanceId"),
            "grpId": obj.get("grpId"),
            "name": None,  # resolved off-thread
            "types": [str(t).replace("CardType_", "") for t in (obj.get("cardTypes") or [])],
            "power": _stat(obj.get("power")),
            "toughness": _stat(obj.get("toughness")),
            "damage": _stat(obj.get("damage")),
            "tapped": bool(obj.get("isTapped")),
            "attacking": obj.get("attackState") == "AttackState_Attacking",
        }

    def describe_card(obj):
        return {
            "iid": obj.get("instanceId"),
            "grpId": obj.get("grpId"),
            "name": None,
            "types": [str(t).replace("CardType_", "") for t in (obj.get("cardTypes") or [])],
        }

    mine, theirs = [], []
    for iid in zone_ids("ZoneType_Battlefield"):
        obj = obj_by_id.get(iid)
        if not obj:
            continue
        (mine if obj.get("controllerSeatId") == my_seat else theirs).append(describe_permanent(obj))

    hand = [describe_card(obj_by_id[i]) for i in zone_ids("ZoneType_Hand", my_seat) if i in obj_by_id]
    stack = []
    for iid in zone_ids("ZoneType_Stack"):
        obj = obj_by_id.get(iid)
        if obj:
            item = describe_card(obj)
            item["controller"] = obj.get("controllerSeatId")
            stack.append(item)

    life = {}
    for p in players:
        seat = p.get("systemSeatNumber")
        if seat is None:
            continue
        life["me" if seat == my_seat else "opp"] = p.get("lifeTotal")

    graveyard = {
        "me": len(zone_ids("ZoneType_Graveyard", my_seat)) if my_seat is not None else None,
    }

    available = []
    for wrapper in actions:
        action = wrapper.get("action", wrapper) if isinstance(wrapper, dict) else {}
        available.append(
            {
                "type": action.get("actionType"),
                "iid": action.get("instanceId"),
                "grpId": action.get("grpId"),
                "name": None,
                "manaCost": copy.deepcopy(action.get("manaCost")),
            }
        )

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "my_seat": my_seat,
        "turn": {
            "turnNumber": turn_info.get("turnNumber"),
            "phase": turn_info.get("phase"),
            "step": turn_info.get("step"),
            "activePlayer": turn_info.get("activePlayer"),
            "priorityPlayer": turn_info.get("priorityPlayer"),
            "decisionPlayer": turn_info.get("decisionPlayer"),
        },
        "life": life,
        "battlefield": {"mine": mine, "theirs": theirs},
        "hand": hand,
        "stack": stack,
        "graveyard_counts": graveyard,
        "available_actions": available,
    }
    if my_seat is None:
        # No local seat yet (very early in a match): mine/theirs split and the
        # hand filter are unreliable, flag it so the reader doesn't trust them.
        record["seat_unknown"] = True
    return record


def _resolve_names(record: dict, unresolved: set) -> None:
    """Fill in card names offline. Records unknown grpIds in `unresolved`."""
    if CardInfo is None:
        return

    def name_for(grp_id):
        if not grp_id:
            return None
        try:
            info = CardInfo.get_card_info_local(int(grp_id))
        except Exception:
            info = None
        if info and info.get("name"):
            return info.get("name")
        try:
            unresolved.add(int(grp_id))
        except Exception:
            pass
        return None

    for section in ("mine", "theirs"):
        for obj in record.get("battlefield", {}).get(section, []):
            obj["name"] = name_for(obj.get("grpId"))
    for obj in record.get("hand", []):
        obj["name"] = name_for(obj.get("grpId"))
    for obj in record.get("stack", []):
        obj["name"] = name_for(obj.get("grpId"))
    for obj in record.get("available_actions", []):
        obj["name"] = name_for(obj.get("grpId"))


def _fmt_permanent(obj: dict) -> str:
    label = obj.get("name") or (f"grp{obj['grpId']}" if obj.get("grpId") else "?")
    types = obj.get("types") or []
    if "Creature" in types:
        pt = f" {obj.get('power')}/{obj.get('toughness')}"
        dmg = f"(dmg{obj['damage']})" if obj.get("damage") else ""
        label = f"{label}{pt}{dmg}"
    if obj.get("tapped"):
        label += "*T"
    if obj.get("attacking"):
        label += "*A"
    return label


def _render_board(record: dict) -> str:
    turn = record.get("turn", {})
    life = record.get("life", {})
    seq = record.get("seq", "?")
    lines = [
        "#{seq} T{t} {phase} | me:{me} opp:{opp} | decision={kind}".format(
            seq=seq,
            t=turn.get("turnNumber"),
            phase=turn.get("phase"),
            me=life.get("me"),
            opp=life.get("opp"),
            kind=record.get("decision_kind"),
        )
    ]
    bf = record.get("battlefield", {})
    lines.append("  ME : " + ", ".join(_fmt_permanent(o) for o in bf.get("mine", [])))
    lines.append("  OPP: " + ", ".join(_fmt_permanent(o) for o in bf.get("theirs", [])))
    hand = record.get("hand", [])
    if hand:
        lines.append("  HAND: " + ", ".join((o.get("name") or f"grp{o.get('grpId')}") for o in hand))
    stack = record.get("stack", [])
    if stack:
        lines.append("  STACK: " + ", ".join((o.get("name") or f"grp{o.get('grpId')}") for o in stack))
    actions = record.get("available_actions", [])
    if actions:
        summary = ", ".join(
            "{t}({n})".format(t=(a.get("type") or "?").replace("ActionType_", ""), n=a.get("name") or a.get("iid"))
            for a in actions[:12]
        )
        lines.append("  ACTIONS: " + summary)
    move = record.get("move")
    if move:
        lines.append("  -> MOVE: {n} {d}".format(n=move.get("name"), d=move.get("data")))
    else:
        lines.append("  -> MOVE: <none>")
    return "\n".join(lines)


# Module-level singleton, mirroring bot_logger's usage style.
_recorder = DecisionRecorder()


def start_session() -> None:
    _recorder.start_session()


def capture(game_state, my_seat, match_id, decision_kind, extra=None):
    return _recorder.capture(game_state, my_seat, match_id, decision_kind, extra=extra)


def attach_move(handle, move_name, move_data) -> None:
    _recorder.attach_move(handle, move_name, move_data)


def record(game_state, my_seat, match_id, decision_kind, move_name, move_data, extra=None) -> None:
    _recorder.record(game_state, my_seat, match_id, decision_kind, move_name, move_data, extra=extra)


def end_match(result=None) -> None:
    _recorder.end_match(result)


def current_seq() -> int:
    return _recorder.current_seq()
