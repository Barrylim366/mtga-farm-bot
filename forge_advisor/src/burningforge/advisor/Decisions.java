package burningforge.advisor;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import forge.ai.AiController;
import forge.ai.PlayerControllerAi;
import forge.game.Game;
import forge.game.GameEntity;
import org.apache.commons.lang3.tuple.ImmutablePair;

import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.combat.Combat;
import forge.game.phase.PhaseHandler;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;

/** Asks Forge's own AI what it would do in a loaded position, in terms of Arena instance ids. */
public final class Decisions {
    private Decisions() { }

    private static AiController ai(Player p) {
        return ((PlayerControllerAi) p.getController()).getAi();
    }

    private static Set<Integer> ids(JsonObject req, String key) {
        Set<Integer> out = new HashSet<>();
        JsonElement e = req.get(key);
        if (e != null && e.isJsonArray()) {
            for (JsonElement x : e.getAsJsonArray()) {
                out.add(x.getAsInt());
            }
        }
        return out;
    }

    private static String label(Card c) {
        return c.isCreature() ? c.getName() + " " + c.getNetPower() + "/" + c.getNetToughness() : c.getName();
    }

    // ---------------------------------------------------------------- attack

    /** Which of my creatures Forge's AI would attack with, given the board Arena shows before attackers are declared. */
    public static JsonObject attack(Position pos, JsonObject req) {
        Game game = pos.game;
        PhaseHandler ph = game.getPhaseHandler();
        int turn = ph.getTurn();
        ph.devModeSet(PhaseType.COMBAT_DECLARE_ATTACKERS, pos.me, false, turn);
        Combat combat = new Combat(pos.me);
        ph.setCombat(combat);

        AiController ai = ai(pos.me);
        ai.declareAttackers(pos.me, combat);

        Set<Integer> legal = ids(req, "legalAttackers");
        boolean restrict = req.has("legalAttackers");
        JsonArray attackers = new JsonArray();
        JsonArray names = new JsonArray();
        JsonArray illegal = new JsonArray();
        for (Card c : combat.getAttackers()) {
            Integer id = pos.arenaId(c);
            if (id == null || (restrict && !legal.contains(id))) {
                // Forge believes this creature can attack and Arena does not: Arena wins
                illegal.add(label(c));
                continue;
            }
            JsonObject a = new JsonObject();
            a.addProperty("id", id);
            GameEntity defender = combat.getDefenderByAttacker(c);
            if (defender instanceof Card) {
                Integer target = pos.arenaId((Card) defender);
                if (target != null) {
                    a.addProperty("target", target);
                }
            }
            attackers.add(a);
            names.add(label(c));
        }
        JsonObject out = new JsonObject();
        out.add("attackers", attackers);
        out.add("names", names);
        out.addProperty("aggression", ai.getAttackAggression());
        if (!illegal.isEmpty()) {
            out.add("notLegalInArena", illegal);
        }

        if (req.has("evaluate") && req.get("evaluate").getAsBoolean()) {
            // back to the position as loaded before anything is copied for a playout
            List<Card> picked = new ArrayList<>();
            for (JsonElement a : attackers) {
                picked.add(pos.card(a.getAsJsonObject().get("id").getAsInt()));
            }
            ph.setCombat(null);
            ph.devModeSet(PhaseType.MAIN1, pos.me, false, turn);
            JsonObject scores = new JsonObject();
            try {
                List<Card> everyone = cards(pos, legal);
                if (!restrict) {
                    for (Card c : pos.me.getCreaturesInPlay()) {
                        if (forge.game.combat.CombatUtil.canAttack(c)) {
                            everyone.add(c);
                        }
                    }
                }
                out.addProperty("candidates", everyone.size());
                boolean trace = req.has("trace") && req.get("trace").getAsBoolean();
                AttackEvaluator.Result none = AttackEvaluator.evaluate(pos, new ArrayList<>(), trace);
                AttackEvaluator.Result all = AttackEvaluator.evaluate(pos, everyone, trace);
                scores.addProperty("none", none.score);
                scores.addProperty("all", all.score);
                if (trace) {
                    JsonObject traces = new JsonObject();
                    JsonArray noneLog = new JsonArray();
                    none.log.forEach(noneLog::add);
                    JsonArray allLog = new JsonArray();
                    all.log.forEach(allLog::add);
                    traces.add("none", noneLog);
                    traces.add("all", allLog);
                    out.add("traces", traces);
                }
                if (picked.isEmpty()) {
                    scores.add("pick", scores.get("none"));
                } else if (picked.size() == everyone.size()) {
                    scores.add("pick", scores.get("all"));
                } else {
                    scores.addProperty("pick", AttackEvaluator.evaluate(pos, picked));
                }
            } catch (RuntimeException e) {
                // the AI's own pick above is still a valid answer without the playouts
                out.addProperty("evaluateError", String.valueOf(e));
            }
            out.add("scores", scores);
        }
        return out;
    }

    // ---------------------------------------------------------------- block

    /** How Forge's AI would block the attackers Arena reports. */
    public static JsonObject block(Position pos, JsonObject req) {
        Game game = pos.game;
        PhaseHandler ph = game.getPhaseHandler();
        int turn = ph.getTurn();
        ph.devModeSet(PhaseType.COMBAT_DECLARE_ATTACKERS, pos.opp, false, turn);
        Combat combat = new Combat(pos.opp);

        JsonArray missing = new JsonArray();
        JsonElement attackersEl = req.get("attackers");
        if (attackersEl != null && attackersEl.isJsonArray()) {
            for (JsonElement ae : attackersEl.getAsJsonArray()) {
                JsonObject a = ae.getAsJsonObject();
                Card attacker = pos.card(a.get("id").getAsInt());
                if (attacker == null || !attacker.isInPlay()) {
                    missing.add(a.get("id").getAsInt());
                    continue;
                }
                GameEntity defender = pos.me;
                if (a.has("target") && !a.get("target").isJsonNull()) {
                    Card target = pos.card(a.get("target").getAsInt());
                    if (target != null && target.isInPlay()) {
                        defender = target;
                    }
                }
                combat.addAttacker(attacker, defender);
            }
        }
        ph.setCombat(combat);
        ph.devModeSet(PhaseType.COMBAT_DECLARE_BLOCKERS, pos.opp, false, turn);

        ai(pos.me).declareBlockersFor(pos.me, combat);

        // Arena's own legal blocker -> attackers graph, when the caller has it
        JsonObject legal = req.has("legalBlocks") && req.get("legalBlocks").isJsonObject()
                ? req.getAsJsonObject("legalBlocks") : null;

        JsonArray blocks = new JsonArray();
        JsonArray names = new JsonArray();
        JsonArray illegal = new JsonArray();
        for (Card blocker : combat.getAllBlockers()) {
            Integer blockerId = pos.arenaId(blocker);
            for (Card attacker : combat.getAttackersBlockedBy(blocker)) {
                Integer attackerId = pos.arenaId(attacker);
                boolean ok = blockerId != null && attackerId != null;
                if (ok && legal != null) {
                    ok = false;
                    JsonElement allowed = legal.get(String.valueOf(blockerId));
                    if (allowed != null && allowed.isJsonArray()) {
                        for (JsonElement x : allowed.getAsJsonArray()) {
                            ok |= x.getAsInt() == attackerId;
                        }
                    }
                }
                String text = label(blocker) + " blocks " + label(attacker);
                if (!ok) {
                    illegal.add(text);
                    continue;
                }
                JsonObject b = new JsonObject();
                b.addProperty("blocker", blockerId);
                b.addProperty("attacker", attackerId);
                blocks.add(b);
                names.add(text);
            }
        }
        JsonObject out = new JsonObject();
        out.add("blocks", blocks);
        out.add("names", names);
        if (!illegal.isEmpty()) {
            out.add("notLegalInArena", illegal);
        }
        if (!missing.isEmpty()) {
            out.add("missingAttackers", missing);
        }
        return out;
    }

    // ---------------------------------------------------------------- main phase

    private static JsonObject targetsOf(Position pos, SpellAbility sa) {
        JsonArray cards = new JsonArray();
        JsonArray players = new JsonArray();
        JsonArray names = new JsonArray();
        for (SpellAbility part = sa; part != null; part = part.getSubAbility()) {
            if (!part.usesTargeting() || part.getTargets() == null) {
                continue;
            }
            for (Card c : part.getTargets().getTargetCards()) {
                Integer id = pos.arenaId(c);
                if (id != null) {
                    cards.add(id);
                }
                names.add(label(c));
            }
            for (Player p : part.getTargets().getTargetPlayers()) {
                players.add(p == pos.me ? 0 : 1);
                names.add(p == pos.me ? "me" : "opponent");
            }
        }
        JsonObject out = new JsonObject();
        out.add("cards", cards);
        out.add("players", players);
        out.add("names", names);
        return out;
    }

    /**
     * The land to play or spell to cast now, limited to what Arena is offering.
     *
     * Forge may prefer something the caller cannot do (an activated ability, a card Arena does not
     * offer); that choice is set aside and the AI is asked again, so the answer is its best
     * executable play rather than "nothing".
     */
    public static JsonObject main(Position pos, JsonObject req) {
        Game game = pos.game;
        Player me = pos.me;
        AiController ai = ai(me);
        enterPhase(pos, req);
        game.getPhaseHandler().setPriority(me);

        Set<Integer> castable = ids(req, "castable");
        Set<Integer> playable = ids(req, "playable");
        JsonArray passedOver = new JsonArray();
        JsonObject out = new JsonObject();
        out.addProperty("action", "none");

        for (int attempt = 0; attempt < 8; attempt++) {
            List<SpellAbility> chosen = ai.chooseSpellAbilityToPlay();
            if (chosen == null || chosen.isEmpty()) {
                break;
            }
            SpellAbility sa = chosen.get(0);
            Card host = sa.getHostCard();
            Integer id = pos.arenaId(host);
            String kind = sa.isLandAbility() ? "play" : sa.isSpell() ? "cast" : "activate";
            boolean offered = id != null
                    && ("play".equals(kind) ? playable.contains(id) : "cast".equals(kind) && castable.contains(id));
            if (offered) {
                out.addProperty("action", kind);
                out.addProperty("id", id);
                out.addProperty("card", host.getName());
                out.addProperty("description", String.valueOf(sa));
                out.add("targets", targetsOf(pos, sa));
                break;
            }
            JsonObject skipped = new JsonObject();
            skipped.addProperty("kind", kind);
            skipped.addProperty("card", host == null ? "?" : host.getName());
            if (id != null) {
                skipped.addProperty("id", id);
            }
            skipped.addProperty("description", String.valueOf(sa));
            passedOver.add(skipped);

            if (sa.isLandAbility()) {
                // Arena is not offering this land drop; stop Forge from proposing lands at all
                me.setLandsPlayedThisTurn(me.getLandsPlayedThisTurn() + 100);
                continue;
            }
            String description = String.valueOf(sa);
            boolean marked = false;
            if (host != null) {
                for (SpellAbility other : host.getAllSpellAbilities()) {
                    if (String.valueOf(other).equals(description)) {
                        other.setSkip(true);
                        marked = true;
                    }
                }
            }
            sa.setSkip(true);
            if (!marked && attempt > 0) {
                // the AI handed back a derived copy we cannot mark; asking again would loop
                JsonObject last = passedOver.get(passedOver.size() - 2).getAsJsonObject();
                if (last.get("description").getAsString().equals(description)) {
                    break;
                }
            }
        }
        if (!passedOver.isEmpty()) {
            out.add("passedOver", passedOver);
        }
        return out;
    }

    /**
     * Outside the two main phases (the opponent's turn, a combat step, an end step) the game is put
     * into the phase Arena reports, with the combat Arena shows, so the AI judges instants and flash
     * creatures in the situation they would actually be cast in.
     */
    private static void enterPhase(Position pos, JsonObject req) {
        JsonObject state = req.getAsJsonObject("state");
        String phaseName = state.has("phase") ? state.get("phase").getAsString() : "MAIN1";
        if (phaseName.startsWith("MAIN")) {
            return;
        }
        PhaseHandler ph = pos.game.getPhaseHandler();
        Player active = ph.getPlayerTurn();
        int turn = ph.getTurn();
        PhaseType phase = PhaseType.smartValueOf(phaseName);
        JsonObject combatReq = req.has("combat") && req.get("combat").isJsonObject() ? req.getAsJsonObject("combat") : null;
        boolean inCombat = phase == PhaseType.COMBAT_DECLARE_ATTACKERS || phase == PhaseType.COMBAT_DECLARE_BLOCKERS
                || phase == PhaseType.COMBAT_FIRST_STRIKE_DAMAGE || phase == PhaseType.COMBAT_DAMAGE;
        if (inCombat && combatReq != null) {
            ph.devModeSet(PhaseType.COMBAT_DECLARE_ATTACKERS, active, false, turn);
            Combat combat = new Combat(active);
            Player defender = active == pos.me ? pos.opp : pos.me;
            if (combatReq.has("attackers")) {
                for (JsonElement ae : combatReq.getAsJsonArray("attackers")) {
                    JsonObject a = ae.getAsJsonObject();
                    Card attacker = pos.card(a.get("id").getAsInt());
                    if (attacker == null || !attacker.isInPlay() || attacker.getController() != active) {
                        continue;
                    }
                    GameEntity target = defender;
                    if (a.has("target") && !a.get("target").isJsonNull()) {
                        Card planeswalker = pos.card(a.get("target").getAsInt());
                        if (planeswalker != null && planeswalker.isInPlay()) {
                            target = planeswalker;
                        }
                    }
                    combat.addAttacker(attacker, target);
                }
            }
            if (combatReq.has("blocks")) {
                for (JsonElement be : combatReq.getAsJsonArray("blocks")) {
                    JsonObject b = be.getAsJsonObject();
                    Card blocker = pos.card(b.get("blocker").getAsInt());
                    Card attacker = pos.card(b.get("attacker").getAsInt());
                    if (blocker != null && attacker != null && combat.isAttacking(attacker) && blocker.isInPlay()) {
                        combat.addBlocker(attacker, blocker);
                    }
                }
            }
            ph.setCombat(combat);
        }
        ph.devModeSet(phase, active, false, turn);
    }

    // ---------------------------------------------------------------- scry / surveil

    /**
     * Which of the looked-at cards to keep on top of the library, and which to send away: to the
     * bottom for a scry, to the graveyard for a surveil. The cards must be in our library in the
     * loaded position (the caller lists them there, top first).
     */
    public static JsonObject scry(Position pos, JsonObject req) {
        boolean surveil = req.has("mode") && "surveil".equals(req.get("mode").getAsString());
        CardCollection looked = new CardCollection();
        JsonArray missing = new JsonArray();
        for (Integer id : orderedIds(req, "cards")) {
            Card c = pos.card(id);
            if (c == null) {
                missing.add(id);
            } else {
                looked.add(c);
            }
        }
        PlayerControllerAi controller = (PlayerControllerAi) pos.me.getController();
        ImmutablePair<CardCollection, CardCollection> result =
                surveil ? controller.arrangeForSurveil(looked) : controller.arrangeForScry(looked);
        JsonObject out = new JsonObject();
        JsonArray keep = new JsonArray();
        JsonArray away = new JsonArray();
        JsonArray keepNames = new JsonArray();
        JsonArray awayNames = new JsonArray();
        if (result.getLeft() != null) {
            for (Card c : result.getLeft()) {
                keep.add(pos.arenaId(c));
                keepNames.add(c.getName());
            }
        }
        if (result.getRight() != null) {
            for (Card c : result.getRight()) {
                away.add(pos.arenaId(c));
                awayNames.add(c.getName());
            }
        }
        out.addProperty("mode", surveil ? "surveil" : "scry");
        out.add("top", keep);
        out.add("away", away);
        out.add("topNames", keepNames);
        out.add("awayNames", awayNames);
        if (!missing.isEmpty()) {
            out.add("unknownCards", missing);
        }
        return out;
    }

    private static List<Integer> orderedIds(JsonObject req, String key) {
        List<Integer> out = new ArrayList<>();
        JsonElement e = req.get(key);
        if (e != null && e.isJsonArray()) {
            for (JsonElement x : e.getAsJsonArray()) {
                out.add(x.getAsInt());
            }
        }
        return out;
    }

    // ---------------------------------------------------------------- inspection

    /** What Forge ended up with, so a caller can compare it against Arena. */
    public static JsonObject describe(Position pos) {
        JsonObject out = new JsonObject();
        String[] who = { "me", "opp" };
        Player[] players = { pos.me, pos.opp };
        for (int i = 0; i < 2; i++) {
            Player p = players[i];
            JsonObject po = new JsonObject();
            po.addProperty("life", p.getLife());
            JsonArray bf = new JsonArray();
            for (Card c : p.getCardsIn(forge.game.zone.ZoneType.Battlefield)) {
                String s = label(c);
                if (c.isTapped()) {
                    s += " (T)";
                }
                if (c.isSick() && c.isCreature()) {
                    s += " (sick)";
                }
                bf.add(s);
            }
            po.add("battlefield", bf);
            JsonArray hand = new JsonArray();
            for (Card c : p.getCardsIn(forge.game.zone.ZoneType.Hand)) {
                hand.add(c.getName());
            }
            po.add("hand", hand);
            po.addProperty("library", p.getCardsIn(forge.game.zone.ZoneType.Library).size());
            out.add(who[i], po);
        }
        out.addProperty("phase", String.valueOf(pos.game.getPhaseHandler().getPhase()));
        out.addProperty("turn", pos.game.getPhaseHandler().getTurn());
        out.addProperty("activePlayer", pos.game.getPhaseHandler().getPlayerTurn() == pos.me ? "me" : "opp");
        return out;
    }

    static List<Card> cards(Position pos, Set<Integer> arenaIds) {
        List<Card> out = new ArrayList<>();
        for (Integer id : arenaIds) {
            Card c = pos.card(id);
            if (c != null) {
                out.add(c);
            }
        }
        return out;
    }

    static JsonObject merge(JsonObject into, JsonObject from) {
        for (Map.Entry<String, JsonElement> kv : from.entrySet()) {
            into.add(kv.getKey(), kv.getValue());
        }
        return into;
    }
}
