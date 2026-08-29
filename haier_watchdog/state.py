"""Máquina de estados: una pregunta por encendido, no por cada poll."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WatchdogState:
    last_was_on: bool | None = None
    authorized_until_off: bool = False


def apply_poll(state: WatchdogState, is_on: bool) -> tuple[WatchdogState, bool]:
    """Devuelve el nuevo estado y si hay que preguntar por Telegram.

    Se pregunta como máximo una vez por cada flanco apagado→encendido
    (y si al arrancar ya está encendido). Los MQTT de temperatura u otros
    parámetros mientras sigue encendido no repreguntan.
    Tras un Sí, no se vuelve a preguntar hasta ver el AC apagado y un
    encendido posterior.
    """
    if not is_on:
        return WatchdogState(last_was_on=False, authorized_until_off=False), False
    if state.authorized_until_off:
        return WatchdogState(last_was_on=True, authorized_until_off=True), False
    rising_edge = state.last_was_on is not True
    return WatchdogState(last_was_on=True, authorized_until_off=False), rising_edge
