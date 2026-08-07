"""Combat logic: which creatures to attack with, and which attackers to block.

Phase 1 is deliberately rule-based rather than a search. The decision runs on
the in-game priority timer (the "rope"), so it must be cheap and must never make
a blocking network call -- card data is read through
`CardInfo.get_card_info_local`, which never falls back to Scryfall.

**Legality comes from MTGA, not from us.** Both callers pass the game's own
request payload:

  * `DeclareAttackersReq.attackers[].attackerInstanceId` is exactly the set of
    creatures that may attack (summoning sickness, tapped, "can't attack" and
    attack costs are already applied).
  * `DeclareBlockersReq.blockers[]` is the full legal blocker->attacker graph
    (`{"blockerInstanceId": 313, "attackerInstanceIds": [287], "maxAttackers": 1}`),
    so flying / menace / "can't be blocked" never have to be reasoned about here.

What phase 1 deliberately does NOT do (documented so the gaps are known rather
than discovered in a match):

  * one blocker per attacker and one attacker per blocker -- multi-blocks pull in
    the AssignDamageReq flow, which is a separate piece of machinery;
  * keywords are the *printed* ones from the local card DB, so a keyword granted
    by another permanent or an aura is invisible here;
  * no reasoning about combat tricks the opponent may be holding.

Every entry point returns a plain dict (decision + the numbers behind it) so the
caller can log it verbatim into the decision snapshots. Nothing here touches the
screen, the log, or the network: it is all pure functions over the game state,
which is what makes it testable offline.
"""

from __future__ import annotations

import AI.Utilities.CardInfo as CardInfo
import AI.Utilities.RemovalLogic as RemovalLogic

# Block outcomes, named from the BLOCKER's point of view.
KILL_AND_SURVIVE = "kill_and_survive"   # blocker kills the attacker and lives
TRADE = "trade"                         # both die
ABSORB = "absorb"                       # neither dies; the damage is stopped
CHUMP = "chump"                         # blocker dies, attacker lives

# Preference order when several blocks are available for the same attacker.
_BLOCK_PREFERENCE = {KILL_AND_SURVIVE: 0, ABSORB: 1, TRADE: 2, CHUMP: 3}

# Double strike also kills before the other creature ever deals damage, so for
# "who dies" purposes it behaves exactly like first strike.
_FIRST_STRIKE_KEYWORDS = {"first strike", "double strike"}

# How much life we insist on keeping when deciding whether to hold creatures
# back on defence. 0 means "surviving at 1 life is acceptable"; the guard only
# fires when the projected counter-attack would actually kill us.
LIFE_BUFFER = 0


# --- Board reading -----------------------------------------------------------
def creature_keywords(creature: dict) -> set[str]:
    """Printed keywords for this creature's card, from the LOCAL card DB only.

    `RemovalLogic._creature_keywords` does the same lookup through
    `CardInfo.get_card_info`, which falls back to a blocking Scryfall request on
    a miss. Combat evaluates every creature on BOTH boards, so one unknown grpId
    there could cost 8s per creature while the rope burns. An empty keyword set
    is a far cheaper failure than a stalled combat.
    """
    grp_id = creature.get("grpId")
    if grp_id is None:
        return set()
    try:
        info = CardInfo.get_card_info_local(grp_id) or {}
    except Exception:
        return set()
    kws = {str(k).lower() for k in (info.get("keywords") or [])}
    oracle = str(info.get("oracleText") or "").lower()
    for kw in ("indestructible", "deathtouch", "first strike", "double strike",
               "trample", "vigilance"):
        if kw in oracle:
            kws.add(kw)
    return kws


def power(creature: dict) -> int:
    return RemovalLogic._stat(creature.get("power"))


def toughness(creature: dict) -> int:
    """Effective toughness: printed toughness minus damage already marked."""
    return RemovalLogic.effective_toughness(creature)


def is_tapped(creature: dict) -> bool:
    return bool(creature.get("isTapped"))


def creature_value(creature: dict) -> int:
    """Rough "how much do we mind losing this" score.

    Mana value dominates (it is the closest thing to card quality we can read
    offline) with the body as a tiebreak. Only ever compared against another
    creature's score, so the absolute scale does not matter.
    """
    try:
        info = CardInfo.get_card_info_local(creature.get("grpId")) or {}
    except Exception:
        info = {}
    cmc = 0
    try:
        cmc = CardInfo.calculate_cmc(info.get("manaCost") or "")
    except Exception:
        cmc = 0
    return cmc * 2 + power(creature) + RemovalLogic._stat(creature.get("toughness"))


def our_creatures(
    game_objects: list[dict],
    my_seat: int,
    live_instance_ids: set[int] | None = None,
) -> list[dict]:
    """Creatures we control that are actually on the battlefield.

    `FightLogic.our_creatures` is the same idea but filters on `zoneId`, which
    goes stale (see `index_by_instance`); this one filters on the zone's own
    membership list instead.
    """
    out = []
    for obj in game_objects or []:
        if not isinstance(obj, dict):
            continue
        if "CardType_Creature" not in (obj.get("cardTypes") or []):
            continue
        if obj.get("controllerSeatId") != my_seat:
            continue
        instance_id = obj.get("instanceId")
        if instance_id is None:
            continue
        if live_instance_ids is not None and instance_id not in live_instance_ids:
            continue
        out.append(obj)
    return out


def index_by_instance(
    game_objects: list[dict],
    live_instance_ids: set[int] | None = None,
) -> dict[int, dict]:
    """instanceId -> object, restricted to what is actually on the battlefield.

    `live_instance_ids` should come from `RemovalLogic.battlefield_instance_ids`.
    GameState merges gameObjects across diffs and only prunes on an explicit
    delete, so without that filter a creature that died turns ago is still
    "on the board" here -- the same class of bug that once had removal chasing a
    17-minute-old ghost.
    """
    out: dict[int, dict] = {}
    for obj in game_objects or []:
        if not isinstance(obj, dict):
            continue
        instance_id = obj.get("instanceId")
        if instance_id is None:
            continue
        if live_instance_ids is not None and instance_id not in live_instance_ids:
            continue
        out[instance_id] = obj
    return out


def my_life_from_players(players: list[dict], my_seat: int) -> int | None:
    """Our own life total. Mirror of RemovalLogic.opponent_life_from_players."""
    for player in players or []:
        if not isinstance(player, dict):
            continue
        if player.get("systemSeatNumber") != my_seat:
            continue
        life = player.get("lifeTotal", player.get("life"))
        if life is None:
            return None
        try:
            return int(life)
        except (TypeError, ValueError):
            return None
    return None


# --- Combat math -------------------------------------------------------------
def _deals_lethal_damage(source: dict, target: dict, source_kws: set[str], target_kws: set[str]) -> bool:
    if "indestructible" in target_kws:
        return False
    src_power = power(source)
    if src_power <= 0:
        return False
    if "deathtouch" in source_kws:
        return True
    return src_power >= toughness(target)


def resolve_combat_pair(attacker: dict, blocker: dict) -> dict:
    """What happens if `blocker` blocks `attacker`, one on one.

    Returns {"outcome", "attacker_dies", "blocker_dies", "damage_prevented"},
    where `outcome` is named from the blocker's point of view.
    """
    a_kws = creature_keywords(attacker)
    b_kws = creature_keywords(blocker)

    blocker_dies = _deals_lethal_damage(attacker, blocker, a_kws, b_kws)
    attacker_dies = _deals_lethal_damage(blocker, attacker, b_kws, a_kws)

    # First strike is not a tiebreak, it is a whole extra damage step: whoever
    # strikes first kills before the other creature deals any damage at all.
    a_first = bool(a_kws & _FIRST_STRIKE_KEYWORDS)
    b_first = bool(b_kws & _FIRST_STRIKE_KEYWORDS)
    if a_first and not b_first and blocker_dies:
        attacker_dies = False
    elif b_first and not a_first and attacker_dies:
        blocker_dies = False

    if "trample" in a_kws:
        # Only the blocker's toughness worth of damage is soaked up; the rest
        # tramples through to us.
        damage_prevented = min(power(attacker), max(0, toughness(blocker)))
    else:
        damage_prevented = max(0, power(attacker))

    if attacker_dies and not blocker_dies:
        outcome = KILL_AND_SURVIVE
    elif attacker_dies and blocker_dies:
        outcome = TRADE
    elif not attacker_dies and not blocker_dies:
        outcome = ABSORB
    else:
        outcome = CHUMP

    return {
        "outcome": outcome,
        "attacker_dies": attacker_dies,
        "blocker_dies": blocker_dies,
        "damage_prevented": damage_prevented,
    }


# --- Blocking ----------------------------------------------------------------
def choose_blocks(
    legal_blockers: list[dict],
    game_objects: list[dict],
    my_seat: int,
    my_life: int | None,
    live_instance_ids: set[int] | None = None,
    attacking_instance_ids: set[int] | None = None,
) -> dict:
    """Pick blocks from MTGA's own legal blocker->attacker graph.

    `legal_blockers` is `declareBlockersReq.blockers` verbatim.

    `attacking_instance_ids` is who is attacking *right now*. It matters because
    the block graph only lists attackers somebody of ours can block: a flier we
    have no answer to appears nowhere in it, so counting incoming damage from
    the graph alone would understate it -- and understating it is exactly the
    case where we fail to chump and die. The caller reads these ids from the
    game-state diffs the GRE bundles into the same message as this request, so
    they are current; `attackState` on the merged board is NOT usable for this,
    because MTGA never clears it and a creature that attacked five turns ago
    still claims to be attacking.

    Two passes, in this order because they answer different questions:

      1. *value* -- take every block that is good on its own terms (kills and
         survives, or soaks damage for free, or is a trade we are happy with),
      2. *survival* -- if the damage still coming through would kill us, keep
         adding blocks (chumping if that is all that is left) until it does not.

    Returns a dict with the assignments and the numbers behind them, ready to be
    logged straight into a decision snapshot.
    """
    board = index_by_instance(game_objects, live_instance_ids)
    assignments: list[tuple[int, int]] = []
    detail: list[dict] = []

    # blocker_id -> set of attacker_ids it may legally block.
    options: dict[int, list[int]] = {}
    for entry in legal_blockers or []:
        if not isinstance(entry, dict):
            continue
        blocker_id = entry.get("blockerInstanceId")
        if blocker_id is None or blocker_id not in board:
            continue
        attacker_ids = [a for a in (entry.get("attackerInstanceIds") or []) if a in board]
        if attacker_ids:
            options[blocker_id] = attacker_ids

    blockable_ids = sorted({a for ids in options.values() for a in ids})

    # Everything attacking us, blockable or not. Anything in here but not in the
    # graph is an attacker we simply cannot stop -- it still hits us, so it
    # counts towards lethal even though no assignment can ever remove it.
    unstoppable_ids = sorted(
        instance_id for instance_id in (attacking_instance_ids or ())
        if instance_id in board
        and instance_id not in blockable_ids
        and board[instance_id].get("controllerSeatId") != my_seat
    )

    # Damage aimed at our face. An attacker pointed at a planeswalker still
    # appears in the block graph but does not threaten our life total.
    def _hits_us(attacker: dict) -> bool:
        info = attacker.get("attackInfo") or {}
        target = info.get("targetId")
        return target is None or target == my_seat

    incoming = sum(
        power(board[a]) for a in blockable_ids + unstoppable_ids if _hits_us(board[a])
    )
    lethal = my_life is not None and incoming >= my_life

    if not blockable_ids:
        return {
            "assignments": [],
            "detail": [],
            "incoming_damage": incoming,
            "unblocked_damage": incoming,
            "my_life": my_life,
            "lethal_without_blocks": lethal,
            "reason": "no legal blocks offered",
        }

    attacker_ids = blockable_ids
    unblocked = set(attacker_ids) | set(unstoppable_ids)
    free_blockers = set(options)

    # Pass 1 -- value blocks. Biggest attacker first: it is both the one that
    # hurts most and the one whose good block is most worth spending.
    for attacker_id in sorted(attacker_ids, key=lambda a: -power(board[a])):
        best = None
        for blocker_id in sorted(free_blockers):
            if attacker_id not in options[blocker_id]:
                continue
            result = resolve_combat_pair(board[attacker_id], board[blocker_id])
            outcome = result["outcome"]
            if outcome == CHUMP:
                continue  # pass 2's business, and only to stay alive
            if outcome == TRADE and not lethal:
                # Only trade up (or evenly): losing a better creature than we
                # kill is a real loss, and unlike a chump block nothing forces
                # it. When the alternative is dying, any trade is fine.
                if creature_value(board[blocker_id]) > creature_value(board[attacker_id]):
                    continue
            if outcome == ABSORB and result["damage_prevented"] <= 0:
                continue  # a 0-power attacker: blocking gains nothing
            candidate = (
                _BLOCK_PREFERENCE[outcome],
                creature_value(board[blocker_id]),  # spend the cheapest body that works
                blocker_id,
            )
            if best is None or candidate < best[0]:
                best = (candidate, blocker_id, result)
        if best is None:
            continue
        _, blocker_id, result = best
        assignments.append((blocker_id, attacker_id))
        detail.append({
            "blocker": blocker_id,
            "attacker": attacker_id,
            "outcome": result["outcome"],
            "pass": "value",
        })
        free_blockers.discard(blocker_id)
        unblocked.discard(attacker_id)

    def _damage_through() -> int:
        return sum(power(board[a]) for a in unblocked if _hits_us(board[a]))

    # Pass 2 -- survival. Only reached when the damage still getting through
    # would kill us; here a creature is worth strictly less than the game.
    if my_life is not None:
        while free_blockers and _damage_through() >= my_life - LIFE_BUFFER:
            best = None
            for attacker_id in sorted(unblocked, key=lambda a: -power(board[a])):
                if not _hits_us(board[attacker_id]):
                    continue
                for blocker_id in sorted(free_blockers):
                    if attacker_id not in options[blocker_id]:
                        continue
                    result = resolve_combat_pair(board[attacker_id], board[blocker_id])
                    candidate = (
                        -result["damage_prevented"],           # stop the most damage
                        creature_value(board[blocker_id]),     # with the cheapest body
                        blocker_id,
                    )
                    if best is None or candidate < best[0]:
                        best = (candidate, blocker_id, attacker_id, result)
            if best is None:
                break  # nothing legal left to add
            _, blocker_id, attacker_id, result = best
            if result["damage_prevented"] <= 0:
                break  # adding this block would not move the life total
            assignments.append((blocker_id, attacker_id))
            detail.append({
                "blocker": blocker_id,
                "attacker": attacker_id,
                "outcome": result["outcome"],
                "pass": "survival",
            })
            free_blockers.discard(blocker_id)
            unblocked.discard(attacker_id)

    remaining = _damage_through()
    if lethal:
        reason = (
            "survived lethal" if my_life is not None and remaining < my_life
            else "lethal incoming, blocked what we could"
        )
    elif assignments:
        reason = "value blocks"
    else:
        reason = "no block worth making"

    return {
        "assignments": assignments,
        "detail": detail,
        "incoming_damage": incoming,
        "unblocked_damage": remaining,
        "my_life": my_life,
        "lethal_without_blocks": lethal,
        "reason": reason,
    }


# --- Attacking ---------------------------------------------------------------
def choose_attackers(
    legal_attackers: list[dict],
    game_objects: list[dict],
    my_seat: int,
    my_life: int | None,
    opp_life: int | None,
    live_instance_ids: set[int] | None = None,
) -> dict:
    """Pick attackers from MTGA's own list of creatures that may attack.

    `legal_attackers` is `declareAttackersReq.attackers` (or `qualifiedAttackers`)
    verbatim.

    Three rules, in order:

      1. **Lethal** -- if enough damage gets through even assuming the opponent
         blocks with everything untapped, swing with the whole team.
      2. **Per-creature safety** -- do not attack with a creature that simply
         dies to an available block, nor make a trade that loses us the better
         creature.
      3. **Life guard** -- attackers stay tapped through the opponent's turn, so
         attacking is also a defensive decision. If the projected counter-attack
         would kill us, hold back the best blockers until it would not.
    """
    board = index_by_instance(game_objects, live_instance_ids)

    candidate_ids: list[int] = []
    for entry in legal_attackers or []:
        if isinstance(entry, dict):
            attacker_id = entry.get("attackerInstanceId")
        else:
            attacker_id = entry
        if attacker_id is not None and attacker_id in board and attacker_id not in candidate_ids:
            candidate_ids.append(attacker_id)

    if not candidate_ids:
        return {
            "attackers": [],
            "hold_back": [],
            "reason": "no legal attackers",
            "lethal": False,
            "projected_counter_attack": 0,
            "projected_life_after": my_life,
        }

    enemies = RemovalLogic.opponent_creatures(
        game_objects, my_seat, None, live_instance_ids
    )
    untapped_enemies = [e for e in enemies if not is_tapped(e)]

    # Rule 1 -- lethal. Assume the worst: every untapped enemy creature blocks,
    # and each one fully absorbs one of our biggest attackers. Whatever is left
    # is damage the opponent cannot stop.
    powers = sorted((power(board[i]) for i in candidate_ids), reverse=True)
    guaranteed = sum(powers[len(untapped_enemies):])
    if opp_life is not None and guaranteed >= opp_life:
        return {
            "attackers": list(candidate_ids),
            "hold_back": [],
            "reason": f"lethal: {guaranteed} unblockable damage vs {opp_life} life",
            "lethal": True,
            "projected_counter_attack": sum(power(e) for e in enemies),
            "projected_life_after": my_life,
        }

    # Rule 2 -- per-creature safety. The opponent picks the block that is best
    # for them, so a creature is only safe if no available blocker beats it.
    attackers: list[int] = []
    hold_back: list[int] = []
    skipped: dict[int, str] = {}
    for attacker_id in candidate_ids:
        attacker = board[attacker_id]
        if power(attacker) <= 0:
            hold_back.append(attacker_id)
            skipped[attacker_id] = "no power"
            continue
        verdict = None
        for blocker in untapped_enemies:
            result = resolve_combat_pair(attacker, blocker)
            if result["outcome"] == KILL_AND_SURVIVE:
                verdict = "dies to a block for nothing"
                break
            if result["outcome"] == TRADE and creature_value(attacker) > creature_value(blocker):
                verdict = "would trade down"
        if verdict:
            hold_back.append(attacker_id)
            skipped[attacker_id] = verdict
        else:
            attackers.append(attacker_id)

    # Rule 3 -- life guard. Everything of ours that is untapped when the
    # opponent's turn starts can block; a non-vigilant attacker is tapped and
    # cannot. (Our permanents untap in OUR untap step, not theirs.)
    ours = our_creatures(game_objects, my_seat, live_instance_ids)
    enemy_powers = sorted((power(e) for e in enemies), reverse=True)
    counter_attack = sum(enemy_powers)

    def _defender_count(chosen: list[int]) -> int:
        count = 0
        for creature in ours:
            instance_id = creature.get("instanceId")
            if is_tapped(creature):
                continue  # already tapped; still tapped on their turn
            if instance_id in chosen and "vigilance" not in creature_keywords(creature):
                continue  # attacking taps it
            count += 1
        return count

    def _damage_taken(chosen: list[int]) -> int:
        # Each defender is assumed to fully stop one of the biggest attackers.
        return sum(enemy_powers[_defender_count(chosen):])

    life_guard_used = []
    if my_life is not None:
        # Pull attackers back best-blocker-first until we would survive, or
        # until there is nothing left to pull back.
        pullable = sorted(
            attackers,
            key=lambda i: (toughness(board[i]), power(board[i])),
            reverse=True,
        )
        for attacker_id in pullable:
            if my_life - _damage_taken(attackers) > LIFE_BUFFER:
                break
            if "vigilance" in creature_keywords(board[attacker_id]):
                continue  # vigilant creatures defend without being held back
            attackers.remove(attacker_id)
            hold_back.append(attacker_id)
            life_guard_used.append(attacker_id)
            skipped[attacker_id] = "held back to survive the counter-attack"

    projected_life = None if my_life is None else my_life - _damage_taken(attackers)
    if not attackers:
        reason = "no safe attack"
    elif life_guard_used:
        reason = f"attacking with {len(attackers)}, {len(life_guard_used)} held back on defence"
    elif hold_back:
        reason = f"attacking with {len(attackers)}, {len(hold_back)} would lose value"
    else:
        reason = "all attackers safe"

    return {
        "attackers": attackers,
        "hold_back": hold_back,
        "skipped": skipped,
        "reason": reason,
        "lethal": False,
        "projected_counter_attack": counter_attack,
        "projected_life_after": projected_life,
    }
