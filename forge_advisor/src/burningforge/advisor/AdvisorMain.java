package burningforge.advisor;

import java.io.BufferedReader;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import forge.StaticData;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

/**
 * Forge as a decision service: one JSON request per line on stdin, one JSON reply per line on stdout.
 *
 * Forge and its libraries print freely to System.out, so that stream is pointed at stderr before
 * anything else loads; only replies ever reach the real stdout.
 */
public final class AdvisorMain {
    private static final Gson GSON = new GsonBuilder().serializeNulls().create();
    private static PrintStream protocol;
    private static ExecutorService worker = newWorker();

    private AdvisorMain() { }

    /** Forge runs game logic inline only on threads it recognises as game threads, which it does by name. */
    private static ExecutorService newWorker() {
        return Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "Game-advisor");
            t.setDaemon(true);
            return t;
        });
    }

    public static void main(String[] args) throws Exception {
        protocol = new PrintStream(new FileOutputStream(FileDescriptor.out), true, StandardCharsets.UTF_8);
        System.setOut(System.err);
        System.setProperty("java.awt.headless", "true");

        String assets = System.getProperty("forge.assets");
        if (assets == null || assets.isEmpty()) {
            System.err.println("Set -Dforge.assets=<path to forge/forge-gui/>");
            System.exit(2);
        }

        long start = System.currentTimeMillis();
        GuiBase.setInterface(new HeadlessGui(assets));
        FModel.initialize(null, preferences -> {
            // every card script up front: the state loader looks cards up by name and never triggers a lazy load
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
        JsonObject ready = new JsonObject();
        ready.addProperty("ready", true);
        ready.addProperty("cards", StaticData.instance().getCommonCards().getUniqueCards().size());
        ready.addProperty("startupMs", System.currentTimeMillis() - start);
        protocol.println(GSON.toJson(ready));

        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String line;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) {
                continue;
            }
            JsonObject reply = handleLine(line);
            protocol.println(GSON.toJson(reply));
            if (reply.has("bye")) {
                break;
            }
        }
        System.exit(0);
    }

    private static JsonObject handleLine(String line) {
        JsonObject reply = new JsonObject();
        long start = System.currentTimeMillis();
        JsonObject req;
        try {
            req = JsonParser.parseString(line).getAsJsonObject();
        } catch (RuntimeException e) {
            reply.addProperty("ok", false);
            reply.addProperty("error", "bad request: " + e.getMessage());
            return reply;
        }
        if (req.has("id")) {
            reply.add("id", req.get("id"));
        }
        long timeoutMs = req.has("timeoutMs") ? req.get("timeoutMs").getAsLong() : 8000;
        Future<JsonObject> future = worker.submit(() -> dispatch(req));
        try {
            JsonObject result = future.get(timeoutMs, TimeUnit.MILLISECONDS);
            Decisions.merge(reply, result);
            reply.addProperty("ok", true);
        } catch (TimeoutException e) {
            // the stuck thread cannot be trusted with another request; leave it behind
            future.cancel(true);
            worker.shutdownNow();
            worker = newWorker();
            reply.addProperty("ok", false);
            reply.addProperty("error", "timeout after " + timeoutMs + " ms");
        } catch (Exception e) {
            Throwable cause = e.getCause() != null ? e.getCause() : e;
            StringWriter sw = new StringWriter();
            cause.printStackTrace(new PrintWriter(sw));
            String trace = sw.toString();
            reply.addProperty("ok", false);
            reply.addProperty("error", String.valueOf(cause));
            reply.addProperty("trace", trace.length() > 3000 ? trace.substring(0, 3000) : trace);
        }
        reply.addProperty("ms", System.currentTimeMillis() - start);
        return reply;
    }

    private static JsonObject dispatch(JsonObject req) throws Exception {
        String op = req.has("op") ? req.get("op").getAsString() : "decide";
        JsonObject out = new JsonObject();
        switch (op) {
            case "ping":
                out.addProperty("pong", true);
                return out;
            case "quit":
                out.addProperty("bye", true);
                return out;
            case "names": {
                // which of these Arena card names Forge has a script for
                JsonObject known = new JsonObject();
                for (JsonElement n : req.getAsJsonArray("names")) {
                    String[] found = Position.lookupName(n.getAsString());
                    if (found == null) {
                        known.add(n.getAsString(), com.google.gson.JsonNull.INSTANCE);
                    } else {
                        known.addProperty(n.getAsString(), found[0]);
                    }
                }
                out.add("known", known);
                return out;
            }
            case "decide":
                return decide(req);
            default:
                throw new IllegalArgumentException("unknown op: " + op);
        }
    }

    private static JsonObject decide(JsonObject req) throws Exception {
        JsonObject state = req.getAsJsonObject("state");
        String kind = req.get("kind").getAsString();
        String simulation = req.has("simulation") ? req.get("simulation").getAsString() : "none";
        String profile = req.has("profile") ? req.get("profile").getAsString() : "Default";

        String setupPhase = "MAIN1";
        if ("main".equals(kind) && state.has("phase") && state.get("phase").getAsString().startsWith("MAIN")) {
            // any other phase is set by the decision itself, after loading: handing a combat phase to
            // Forge's loader would make it declare attackers on its own
            setupPhase = state.get("phase").getAsString();
        }
        Position pos = Position.load(state, setupPhase, simulation, profile);

        JsonObject out = new JsonObject();
        JsonArray want = req.has("include") ? req.getAsJsonArray("include") : new JsonArray();
        for (JsonElement w : want) {
            if ("board".equals(w.getAsString())) {
                // taken before the decision runs, which moves phases and declares combat
                out.add("board", Decisions.describe(pos));
            }
        }
        JsonObject decision;
        switch (kind) {
            case "attack":
                decision = Decisions.attack(pos, req);
                break;
            case "block":
                decision = Decisions.block(pos, req);
                break;
            case "main":
                decision = Decisions.main(pos, req);
                break;
            case "scry":
                decision = Decisions.scry(pos, req);
                break;
            case "load":
                decision = new JsonObject();
                break;
            default:
                throw new IllegalArgumentException("unknown decision kind: " + kind);
        }
        out.add("decision", decision);
        out.add("diag", pos.diag);
        return out;
    }
}
