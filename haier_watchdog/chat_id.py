"""Arranca el bot solo para mostrar tu chat_id de Telegram."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from haier_watchdog.config import load_config


async def _on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.message is None:
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"Tu chat_id es <code>{chat_id}</code>.\n"
        "Pónlo en TELEGRAM_CHAT_ID del .env y ya puedes parar este comando (Ctrl+C).",
        parse_mode=ParseMode.HTML,
    )
    print(f"TELEGRAM_CHAT_ID={chat_id}", flush=True)


async def async_main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = load_config(require_hon=False, require_telegram=True)
    except ValueError as exc:
        print(f"Configuración incompleta: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    app = Application.builder().token(config.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", _on_start))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    print(
        "Bot arrancado. Ábrelo en Telegram y pulsa /start. "
        "Cuando veas el chat_id, Ctrl+C.",
        flush=True,
    )
    await app.initialize()
    await app.start()
    if app.updater is None:
        raise RuntimeError("python-telegram-bot no ha creado el updater")
    await app.updater.start_polling(drop_pending_updates=True)
    try:
        await stop.wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
