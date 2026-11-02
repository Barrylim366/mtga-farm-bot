"""Arena-confirmed decision ownership and progress. No input, timers, or vision."""
from dataclasses import dataclass, field
from copy import deepcopy
import time


REQUESTS = {
    "MulliganReq": "mulligan", "DeclareAttackersReq": "attackers",
    "DeclareBlockersReq": "blockers", "SelectTargetsReq": "targets",
    "SelectNReq": "select_n", "SearchReq": "search", "OrderReq": "order",
    "GroupReq": "group", "PayCostsReq": "pay_costs",
    "CastingTimeOptionsReq": "casting_options", "AssignDamageReq": "assign_damage",
}


def freeze(value):
    if isinstance(value, dict):
        return tuple((k, freeze(v)) for k, v in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


@dataclass
class PendingDecision:
    key: tuple
    kind: str
    evidence: str
    last_progress_at: float
    generation: int
    explicit: bool = False
    high_water: dict = field(default_factory=dict)
    seen: set = field(default_factory=set)
    request: dict | None = None
    assignments: dict = field(default_factory=dict)

    @property
    def token(self):
        return (self.kind, self.key, self.generation, self.last_progress_at)


class DecisionWatchdog:
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.pending = None
        self.match_id = None
        self.scope = None
        self.generation = 0
        self.reason = "idle"
        self._last_state = None
        self._request_state_id = -1
        self._local_timer = None
        self._opponent_request_scope = None

    def clear(self, reason="decision completed"):
        self.pending = None
        self.generation += 1
        self.reason = reason

    def reset(self, reason):
        self.clear(reason)
        self.match_id = None
        self.scope = None
        self._last_state = None
        self._request_state_id = -1
        self._local_timer = None
        self._opponent_request_scope = None

    def _scope(self, match_id, turn):
        scope = (turn.get("turnNumber"), turn.get("phase"), turn.get("step"))
        if match_id != self.match_id:
            self.clear("match changed")
            self.match_id = match_id
            self.scope = None
            self._last_state = None
            self._request_state_id = -1
            self._opponent_request_scope = None
            self._local_timer = None
        if self.scope is not None and scope != self.scope:
            self.clear("turn or phase advanced")
            self._local_timer = None
        self.scope = scope

    def _begin(self, key, kind, evidence, explicit=False):
        key = (self.match_id, self.scope, key)
        if self.pending is None or self.pending.key != key:
            self.generation += 1
            self.pending = PendingDecision(key, kind, evidence, self.clock(),
                                           self.generation, explicit)
            self._last_state = None
            self.reason = "new local decision: " + evidence

    def progress(self, name, count):
        """Only advancing an accepted completion count earns more time."""
        pending = self.pending
        if pending is None or not isinstance(count, int) or count <= 0:
            return
        previous = pending.high_water.get(name, 0)
        if count > previous:
            pending.high_water[name] = count
            self._earned_progress()
            self.reason = "accepted progress: " + str(name)

    def _earned_progress(self):
        self.generation += 1
        self.pending.generation = self.generation
        self.pending.last_progress_at = self.clock()

    def retry(self):
        """A failed concession gets a fresh window, without forgetting milestones."""
        if self.pending is not None:
            self.generation += 1
            self.pending.generation = self.generation
            self.pending.last_progress_at = self.clock()
            self.reason = "concession failed; resumed gameplay"

    def observe_requests(self, payload, match_id, seat, turn):
        if not match_id or seat is None:
            return
        messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
        local_requests = any(seat in (m.get("systemSeatIds") or []) and
                             str(m.get("type", "")).startswith("GREMessageType_") and
                             str(m.get("type", "")).endswith("Req") for m in messages)
        turn = dict(turn or {})
        for msg in messages:
            if msg.get("type") == "GREMessageType_GameStateMessage":
                fresh_turn = (msg.get("gameStateMessage") or {}).get("turnInfo") or {}
                turn.update(fresh_turn)
                if fresh_turn.get("decisionPlayer") == seat:
                    self._opponent_request_scope = None
        self._scope(match_id, turn)
        for msg in messages:
            timer = msg.get("timerStateMessage") or {}
            if timer.get("seatId") == seat:
                self._local_timer = any(t.get("running") and t.get("type") in
                                        ("TimerType_ActivePlayer", "TimerType_NonActivePlayer")
                                        for t in timer.get("timers", []))
                if self._local_timer:
                    self._opponent_request_scope = None
            short = str(msg.get("type", "")).removeprefix("GREMessageType_")
            if not str(msg.get("type", "")).startswith("GREMessageType_") or not short.endswith("Req"):
                continue
            seats = msg.get("systemSeatIds") or []
            if seat not in seats:
                # An explicit request to the opponent retires stale local data.
                if seats and not local_requests:
                    self._scope(match_id, turn)
                    self.clear("request belongs to opponent")
                    self._opponent_request_scope = self.scope
                continue
            self._scope(match_id, turn)
            self._opponent_request_scope = None
            state_id = msg.get("gameStateId")
            if isinstance(state_id, int):
                self._request_state_id = max(self._request_state_id, state_id)
            req = msg.get(short[0].lower() + short[1:]) or {}
            kind = REQUESTS.get(short, short.removesuffix("Req").lower())
            # Recipients, source, and turn establish identity. Message IDs,
            # candidate lists, and prompt sequence numbers may change on retries.
            if short == "ActionsAvailableReq":
                self._begin(("priority",), "priority", "local ActionsAvailableReq")
            else:
                identity = (kind, req.get("sourceId"), req.get("context"))
                self._begin(identity, kind, "local " + short, explicit=True)
                self.pending.request = deepcopy(msg)
            self._selected_counts(req)
        if not local_requests:
            for msg in messages:
                diff = msg.get("gameStateMessage") or {}
                fresh_turn = diff.get("turnInfo") or {}
                if fresh_turn.get("decisionPlayer") not in (None, seat):
                    self.clear("server passed decision to opponent")
                    self._opponent_request_scope = self.scope
        # Read accepted assignments from fresh server objects. Merged objects
        # can retain an old combat's blockInfo/attackInfo and are not proof of a
        # new assignment. Partial diffs accumulate within this decision only.
        pending = self.pending
        if pending is not None and pending.kind in ("blockers", "attackers"):
            for msg in messages:
                for obj in (msg.get("gameStateMessage") or {}).get("gameObjects", []):
                    if obj.get("controllerSeatId") != seat:
                        continue
                    if pending.kind == "blockers":
                        selected = bool((obj.get("blockInfo") or {}).get("attackerIds"))
                    else:
                        selected = (bool((obj.get("attackInfo") or {}).get("targetId")) and
                                    obj.get("attackState") in ("AttackState_Declared", "AttackState_Attacking"))
                    pending.assignments[obj.get("instanceId")] = selected
            self.progress("accepted assignments", sum(pending.assignments.values()))

    def _selected_counts(self, value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.startswith("selected") and key != "selectedDamageRecipient":
                    count = len(item) if isinstance(item, list) else item
                    if type(count) is int and (isinstance(item, list) or key in
                                              ("selectedTargets", "selectedCount", "selectedItems")):
                        self.progress(path + (key,), count)
                elif isinstance(item, (list, dict)):
                    self._selected_counts(item, path + (key,))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                identity = item.get("targetIdx", index) if isinstance(item, dict) else index
                self._selected_counts(item, path + (identity,))

    def observe_state(self, match_id, seat, state):
        if not match_id or seat is None:
            self.clear("no verified local match")
            return None
        turn = state.get("turnInfo") or {}
        # Request callbacks can precede the merge of their bundled state diff.
        state_id = state.get("gameStateId", -1)
        if (match_id == self.match_id and isinstance(state_id, int)
                and state_id < self._request_state_id):
            return self.pending.token if self.pending else None
        self._scope(match_id, turn)
        info = state.get("gameInfo") or {}
        if info.get("stage") == "GameStage_GameOver":
            self.clear("game over")
            return None
        local = next((p for p in state.get("players", [])
                      if p.get("systemSeatNumber") == seat), {})
        pending_type = local.get("pendingMessageType")
        # An explicit pendingMessageType transition is server confirmation that
        # the previous choice has completed. Cached MulliganResp alone is not.
        previous = self._last_state or {}
        previous_type = previous.get("pending_type")
        if (self.pending and self.pending.explicit and previous_type
                and pending_type != previous_type):
            self.clear("server advanced pending response")
        timer_ids = set(local.get("timerIds") or [])
        running = any(t.get("timerId") in timer_ids and t.get("running")
                      and t.get("type") in ("TimerType_ActivePlayer", "TimerType_NonActivePlayer")
                      for t in state.get("timers", []))
        decision_local = turn.get("decisionPlayer") == seat
        opponent_priority = turn.get("priorityPlayer") not in (None, seat)
        stale_mulligan = (pending_type == "ClientMessageType_MulliganResp"
                          and opponent_priority)
        requested_priority = (self.pending is not None and
                              self.pending.evidence == "local ActionsAvailableReq")
        owned = self._opponent_request_scope != self.scope and (running or self._local_timer is True or requested_priority or
                 (not stale_mulligan and not opponent_priority and
                  (decision_local or turn.get("priorityPlayer") == seat)))
        if self.pending is None and owned:
            self._begin(("priority",), "priority", "local decision timer" if running
                        else "local decision player")
        elif self.pending is not None and not self.pending.explicit and not owned:
            self.clear("local responsibility ended")
        snapshot = self._accepted_state(state, seat)
        self._last_state = {"pending_type": pending_type, "snapshot": snapshot}
        pending = self.pending
        if pending is None:
            return None
        if not pending.explicit:
            # Count only accepted board/hand changes relevant to our actions,
            # not available-action lists, clocks, or arbitrary object metadata.
            if snapshot not in pending.seen:
                had_baseline = bool(pending.seen)
                pending.seen.add(snapshot)
                if had_baseline:
                    self._earned_progress()
                    self.reason = "accepted local board/hand change"
        return pending.token

    @staticmethod
    def _accepted_state(state, seat):
        zones = tuple(sorted((z.get("zoneId", -1), tuple(sorted(z.get("objectInstanceIds") or [])))
                             for z in state.get("zones", [])
                             if z.get("ownerSeatId") == seat and z.get("type") in
                             ("ZoneType_Hand", "ZoneType_Battlefield")))
        local_ids = {iid for z in state.get("zones", []) if z.get("ownerSeatId") == seat
                     for iid in (z.get("objectInstanceIds") or [])}
        objects = tuple(sorted((o.get("instanceId", -1), o.get("zoneId"), o.get("isTapped", o.get("tapped")),
                                o.get("damage"), freeze(o.get("power")), freeze(o.get("toughness")))
                               for o in state.get("gameObjects", []) if o.get("controllerSeatId") == seat
                               or ("controllerSeatId" not in o and o.get("instanceId") in local_ids)))
        return zones, objects

    def describe(self):
        p = self.pending
        return {"decision": p.kind if p else None, "evidence": p.evidence if p else None,
                "reason": self.reason, "age_sec": max(0, self.clock() - p.last_progress_at) if p else None}
