from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

from state.state_machine import BotState
from vision.vision import VisionEngine


@dataclass(frozen=True)
class ActionSpec:
    name: str
    required_state: BotState | None = None
    click_rel: tuple[int, int] | None = None
    click_template: str | None = None
    click_search_roi_rel: tuple[int, int, int, int] | None = None
    pre_assert_template: str | None = None
    pre_assert_roi_rel: tuple[int, int, int, int] | None = None
    post_expected_state: BotState | None = None
    post_assert_template: str | None = None
    post_assert_roi_rel: tuple[int, int, int, int] | None = None
    threshold: float = 0.88
    pre_timeout_sec: float = 1.2
    post_timeout_sec: float = 6.0
    max_retries: int = 2


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    reason: str


def run_action(
    spec: ActionSpec,
    *,
    state_getter: Callable[[], BotState],
    vision: VisionEngine,
    arena_region_getter: Callable[[], tuple[int, int, int, int] | None],
    click_abs: Callable[[int, int, str], None],
    recover_once: Callable[[str, int], None] | None = None,
) -> ActionResult:
    for attempt in range(1, max(1, spec.max_retries) + 1):
        arena_region = arena_region_getter()
        if arena_region is None:
            if recover_once is not None:
                recover_once(spec.name, attempt)
            continue
        if spec.required_state is not None:
            cur_state = state_getter()
            if cur_state not in (spec.required_state, BotState.UNKNOWN):
                if recover_once is not None:
                    recover_once(spec.name, attempt)
                continue

        if not _run_pre_assert(spec, vision, arena_region):
            if recover_once is not None:
                recover_once(spec.name, attempt)
            continue

        click_done = _click_step(spec, vision, arena_region, click_abs)
        if not click_done:
            if recover_once is not None:
                recover_once(spec.name, attempt)
            continue

        if _run_post_assert(spec, vision, arena_region, state_getter):
            return ActionResult(ok=True, reason="ok")

        if recover_once is not None:
            recover_once(spec.name, attempt)

    return ActionResult(ok=False, reason=f"action_failed:{spec.name}")


def _run_pre_assert(spec: ActionSpec, vision: VisionEngine, arena: tuple[int, int, int, int]) -> bool:
    if not spec.pre_assert_template or not spec.pre_assert_roi_rel:
        return True
    if not os.path.exists(spec.pre_assert_template):
        return True
    return vision.wait_for_template(
        _abs_region(arena, spec.pre_assert_roi_rel),
        spec.pre_assert_template,
        threshold=spec.threshold,
        timeout_sec=spec.pre_timeout_sec,
    )


def _run_post_assert(
    spec: ActionSpec,
    vision: VisionEngine,
    arena: tuple[int, int, int, int],
    state_getter: Callable[[], BotState],
) -> bool:
    deadline = time.time() + max(0.0, spec.post_timeout_sec)
    while time.time() < deadline:
        if spec.post_expected_state is not None:
            cur_state = state_getter()
            if cur_state in (spec.post_expected_state, BotState.UNKNOWN):
                if not spec.post_assert_template or not spec.post_assert_roi_rel:
                    return True

        if spec.post_assert_template and spec.post_assert_roi_rel and os.path.exists(spec.post_assert_template):
            vision.begin_tick()
            if vision.assert_template(
                _abs_region(arena, spec.post_assert_roi_rel),
                spec.post_assert_template,
                threshold=spec.threshold,
            ):
                return True

        time.sleep(0.2)

    return False


def _click_step(
    spec: ActionSpec,
    vision: VisionEngine,
    arena: tuple[int, int, int, int],
    click_abs: Callable[[int, int, str], None],
) -> bool:
    if spec.click_rel is not None:
        x = int(arena[0] + spec.click_rel[0])
        y = int(arena[1] + spec.click_rel[1])
        click_abs(x, y, spec.name)
        return True

    if spec.click_template and spec.click_search_roi_rel and os.path.exists(spec.click_template):
        search_abs = _abs_region(arena, spec.click_search_roi_rel)
        vision.begin_tick()
        img = vision.capture(search_abs)
        if img is None:
            return False
        match = vision.find_template(img, spec.click_template, threshold=spec.threshold)
        if match is None:
            return False
        x = int(search_abs[0] + match.x)
        y = int(search_abs[1] + match.y)
        click_abs(x, y, spec.name)
        return True

    return False


def _abs_region(
    arena_region: tuple[int, int, int, int],
    rel_region: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return (
        int(arena_region[0] + rel_region[0]),
        int(arena_region[1] + rel_region[1]),
        int(rel_region[2]),
        int(rel_region[3]),
    )
