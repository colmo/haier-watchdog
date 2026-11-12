import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pyhon.exceptions import HonAuthenticationError
from telegram.error import NetworkError

from haier_watchdog import __version__
from haier_watchdog.config import Config
from haier_watchdog.hon_client import HonClient
from haier_watchdog.telegram_bot import _QuietTelegramNetwork, _quiet_telegram_network_logs
from haier_watchdog.watchdog import (
    HON_RETRY_MAX_FAILURES,
    HON_RETRY_START_SECONDS,
    Watchdog,
)


def _config() -> Config:
    return Config(
        hon_email="a@b.c",
        hon_password="x",
        hon_appliance="",
        telegram_bot_token="t",
        telegram_chat_id=1,
        poll_interval_seconds=900,
        confirm_timeout_seconds=300,
        dry_run=True,
        timezone="Europe/Madrid",
        log_dir=Path("/tmp"),
    )


class FetchStatusReconnectTest(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_connects_before_update(self) -> None:
        client = HonClient(_config())
        appliance = AsyncMock()
        order: list[str] = []

        async def connect() -> None:
            order.append("connect")
            client._appliance = appliance

        async def update(*, force: bool = True) -> None:
            order.append("update")

        client.connect = connect  # type: ignore[method-assign]
        appliance.update = update
        with patch("haier_watchdog.hon_client.read_status", return_value="ok"):
            result = await client.fetch_status()
        self.assertEqual(result, "ok")
        self.assertEqual(order, ["connect", "update"])
        self.assertTrue(client.connected)

    async def test_update_failure_reconnects_instead_of_staying_down(self) -> None:
        client = HonClient(_config())
        appliance = AsyncMock()
        order: list[str] = []
        client._appliance = appliance

        async def connect() -> None:
            order.append("connect")
            client._appliance = appliance

        updates = 0

        async def update(*, force: bool = True) -> None:
            nonlocal updates
            updates += 1
            order.append("update")
            if updates == 1:
                raise RuntimeError("mqtt colgado")

        client.connect = connect  # type: ignore[method-assign]
        appliance.update = update
        with patch("haier_watchdog.hon_client.read_status", return_value="ok"):
            result = await client.fetch_status()
        self.assertEqual(result, "ok")
        self.assertEqual(order, ["update", "connect", "update"])


class WatchdogLoginBackoffTest(unittest.IsolatedAsyncioTestCase):
    def _watchdog(self, hon: object) -> Watchdog:
        return Watchdog(_config(), hon, telegram=None, events=None)  # type: ignore[arg-type]

    async def test_session_errors_backoff_then_exit(self) -> None:
        class _Down:
            async def fetch_status(self) -> None:
                raise HonAuthenticationError("502")

        watchdog = self._watchdog(_Down())
        for attempt in range(1, HON_RETRY_MAX_FAILURES):
            await watchdog._safe_tick(use_cache=False, trigger="http_poll")
            self.assertEqual(watchdog._hon_failures, attempt)
            expected = min(HON_RETRY_START_SECONDS * (2 ** (attempt - 1)), 300)
            self.assertEqual(watchdog._reconnect_delay, expected)

        with self.assertRaises(SystemExit) as caught:
            await watchdog._safe_tick(use_cache=False, trigger="http_poll")
        self.assertEqual(caught.exception.code, 1)

    async def test_success_clears_the_failure_streak(self) -> None:
        class _Flaky:
            def __init__(self) -> None:
                self.calls = 0

            async def fetch_status(self) -> SimpleNamespace:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("hOn no está conectado")
                return SimpleNamespace(is_on=False, name="AC")

        watchdog = self._watchdog(_Flaky())
        await watchdog._safe_tick(use_cache=False, trigger="http_poll")
        self.assertEqual(watchdog._hon_failures, 1)
        await watchdog._safe_tick(use_cache=False, trigger="http_poll")
        self.assertEqual(watchdog._hon_failures, 0)
        self.assertIsNone(watchdog._reconnect_delay)

    async def test_other_errors_do_not_count_toward_exit(self) -> None:
        class _Bug:
            async def fetch_status(self) -> None:
                raise ValueError("dato raro")

        watchdog = self._watchdog(_Bug())
        with self.assertLogs("haier_watchdog.watchdog", level="ERROR"):
            await watchdog._safe_tick(use_cache=False, trigger="http_poll")
        self.assertEqual(watchdog._hon_failures, 0)
        self.assertIsNone(watchdog._reconnect_delay)

    async def test_reconnect_wait_uses_backoff_not_the_poll_interval(self) -> None:
        watchdog = self._watchdog(object())
        watchdog._reconnect_delay = 0.01
        started = asyncio.get_running_loop().time()
        reason = await watchdog._wait_wake_or_poll(asyncio.Event())
        elapsed = asyncio.get_running_loop().time() - started
        self.assertEqual(reason, "poll")
        self.assertLess(elapsed, 1.0)
        self.assertIsNone(watchdog._reconnect_delay)


class TelegramNetworkLogTest(unittest.TestCase):
    def test_network_error_is_one_warning_without_the_library_record(self) -> None:
        record = logging.LogRecord(
            name="telegram.ext.Updater",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Exception happened while polling for updates.",
            args=(),
            exc_info=(NetworkError, NetworkError("Bad Gateway"), None),
        )
        with self.assertLogs("haier_watchdog.telegram_bot", level="WARNING") as captured:
            self.assertFalse(_QuietTelegramNetwork().filter(record))
        self.assertEqual(len(captured.records), 1)
        self.assertIn("Bad Gateway", captured.records[0].getMessage())
        self.assertFalse(captured.records[0].exc_info)

    def test_filter_is_attached_to_the_updater_logger(self) -> None:
        _quiet_telegram_network_logs()
        log = logging.getLogger("telegram.ext.Updater")
        self.assertTrue(any(isinstance(item, _QuietTelegramNetwork) for item in log.filters))

    def test_version(self) -> None:
        self.assertEqual(__version__, "1.0.3")
