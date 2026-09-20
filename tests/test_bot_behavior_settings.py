import json
import pathlib
import tempfile
import threading
import types
import unittest

import run_bot
import ui


class _BoolVar:
    def __init__(self, value):
        self.value = bool(value)

    def get(self):
        return self.value

    def set(self, value):
        self.value = bool(value)


class _Config:
    def __init__(self, value=True):
        self.value = bool(value)

    def get_auto_concede_stalled_matches(self):
        return self.value

    def set_auto_concede_stalled_matches(self, value):
        self.value = bool(value)


class _Controller:
    def __init__(self):
        self.values = []

    def set_auto_concede_stalled_matches(self, value):
        self.values.append(bool(value))


def _app(config, controller=None):
    app = type("App", (), {})()
    app._controller_lock = threading.RLock()
    app.config_manager = config
    app._controller = controller
    app._publish_controller = types.MethodType(ui.MTGBotUI._publish_controller, app)
    app._set_auto_concede_stalled_matches = types.MethodType(
        ui.MTGBotUI._set_auto_concede_stalled_matches,
        app,
    )
    return app


class BotBehaviorSettingsTest(unittest.TestCase):
    def test_new_config_defaults_to_enabled(self):
        with tempfile.TemporaryDirectory(prefix="bot-behavior-") as directory:
            config_path = pathlib.Path(directory) / "calibration_config.json"
            self.assertTrue(ui.ConfigManager(config_path=config_path).get_auto_concede_stalled_matches())

    def test_toggle_persists_and_updates_running_controller(self):
        controller = _Controller()
        config = _Config(True)
        app = _app(config, controller)
        window = type("Window", (), {})()
        window._enabled = _BoolVar(False)
        window._config_manager = config
        window._parent = type("Parent", (), {"master": app})()

        ui.BotBehaviorWindow._apply_auto_concede_setting(window)

        self.assertFalse(window._config_manager.value)
        self.assertFalse(window._enabled.get())
        self.assertEqual(controller.values, [False])

    def test_toggle_during_startup_is_reapplied_when_controller_is_published(self):
        config = _Config(True)
        app = _app(config)
        controller = _Controller()

        app._set_auto_concede_stalled_matches(False)
        app._publish_controller(controller)

        self.assertIs(app._controller, controller)
        self.assertEqual(controller.values, [False])


class CliBotBehaviorConfigTest(unittest.TestCase):
    def _load(self, payload):
        with tempfile.TemporaryDirectory(prefix="cli-bot-behavior-") as directory:
            path = pathlib.Path(directory) / "calibration_config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return run_bot._load_runtime_bot_config(path)

    def test_legacy_config_without_key_keeps_enabled_default(self):
        _targets, enabled = self._load({"click_targets": {"concede": {"x": 1, "y": 2}}})
        self.assertTrue(enabled)

    def test_explicit_boolean_values_are_honored(self):
        for configured in (False, True):
            with self.subTest(configured=configured):
                _targets, enabled = self._load({"auto_concede_stalled_matches": configured})
                self.assertIs(enabled, configured)


if __name__ == "__main__":
    unittest.main()
