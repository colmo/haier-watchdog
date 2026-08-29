"""Carga de configuración desde .env y variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env", interpolate=False)


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un entero, recibido: {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    hon_email: str
    hon_password: str
    hon_appliance: str
    telegram_bot_token: str
    telegram_chat_id: int | None
    poll_interval_seconds: int
    confirm_timeout_seconds: int
    dry_run: bool
    timezone: str
    log_dir: Path

    @property
    def has_telegram_chat(self) -> bool:
        return self.telegram_chat_id is not None


def load_config(*, require_hon: bool = True, require_telegram: bool = True) -> Config:
    _load_env()
    email = (os.getenv("HON_EMAIL") or "").strip().lower()
    password = (os.getenv("HON_PASSWORD") or "").strip()
    if len(password) >= 2 and password[0] == password[-1] and password[0] in {"'", '"'}:
        password = password[1:-1]
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_raw = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chat_id: int | None = None
    if chat_raw:
        try:
            chat_id = int(chat_raw)
        except ValueError as exc:
            raise ValueError(
                f"TELEGRAM_CHAT_ID debe ser un número, recibido: {chat_raw!r}"
            ) from exc

    if require_hon and (not email or not password):
        raise ValueError("Faltan HON_EMAIL o HON_PASSWORD en .env")
    if require_telegram and not token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN en .env")
    if require_telegram and require_hon and chat_id is None:
        raise ValueError(
            "Falta TELEGRAM_CHAT_ID en .env. Ejecuta "
            "`python -m haier_watchdog.chat_id` y copia el número."
        )

    log_raw = (os.getenv("LOG_DIR") or "").strip()
    log_dir = Path(log_raw).expanduser() if log_raw else PROJECT_ROOT / "logs"

    return Config(
        hon_email=email,
        hon_password=password,
        hon_appliance=(os.getenv("HON_APPLIANCE") or "").strip(),
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        poll_interval_seconds=_as_int("POLL_INTERVAL_SECONDS", 900),
        confirm_timeout_seconds=_as_int("CONFIRM_TIMEOUT_SECONDS", 300),
        dry_run=_as_bool(os.getenv("DRY_RUN"), default=False),
        timezone=(os.getenv("TZ") or "Europe/Madrid").strip() or "Europe/Madrid",
        log_dir=log_dir,
    )
