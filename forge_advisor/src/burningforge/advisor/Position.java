package burningforge.advisor;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import forge.StaticData;
import forge.ai.AIOption;
import forge.ai.LobbyPlayerAi;
import forge.card.CardRules;
import forge.card.CardSplitType;
import forge.card.CardStateName;
import forge.card.ICardFace;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameStage;
import forge.game.GameState;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.card.CounterEnumType;
import forge.game.card.CounterType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.zone.ZoneType;
import forge.item.PaperCard;

/**
 * An Arena position rebuilt inside a fresh Forge game.
 *
 * The request describes what Arena shows (who controls what, tapped, counters, attachments, current
 * power/toughness). It is turned into Forge's own dev-mode state format and applied by Forge's
 * loader, so zone moves, attachments and static effects are set up by the engine itself. Whatever
 * Forge then computes differently from Arena (an until-end-of-turn pump it never saw, an effect from
 * a card it does not know) is corrected afterwards from Arena's numbers.
 */
public final class Position {
    /** Stands in for cards we cannot see: the opponent's hand and both libraries. */
    static final String FILLER = "Wastes";

    public Game game;
    public Player me;
    public Player opp;
    public final JsonObject diag = new JsonObject();

    private final Map<Integer, Card> byArenaId = new HashMap<>();
    private final Map<Integer, Integer> arenaIdByForgeId = new HashMap<>();

    public Card card(int arenaId) {
        return byArenaId.get(arenaId);
    }

    public Integer arenaId(Card c) {
        return c == null ? null : arenaIdByForgeId.get(c.getId());
    }

    public Player player(int index) {
        return index == 0 ? me : opp;
    }

    // ---------------------------------------------------------------- card names

    private static Map<String, String[]> otherFaces;

    /** Back-face / second-face name -> {front name, split type}; built once from the loaded card rules. */
    private static synchronized Map<String, String[]> otherFaces() {
        if (otherFaces == null) {
            Map<String, String[]> map = new HashMap<>();
            for (PaperCard pc : StaticData.instance().getCommonCards().getUniqueCards()) {
                CardRules rules = pc.getRules();
                if (rules == null) {
                    continue;
                }
                ICardFace other = rules.getOtherPart();
                if (other != null && other.getName() != null) {
                    map.putIfAbsent(other.getName().toLowerCase(Locale.ROOT),
                            new String[] { pc.getName(), String.valueOf(rules.getSplitType()) });
                }
            }
            otherFaces = map;
        }
        return otherFaces;
    }

    static final class Resolved {
        String head;          // what goes first in the state entry
        boolean backFace;     // Arena shows the back of a double-faced card
        boolean modal;
        boolean standIn;      // not the real card: a vanilla body with Arena's characteristics
    }

    /** Forge's name for an Arena card name, or null when Forge has no such card. */
    static String[] lookupName(String name) {
        if (name == null || name.isEmpty()) {
            return null;
        }
        var db = StaticData.instance().getCommonCards();
        PaperCard pc = db.getCard(name);
        if (pc == null && name.startsWith("A-")) {
            // Alchemy rebalanced printing: fall back to the paper card it was rebalanced from
            pc = db.getCard(name.substring(2));
        }
        if (pc != null) {
            // the lookup also answers for a card's second face, handing back the whole card
            CardRules rules = pc.getRules();
            ICardFace other = rules == null ? null : rules.getOtherPart();
            if (other != null && name.equalsIgnoreCase(other.getName()) && !name.equalsIgnoreCase(pc.getName())) {
                return new String[] { pc.getName(), String.valueOf(rules.getSplitType()) };
            }
            return new String[] { pc.getName(), "front" };
        }
        String[] other = otherFaces().get(name.toLowerCase(Locale.ROOT));
        if (other != null) {
            return new String[] { other[0], other[1] };
        }
        return null;
    }

    private static final Map<String, String> NAMED_TOKEN_SCRIPTS = Map.of(
            "treasure", "c_a_treasure_sac",
            "food", "c_a_food_sac",
            "clue", "c_a_clue_draw",
            "blood", "c_a_blood_draw",
            "map", "c_a_map_sac_explore",
            "powerstone", "c_a_powerstone");

    private static String str(JsonObject o, String key, String dflt) {
        JsonElement e = o.get(key);
        return e == null || e.isJsonNull() ? dflt : e.getAsString();
    }

    private static int num(JsonObject o, String key, int dflt) {
        JsonElement e = o.get(key);
        return e == null || e.isJsonNull() ? dflt : e.getAsInt();
    }

    private static boolean flag(JsonObject o, String key) {
        JsonElement e = o.get(key);
        return e != null && !e.isJsonNull() && e.getAsBoolean();
    }

    private static List<String> strs(JsonObject o, String key) {
        List<String> out = new ArrayList<>();
        JsonElement e = o.get(key);
        if (e != null && e.isJsonArray()) {
            for (JsonElement x : e.getAsJsonArray()) {
                out.add(x.getAsString());
            }
        }
        return out;
    }

    private static String tokenScriptFor(JsonObject c) {
        var tokens = StaticData.instance().getAllTokens();
        String name = str(c, "name", "").toLowerCase(Locale.ROOT);
        String named = NAMED_TOKEN_SCRIPTS.get(name);
        if (named != null && tokens.containsRule(named)) {
            return named;
        }
        List<String> types = strs(c, "types");
        if (!types.contains("Creature")) {
            return null;
        }
        String colors = str(c, "colors", "").toLowerCase(Locale.ROOT);
        StringBuilder base = new StringBuilder(colors.isEmpty() ? "c" : colors);
        base.append('_').append(num(c, "basePower", num(c, "power", 0)));
        base.append('_').append(num(c, "baseToughness", num(c, "toughness", 0)));
        if (types.contains("Artifact")) {
            base.append("_a");
        }
        if (types.contains("Enchantment")) {
            base.append("_e");
        }
        for (String sub : strs(c, "subtypes")) {
            base.append('_').append(sub.toLowerCase(Locale.ROOT).replace(' ', '_'));
        }
        StringBuilder withKeywords = new StringBuilder(base);
        for (String kw : strs(c, "keywords")) {
            withKeywords.append('_').append(kw.toLowerCase(Locale.ROOT).replace(' ', '_'));
        }
        if (tokens.containsRule(withKeywords.toString())) {
            return withKeywords.toString();
        }
        // a script without the keywords would silently drop them (a flier that cannot fly); use a stand-in instead
        if (strs(c, "keywords").isEmpty() && tokens.containsRule(base.toString())) {
            return base.toString();
        }
        return null;
    }

    /** A vanilla body carrying exactly what Arena reports, for tokens and cards Forge has no script for. */
    private static String standInFor(JsonObject c) {
        List<String> types = new ArrayList<>(strs(c, "types"));
        if (types.isEmpty() || types.contains("Planeswalker") || types.contains("Battle")) {
            // Forge gives these their starting counters from the card's own text; a body without a card
            // behind it has none, and loading one fails. Leaving it out is the lesser loss.
            return null;
        }
        types.addAll(strs(c, "subtypes"));
        if (strs(c, "supertypes").contains("Legendary")) {
            types.add("Legendary");
        }
        String name = str(c, "name", "Unknown").replace(",", "").replace("|", "").replace(";", "").replace(":", "");
        if (name.isEmpty()) {
            name = "Unknown";
        }
        StringBuilder sb = new StringBuilder("t:").append(name);
        sb.append(",P:").append(num(c, "basePower", num(c, "power", 0)));
        sb.append(",T:").append(num(c, "baseToughness", num(c, "toughness", 0)));
        sb.append(",Cost:no cost");
        String colors = str(c, "colors", "");
        if (!colors.isEmpty()) {
            // Forge's token format reads one or two colour letters here, no more
            sb.append(",Color:").append(colors.length() > 2 ? colors.substring(0, 2) : colors);
        }
        sb.append(",Types:").append(String.join("-", types));
        // always present, even when empty: Forge's token loader dereferences the keyword list unconditionally
        sb.append(",Keywords:").append(String.join("-", strs(c, "keywords")));
        return sb.toString();
    }

    private static Resolved resolve(JsonObject c, boolean onBattlefield) {
        Resolved r = new Resolved();
        boolean token = flag(c, "token");
        if (!token) {
            String[] found = lookupName(str(c, "name", null));
            if (found != null) {
                r.head = found[0];
                if (!"front".equals(found[1])) {
                    r.backFace = CardSplitType.Transform.name().equals(found[1]);
                    r.modal = CardSplitType.Modal.name().equals(found[1]);
                }
                return r;
            }
        }
        if (!onBattlefield) {
            return null;
        }
        if (token) {
            String script = tokenScriptFor(c);
            if (script != null) {
                r.head = "T:" + script;
                return r;
            }
            // a token that is a copy of a real card: use that card, marked as a token
            String[] original = lookupName(str(c, "name", null));
            if (original != null && "front".equals(original[1])) {
                r.head = original[0] + "|IsToken";
                return r;
            }
        }
        r.head = standInFor(c);
        r.standIn = true;
        return r.head == null ? null : r;
    }

    private static String counterName(String arenaName) {
        try {
            return CounterEnumType.getType(arenaName.replace(" ", "")).name();
        } catch (RuntimeException e) {
            return null;
        }
    }

    // ---------------------------------------------------------------- loading

    static Game newGame(String simulation, String profile) {
        Set<AIOption> options = null;
        if ("full".equals(simulation)) {
            options = EnumSet.of(AIOption.USE_FULL_SIMULATION);
        } else if ("hybrid".equals(simulation)) {
            options = EnumSet.of(AIOption.USE_HYBRID_SIMULATION);
        }
        LobbyPlayerAi meLobby = new LobbyPlayerAi("me", options);
        LobbyPlayerAi oppLobby = new LobbyPlayerAi("opp", null);
        if (profile != null && !profile.isEmpty()) {
            meLobby.setAiProfile(profile);
            oppLobby.setAiProfile(profile);
        }
        List<RegisteredPlayer> players = new ArrayList<>();
        players.add(new RegisteredPlayer(new Deck()).setPlayer(meLobby));
        players.add(new RegisteredPlayer(new Deck()).setPlayer(oppLobby));
        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, players, "advisor");
        Game game = new Game(players, rules, match);
        game.setAge(GameStage.Play);
        game.EXPERIMENTAL_RESTORE_SNAPSHOT = false;
        return game;
    }

    private static void append(StringBuilder zone, String entry) {
        if (zone.length() > 0) {
            zone.append(';');
        }
        zone.append(entry);
    }

    /**
     * @param setupPhase the phase Forge's loader is told about. Combat is never handed to the loader: it would
     *                   re-run attack triggers Arena has already resolved, so callers set combat up themselves.
     */
    @SuppressWarnings("unchecked")
    public static Position load(JsonObject state, String setupPhase, String simulation, String profile) throws Exception {
        Position pos = new Position();
        pos.game = newGame(simulation, profile);
        for (Player p : pos.game.getPlayers()) {
            if ("me".equals(p.getName())) {
                pos.me = p;
            } else {
                pos.opp = p;
            }
        }
        int meIndex = pos.game.getPlayers().indexOf(pos.me);
        int[] forgeIndex = { meIndex, 1 - meIndex };

        JsonArray players = state.getAsJsonArray("players");
        JsonArray dropped = new JsonArray();
        JsonArray standIns = new JsonArray();
        JsonArray notes = new JsonArray();

        Set<Integer> battlefieldIds = new HashSet<>();
        for (JsonElement pe : players) {
            JsonArray bf = pe.getAsJsonObject().getAsJsonArray("battlefield");
            if (bf != null) {
                for (JsonElement ce : bf) {
                    battlefieldIds.add(num(ce.getAsJsonObject(), "id", -1));
                }
            }
        }

        List<String> lines = new ArrayList<>();
        lines.add("turn=" + Math.max(1, num(state, "turn", 1)));
        lines.add("activeplayer=p" + forgeIndex[num(state, "activePlayer", 0)]);
        lines.add("activephase=" + setupPhase);

        // arena id -> what Arena says the permanent currently is, applied after Forge has settled the board
        Map<Integer, JsonObject> expected = new HashMap<>();
        // Permanents showing their back face are placed by hand afterwards: Forge's loader moves every
        // card through the hand, which turns it face-up again -- and a modal card whose front is an
        // instant then never reaches the battlefield at all.
        List<Object[]> backFaces = new ArrayList<>();
        for (int i = 0; i < 2; i++) {
            JsonArray bf = players.get(i).getAsJsonObject().getAsJsonArray("battlefield");
            if (bf == null) {
                continue;
            }
            for (JsonElement ce : bf) {
                Resolved r = resolve(ce.getAsJsonObject(), true);
                if (r != null && (r.backFace || r.modal)) {
                    // not a valid attachment target for the loader either
                    battlefieldIds.remove(num(ce.getAsJsonObject(), "id", -1));
                }
            }
        }

        String[] zoneKeys = { "battlefield", "hand", "graveyard", "exile", "library" };
        for (int i = 0; i < 2; i++) {
            JsonObject p = players.get(i).getAsJsonObject();
            String prefix = "p" + forgeIndex[i];
            lines.add(prefix + "life=" + num(p, "life", 20));
            lines.add(prefix + "landsplayed=" + num(p, "landsPlayed", 0));
            for (String zoneKey : zoneKeys) {
                boolean onBattlefield = zoneKey.equals("battlefield");
                StringBuilder zone = new StringBuilder();
                int known = 0;
                JsonArray cards = p.getAsJsonArray(zoneKey);
                if (cards != null) {
                    for (JsonElement ce : cards) {
                        JsonObject c = ce.getAsJsonObject();
                        int id = num(c, "id", -1);
                        Resolved r = resolve(c, onBattlefield);
                        if (r == null) {
                            JsonObject d = new JsonObject();
                            d.addProperty("id", id);
                            d.addProperty("name", str(c, "name", "?"));
                            d.addProperty("zone", zoneKey);
                            dropped.add(d);
                            continue;
                        }
                        known++;
                        if (onBattlefield && (r.backFace || r.modal)) {
                            backFaces.add(new Object[] { c, i, r });
                            if (id >= 0) {
                                expected.put(id, c);
                            }
                            continue;
                        }
                        StringBuilder e = new StringBuilder(r.head);
                        if (id >= 0) {
                            e.append("|Id:").append(id);
                        }
                        if (r.standIn) {
                            JsonObject s = new JsonObject();
                            s.addProperty("id", id);
                            s.addProperty("name", str(c, "name", "?"));
                            standIns.add(s);
                        }
                        if (onBattlefield) {
                            if (flag(c, "tapped")) {
                                e.append("|Tapped");
                            }
                            if (flag(c, "sick")) {
                                e.append("|SummonSick");
                            }
                            if (flag(c, "faceDown")) {
                                e.append("|FaceDown");
                            }
                            if (c.has("owner") && num(c, "owner", i) != i) {
                                e.append("|Owner:P").append(forgeIndex[num(c, "owner", i)]);
                            }
                            int attachedTo = num(c, "attachedTo", -1);
                            if (attachedTo >= 0 && attachedTo != id && battlefieldIds.contains(attachedTo)) {
                                e.append("|AttachedTo:").append(attachedTo);
                            }
                            JsonElement counters = c.get("counters");
                            if (counters != null && counters.isJsonObject()) {
                                List<String> parts = new ArrayList<>();
                                for (Map.Entry<String, JsonElement> kv : counters.getAsJsonObject().entrySet()) {
                                    String type = counterName(kv.getKey());
                                    if (type == null) {
                                        notes.add("unknown counter type '" + kv.getKey() + "' on " + str(c, "name", "?"));
                                    } else if (kv.getValue().getAsInt() > 0) {
                                        parts.add(type + "=" + kv.getValue().getAsInt());
                                    }
                                }
                                if (!parts.isEmpty()) {
                                    e.append("|Counters:").append(String.join(",", parts));
                                }
                            }
                            if (id >= 0) {
                                expected.put(id, c);
                            }
                        }
                        append(zone, e.toString());
                    }
                }
                int fill = 0;
                if (zoneKey.equals("hand")) {
                    fill = num(p, "handSize", known) - (cards == null ? 0 : cards.size());
                } else if (zoneKey.equals("library")) {
                    fill = num(p, "librarySize", 0) - known;
                }
                for (int k = 0; k < fill; k++) {
                    append(zone, FILLER);
                }
                lines.add(prefix + zoneKey + "=" + zone);
            }
        }

        GameState gs = new GameState();
        gs.parse(lines);
        gs.applyToGame(pos.game);

        Field idField = GameState.class.getDeclaredField("idToCard");
        idField.setAccessible(true);
        for (Map.Entry<Integer, Card> kv : ((Map<Integer, Card>) idField.get(gs)).entrySet()) {
            pos.byArenaId.put(kv.getKey(), kv.getValue());
            pos.arenaIdByForgeId.put(kv.getValue().getId(), kv.getKey());
        }

        for (Object[] entry : backFaces) {
            JsonObject c = (JsonObject) entry[0];
            Player controller = pos.player((Integer) entry[1]);
            PaperCard paper = StaticData.instance().getCommonCards().getCard(((Resolved) entry[2]).head);
            Card card = Card.fromPaperCard(paper, controller);
            card.setState(CardStateName.Backside, true);
            card.setBackSide(true);
            card.setGameTimestamp(pos.game.getNextTimestamp());
            controller.getZone(ZoneType.Battlefield).add(card);
            card.setTapped(flag(c, "tapped"));
            card.setSickness(flag(c, "sick"));
            JsonElement counters = c.get("counters");
            if (counters != null && counters.isJsonObject()) {
                for (Map.Entry<String, JsonElement> kv : counters.getAsJsonObject().entrySet()) {
                    String type = counterName(kv.getKey());
                    if (type != null && kv.getValue().getAsInt() > 0) {
                        card.addCounterInternal(CounterType.getType(type), kv.getValue().getAsInt(), null, false, null, null);
                    }
                }
            }
            int id = num(c, "id", -1);
            if (id >= 0) {
                pos.byArenaId.put(id, card);
                pos.arenaIdByForgeId.put(card.getId(), id);
            }
        }
        if (!backFaces.isEmpty()) {
            pos.game.getAction().checkStateEffects(true);
        }

        // Arena is the source of truth for what a permanent currently looks like.
        JsonArray adjusted = new JsonArray();
        JsonArray lost = new JsonArray();
        for (Map.Entry<Integer, JsonObject> kv : expected.entrySet()) {
            Card card = pos.byArenaId.get(kv.getKey());
            JsonObject c = kv.getValue();
            if (card == null || !card.isInZone(ZoneType.Battlefield)) {
                JsonObject l = new JsonObject();
                l.addProperty("id", kv.getKey());
                l.addProperty("name", str(c, "name", "?"));
                lost.add(l);
                continue;
            }
            if (card.isCreature() && c.has("power") && c.has("toughness")) {
                int dp = num(c, "power", 0) - card.getNetPower();
                int dt = num(c, "toughness", 0) - card.getNetToughness();
                if (dp != 0 || dt != 0) {
                    card.addPTBoost(dp, dt, pos.game.getNextTimestamp(), 0);
                    JsonObject a = new JsonObject();
                    a.addProperty("id", kv.getKey());
                    a.addProperty("name", card.getName());
                    a.addProperty("power", dp);
                    a.addProperty("toughness", dt);
                    adjusted.add(a);
                }
            }
            int damage = num(c, "damage", 0);
            if (damage > 0) {
                card.setDamage(damage);
            }
        }
        pos.game.getAction().checkStateEffects(true);
        for (Map.Entry<Integer, JsonObject> kv : expected.entrySet()) {
            Card card = pos.byArenaId.get(kv.getKey());
            if (card != null && !card.isInZone(ZoneType.Battlefield)) {
                boolean already = false;
                for (JsonElement l : lost) {
                    already |= l.getAsJsonObject().get("id").getAsInt() == kv.getKey();
                }
                if (!already) {
                    JsonObject l = new JsonObject();
                    l.addProperty("id", kv.getKey());
                    l.addProperty("name", card.getName());
                    lost.add(l);
                }
            }
        }

        pos.diag.add("dropped", dropped);
        pos.diag.add("standIns", standIns);
        pos.diag.add("adjusted", adjusted);
        pos.diag.add("lost", lost);
        if (!notes.isEmpty()) {
            pos.diag.add("notes", notes);
        }
        return pos;
    }
}
