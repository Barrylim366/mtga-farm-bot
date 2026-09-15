import unittest

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


class BotBehaviorSettingsTest(unittest.TestCase):
    def test_new_config_defaults_to_enabled(self):
        self.assertTrue(ui.ConfigManager(config_path=self._temp_config()).get_auto_concede_stalled_matches())

    def test_toggle_persists_and_updates_running_controller(self):
        controller = _Controller()
        window = type("Window", (), {})()
        window._enabled = _BoolVar(False)
        window._config_manager = _Config(True)
        window._parent = type("Parent", (), {"master": type("App", (), {"_controller": controller})()})()

        ui.BotBehaviorWindow._apply_auto_concede_setting(window)

        self.assertFalse(window._config_manager.value)
        self.assertFalse(window._enabled.get())
        self.assertEqual(controller.values, [False])

    @staticmethod
    def _temp_config():
        import os
        import tempfile
        directory = tempfile.mkdtemp(prefix="bot-behavior-")
        return os.path.join(directory, "calibration_config.json")


if __name__ == "__main__":
    unittest.main()
