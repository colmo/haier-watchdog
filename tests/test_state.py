import unittest

from haier_watchdog.state import WatchdogState, apply_poll


class ApplyPollTest(unittest.TestCase):
    def test_startup_off_does_not_ask(self) -> None:
        state, ask = apply_poll(WatchdogState(), False)
        self.assertFalse(ask)
        self.assertFalse(state.last_was_on)
        self.assertFalse(state.authorized_until_off)

    def test_startup_on_asks_once(self) -> None:
        state, ask = apply_poll(WatchdogState(), True)
        self.assertTrue(ask)
        self.assertTrue(state.last_was_on)
        self.assertFalse(state.authorized_until_off)

    def test_yes_does_not_reask_while_on(self) -> None:
        authorized = WatchdogState(last_was_on=True, authorized_until_off=True)
        for _ in range(5):
            authorized, ask = apply_poll(authorized, True)
            self.assertFalse(ask)
            self.assertTrue(authorized.authorized_until_off)

    def test_asks_again_only_after_off_then_on(self) -> None:
        state = WatchdogState(last_was_on=True, authorized_until_off=True)
        state, ask = apply_poll(state, False)
        self.assertFalse(ask)
        self.assertFalse(state.authorized_until_off)
        state, ask = apply_poll(state, True)
        self.assertTrue(ask)

    def test_still_on_does_not_reask(self) -> None:
        state = WatchdogState(last_was_on=True, authorized_until_off=False)
        state, ask = apply_poll(state, True)
        self.assertFalse(ask)

    def test_failed_off_asks_again_after_treating_as_off(self) -> None:
        state = WatchdogState(last_was_on=False, authorized_until_off=False)
        state, ask = apply_poll(state, True)
        self.assertTrue(ask)


if __name__ == "__main__":
    unittest.main()
