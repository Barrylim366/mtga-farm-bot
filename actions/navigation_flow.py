from __future__ import annotations

import os

from actions.actions import ActionSpec
from state.state_machine import BotState


# Search boxes for the Historic navigation, in the 1920x1080 client reference
# frame. Module level because the Controller's final pre-queue gate has to look
# for the very same anchors on the very same screen: when these move, one place
# has to change, not two -- the gate used to carry its own copy of a top-left
# box for two anchors that are nowhere near the top left, and refused to queue
# forever on a screen it had itself navigated to correctly.
#
# Screen-identity anchors in the top-left nav strip sit FLUSH WITH THE TOP EDGE:
# home_anchor.png was measured at rect x=43..165, y=0..79. This ROI used to start
# at y=20, which sliced 20 rows off it -- 0.218 in the box against a 0.84
# threshold, 0.998 over the full client area -- so POST_LOGIN_PLAY's pre-assert
# could never pass and navigation never got past its first action.
HOME_ANCHOR_ROI = (0, 0, 760, 260)
HOME_PLAY_ROI = (1450, 820, 440, 220)
# The Play blade occupies the right-hand column. None of these are top-left.
BLADE_TABS_ROI = (1550, 40, 370, 220)      # Events / Find Match / Recently Played
BLADE_CONTENT_ROI = (1550, 180, 370, 220)  # the selected tab's header
SUBTAB_ROI = (1550, 250, 370, 180)         # Ranked / Play / Brawl
FORMAT_LIST_ROI = (1550, 430, 370, 380)    # Standard / Alchemy / Historic / ...
DECKS_HEADER_ROI = (0, 250, 800, 220)      # "> My Decks" on the left
DECKS_GRID_ROI = (0, 450, 800, 400)        # the "+" add-deck tile


# Home's Play button, centre, in the 1920x1080 client reference frame. Measured
# from a live capture on 2026-09-20: the button rect is x=1595..1871,
# y=974..1040. Used as POST_LOGIN_PLAY's coordinate fallback when play_btn.png
# does not match -- the calibrated queue point is preferred when the caller
# passes one, because it is the same button the queue click already uses.
HOME_PLAY_POINT = (1733, 1007)


def build_post_login_navigation_actions(
    *, assets_dir: str, buttons_dir: str, home_play_rel: tuple[int, int] | None = None
) -> list[ActionSpec]:
    # Historic queue navigation. The Starter Deck flow does NOT use this OOB
    # state-machine path (there are no player-log scenes for the Events blade, so
    # required_state gating would ESC-recover and get lost). Starter navigation
    # lives in Controller._navigate_starter_deck, driven purely by templates.
    #
    # Every ROI, template and threshold below was measured against the live
    # client on 2026-09-15 by walking the flow by hand and scoring each template
    # on each screen. The numbers in the comments are those measurements; they
    # are the reason the values are what they are, so re-measure before changing
    # one. Arena/client reference frame is 1920x1080.
    #
    # The two facts that shape the whole flow:
    #
    # 1. THE PLAY BLADE IS NOT A SCENE. This client only ever logs seven scene
    #    names (Home, EventLanding, ConstructedDeckSelect, Profile,
    #    DeckListViewer, DeckBuilder, RewardTrack) -- no Play, no matchmaking, no
    #    store, no options. So `post_expected_state` can never be satisfied for
    #    the blade steps and every one of them has to be decided on a template.
    # 2. SEVERAL OF THESE WIDGETS ARE TOGGLES. Clicking "My Decks" when the grid
    #    is already open collapses it; clicking Play on Home when the blade is
    #    already up closes it. Hence `skip_if_template` on each step -- it makes
    #    the flow idempotent and resumable from wherever MTGA happens to be.
    def a(name: str) -> str:
        return os.path.join(assets_dir, name)

    def nav(name: str) -> str:
        # Templates cut from the live client for THIS flow. Kept apart from the
        # shared Buttons/ set, whose copies are still used by the starter flow and
        # the legacy full-screen fallback and were measured stale here (the old
        # hist_play_btn/my_decks/play_format_tab scored 0.49/0.36/0.52 against the
        # screens they are supposed to identify).
        return os.path.join(assets_dir, "nav", name)

    def b(name: str) -> str:
        return os.path.join(buttons_dir, name)

    home_anchor_roi = HOME_ANCHOR_ROI
    home_play_roi = HOME_PLAY_ROI
    blade_tabs_roi = BLADE_TABS_ROI
    blade_content_roi = BLADE_CONTENT_ROI
    subtab_roi = SUBTAB_ROI
    format_list_roi = FORMAT_LIST_ROI
    decks_header_roi = DECKS_HEADER_ROI
    decks_grid_roi = DECKS_GRID_ROI
    home_play_point = tuple(home_play_rel) if home_play_rel else HOME_PLAY_POINT

    return [
        ActionSpec(
            name="POST_LOGIN_PLAY",
            required_state=BotState.HOME,
            click_template=b("play_btn.png"),
            click_search_roi_rel=home_play_roi,
            # This step is the whole flow's gate, and it hangs off ONE template on
            # a button that sits under whatever else the desktop puts there.
            # Measured live 2026-09-20 with an always-on-top window over its lower
            # half: play_btn.png scored 0.727 here (0.895 on the uncovered rows),
            # the click step failed on every attempt, and Historic refused to queue
            # forever. The button does not move, so a measured point is a better
            # answer than a failed navigation -- and post_assert below still has to
            # see the blade open, so a fallback click that misses fails the step.
            click_fallback_rel=home_play_point,
            pre_assert_template=a("home_anchor.png"),
            pre_assert_roi_rel=home_anchor_roi,
            # The blade may already be open -- the Home Play button is then
            # partly covered (play_btn drops 0.954 -> 0.65) and clicking it again
            # would close the blade.
            skip_if_template=b("find_match_btn.png"),
            skip_if_roi_rel=blade_tabs_roi,
            # Not play_menu_anchor.png: that was captured with the Events tab
            # SELECTED, so it only matches in one of the three tab states and
            # scored 0.518 on a live blade sitting on Recently Played. The Find
            # Match TAB is in the row whichever tab is active -- measured 0.998
            # unselected, 0.939 selected, 0.908 on the Historic page.
            post_assert_template=b("find_match_btn.png"),
            post_assert_roi_rel=blade_tabs_roi,
            threshold=0.85,
            post_timeout_sec=7.0,
        ),
        ActionSpec(
            name="POST_LOGIN_FIND_MATCH",
            # The blade is not a scene, so the state here is still HOME.
            required_state=BotState.HOME,
            click_template=b("find_match_btn.png"),
            click_search_roi_rel=blade_tabs_roi,
            # find_match_anchor.png is the Find Match panel header: 0.998 once the
            # tab is open, 0.649 while it is not.
            post_assert_template=a("find_match_anchor.png"),
            post_assert_roi_rel=blade_content_roi,
            threshold=0.82,
        ),
        ActionSpec(
            name="POST_LOGIN_PLAY_SUBTAB",
            required_state=BotState.HOME,
            # Ranked / Play / Brawl. The format list (Standard, Alchemy, Historic
            # ...) only exists under Play, so if Historic Play is already listed
            # this step has nothing to do.
            click_template=nav("nav_play_subtab.png"),
            click_search_roi_rel=subtab_roi,
            skip_if_template=nav("nav_historic_play.png"),
            skip_if_roi_rel=format_list_roi,
            # A miss means the account already had Play selected, which is the
            # normal case -- it must not fail the navigation.
            optional=True,
            threshold=0.84,
            post_timeout_sec=2.0,
        ),
        ActionSpec(
            name="POST_LOGIN_HIST_PLAY",
            required_state=BotState.HOME,
            click_template=nav("nav_historic_play.png"),
            click_search_roi_rel=format_list_roi,
            # The row's label brightens once selected, so the click template drops
            # to 0.788 and would fail on an account already on Historic. The page
            # header is the reliable "we are there" signal: historic_anchor.png
            # measured 0.989 selected against 0.734 not.
            skip_if_template=a("historic_anchor.png"),
            skip_if_roi_rel=format_list_roi,
            post_assert_template=a("historic_anchor.png"),
            post_assert_roi_rel=format_list_roi,
            threshold=0.85,
            post_timeout_sec=10.0,
        ),
        ActionSpec(
            name="POST_LOGIN_MY_DECKS",
            required_state=BotState.HOME,
            click_template=nav("nav_my_decks.png"),
            click_search_roi_rel=decks_header_roi,
            # "My Decks" is a toggle and MTGA remembers it open between sessions:
            # clicking the header when the grid is already expanded COLLAPSES it.
            # The "+" add-deck tile only exists while expanded (0.882 open, 0.442
            # closed), so it is both the skip test and the post-assert.
            skip_if_template=b("my_decks_grid_open.png"),
            skip_if_roi_rel=decks_grid_roi,
            post_assert_template=b("my_decks_grid_open.png"),
            post_assert_roi_rel=decks_grid_roi,
            threshold=0.80,
            post_timeout_sec=8.0,
        ),
    ]
