package burningforge.advisor;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.BiPredicate;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.ai.ComputerUtilCard;
import forge.ai.ComputerUtilCombat;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.player.Player;

/**
 * Putting a creature in front of an attacker to take less damage, although the creature dies.
 *
 * Forge's blocking AI only does that when the attack would leave it below a few life (4, by its
 * default profile). Until then it takes the damage, however much, unless a block is a good trade.
 * A big attack is therefore taken in full at 20 life while cheap creatures stand by.
 *
 * This runs after Forge has made its own blocks, which are left as they are. If the attack would
 * still take more than a set share of our life, creatures that are not blocking are put in front
 * of unblocked attackers, one at a time, until it no longer does:
 *
 *   - each time, the block that saves the most life is added (what a block saves is worked out
 *     by Forge's own damage prediction, so a trampler only loses what the blocker soaks up);
 *   - an attacker is stopped by the cheapest creature that can block it, and never by one worth
 *     as much as the attacker or more, by Forge's creature evaluation;
 *   - a block has to be worth the creature: it must save at least as much life as the creature's
 *     power and toughness together, and never less than three. That rules out standing in front
 *     of a trampler for the little the blocker soaks up;
 *   - an attacker that needs two blockers (menace) is left alone.
 *
 * Stopping when the limit is met, rather than blocking everything, is what keeps this from
 * emptying the board for a few points of life.
 */
final class Mitigation {
    private Mitigation() { }

    static final int LEAST_WORTH_A_CREATURE = 3;

    /** The life a block has to save for this creature to be given up for it. */
    static int price(Card blocker) {
        return Math.max(LEAST_WORTH_A_CREATURE, blocker.getNetPower() + blocker.getNetToughness());
    }

    /**
     * Adds blocks to `combat`. `share` is the part of our current life one attack may take (0.4 =
     * 40%); `allowed` says whether Arena lets this blocker block that attacker. What was done is
     * returned for the caller's report, or null when nothing was called for.
     */
    static JsonObject apply(Position pos, Combat combat, double share, BiPredicate<Card, Card> allowed) {
        Player me = pos.me;
        int life = me.getLife();
        int limit = (int) Math.floor(life * share);
        int before = life - ComputerUtilCombat.lifeThatWouldRemain(me, combat);
        if (before <= limit) {
            return null;
        }

        List<Card> free = new ArrayList<>();
        for (Card c : me.getCreaturesInPlay()) {
            if (!combat.isBlocking(c) && CombatUtil.canBlock(c, combat)) {
                free.add(c);
            }
        }
        free.sort(Comparator.comparingInt(ComputerUtilCard::evaluateCreature));

        JsonArray added = new JsonArray();
        int taking = before;
        while (taking > limit && !free.isEmpty()) {
            Card bestAttacker = null;
            Card bestBlocker = null;
            int bestSaved = 0;
            for (Card attacker : combat.getAttackersOf(me)) {
                if (!combat.getBlockers(attacker).isEmpty()
                        || CombatUtil.getMinNumBlockersForAttacker(attacker, me) > 1) {
                    continue;
                }
                int attackerWorth = ComputerUtilCard.evaluateCreature(attacker);
                for (Card blocker : free) {
                    if (ComputerUtilCard.evaluateCreature(blocker) >= attackerWorth) {
                        break;  // sorted: every further one is worth more still
                    }
                    if (!CombatUtil.canBlock(attacker, blocker, combat) || !allowed.test(blocker, attacker)) {
                        continue;
                    }
                    combat.addBlocker(attacker, blocker);
                    int saved = taking - (life - ComputerUtilCombat.lifeThatWouldRemain(me, combat));
                    combat.removeBlockAssignment(attacker, blocker);
                    if (saved >= price(blocker) && saved > bestSaved) {
                        bestAttacker = attacker;
                        bestBlocker = blocker;
                        bestSaved = saved;
                    }
                    break;  // the cheapest creature that can stop this attacker is the one to weigh
                }
            }
            if (bestAttacker == null) {
                break;
            }
            combat.addBlocker(bestAttacker, bestBlocker);
            free.remove(bestBlocker);
            taking -= bestSaved;
            JsonObject block = new JsonObject();
            block.addProperty("blocker", pos.arenaId(bestBlocker));
            block.addProperty("attacker", pos.arenaId(bestAttacker));
            block.addProperty("blockerName", bestBlocker.getName());
            block.addProperty("attackerName", bestAttacker.getName());
            block.addProperty("saves", bestSaved);
            added.add(block);
        }

        JsonObject report = new JsonObject();
        report.addProperty("life", life);
        report.addProperty("limit", limit);
        report.addProperty("wouldTake", before);
        report.addProperty("takes", taking);
        report.add("blocks", added);
        return report;
    }
}
