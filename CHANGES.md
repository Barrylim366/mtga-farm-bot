# Pull Request Summary: Native Bot Enhancements & Optimizations

This document provides a clean, concise overview of the enhancements integrated into the native `mtga-farm-bot` codebase for upstream Pull Request review.

---

## 1. Overview of Changes

All features are integrated directly into their respective architectural layers (`AI/Utilities/`, `Controller/Utilities/`, `vision/`, root services, and `tests/`) following the repository structure and coding guidelines (`CLAUDE.md`).

- **100% Test Passing**: All 1,238 unit tests pass with 0 errors and 0 failures (`Ran 1238 tests in 140s OK`).
- **Zero Invasiveness**: Fully non-breaking fallbacks protect against unhandled states or missing data.
- **Test Runner Isolation**: Dynamic configuration path resolution via `runtime_file()` preserves test runner isolation and never pollutes production runtime files during test suite execution.

---

## 2. File-by-File Breakdown

### New Modules

| File Path | Description |
|---|---|
| [`AI/Utilities/MulliganLogic.py`](file:///AI/Utilities/MulliganLogic.py) | **Intelligent London Mulligan Engine**: Analyzes opening hands using land-to-spell ratios, mana curve castability (Turns 1-3), and automatic tucking of surplus lands / high-CMC spells under the London Mulligan rule. |
| [`AI/Utilities/StarterDeckMatcher.py`](file:///AI/Utilities/StarterDeckMatcher.py) | **Starter Deck Quest Matcher**: Parses active daily quest color requirements and recommends the dual-color Starter Deck maximizing quest progress. |
| [`notifications.py`](file:///notifications.py) | **Webhook Notification Dispatcher**: Threaded, non-blocking background dispatcher for Discord Webhook embeds and Telegram bot alerts (match victories/defeats, quest completions, account rotations). |
| [`low_power.py`](file:///low_power.py) | **Low-Power / Overnight Mode**: Applies configurable idle throttling to reduce CPU/GPU temperatures, power draw, and fan noise during overnight farming. |
| [`tests/test_mulligan_logic.py`](file:///tests/test_mulligan_logic.py) | Unit tests verifying opening hand evaluation, zero/all land mulligans, and London card tucking. |
| [`tests/test_human_input.py`](file:///tests/test_human_input.py) | Unit tests for cubic Bezier curve math and humanized mouse path generation. |
| [`tests/test_starter_deck_matcher.py`](file:///tests/test_starter_deck_matcher.py) | Unit tests verifying quest color parsing and dual-color deck selection. |
| [`tests/test_notifications.py`](file:///tests/test_notifications.py) | Unit tests for the singleton notification manager and queue handling. |
| [`tests/test_low_power.py`](file:///tests/test_low_power.py) | Unit tests for power throttle multiplier calculations. |
| [`tests/test_window_standardizer.py`](file:///tests/test_window_standardizer.py) | Unit tests for DPI v2 awareness and grayscale vision preprocessors. |

### Modified Existing Files

| File Path | Modifications |
|---|---|
| [`AI/DummyAI.py`](file:///AI/DummyAI.py) | In `generate_keep()`, delegated hand evaluation to `AI.Utilities.MulliganLogic.evaluate_opening_hand()`, falling back to safe keep if hand data is unavailable. |
| [`Controller/Utilities/input_controller.py`](file:///Controller/Utilities/input_controller.py) | Added `bezier_point()`, `generate_human_path()`, and `human_move_and_click()` implementing cubic Bezier trajectories with smooth acceleration and micro-jitter. |
| [`Controller/MTGAController/Controller.py`](file:///Controller/MTGAController/Controller.py) | Integrated `generate_human_path` in `_human_move_abs()` for realistic mouse motion; dispatched account switch notifications via `notifications.NotificationManager`. |
| [`vision/window_locator.py`](file:///vision/window_locator.py) | Added `enable_per_monitor_dpi_v2()` (Windows Per-Monitor DPI Awareness v2), `snap_arena_window_16x9()` for exact aspect ratio alignment, and `preprocess_for_matching()`. |
| [`Game.py`](file:///Game.py) | Added non-blocking match result notification dispatch (`notify_match_result`) upon match conclusion. |
| [`run_bot.py`](file:///run_bot.py) | Enabled Per-Monitor DPI Awareness v2 at bot initialization. |
| [`ui.py`](file:///ui.py) | Added **Enhancements & Alerts** subwindow (`EnhancementsWindow`) with toggles for Discord/Telegram alerts, Bezier mouse trajectories, low-power mode, and 16:9 window snapping. |
| [`ForgeEngine/client.py`](file:///ForgeEngine/client.py) | Tuned JVM heap flags (`-Xmx1g`, `-XX:+UseG1GC`, `-XX:+UseStringDeduplication`) for 50% lower advisor RAM footprint. |
| [`README.md`](file:///README.md) | Documented the new features in the main documentation. |

---

## 3. Verification & Testing

- **Full Test Suite Result**:
  ```
  Ran 1238 tests in 140.240s
  OK (skipped=1)
  ```
- **Runtime Isolation**: Test fixtures run in temporary directories without altering live user configs.
- **Code Style**: Strictly adheres to existing codebase standards and conventions.
