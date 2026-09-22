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
    # When this template is already on screen the action is considered done and
    # is skipped without clicking. Two reasons, both live-measured on 2026-09-15:
    # a step may already be satisfied (the account left the Play blade open, or
    # Historic Play already selected), and several of these widgets are TOGGLES --
    # clicking "My Decks" when the grid is already expanded COLLAPSES it. It also
    # makes the flow resumable from wherever MTGA happens to be.
    skip_if_template: str | None = None
    skip_if_roi_rel: tuple[int, int, int, int] | None = None
    # A step that only sometimes needs doing (e.g. selecting a sub-tab that the
    # account already had selected). Not finding its click target is then the
    # normal case, not a failure.
    optional: bool = False
    # Measured 1920x1080-relative point to click when `click_template` does not
    # match. Only for a widget that is at a FIXED place in the client and whose
    # step has a post-assert, so the blind click is still verified -- otherwise a
    # missed template would be answered by a click into an unknown screen.
    #
    # It exists because one stale or covered template must not be able to stop
    # the whole flow at its first step. Measured live on 2026-09-20: Home's Play
    # button was partly covered by an always-on-top window, play_btn.png scored
    # 0.727 against the 0.85 threshold (0.895 on the rows that were not covered),
    # POST_LOGIN_PLAY failed at step=click on all attempts, and Historic could
    # never reach the deck screen -- so it refused to queue, every single tick.
    click_fallback_rel: tuple[int, int] | None = None
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
    on_diagnostic: Callable[[str], None] | None = None,
    on_step_failed: Callable[[str, str, int], None] | None = None,
) -> ActionResult:
    # Capture the screen at the MOMENT a step fails, before the recovery runs.
    # Without this the only screenshot is the one written after the action gave
    # up -- by which time the recovery has already navigated back to Home, so the
    # bundle shows a healthy Home screen and says nothing about the screen the
    # assert actually failed on. That cost a whole debugging round on 2026-09-15:
    # POST_LOGIN_PLAY clicked Play correctly and then failed its post-assert, and
    # every saved bundle showed Home. Once per step per action, so a failing loop
    # cannot fill the disk.
    captured: set[str] = set()

    def _step_failed(step: str, attempt: int) -> None:
        if on_step_failed is None or step in captured:
            return
        captured.add(step)
        try:
            on_step_failed(spec.name, step, attempt)
        except Exception:
            pass

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

        # After the state gate, not before it: skipping is still a claim about
        # what is on screen, and it must not be reachable on a screen the action
        # was not allowed to run on in the first place.
        if _already_satisfied(spec, vision, arena_region):
            if on_diagnostic is not None:
                on_diagnostic(
                    f"ACTION_ALREADY_SATISFIED: {spec.name} skipped; "
                    f"{os.path.basename(spec.skip_if_template or '')} is already on screen."
                )
            return ActionResult(ok=True, reason="already_satisfied")

        if not _run_pre_assert(spec, vision, arena_region, on_diagnostic):
            _step_failed("pre_assert", attempt)
            if recover_once is not None:
                recover_once(spec.name, attempt)
            continue

        click_done = _click_step(spec, vision, arena_region, click_abs, on_diagnostic)
        if not click_done and spec.optional:
            if on_diagnostic is not None:
                on_diagnostic(
                    f"ACTION_OPTIONAL_SKIPPED: {spec.name} click target not found; "
                    "treating as already done."
                )
            return ActionResult(ok=True, reason="optional_skipped")
        if not click_done:
            _step_failed("click", attempt)
            if recover_once is not None:
                recover_once(spec.name, attempt)
            continue

        if _run_post_assert(spec, vision, arena_region, state_getter, on_diagnostic):
            return ActionResult(ok=True, reason="ok")

        _step_failed("post_assert", attempt)
        if recover_once is not None:
            recover_once(spec.name, attempt)

    return ActionResult(ok=False, reason=f"action_failed:{spec.name}")


def _already_satisfied(
    spec: ActionSpec,
    vision: VisionEngine,
    arena: tuple[int, int, int, int],
) -> bool:
    """Whether this action's end state is already on screen (see skip_if_template)."""
    if not spec.skip_if_template or not spec.skip_if_roi_rel:
        return False
    if not os.path.exists(spec.skip_if_template):
        return False
    vision.begin_tick()
    return vision.assert_template(
        _abs_region(arena, spec.skip_if_roi_rel),
        spec.skip_if_template,
        threshold=spec.threshold,
    )


def _assert_anchor(
    spec: ActionSpec,
    vision: VisionEngine,
    arena: tuple[int, int, int, int],
    template: str,
    roi_rel: tuple[int, int, int, int],
    kind: str,
    on_diagnostic: Callable[[str], None] | None,
) -> bool:
    """Look for a screen-identity anchor in its ROI, then in the whole client area.

    A too-tight anchor ROI is the worst kind of bug here, because it is invisible:
    the action reports "the screen is not what I expected" and the recovery starts
    clicking, while the anchor is plainly on screen a few pixels outside the box.
    That is exactly what happened to POST_LOGIN_PLAY, whose ROI started 20 rows
    below the top edge and so sliced the top off an anchor that sits flush against
    it (0.22 in the ROI, 0.998 over the full area). So: if the ROI misses, look
    once more across the whole arena, and when THAT hits, say so loudly and carry
    on -- a misconfigured box must cost a log line, not the navigation.

    The widening is only ever applied to anchors ("am I on the right screen?"),
    never to a click template, where searching outside the intended box could put
    a real click on the wrong widget.
    """
    if vision.assert_template(
        _abs_region(arena, roi_rel), template, threshold=spec.threshold
    ):
        return True
    full_rel = (0, 0, int(arena[2]), int(arena[3]))
    if tuple(roi_rel) == full_rel:
        return False
    vision.begin_tick()
    match = None
    image = vision.capture(_abs_region(arena, full_rel))
    if image is not None:
        match = vision.find_template(image, template, threshold=spec.threshold)
    if match is None:
        return False
    if on_diagnostic is not None:
        on_diagnostic(
            "ACTION_ROI_TOO_TIGHT: {} {}-assert template {} was not in roi_rel={} but "
            "matched the full client area at ({}, {}) score={:.3f}. The ROI is "
            "misconfigured; continuing on the wider match.".format(
                spec.name, kind, os.path.basename(template), tuple(roi_rel),
                match.x, match.y, match.score,
            )
        )
    return True


def _run_pre_assert(
    spec: ActionSpec,
    vision: VisionEngine,
    arena: tuple[int, int, int, int],
    on_diagnostic: Callable[[str], None] | None = None,
) -> bool:
    if not spec.pre_assert_template or not spec.pre_assert_roi_rel:
        return True
    if not os.path.exists(spec.pre_assert_template):
        return True
    deadline = time.time() + max(0.0, spec.pre_timeout_sec)
    while True:
        vision.begin_tick()
        if _assert_anchor(
            spec, vision, arena, spec.pre_assert_template, spec.pre_assert_roi_rel,
            "pre", on_diagnostic,
        ):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.2)


def _run_post_assert(
    spec: ActionSpec,
    vision: VisionEngine,
    arena: tuple[int, int, int, int],
    state_getter: Callable[[], BotState],
    on_diagnostic: Callable[[str], None] | None = None,
) -> bool:
    # A step that declares no post-condition at all has nothing to wait for, and
    # a confirmed click is all the success it can report. Without this the loop
    # below simply runs out its deadline and returns False, so such a step FAILS
    # however well it went -- measured live 2026-09-15: POST_LOGIN_PLAY_SUBTAB
    # clicked the Play sub-tab correctly and then reported
    # `action_failed:POST_LOGIN_PLAY_SUBTAB` every single time, which failed the
    # whole navigation and left Historic unable to queue. It used to carry a
    # `post_expected_state` that made it pass; dropping that (the Play blade is
    # not a scene, so the state was never really evidence) exposed the hole.
    has_template = bool(
        spec.post_assert_template
        and spec.post_assert_roi_rel
        and os.path.exists(spec.post_assert_template)
    )
    if spec.post_expected_state is None and not has_template:
        return True
    deadline = time.time() + max(0.0, spec.post_timeout_sec)
    while time.time() < deadline:
        if spec.post_expected_state is not None:
            cur_state = state_getter()
            if cur_state in (spec.post_expected_state, BotState.UNKNOWN):
                # `has_template`, not just "a path is configured": a path naming
                # an asset that is not installed is no evidence, and treating it
                # as one to be waited for would fail the step forever.
                if not has_template:
                    return True

        if has_template:
            vision.begin_tick()
            if _assert_anchor(
                spec, vision, arena, spec.post_assert_template, spec.post_assert_roi_rel,
                "post", on_diagnostic,
            ):
                return True

        time.sleep(0.2)

    return False


def _click_step(
    spec: ActionSpec,
    vision: VisionEngine,
    arena: tuple[int, int, int, int],
    click_abs: Callable[[int, int, str], None],
    on_diagnostic: Callable[[str], None] | None = None,
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
        if img is not None:
            match = vision.find_template(img, spec.click_template, threshold=spec.threshold)
            if match is not None:
                x = int(search_abs[0] + match.x)
                y = int(search_abs[1] + match.y)
                click_abs(x, y, spec.name)
                return True
        return _click_fallback(spec, arena, click_abs, on_diagnostic, searched=True)

    # No usable click template at all (none configured, or the asset is not in this
    # install). Reported as such: saying the template "did not match" would claim a
    # search happened and scored below threshold, which is a different fault with a
    # different fix -- re-cut the template vs. put the file there.
    return _click_fallback(spec, arena, click_abs, on_diagnostic, searched=False)


def _click_fallback(
    spec: ActionSpec,
    arena: tuple[int, int, int, int],
    click_abs: Callable[[int, int, str], None],
    on_diagnostic: Callable[[str], None] | None,
    *,
    searched: bool = True,
) -> bool:
    """Click the step's measured position after its click template missed.

    Reported as an error, not silently: the template is the thing that is
    supposed to work, and a run that keeps falling back is a template that needs
    re-cutting. The click itself is safe because a step only carries a fallback
    when its widget is at a fixed place AND it post-asserts the result, so a
    fallback that lands on nothing fails the step exactly as before.
    """
    if spec.click_fallback_rel is None:
        return False
    # The ROI the template search was allowed to use is also the bound on where
    # the fallback may click: a point outside it is a misconfiguration, and
    # clicking it would put a click on a widget this step never meant to touch.
    if spec.click_search_roi_rel is not None:
        rx, ry, rw, rh = spec.click_search_roi_rel
        fx, fy = spec.click_fallback_rel
        if not (rx <= fx <= rx + rw and ry <= fy <= ry + rh):
            if on_diagnostic is not None:
                on_diagnostic(
                    "ACTION_FALLBACK_OUT_OF_ROI: {} fallback point {} is outside its "
                    "click ROI {}; not clicking.".format(
                        spec.name, spec.click_fallback_rel, spec.click_search_roi_rel
                    )
                )
            return False
    x = int(arena[0] + spec.click_fallback_rel[0])
    y = int(arena[1] + spec.click_fallback_rel[1])
    if on_diagnostic is not None:
        if searched:
            why = "click template {} did not match in roi_rel={} (threshold={:.2f})".format(
                os.path.basename(spec.click_template or "-"),
                tuple(spec.click_search_roi_rel or ()), spec.threshold,
            )
        else:
            why = "has no usable click template ({} is not in this install)".format(
                os.path.basename(spec.click_template or "none configured")
            )
        on_diagnostic(
            "ACTION_CLICK_FALLBACK: {} {}; clicking the measured position {} "
            "instead. The post-assert still has to confirm it.".format(
                spec.name, why, spec.click_fallback_rel,
            )
        )
    click_abs(x, y, f"{spec.name}_FALLBACK")
    return True


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
