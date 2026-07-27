"""Unit tests for the missing-card refresh at startup.

refresh_missing_cards() runs on the start path and blocks. Every Arena ID that
Scryfall answers 404 for is an Arena-only object (token, Alchemy rebalance) that
will never be there, so re-requesting the whole list at every start cost seconds
of startup for an answer that cannot change -- and the list only grows.

These tests pin the resulting contract: 404s back off, transient failures do not,
resolved cards leave the list, the pass is bounded in wall-clock time, and the
legacy plain-list file still loads.

No network: the Scryfall fetch is stubbed in every test.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from AI.Utilities import CardInfo


class _MissingCardsTestBase(unittest.TestCase):
    """Redirect missing_cards.json/cards.json at a temp dir and stub the network."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.missing_path = os.path.join(self.tmp, "missing_cards.json")
        self.cards_path = os.path.join(self.tmp, "cards.json")
        # _card_data_index is derived from _card_data and cached behind an
        # (id, len) staleness check; restore it too so a test that swaps
        # _card_data can't leave a stale index for the rest of the suite.
        self._saved_index = (CardInfo._card_data_index, CardInfo._card_data_index_state)
        self._saved = (
            CardInfo.MISSING_CARDS_PATH,
            CardInfo.CARD_DATA_PATH,
            CardInfo._card_data,
            CardInfo._fetch_card_info_with_status,
            CardInfo._resource_data_path,
        )
        CardInfo.MISSING_CARDS_PATH = self.missing_path
        CardInfo.CARD_DATA_PATH = self.cards_path
        CardInfo._card_data = []
        # No bundled fallback file may leak into these tests.
        CardInfo._resource_data_path = lambda name: os.path.join(self.tmp, "__absent__", name)
        self.calls = []

    def tearDown(self):
        (
            CardInfo.MISSING_CARDS_PATH,
            CardInfo.CARD_DATA_PATH,
            CardInfo._card_data,
            CardInfo._fetch_card_info_with_status,
            CardInfo._resource_data_path,
        ) = self._saved
        CardInfo._card_data_index, CardInfo._card_data_index_state = self._saved_index

    def stub_fetch(self, responder):
        def fetch(arena_id):
            self.calls.append(arena_id)
            return responder(arena_id)
        CardInfo._fetch_card_info_with_status = fetch

    def write_missing(self, payload):
        with open(self.missing_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def read_missing(self):
        with open(self.missing_path, "r", encoding="utf-8") as f:
            return json.load(f)


class NotFoundBackoffTests(_MissingCardsTestBase):
    def test_a_404_is_recorded_and_not_retried_on_the_next_run(self):
        self.write_missing([111])
        self.stub_fetch(lambda _id: (None, "not_found"))

        CardInfo.refresh_missing_cards()
        self.assertEqual(self.calls, [111], "first run must still ask once")
        stored = self.read_missing()
        self.assertEqual(stored["111"]["status"], "not_found")
        self.assertGreater(stored["111"]["last_try"], 0)

        # Second start: still tracked, but no request goes out.
        self.calls.clear()
        CardInfo.refresh_missing_cards()
        self.assertEqual(self.calls, [], "a recorded 404 must not be re-requested")
        self.assertIn("111", self.read_missing())

    def test_the_backoff_expires(self):
        import time as _t
        stale = _t.time() - CardInfo._MISSING_NOT_FOUND_RETRY_SEC - 1
        self.write_missing({"111": {"status": "not_found", "last_try": stale}})
        self.stub_fetch(lambda _id: (None, "not_found"))
        CardInfo.refresh_missing_cards()
        self.assertEqual(self.calls, [111], "an expired backoff must be asked again")

    def test_a_future_timestamp_is_treated_as_due(self):
        """A clock that jumped backwards must not park an entry indefinitely."""
        import time as _t
        self.write_missing({"111": {"status": "not_found", "last_try": _t.time() + 99999}})
        self.stub_fetch(lambda _id: (None, "not_found"))
        CardInfo.refresh_missing_cards()
        self.assertEqual(self.calls, [111])

    def test_transient_failures_stay_due(self):
        """No network says nothing about the card -- retry on the next start."""
        self.write_missing([111])
        self.stub_fetch(lambda _id: (None, "error"))
        CardInfo.refresh_missing_cards()
        self.calls.clear()
        CardInfo.refresh_missing_cards()
        self.assertEqual(self.calls, [111])


class ResolutionTests(_MissingCardsTestBase):
    def test_a_resolved_card_leaves_the_list_and_lands_in_cards_json(self):
        self.write_missing([111])
        self.stub_fetch(lambda aid: ({"grpId": aid, "name": "Found"}, "ok"))
        CardInfo.refresh_missing_cards()
        self.assertEqual(self.read_missing(), {})
        with open(self.cards_path, "r", encoding="utf-8") as f:
            self.assertEqual([c["grpId"] for c in json.load(f)], [111])

    def test_ids_the_local_export_now_carries_are_dropped_without_a_request(self):
        """An Arena patch adding the card locally is the usual way these resolve."""
        CardInfo._card_data = [{"grpId": 111, "name": "Now Local"}]
        self.write_missing([111])
        self.stub_fetch(lambda _id: (None, "not_found"))
        CardInfo.refresh_missing_cards()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.read_missing(), {})

    def test_recording_a_missing_id_does_not_rearm_an_existing_backoff(self):
        """get_card_info records the same ID on every board that contains it."""
        self.write_missing({"111": {"status": "not_found", "last_try": 12345.0}})
        CardInfo._record_missing_card(111)
        self.assertEqual(self.read_missing()["111"]["last_try"], 12345.0)

    def test_recording_a_new_id_adds_it_as_due(self):
        self.write_missing({})
        CardInfo._record_missing_card(222)
        self.assertEqual(self.read_missing(), {"222": {}})


class BudgetAndFormatTests(_MissingCardsTestBase):
    def test_the_pass_stops_at_its_time_budget(self):
        """With Scryfall unreachable every request sits out its full timeout, so
        an unbounded pass over a long list would stall startup for minutes."""
        import time as _t
        self.write_missing(list(range(100, 140)))
        slept = {"n": 0}

        def slow(_id):
            slept["n"] += 1
            # Simulate a request that burns a big slice of the budget.
            _t.sleep(0.01)
            return (None, "error")

        original_budget = CardInfo._MISSING_REFRESH_BUDGET_SEC
        CardInfo._MISSING_REFRESH_BUDGET_SEC = 0.05
        try:
            self.stub_fetch(slow)
            CardInfo.refresh_missing_cards()
        finally:
            CardInfo._MISSING_REFRESH_BUDGET_SEC = original_budget
        self.assertLess(len(self.calls), 40, "the pass must stop before the list ends")
        # Nothing is lost: everything not reached is still tracked.
        self.assertEqual(len(self.read_missing()), 40)

    def test_the_budget_rotates_instead_of_starving_the_tail(self):
        """With Scryfall down every entry stays due. A fixed numeric order would
        spend the whole budget on the same lowest IDs at every start, so the rest
        would never be tried at all -- least-recently-tried first rotates."""
        import time as _t
        now = _t.time()
        self.write_missing({
            "10": {"status": "error", "last_try": now},        # just tried
            "20": {"status": "error", "last_try": now - 600},  # older
            "30": {},                                          # never tried
        })
        self.stub_fetch(lambda _id: (None, "error"))
        original = CardInfo._MISSING_REFRESH_BUDGET_SEC
        CardInfo._MISSING_REFRESH_BUDGET_SEC = 1000.0  # no cut-off, order only
        try:
            CardInfo.refresh_missing_cards()
        finally:
            CardInfo._MISSING_REFRESH_BUDGET_SEC = original
        self.assertEqual(
            self.calls, [30, 20, 10],
            "never-tried first, then oldest -- not lowest id first",
        )

    def test_the_legacy_list_format_still_loads(self):
        self.write_missing([5, 7, 9])
        self.assertEqual(
            CardInfo._load_missing_entries(), {"5": {}, "7": {}, "9": {}}
        )

    def test_entries_are_written_in_numeric_order(self):
        self.write_missing([30, 4, 200])
        self.stub_fetch(lambda _id: (None, "not_found"))
        CardInfo.refresh_missing_cards()
        self.assertEqual(list(self.read_missing().keys()), ["4", "30", "200"])


class FetchStatusClassificationTests(unittest.TestCase):
    """The one thing the rest of this file stubs out: which HTTP outcome becomes
    "not_found" (30-day backoff) and which becomes "error" (retry next start).

    Getting this wrong is the expensive failure -- a rate-limited or offline run
    misfiled as "not_found" takes a card the bot really needs out of circulation
    for a month -- and it is invisible to every test that stubs the fetch."""

    def setUp(self):
        self._saved_urlopen = CardInfo.urllib.request.urlopen
        # The cooldown is module-global and keyed by arena id; start each test
        # from a clean slate and restore whatever was there.
        self._saved_cooldown = dict(CardInfo._transient_failure_until)
        CardInfo._transient_failure_until.clear()

    def tearDown(self):
        CardInfo.urllib.request.urlopen = self._saved_urlopen
        CardInfo._transient_failure_until.clear()
        CardInfo._transient_failure_until.update(self._saved_cooldown)

    def _raise(self, exc):
        def urlopen(*_a, **_k):
            raise exc
        CardInfo.urllib.request.urlopen = urlopen

    def test_404_is_not_found(self):
        self._raise(urllib.error.HTTPError("u", 404, "Not Found", {}, None))
        card, status = CardInfo._fetch_card_info_with_status(1)
        self.assertIsNone(card)
        self.assertEqual(status, "not_found")
        self.assertFalse(
            CardInfo._transient_failure_active("1"),
            "a 404 is a real answer, not a transient failure",
        )

    def test_rate_limit_is_an_error_not_a_404(self):
        """429 must never start a 30-day backoff -- the card may well exist."""
        self._raise(urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None))
        self.assertEqual(CardInfo._fetch_card_info_with_status(2)[1], "error")

    def test_server_error_is_an_error(self):
        self._raise(urllib.error.HTTPError("u", 500, "Server Error", {}, None))
        self.assertEqual(CardInfo._fetch_card_info_with_status(3)[1], "error")

    def test_no_network_is_an_error(self):
        self._raise(urllib.error.URLError("no route to host"))
        self.assertEqual(CardInfo._fetch_card_info_with_status(4)[1], "error")

    def test_unparseable_payload_is_an_error(self):
        """A captive portal answering 200 with HTML must not look like a card."""
        class _Resp:
            def read(self):
                return b"<html>login</html>"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        CardInfo.urllib.request.urlopen = lambda *a, **k: _Resp()
        self.assertEqual(CardInfo._fetch_card_info_with_status(5)[1], "error")

    def test_the_cooldown_shortcut_reports_error_not_not_found(self):
        """While the in-memory cooldown is active no request goes out at all --
        that silence says nothing about the card and must not be filed as a 404."""
        CardInfo._mark_transient_failure("6")
        called = []
        CardInfo.urllib.request.urlopen = lambda *a, **k: called.append(1)
        card, status = CardInfo._fetch_card_info_with_status(6)
        self.assertEqual(called, [], "the cooldown must short-circuit the request")
        self.assertIsNone(card)
        self.assertEqual(status, "error")

    def test_a_hit_is_ok(self):
        payload = {
            "oracle_id": "abc", "mana_cost": "{1}{G}", "colors": ["G"],
            "type_line": "Creature - Elf", "set": "dom", "rarity": "common",
            "name": "Llanowar Elves", "oracle_text": "T: Add G.", "keywords": [],
        }

        class _Resp:
            def read(self):
                return json.dumps(payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        CardInfo.urllib.request.urlopen = lambda *a, **k: _Resp()
        card, status = CardInfo._fetch_card_info_with_status(7)
        self.assertEqual(status, "ok")
        self.assertEqual(card["name"], "Llanowar Elves")
        self.assertEqual(card["grpId"], 7)


class RecordMissingRobustnessTests(_MissingCardsTestBase):
    """_record_missing_card runs on the in-match decision path (get_card_info is
    reached from DummyAI with a grpId that can legitimately be None). A bookkeeping
    write must never break the move being decided -- the load-append-save it
    replaced swallowed a bad ID inside _save_missing_cards' except."""

    def test_a_non_numeric_id_is_ignored_instead_of_raising(self):
        self.write_missing({})
        for bad in (None, "", "abc", object()):
            CardInfo._record_missing_card(bad)
        self.assertEqual(self.read_missing(), {})

    def test_a_404_seen_in_match_starts_its_backoff_immediately(self):
        """Otherwise the next start pays one more request to learn what we know."""
        self.write_missing({})
        CardInfo._record_missing_card(555, "not_found")
        entry = self.read_missing()["555"]
        self.assertEqual(entry["status"], "not_found")
        self.assertGreater(entry["last_try"], 0)

    def test_a_transient_miss_in_match_stays_due(self):
        self.write_missing({})
        CardInfo._record_missing_card(556, "error")
        self.assertEqual(self.read_missing(), {"556": {}})


if __name__ == "__main__":
    unittest.main()
