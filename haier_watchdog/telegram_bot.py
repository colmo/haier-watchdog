"""Bot de Telegram: alerta con botones y espera de confirmación."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import NetworkError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from haier_watchdog.config import Config
from haier_watchdog.hon_client import AcStatus

_LOGGER = logging.getLogger(__name__)


class _QuietTelegramNetwork(logging.Filter):
    """El updater de PTB loguea Bad Gateway con traceback en cada reintento."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, NetworkError):
            _LOGGER.warning("Telegram sin red (%s); el polling reintenta solo", exc)
            return False
        return True


def _quiet_telegram_network_logs() -> None:
    # El filtro no se hereda: el traceback sale del logger telegram.ext.Updater.
    for name in ("telegram.ext", "telegram.ext.Updater"):
        log = logging.getLogger(name)
        if any(isinstance(item, _QuietTelegramNetwork) for item in log.filters):
            continue
        log.addFilter(_QuietTelegramNetwork())


Decision = Literal["yes", "no"]
StatusProvider = Callable[[], Awaitable[AcStatus]]


class TelegramBot:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._app = (
            Application.builder().token(config.telegram_bot_token).build()
        )
        self._sessions: dict[str, asyncio.Future[Decision]] = {}
        self._status_provider: StatusProvider | None = None
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("status", self._on_status))
        self._app.add_handler(CallbackQueryHandler(self._on_callback, pattern=r"^ac:"))
        self._app.add_error_handler(self._on_error)
        _quiet_telegram_network_logs()

    def set_status_provider(self, provider: StatusProvider) -> None:
        self._status_provider = provider

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        if isinstance(err, NetworkError):
            _LOGGER.warning("Telegram sin red (%s); el polling reintenta solo", err)
            return
        _LOGGER.error("Error de Telegram: %s", err, exc_info=err)

    async def start(self) -> None:
        await self._app.initialize()
        await self._app.start()
        if self._app.updater is None:
            raise RuntimeError("python-telegram-bot no ha creado el updater")
        await self._app.updater.start_polling(drop_pending_updates=True)
        _LOGGER.info("Bot de Telegram en marcha")

    async def stop(self) -> None:
        for future in list(self._sessions.values()):
            if not future.done():
                future.cancel()
        try:
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            if self._app.running:
                await self._app.stop()
            await self._app.shutdown()
        except Exception:
            _LOGGER.debug("Parada de Telegram incompleta", exc_info=True)

    def _allowed(self, chat_id: int | None) -> bool:
        if self._config.telegram_chat_id is None:
            return False
        return chat_id == self._config.telegram_chat_id

    async def _on_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if update.effective_chat is None or update.message is None:
            return
        chat_id = update.effective_chat.id
        text = (
            f"Tu chat_id es <code>{chat_id}</code>.\n"
            "Cópialo a TELEGRAM_CHAT_ID en el .env y reinicia el watchdog."
        )
        if self._allowed(chat_id):
            text += (
                "\n\nEste chat ya está autorizado. "
                "Cuando el aire se encienda te preguntaré aquí."
            )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def _on_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if update.message is None or update.effective_chat is None:
            return
        if not self._allowed(update.effective_chat.id):
            await update.message.reply_text("Este chat no está autorizado.")
            return
        if self._status_provider is None:
            await update.message.reply_text("El watchdog aún no está listo.")
            return
        try:
            status = await self._status_provider()
        except Exception:
            _LOGGER.exception("Error al consultar /status")
            await update.message.reply_text(
                "No he podido leer el aire (error de hOn). Mira los logs."
            )
            return
        await update.message.reply_text(
            status.format_report(timezone=self._config.timezone),
            parse_mode=ParseMode.HTML,
        )

    async def _on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None or query.data is None:
            return
        chat_id = query.message.chat_id if query.message else None
        if not self._allowed(chat_id):
            await query.answer("Este chat no está autorizado.")
            return
        parts = query.data.split(":", 2)
        if len(parts) != 3 or parts[0] != "ac" or parts[1] not in {"yes", "no"}:
            await query.answer()
            return
        decision: Decision = "yes" if parts[1] == "yes" else "no"
        session_id = parts[2]
        future = self._sessions.get(session_id)
        if future is None or future.done():
            await query.answer("Esta pregunta ya no está activa.")
            return
        future.set_result(decision)
        await query.answer("Hecho")

    async def ask_confirmation(
        self, status: AcStatus, guess: dict | None = None
    ) -> Decision | None:
        if self._config.telegram_chat_id is None:
            raise RuntimeError("Falta TELEGRAM_CHAT_ID")
        session_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Decision] = loop.create_future()
        self._sessions[session_id] = future
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Sí, fui yo", callback_data=f"ac:yes:{session_id}"
                    ),
                    InlineKeyboardButton(
                        "No, apágalo", callback_data=f"ac:no:{session_id}"
                    ),
                ]
            ]
        )
        text = status.format_message(
            timeout_seconds=self._config.confirm_timeout_seconds,
            timezone=self._config.timezone,
            guess=guess,
        )
        message = None
        try:
            message = await self._app.bot.send_message(
                chat_id=self._config.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            try:
                return await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=self._config.confirm_timeout_seconds,
                )
            except asyncio.TimeoutError:
                return None
        finally:
            self._sessions.pop(session_id, None)
            if message is not None:
                try:
                    await message.edit_reply_markup(reply_markup=None)
                except Exception:
                    _LOGGER.debug("No se pudieron quitar los botones", exc_info=True)

    async def notify(self, text: str) -> None:
        if self._config.telegram_chat_id is None:
            _LOGGER.warning("Sin TELEGRAM_CHAT_ID; no se envía: %s", text)
            return
        await self._app.bot.send_message(
            chat_id=self._config.telegram_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
