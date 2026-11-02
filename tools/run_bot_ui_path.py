from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bot_logger
from AI.DummyAI import DummyAI
from Controller.MTGAController.Controller import Controller
from Game import Game
from ui import ConfigManager, _app_path
from vision.window_locator import run_arena_setup_check


def main() -> int:
    config_manager = ConfigManager()
    result = run_arena_setup_check(
        assets_dir=_app_path("assets", "assert"),
        expected_size=(1920, 1080),
        write_debug_on_fail=True,
    )
    if not result.ok:
        print(result.message)
        if result.debug_dir:
            print(f"Debug bundle: {result.debug_dir}")
        return 1

    log_path = config_manager.get_log_path()
    click_targets = config_manager.get_click_targets()
    screen_bounds = config_manager.get_screen_bounds()
    input_backend = config_manager.get_input_backend()
    account_switch_minutes = config_manager.get_account_switch_minutes()
    account_switch_mode = config_manager.get_account_switch_mode()
    account_switch_main_quests = config_manager.get_account_switch_main_quests()
    account_switch_daily_wins = config_manager.get_account_switch_daily_wins()
    account_switch_750_only = config_manager.get_account_switch_750_only()
    account_cycle_index = config_manager.get_account_cycle_index()
    account_play_order = config_manager.get_account_play_order()
    game_mode = config_manager.get_game_mode()
    gold_per_win = config_manager.get_gold_per_win()
    account_switch_enabled = config_manager.get_account_switch_enabled()

    bot_logger.log_info(
        "UI-path runner: init controller log_path={} screen_bounds={} input_backend={} "
        "account_switch_minutes={} account_switch_mode={} game_mode={}".format(
            log_path,
            screen_bounds,
            input_backend,
            account_switch_minutes,
            account_switch_mode,
            game_mode,
        )
    )

    controller = Controller(
        log_path=log_path,
        screen_bounds=screen_bounds,
        click_targets=click_targets,
        input_backend=input_backend,
        account_switch_minutes=account_switch_minutes,
        account_switch_mode=account_switch_mode,
        account_switch_main_quests=account_switch_main_quests,
        account_switch_daily_wins=account_switch_daily_wins,
        account_switch_750_only=account_switch_750_only,
        account_cycle_index=account_cycle_index,
        account_play_order=account_play_order,
        game_mode=game_mode,
        gold_per_win=gold_per_win,
        account_switch_enabled=account_switch_enabled,
    )
    ai = DummyAI()
    game = Game(controller, ai)
    bot_logger.log_info("UI-path runner: game.start() begin")
    game.start()
    bot_logger.log_info("UI-path runner: game.start() completed")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            game.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
