import json
import unittest

from tools import screen_pr


class _FakeGh:
    """Scripted `gh`: the dispatch makes run 42 appear in the run list."""

    def __init__(self, labels, watch_fails=False, run_appears=True):
        self.labels = labels
        self.watch_fails = watch_fails
        self.run_appears = run_appears
        self.dispatched = False
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[:2] == ["run", "list"]:
            ids = [41] + ([42] if self.dispatched and self.run_appears else [])
            return json.dumps([{"databaseId": i} for i in ids])
        if args[:2] == ["workflow", "run"]:
            self.dispatched = True
            return ""
        if args[:2] == ["run", "watch"]:
            if self.watch_fails:
                raise RuntimeError("gh run failed")
            return ""
        if args[:2] == ["run", "view"]:
            return ("screen\tScreen and label\t2026-09-29T12:00:00Z rescreen: #66 body: {\"flagged\": false}\n"
                    "screen\tScreen and label\t2026-09-29T12:00:01Z rescreen: #66 1 item(s), 0 flagged\n"
                    "screen\tScreen and label\t2026-09-29T12:00:01Z unrelated line\n")
        if args[0] == "api":
            return json.dumps(self.labels)
        raise AssertionError(f"unexpected gh call {args}")


class ScreenPrTest(unittest.TestCase):
    def _run(self, gh):
        lines = []
        ticks = iter(range(0, 1000, 5))
        code = screen_pr.screen_pr(66, gh=gh, sleep=lambda _s: None, clock=lambda: next(ticks), out=lines.append)
        return code, "\n".join(lines)

    def test_clean_thread_exits_zero(self):
        gh = _FakeGh(["bug", "injection-screened"])
        code, out = self._run(gh)
        self.assertEqual(code, 0)
        self.assertIn(["workflow", "run", "injection-screen.yml", "--ref", "main", "-f", "number=66"], gh.calls)
        self.assertIn(["run", "watch", "42", "--exit-status"], gh.calls)
        self.assertIn("rescreen: #66 1 item(s), 0 flagged", out)
        self.assertNotIn("unrelated line", out)

    def test_warning_label_wins_over_screened(self):
        for warning in ("possible-injection", "injection-unscreened"):
            with self.subTest(warning=warning):
                code, out = self._run(_FakeGh([warning, "injection-screened"]))
                self.assertEqual(code, 2)
                self.assertIn("ask", out)

    def test_no_screen_label_after_run_is_an_error(self):
        self.assertEqual(self._run(_FakeGh(["bug"]))[0], 1)

    def test_failed_run_is_an_error(self):
        self.assertEqual(self._run(_FakeGh(["injection-screened"], watch_fails=True))[0], 1)

    def test_run_that_never_appears_raises(self):
        with self.assertRaises(RuntimeError):
            self._run(_FakeGh(["injection-screened"], run_appears=False))


class MainTest(unittest.TestCase):
    def test_rejects_non_numbers(self):
        for args in ([], ["abc"], ["66", "67"], ["66;x"]):
            with self.subTest(args=args):
                self.assertEqual(screen_pr.main(args), 1)


if __name__ == "__main__":
    unittest.main()
