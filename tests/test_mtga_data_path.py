"""MTGA local card-data discovery tests."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Game import Game


class MacNativeDataPathTest(unittest.TestCase):
    def test_native_macos_data_directory_is_used_without_prompting(self):
        game = object.__new__(Game)
        game._data_dir_prompt = mock.Mock()
        game._debug = mock.Mock()

        native_path = os.path.expanduser(
            "~/Library/Application Support/com.wizards.mtga/Downloads/Raw"
        )
        completed = mock.Mock(stdout="", stderr="")

        with mock.patch("Game.os.path.isdir", side_effect=lambda path: path == native_path), \
                mock.patch("subprocess.run", return_value=completed) as run, \
                mock.patch("Game.CardInfo.reload_cards_from_disk"):
            game._refresh_card_data()

        game._data_dir_prompt.assert_not_called()
        self.assertEqual(run.call_args.args[0][-1], native_path)


if __name__ == "__main__":
    unittest.main()
