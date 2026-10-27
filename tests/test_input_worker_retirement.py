"""Revoked transactions must release their own reservation, never a newer one."""
import threading
import unittest
from unittest.mock import Mock

from Controller.Utilities.input_controller import ExclusiveInputController, NullInputController


class InputWorkerRetirementTest(unittest.TestCase):
    def setUp(self):
        self.raw = NullInputController()
        self.raw.left_click = Mock()
        self.gate = ExclusiveInputController(self.raw)

    def old_transaction(self):
        entered, finish = threading.Event(), threading.Event()
        acquired = []

        def work():
            with self.gate.input_transaction() as ok:
                acquired.append(ok)
                entered.set()
                finish.wait(2)
                self.gate.left_click()  # remains forbidden after revocation

        worker = threading.Thread(target=self.gate.worker_target(work))
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(finish.set)
        self.assertTrue(entered.wait(1))
        self.assertEqual(acquired, [True])
        return worker, finish

    def retire(self):
        self.gate.revoke_workers()
        self.gate.resume_input(adopt_current=True)

    def test_old_transaction_can_finish_after_revocation_without_leaking_lock(self):
        worker, finish = self.old_transaction()
        self.retire()
        self.gate.release_exclusive()
        finish.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.raw.left_click.assert_not_called()
        self.assertTrue(self.gate._gated("left_click", 1, timeout=.02))

    def test_new_transaction_does_not_wait_for_revoked_worker_and_keeps_ownership(self):
        worker, finish = self.old_transaction()
        self.retire()
        with self.gate.input_transaction(timeout=.02) as acquired:
            self.assertTrue(acquired)
            finish.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.raw.left_click.assert_not_called()
            self.assertEqual(self.gate._transaction_depth, 1)
            self.assertTrue(self.gate.left_click())
        self.assertEqual(self.gate._transaction_depth, 0)

    def test_new_direct_input_does_not_wait_for_revoked_transaction(self):
        worker, finish = self.old_transaction()
        self.retire()
        self.assertTrue(self.gate._gated("left_click", 1, timeout=.02))
        finish.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.raw.left_click.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
