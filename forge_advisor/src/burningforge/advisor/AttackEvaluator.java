package burningforge.advisor;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Random;

import forge.ai.PlayerControllerAi;
import forge.ai.simulation.GameCopier;
import forge.ai.simulation.GameStateEvaluator;
import forge.game.Game;
import forge.game.GameLogEntry;
import forge.game.GameLogEntryType;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.phase.PhaseHandler;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.util.MyRandom;

/**
 * Scores an attack by playing it out on a copy of the game.
 *
 * The copy runs Forge's real turn loop with its AI on both sides: our chosen creatures attack, the
 * opponent blocks, damage is dealt, then the opponent takes their whole next turn (attacking back
 * into whatever we left untapped) and we block. The board at the end of that turn is what gets
 * scored. Two attacks scored from the same starting position are therefore comparable, including the
 * cost of leaving ourselves open.
 *
 * What the copy cannot know is the opponent's hand: it holds placeholder cards, so no trick or removal
 * is ever played against us in a playout.
 */
final class AttackEvaluator {
    private static final int MAX_STEPS = 600;
    private static final long SEED = 20240229L;

    private AttackEvaluator() { }

    /** Our controller in the copy: declares exactly the given attackers once, and is Forge's AI for everything else. */
    private static final class FixedAttack extends PlayerControllerAi {
        private final List<Card> attackers;
        private final Player defender;
        private boolean declared;

        FixedAttack(Game game, Player me, Player defender, List<Card> attackers) {
            super(game, me, me.getLobbyPlayer());
            this.attackers = attackers;
            this.defender = defender;
            getAi().setUseSimulation(null);
        }

        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            if (declared) {
                // asked again: the fixed set was rejected as illegal (a creature that must attack, say),
                // or this is a later turn. Either way the AI decides.
                super.declareAttackers(attacker, combat);
                return;
            }
            declared = true;
            for (Card c : attackers) {
                if (c.isInPlay()) {
                    combat.addAttacker(c, defender);
                }
            }
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            // nothing before our own attack is declared: this is the position as Arena shows it
            return declared ? super.chooseSpellAbilityToPlay() : null;
        }
    }

    static final class Result {
        int score;
        /** What happened in the playout, oldest first; only filled when asked for. */
        List<String> log = new ArrayList<>();
    }

    /** Score for us after attacking with exactly `attackers` (cards of the position's game) and the opponent's reply turn. */
    static int evaluate(Position pos, Collection<Card> attackers) {
        return evaluate(pos, attackers, false).score;
    }

    static Result evaluate(Position pos, Collection<Card> attackers, boolean trace) {
        Random previous = MyRandom.getRandom();
        // the same dice for every candidate, so the scores differ only because the attacks do
        MyRandom.setRandom(new Random(SEED));
        try {
            GameCopier copier = new GameCopier(pos.game);
            Game sim = copier.makeCopy();
            Player me = copier.find(pos.me);
            Player opp = copier.find(pos.opp);
            if (opp.getController() instanceof PlayerControllerAi) {
                ((PlayerControllerAi) opp.getController()).getAi().setUseSimulation(null);
            }
            List<Card> simAttackers = new ArrayList<>();
            for (Card c : attackers) {
                Card copy = copier.find(c);
                if (copy != null) {
                    simAttackers.add(copy);
                }
            }

            PhaseHandler ph = sim.getPhaseHandler();
            int startTurn = ph.getTurn();
            ph.devModeSet(PhaseType.COMBAT_BEGIN, me, false, startTurn);
            // normally created as the beginning of combat starts, which the jump above skips
            ph.setCombat(new Combat(me));
            ph.setPriority(me);
            ph.onStackResolved();

            me.runWithController(() -> {
                for (int step = 0; step < MAX_STEPS && !sim.isGameOver(); step++) {
                    boolean opponentsTurnOver = ph.getTurn() > startTurn
                            && (ph.getPlayerTurn() == me || ph.getPhase() == PhaseType.CLEANUP);
                    if (opponentsTurnOver) {
                        break;
                    }
                    ph.mainLoopStep();
                }
            }, new FixedAttack(sim, me, opp, simAttackers));

            Result result = new Result();
            result.score = new GameStateEvaluator().getScoreForGameState(sim, me).value;
            if (trace) {
                List<GameLogEntry> entries = new ArrayList<>(sim.getGameLog().getAllEntries());
                java.util.Collections.reverse(entries);
                for (GameLogEntry entry : entries) {
                    if (entry.type() != GameLogEntryType.MANA && entry.type() != GameLogEntryType.PHASE) {
                        result.log.add(entry.type() + ": " + entry.message());
                    }
                }
                result.log.add("END: my life " + me.getLife() + ", opponent life " + opp.getLife()
                        + ", game over " + sim.isGameOver());
            }
            return result;
        } finally {
            MyRandom.setRandom(previous);
        }
    }
}
