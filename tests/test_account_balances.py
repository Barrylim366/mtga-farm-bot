"""Unit tests for reading per-account balances out of Player.log.

The behaviour under test is attribution: an InventoryInfo line never names the
account it belongs to, so a balance is only ever booked against the login that
precedes it. Everything here is a real log shape copied from a live Player.log
(2026-11-05); no test touches MTGA, the screen or a Controller.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import account_balances as ab

ACCOUNT_A = "Affinity2004#12345"
ACCOUNT_B = "TEUBAT#67890"


def fields(row: dict) -> dict:
    """Just the numbers, so a test does not pin the provenance fields."""
    return {field: row[field] for field in ab.BALANCE_FIELDS}


def login_line(display_name: str) -> str:
    """MTGA's own login record, as written after a sign-in."""
    return "[Accounts - Login] Logged in successfully. Display Name: %s\n" % display_name


def handshake_line(screen_name: str) -> str:
    """The match-server handshake, the log's other record of who is signed in."""
    return (
        '[UnityCrossThreadLogger]<== Match to 12345: AuthenticateResponse '
        '{"authenticateResponse":{"clientId":"x","screenName":"%s"}}\n' % screen_name
    )


def inventory_line(
    gold: int = 0,
    gems: int = 0,
    draft_tokens: int | None = None,
    mythic: int = 0,
    rare: int = 0,
) -> str:
    """An InventoryInfo line in MTGA's own spelling and field order.

    `draft_tokens=None` leaves CustomTokens out entirely, which is what the
    client writes for an account that owns no tokens.
    """
    custom = ' "CustomTokens": { "DraftToken": %d },' % draft_tokens if draft_tokens is not None else ""
    return (
        '{ "InventoryInfo": { "SeqId": 60, "Gems": %d, "Gold": %d, '
        '"TotalVaultProgress": 48, "WcTrackPosition": 7, "WildCardCommons": 14, '
        '"WildCardUnCommons": 12, "WildCardRares": %d, "WildCardMythics": %d,%s '
        '"Boosters": [ { "CollationId": 100063, "SetCode": "FRA", "Count": 30 } ] } }\n'
        % (gems, gold, rare, mythic, custom)
    )


class AttributionTest(unittest.TestCase):
    def test_a_balance_belongs_to_the_login_before_it(self):
        text = login_line(ACCOUNT_A) + inventory_line(gold=14150)
        balances = ab.parse_balances(text)
        self.assertEqual(balances[ACCOUNT_A]["gold"], 14150)

    def test_two_accounts_keep_their_own_balances(self):
        """The whole point: the newest entry in the log belongs to B, and A's row
        must not be filled with it (the 2026-07-27 inflated row)."""
        text = (
            login_line(ACCOUNT_A)
            + inventory_line(gold=17000, gems=250)
            + login_line(ACCOUNT_B)
            + inventory_line(gold=30500, gems=3300)
        )
        balances = ab.parse_balances(text)
        self.assertEqual(fields(balances[ACCOUNT_A]), {
            "gold": 17000, "gems": 250, "draft_tokens": 0,
            "mythic_wildcards": 0, "rare_wildcards": 0,
        })
        self.assertEqual(balances[ACCOUNT_B]["gold"], 30500)
        self.assertEqual(balances[ACCOUNT_B]["gems"], 3300)

    def test_a_later_balance_replaces_an_earlier_one_for_the_same_account(self):
        text = (
            login_line(ACCOUNT_A)
            + inventory_line(gold=1000)
            + inventory_line(gold=2500)
        )
        self.assertEqual(ab.parse_balances(text)[ACCOUNT_A]["gold"], 2500)

    def test_an_unattributable_balance_is_dropped(self):
        """A rotated log can start mid-session; guessing here is what produced
        the wrong rows, so a balance with no login before it is discarded."""
        balances = ab.parse_balances(inventory_line(gold=999) + login_line(ACCOUNT_B))
        self.assertEqual(balances, {})

    def test_a_balance_written_after_a_re_login_belongs_to_the_new_account(self):
        text = (
            login_line(ACCOUNT_A)
            + inventory_line(gold=100)
            + login_line(ACCOUNT_B)
            + inventory_line(gold=200)
        )
        balances = ab.parse_balances(text)
        self.assertEqual(balances[ACCOUNT_A]["gold"], 100)
        self.assertEqual(balances[ACCOUNT_B]["gold"], 200)


class LoginFormsTest(unittest.TestCase):
    def test_the_display_name_login_identifies_the_account(self):
        balances = ab.parse_balances(login_line("Barrylim#08112") + inventory_line(gold=50))
        self.assertIn("Barrylim#08112", balances)

    def test_a_spaced_display_name_survives_whole(self):
        balances = ab.parse_balances(login_line("Lass E#94890") + inventory_line(gold=50))
        self.assertIn("Lass E#94890", balances)

    def test_appended_metadata_is_not_part_of_the_identity(self):
        """A live log writes `Display Name: Foo#111 (cached)`; the parenthetical
        is not part of the name Arena uses."""
        balances = ab.parse_balances(login_line("Foo#111 (cached)") + inventory_line(gold=50))
        self.assertIn("Foo#111", balances)

    def test_the_handshake_login_identifies_the_account(self):
        balances = ab.parse_balances(handshake_line(ACCOUNT_B) + inventory_line(gold=70))
        self.assertIn(ACCOUNT_B, balances)

    def test_the_later_of_the_two_login_forms_wins(self):
        balances = ab.parse_balances(
            handshake_line(ACCOUNT_A)
            + inventory_line(gold=10)
            + login_line(ACCOUNT_B)
            + inventory_line(gold=20)
        )
        self.assertEqual(balances[ACCOUNT_A]["gold"], 10)
        self.assertEqual(balances[ACCOUNT_B]["gold"], 20)

    def test_a_balance_line_does_not_need_to_be_read_as_a_login(self):
        """An InventoryInfo line is not a login record, even when the surrounding
        text has a screenName-shaped field in it."""
        line = inventory_line(gold=5) + ' "screenName": "NotALogin"\n'
        balances = ab.parse_balances(login_line(ACCOUNT_A) + line)
        self.assertEqual(list(balances), [ACCOUNT_A])


class BareSpellingTest(unittest.TestCase):
    """Live behaviour: the handshake frequently omits the discriminator the
    login record carries. Both spellings are the same account."""

    def test_bare_and_discriminated_spellings_collapse_to_one_row(self):
        text = (
            handshake_line("mycoides")
            + inventory_line(gold=44950)
            + login_line("mycoides#47724")
            + inventory_line(gold=44400)
        )
        balances = ab.parse_balances(text)
        self.assertEqual(list(balances), ["mycoides#47724"])
        self.assertEqual(balances["mycoides#47724"]["gold"], 44400)

    def test_the_newest_reading_survives_the_collapse(self):
        """The bare handshake can be the FRESHER record; collapsing must not
        keep the older balance just because it carries the nicer name."""
        text = (
            login_line("mycoides#47724")
            + inventory_line(gold=44400)
            + handshake_line("mycoides")
            + inventory_line(gold=44950)
        )
        balances = ab.parse_balances(text)
        self.assertEqual(list(balances), ["mycoides#47724"])
        self.assertEqual(balances["mycoides#47724"]["gold"], 44950)

    def test_two_discriminated_accounts_sharing_a_name_are_not_merged(self):
        """`Player#11111` and `Player#22222` are different people. The bare
        record cannot be attributed to either, so nothing is guessed."""
        text = (
            login_line("Player#11111") + inventory_line(gold=10)
            + login_line("Player#22222") + inventory_line(gold=20)
            + handshake_line("Player") + inventory_line(gold=30)
        )
        balances = ab.parse_balances(text)
        self.assertEqual(sorted(balances), ["Player", "Player#11111", "Player#22222"])

    def test_a_bare_account_with_no_discriminated_sibling_is_kept(self):
        balances = ab.parse_balances(handshake_line("solo") + inventory_line(gold=7))
        self.assertEqual(list(balances), ["solo"])

    def test_the_collapsed_row_says_which_spelling_it_was_booked_under(self):
        text = (
            handshake_line("mycoides")
            + inventory_line(gold=1)
            + login_line("mycoides#47724")
            + inventory_line(gold=2)
        )
        self.assertEqual(ab.parse_balances(text)["mycoides#47724"][ab.LOGGED_NAME_FIELD], "mycoides#47724")


class FieldParsingTest(unittest.TestCase):
    def test_all_five_columns_are_read(self):
        balances = ab.parse_balances(
            login_line(ACCOUNT_A) + inventory_line(gold=59550, gems=3300, mythic=8, rare=4)
        )
        self.assertEqual(fields(balances[ACCOUNT_A]), {
            "gold": 59550, "gems": 3300, "draft_tokens": 0,
            "mythic_wildcards": 8, "rare_wildcards": 4,
        })

    def test_draft_tokens_are_read_from_custom_tokens(self):
        balances = ab.parse_balances(login_line(ACCOUNT_A) + inventory_line(draft_tokens=1))
        self.assertEqual(balances[ACCOUNT_A]["draft_tokens"], 1)

    def test_custom_tokens_absent_means_no_tokens(self):
        balances = ab.parse_balances(login_line(ACCOUNT_A) + inventory_line(draft_tokens=None))
        self.assertEqual(balances[ACCOUNT_A]["draft_tokens"], 0)

    def test_other_custom_tokens_do_not_leak_into_draft_tokens(self):
        line = (
            login_line(ACCOUNT_A)
            + '{ "InventoryInfo": { "SeqId": 60, "Gold": 1, "Gems": 2, "CustomTokens": '
              '{ "BattlePass_SOS_Orb": 2, "BonusPackProgress": 2, "DraftToken": 3 } } }\n'
        )
        self.assertEqual(ab.parse_balances(line)[ACCOUNT_A]["draft_tokens"], 3)

    def test_a_truncated_inventory_line_still_yields_what_it_holds(self):
        """MTGA's log can end mid-line (a rotating log, the client dying). The
        scalars are written first, so they are still readable."""
        text = login_line(ACCOUNT_A) + '{ "InventoryInfo": { "SeqId": 61, "Gems": 12, "Gold": 34'
        balance = ab.parse_balances(text)[ACCOUNT_A]
        self.assertEqual((balance["gold"], balance["gems"]), (34, 12))

    def test_a_garbage_inventory_line_is_ignored(self):
        text = login_line(ACCOUNT_A) + '"InventoryInfo": { not json at all\n'
        self.assertEqual(ab.parse_balances(text), {})


class ReadBalancesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.current = os.path.join(self.directory.name, "Player.log")
        self.previous = os.path.join(self.directory.name, "Player-prev.log")

    def write(self, path, *lines):
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)

    def test_the_rotation_is_found_next_to_the_log(self):
        self.assertEqual(
            ab.player_log_paths(self.current),
            [self.previous, self.current],
        )

    def test_no_log_path_means_no_files(self):
        self.assertEqual(ab.player_log_paths(""), [])

    def test_an_older_account_keeps_the_balance_from_the_previous_log(self):
        """Accounts that have not logged in since the client restarted are only
        in Player-prev.log. The newer file must not wipe them."""
        self.write(self.previous, login_line(ACCOUNT_A), inventory_line(gold=111))
        self.write(self.current, login_line(ACCOUNT_B), inventory_line(gold=222))
        balances = ab.read_balances(ab.player_log_paths(self.current))
        self.assertEqual(balances[ACCOUNT_A]["gold"], 111)
        self.assertEqual(balances[ACCOUNT_B]["gold"], 222)

    def test_the_current_log_beats_the_previous_one(self):
        self.write(self.previous, login_line(ACCOUNT_A), inventory_line(gold=111))
        self.write(self.current, login_line(ACCOUNT_A), inventory_line(gold=222))
        self.assertEqual(ab.read_balances(ab.player_log_paths(self.current))[ACCOUNT_A]["gold"], 222)

    def test_a_missing_previous_log_is_not_an_error(self):
        self.write(self.current, login_line(ACCOUNT_A), inventory_line(gold=5))
        balances = ab.read_balances(ab.player_log_paths(self.current))
        self.assertEqual(balances[ACCOUNT_A]["gold"], 5)

    def test_a_missing_directory_is_not_an_error(self):
        missing = os.path.join(self.directory.name, "nope", "Player.log")
        self.assertEqual(ab.read_balances(ab.player_log_paths(missing)), {})

    def test_non_latin_display_names_survive(self):
        self.write(self.current, login_line("Ærø#1234"), inventory_line(gold=9))
        self.assertIn("Ærø#1234", ab.read_balances([self.current]))


class MatchingTest(unittest.TestCase):
    def test_the_discriminator_is_dropped_for_matching(self):
        self.assertEqual(ab.account_key("Barrylim#08112"), ab.account_key("Barrylim"))

    def test_the_discriminator_is_not_shown_to_the_user(self):
        """`#12345` separates two accounts; it is not something to read in a list."""
        self.assertEqual(ab.display_name("Barrylim#08112"), "Barrylim")

    def test_a_name_without_a_discriminator_is_shown_whole(self):
        self.assertEqual(ab.display_name("Lass E"), "Lass E")

    def test_an_empty_name_has_an_empty_display_name(self):
        self.assertEqual(ab.display_name(""), "")
        self.assertEqual(ab.display_name(None), "")

    def test_matching_is_case_insensitive(self):
        self.assertEqual(ab.account_key("Affinity2004"), ab.account_key("affinity2004"))

    def test_a_bare_configured_name_finds_the_discriminated_login(self):
        """The config never recorded discriminators, so `Barrylim` has to find
        `Barrylim#08112`; showing a blank row instead is a dead end."""
        balances = {"Barrylim#08112": {"gold": 77, ab.SEQUENCE_FIELD: 1}}
        self.assertEqual(ab.find_balance(balances, "Barrylim")["gold"], 77)

    def test_an_exact_name_wins_over_a_bare_sibling(self):
        """With two accounts called Player, the exact spelling is the only
        evidence about which one a row is."""
        balances = {
            "Player#11111": {"gold": 1, ab.SEQUENCE_FIELD: 1},
            "Player": {"gold": 2, ab.SEQUENCE_FIELD: 9},
        }
        self.assertEqual(ab.find_balance(balances, "Player#11111")["gold"], 1)

    def test_the_newest_reading_wins_within_a_tier(self):
        balances = {
            "Player#11111": {"gold": 1, ab.SEQUENCE_FIELD: 1},
            "Player#22222": {"gold": 2, ab.SEQUENCE_FIELD: 9},
        }
        self.assertEqual(ab.find_balance(balances, "Player")["gold"], 2)

    def test_an_unknown_account_has_no_balance(self):
        self.assertIsNone(ab.find_balance({"A": {"gold": 1}}, "Nobody"))

    def test_an_empty_name_never_matches(self):
        self.assertIsNone(ab.find_balance({"": {"gold": 1, ab.SEQUENCE_FIELD: 1}}, "", None))


if __name__ == "__main__":
    unittest.main()
