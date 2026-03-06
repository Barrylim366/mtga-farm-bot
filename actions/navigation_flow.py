from __future__ import annotations

import os

from actions.actions import ActionSpec
from state.state_machine import BotState


def build_post_login_navigation_actions(*, assets_dir: str, buttons_dir: str) -> list[ActionSpec]:
    def a(name: str) -> str:
        return os.path.join(assets_dir, name)

    def b(name: str) -> str:
        return os.path.join(buttons_dir, name)

    # ROIs are relative to a 1920x1080 MTGA client area.
    home_play_roi = (1450, 820, 440, 220)
    find_match_roi = (1320, 760, 560, 280)
    center_right_roi = (1180, 460, 700, 520)

    return [
        ActionSpec(
            name="POST_LOGIN_PLAY",
            required_state=BotState.HOME,
            click_template=b("play_btn.png"),
            click_search_roi_rel=home_play_roi,
            pre_assert_template=a("home_anchor.png"),
            pre_assert_roi_rel=(20, 20, 380, 160),
            post_expected_state=BotState.FIND_MATCH,
            post_assert_template=a("play_menu_anchor.png"),
            post_assert_roi_rel=(40, 20, 420, 180),
            threshold=0.84,
            post_timeout_sec=7.0,
        ),
        ActionSpec(
            name="POST_LOGIN_FIND_MATCH",
            required_state=BotState.PLAY_MENU,
            click_template=b("find_match_btn.png"),
            click_search_roi_rel=find_match_roi,
            post_expected_state=BotState.FIND_MATCH,
            post_assert_template=a("find_match_anchor.png"),
            post_assert_roi_rel=(40, 20, 520, 210),
            threshold=0.82,
        ),
        ActionSpec(
            name="POST_LOGIN_HIST_PLAY",
            required_state=BotState.FIND_MATCH,
            click_template=b("hist_play_btn.png"),
            click_search_roi_rel=center_right_roi,
            post_expected_state=BotState.HISTORIC,
            post_assert_template=a("historic_anchor.png"),
            post_assert_roi_rel=(40, 20, 520, 210),
            threshold=0.80,
            post_timeout_sec=10.0,
        ),
        ActionSpec(
            name="POST_LOGIN_MY_DECKS",
            required_state=BotState.HISTORIC,
            click_template=b("my_decks.png"),
            click_search_roi_rel=center_right_roi,
            post_expected_state=BotState.MY_DECKS,
            post_assert_template=a("my_decks_anchor.png"),
            post_assert_roi_rel=(40, 20, 520, 210),
            threshold=0.80,
            post_timeout_sec=8.0,
        ),
    ]
