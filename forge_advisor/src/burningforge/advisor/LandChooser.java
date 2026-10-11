package burningforge.advisor;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.ai.ComputerUtilMana;
import forge.ai.simulation.GameCopier;
import forge.ai.simulation.GameStateEvaluator;
import forge.card.MagicColor;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

/**
 * Picks which land to play by trying each one against the hand.
 *
 * Forge's own choice only looks at the lands: it favours a colour the battlefield does not make yet
 * and, when nothing new becomes castable, a land that enters tapped. It never asks what the cards in
 * hand cost, so it will play a Plains into a hand of blue spells.
 *
 * Here every land is actually played on a copy of the game, and the hand is then paid for with
 * Forge's real mana payment, most expensive spell first, until nothing more can be afforded. Lands
 * are compared, in this order, by:
 *   1. how much mana that spends this turn, then how many spells it casts;
 *   2. the same for next turn, with everything untapped and the whole hand still to cast;
 *   3. how many spells in hand have all their colours available at all;
 *   4. entering tapped, when the turn is not affected anyway (it costs nothing now);
 *   5. Forge's own value for the land.
 *
 * Paying for real is what makes cost reductions, colour clashes between two spells and the extra
 * mana of an untapped land count; nothing is resolved, the spells only show what the lands can pay.
 */
final class LandChooser {
    private LandChooser() { }

    static final class Trial {
        Card land;
        int spentNow;
        int castNow;
        int spentNext;
        int castNext;
        int coloursFit;
        boolean entersTapped;
        int forgeValue;

        int[] key() {
            return new int[] { spentNow, castNow, spentNext, castNext, coloursFit, entersTapped ? 1 : 0, forgeValue };
        }

        boolean betterThan(Trial other) {
            int[] a = key();
            int[] b = other.key();
            for (int i = 0; i < a.length; i++) {
                if (a[i] != b[i]) {
                    return a[i] > b[i];
                }
            }
            return false;
        }
    }

    private static List<SpellAbility> spellsInHand(Player me) {
        List<SpellAbility> spells = new ArrayList<>();
        for (Card c : me.getCardsIn(ZoneType.Hand)) {
            if (c.isLand() || Position.FILLER.equals(c.getName())) {
                continue;
            }
            SpellAbility sa = c.getFirstSpellAbility();
            if (sa != null && sa.isSpell()) {
                sa.setActivatingPlayer(me);
                spells.add(sa);
            }
        }
        return spells;
    }

    private static int tapped(Player me) {
        int count = 0;
        for (Card c : me.getCardsIn(ZoneType.Battlefield)) {
            if (c.isTapped()) {
                count++;
            }
        }
        return count;
    }

    /** Pay for the hand, dearest affordable spell first. Returns {permanents tapped for mana, spells paid for}. */
    private static int[] payForHand(Player me) {
        Set<Card> done = new HashSet<>();
        int spent = 0;
        int cast = 0;
        while (true) {
            SpellAbility best = null;
            for (SpellAbility sa : spellsInHand(me)) {
                if (done.contains(sa.getHostCard()) || !ComputerUtilMana.canPayManaCost(sa, me, 0, false)) {
                    continue;
                }
                if (best == null || sa.getHostCard().getCMC() > best.getHostCard().getCMC()) {
                    best = sa;
                }
            }
            if (best == null) {
                return new int[] { spent, cast };
            }
            done.add(best.getHostCard());
            int before = tapped(me);
            if (ComputerUtilMana.payManaCost(best.getPayCosts(), me, best, false)) {
                spent += tapped(me) - before;
                cast++;
            }
        }
    }

    private static byte coloursAvailable(Player me) {
        byte mask = 0;
        for (Card c : me.getCardsIn(ZoneType.Battlefield)) {
            for (SpellAbility mana : c.getManaAbilities()) {
                mana.setActivatingPlayer(me);
                for (byte colour : MagicColor.WUBRG) {
                    if (mana.canProduce(MagicColor.toShortString(colour))) {
                        mask |= colour;
                    }
                }
            }
        }
        return mask;
    }

    private static Trial tryLand(Position pos, Card land) {
        Trial trial = new Trial();
        trial.land = land;
        trial.forgeValue = GameStateEvaluator.evaluateLand(land);

        GameCopier copier = new GameCopier(pos.game);
        Game sim = copier.makeCopy();
        Player me = copier.find(pos.me);
        Card copy = copier.find(land);
        SpellAbility landAbility = null;
        for (SpellAbility sa : copy.getAllPossibleAbilities(me, true)) {
            if (sa.isLandAbility()) {
                landAbility = sa;
                break;
            }
        }
        Card played = me.playLand(copy, landAbility);
        sim.getAction().checkStateEffects(true);
        trial.entersTapped = played.isTapped();

        int[] now = payForHand(me);
        trial.spentNow = now[0];
        trial.castNow = now[1];

        // next turn: everything of ours untaps, and the hand is paid for afresh
        for (Card c : me.getCardsIn(ZoneType.Battlefield)) {
            c.setTapped(false);
        }
        me.getManaPool().clearPool(false);
        byte available = coloursAvailable(me);
        for (SpellAbility sa : spellsInHand(me)) {
            byte needed = sa.getHostCard().getManaCost().getColorProfile();
            if ((needed & ~available) == 0) {
                trial.coloursFit++;
            }
        }
        int[] next = payForHand(me);
        trial.spentNext = next[0];
        trial.castNext = next[1];
        return trial;
    }

    /**
     * The best of `lands` to play now. `forgePick` is the land Forge's AI named; it is kept when no
     * other land does better. What was compared goes into `report`.
     */
    static Card choose(Position pos, List<Card> lands, Card forgePick, JsonObject report) {
        Map<String, Card> byName = new LinkedHashMap<>();
        if (forgePick != null) {
            byName.put(forgePick.getName(), forgePick);
        }
        for (Card land : lands) {
            byName.putIfAbsent(land.getName(), land);
        }
        if (byName.size() < 2) {
            return forgePick != null ? forgePick : (lands.isEmpty() ? null : lands.get(0));
        }

        Trial best = null;
        Trial forges = null;
        JsonArray tried = new JsonArray();
        for (Card land : byName.values()) {
            Trial trial;
            try {
                trial = tryLand(pos, land);
            } catch (RuntimeException e) {
                // a land the copy could not play (an unusual card): leave it out of the comparison
                continue;
            }
            JsonObject t = new JsonObject();
            t.addProperty("land", land.getName());
            t.addProperty("manaUsedNow", trial.spentNow);
            t.addProperty("spellsNow", trial.castNow);
            t.addProperty("manaUsedNextTurn", trial.spentNext);
            t.addProperty("spellsNextTurn", trial.castNext);
            t.addProperty("coloursFit", trial.coloursFit);
            t.addProperty("entersTapped", trial.entersTapped);
            tried.add(t);
            if (land == forgePick) {
                forges = trial;
            }
            if (best == null || trial.betterThan(best)) {
                best = trial;
            }
        }
        report.add("tried", tried);
        if (best == null) {
            return forgePick;
        }
        if (forges != null && !best.betterThan(forges)) {
            best = forges;  // nothing beats Forge's own pick: keep it
        }
        if (forgePick != null && best.land != forgePick) {
            report.addProperty("insteadOf", forgePick.getName());
            report.addProperty("reason", reason(best, forges));
        }
        return best.land;
    }

    private static String reason(Trial best, Trial other) {
        if (other == null) {
            return "it does more for the cards in hand";
        }
        if (best.spentNow != other.spentNow) {
            return "it lets " + best.spentNow + " mana be used on the hand this turn instead of " + other.spentNow;
        }
        if (best.castNow != other.castNow) {
            return "it lets " + best.castNow + " spell(s) be cast this turn instead of " + other.castNow;
        }
        if (best.spentNext != other.spentNext) {
            return "it lets " + best.spentNext + " mana be used on the hand next turn instead of " + other.spentNext;
        }
        if (best.castNext != other.castNext) {
            return "it lets " + best.castNext + " spell(s) be cast next turn instead of " + other.castNext;
        }
        if (best.coloursFit != other.coloursFit) {
            return "it gives the colours for " + best.coloursFit + " spell(s) in hand instead of " + other.coloursFit;
        }
        if (best.entersTapped != other.entersTapped) {
            return "it enters tapped and nothing is lost by that this turn, which saves the untapped land for later";
        }
        return "Forge values it higher";
    }
}
