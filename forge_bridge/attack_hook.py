"""Lets the bot attack with the creatures Forge chooses instead of always with everything.

Wraps the bot's `all_attack`. When Forge's plan for the turn is to send only some of the legal
attackers, each of those is clicked on our side of the battlefield (in Arena that marks it as an
attacker) and the attack button, which then reads "N Attackers", is pressed through the bot's own
verified press. When the plan is everything, the bot's `all_attack` runs untouched.

Not attacking at all needs Arena's "No Attacks" button. Its position is NO_ATTACKS_POINT below, in
the bot's 1920x1080 reference frame; while that is None the bot falls back to attacking with
everything, because pressing the main button with nothing selected would do exactly that anyway.

MTGA_FORGE_ATTACK: select (default) | shadow | gate
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from forge_bridge import forge_ai

# Centre of the "No Attacks" button during our declare-attackers step, or None while unmeasured.
# Measured from a 1600x900 client screenshot: the button's centre is at (1480, 736) there, directly
# above "All Attack" at (1480, 791) -- which lands on the bot's own calibrated attack button, so the
# two measurements agree.
NO_ATTACKS_POINT: Optional[tuple[int, int]] = (1776, 883)


def _names(bridge: "forge_ai.Bridge", ids: list) -> str:
    return ", ".join(bridge._name(i).split("#")[0] for i in ids) or "nobody"


def _explain(plan: dict) -> list[str]:
    scores = (plan.get("decision") or {}).get("scores") or {}
    lines = []
    if {"pick", "all", "none"} <= set(scores):
        def text(value: int) -> str:
            return "a win" if value > 10**9 else "a loss" if value < -10**9 else str(value)

        lines.append(
            "Played out through the opponent's next turn (higher is better): Forge's pick {}, "
            "everything {}, nobody {}.".format(text(scores["pick"]), text(scores["all"]), text(scores["none"]))
        )
    return lines


def attack(controller: Any, original: Callable[[Any], Any], bridge: "forge_ai.Bridge") -> Any:
    config = forge_ai.Config()
    if config.off or not config.acting or config.attack != "select":
        return original(controller)
    plan = bridge.plan_attack(config)
    if plan is None:
        return original(controller)
    turn = plan["key"][1]
    legal, chosen = plan["legal"], plan["attackers"]
    held = [i for i in legal if i not in chosen]

    if not held:
        forge_ai.publish(
            f"Turn {turn} · Attack — Everything attacks",
            "\n".join(["Forge's plan is to attack with every creature that can."] + _explain(plan)),
        )
        return original(controller)

    if not chosen:
        if NO_ATTACKS_POINT is None:
            forge_ai._log(f"attack turn={turn}: plan is no attack, but the No Attacks button is not located; attacking")
            forge_ai.publish(
                f"Turn {turn} · Attack — Everything attacks",
                "\n".join([
                    "Forge's plan was not to attack. The bot cannot decline yet (the No Attacks button "
                    "has not been located), so it attacked with everything.",
                ] + _explain(plan)),
            )
            return original(controller)
        target, _source = controller._map_abs_point_to_arena(NO_ATTACKS_POINT, label="NO_ATTACKS", force_reacquire=True)
        controller._click_abs(int(target[0]), int(target[1]), "NO_ATTACKS")
        controller._Controller__last_attack_submit_ts = time.time()
        forge_ai._log(f"attack turn={turn}: no attack")
        forge_ai.publish(
            f"Turn {turn} · Attack — No attack",
            "\n".join([f"Holding back: {_names(bridge, held)}."] + _explain(plan)),
        )
        return True

    declared = [i for i in chosen if controller.select_battlefield_permanent(i)]
    if not declared:
        forge_ai._log(f"attack turn={turn}: none of the chosen attackers could be clicked; attacking with everything")
        return original(controller)
    target, source = controller._map_abs_point_to_arena(
        controller.main_br_button_coordinates, label="ATTACK_SELECTED", force_reacquire=True, apply_correction=False
    )
    if source == "absolute_no_arena":
        return False
    # the creatures are already declared, so the first press of the button is the submit
    controller._Controller__press_combat_button_verified(
        target, "ATTACK_SELECTED",
        declared_markers=["AttackState_Declared", '"canSubmitAttackers": true'],
        submitted_markers=["SubmitAttackersReq", "AttackState_Attacking"],
    )
    controller._Controller__last_attack_submit_ts = time.time()
    forge_ai._log(
        f"attack turn={turn}: attacking with [{_names(bridge, declared)}], holding back [{_names(bridge, held)}]"
    )
    why = [f"Holding back: {_names(bridge, held)}."]
    if len(declared) < len(chosen):
        why.append(f"{len(chosen) - len(declared)} chosen attacker(s) could not be clicked and stayed home.")
    why.append({
        "pick": "These are the attackers Forge's AI named.",
        "all": "Chosen by playout.", "none": "Chosen by playout.",
    }[plan.get("choice", "pick")])
    forge_ai.publish(f"Turn {turn} · Attack — {_names(bridge, declared)}", "\n".join(why + _explain(plan)))
    return True


def wrap(original: Callable[[Any], Any], bridge: Optional["forge_ai.Bridge"] = None) -> Callable[[Any], Any]:
    def all_attack(self):
        try:
            return attack(self, original, bridge or forge_ai.Bridge.instance())
        except Exception as exc:
            forge_ai._log(f"selective attack failed ({exc}); attacking with everything")
            return original(self)

    return all_attack


def install() -> bool:
    try:
        from Controller.MTGAController.Controller import Controller  # type: ignore
    except Exception:
        return False
    if getattr(Controller, "_forge_attack_installed", False):
        return True
    Controller.all_attack = wrap(Controller.all_attack)
    Controller._forge_attack_installed = True
    return True
