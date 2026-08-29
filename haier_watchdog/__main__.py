"""Arranque del watchdog: Telegram + sesión hOn persistente."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from haier_watchdog.config import load_config
from haier_watchdog.event_log import EventLog, configure_file_logging
from haier_watchdog.hon_client import HonClient
from haier_watchdog.telegram_bot import TelegramBot
from haier_watchdog.watchdog import Watchdog

_LOGGER = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)


async def _connect_hon(hon: HonClient, stop: asyncio.Event) -> None:
    delay = 5
    while not stop.is_set():
        try:
            await hon.connect()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Login hOn falló; reintento en %s s", delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                delay = min(delay * 2, 300)


async def async_main() -> None:
    _configure_logging()
    try:
        config = load_config(require_hon=True, require_telegram=True)
    except ValueError as exc:
        print(f"Configuración incompleta: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    text_log = configure_file_logging(config.log_dir)
    events = EventLog(config.log_dir / "events.jsonl", config.timezone)
    _LOGGER.info("Log de texto: %s", text_log)
    _LOGGER.info("Log de eventos: %s", events.path)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    hon = HonClient(config)
    telegram = TelegramBot(config)
    telegram.set_status_provider(hon.fetch_status)
    watchdog = Watchdog(config, hon, telegram, events)

    await telegram.start()
    try:
        await _connect_hon(hon, stop)
        if stop.is_set():
            return
        if config.dry_run:
            await telegram.notify(
                "Watchdog Haier arrancado en <b>DRY_RUN</b>: avisaré pero no apagaré."
            )
        else:
            await telegram.notify("Watchdog Haier arrancado. Te avisaré si el aire se enciende.")
        await watchdog.run_forever(stop)
    finally:
        try:
            await telegram.stop()
        except Exception:
            _LOGGER.exception("Error al parar Telegram")
        await hon.close()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
