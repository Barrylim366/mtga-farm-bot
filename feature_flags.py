"""Centralized feature flags and environment variable settings for BurningBot."""
from __future__ import annotations

import os


def is_truthy(val: str | bool | None, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return default


def combat_blocks_enabled() -> bool:
    """Whether smart blocking via CombatLogic is enabled. Default True."""
    val = os.environ.get("MTGA_COMBAT_BLOCKS")
    return is_truthy(val, default=True)


def combat_selective_attack_enabled() -> bool:
    """Whether smart selective attacking via CombatLogic is enabled. Default False."""
    val = os.environ.get("MTGA_SELECTIVE_ATTACK")
    return is_truthy(val, default=False)


def combat_shadow_enabled() -> bool:
    """Whether shadow mode logging for combat decisions is enabled. Default True."""
    val = os.environ.get("MTGA_COMBAT_SHADOW")
    return is_truthy(val, default=True)


def combat_debug_capture_enabled() -> bool:
    """Whether debug screen captures for declare blocks are enabled. Default True."""
    val = os.environ.get("MTGA_COMBAT_BLOCK_CAPTURE")
    return is_truthy(val, default=True)


def low_power_enabled() -> bool:
    """Whether low power throttled sleep mode is enabled."""
    val = os.environ.get("MTGA_LOW_POWER")
    return is_truthy(val, default=False)


def human_mouse_enabled() -> bool:
    """Whether humanized Bezier mouse curves are enabled. Default True."""
    val = os.environ.get("MTGA_HUMAN_MOUSE")
    return is_truthy(val, default=True)


def active_mulligan_enabled() -> bool:
    """Whether intelligent London Mulligan evaluation and tucking is enabled. Default True."""
    val = os.environ.get("MTGA_ACTIVE_MULLIGAN")
    return is_truthy(val, default=True)


def lifeguard_untapped_only_enabled() -> bool:
    """Whether the combat life guard strictly counts only untapped enemy creatures for counter-attack."""
    val = os.environ.get("MTGA_LIFEGUARD_UNTAPPED_ONLY")
    return is_truthy(val, default=True)


def forge_free_reign_enabled() -> bool:
    """Whether Forge is given free reign to cast legal offered cards without requiring DummyAI agreement (experimental)."""
    val = os.environ.get("MTGA_FORGE_FREE")
    return is_truthy(val, default=False)
