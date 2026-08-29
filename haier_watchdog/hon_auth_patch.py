"""Alinea el login CIAM con la app hOn actual (2.29.8)."""

from __future__ import annotations

from pyhon import const
from pyhon.connection.auth import HonAuth
from pyhon.connection.handler.auth import HonAuthConnectionHandler

_PATCHED = False

# Versión publicada en Play Store (ago 2026). pyhon-revived aún declara 2.27.9.
CURRENT_APP_VERSION = "2.29.8"
ANDROID_UA = (
    "hOn/2.29.8 (Linux; Android 14; Pixel 8) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return

    const.APP_VERSION = CURRENT_APP_VERSION
    HonAuthConnectionHandler._HEADERS = {
        "user-agent": ANDROID_UA,
        "x-api-key": const.API_KEY,
        "accept": "application/json",
    }

    async def _get_session_id(self: HonAuth, code_challenge: str) -> str:
        params = {
            "username": self._email,
            "password": self._password,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        async with self._request.get(
            f"{const.API_URL}/ciam/authorize", params=params
        ) as response:
            if response.status != 200:
                await self._error_logger(response)
                return ""
            session_id: str = (await response.json()).get("session_id", "")
            if not session_id:
                await self._error_logger(response)
            return session_id

    HonAuth._get_session_id = _get_session_id  # type: ignore[method-assign]
    _PATCHED = True
