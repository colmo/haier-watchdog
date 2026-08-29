"""Bucle de vigilancia: MQTT al instante, poll HTTP de respaldo."""

from __future__ import annotations

import asyncio
import logging
import uuid

from haier_watchdog import mqtt_capture
from haier_watchdog.config import Config
from haier_watchdog.diagnose import (
    guess_source,
    mqtt_changed_params,
    recent_cloud_commands,
    slim_history_item,
)
from haier_watchdog.event_log import EventLog
from haier_watchdog.hon_client import AcStatus, HonClient, _to_int
from haier_watchdog.state import WatchdogState, apply_poll
from haier_watchdog.telegram_bot import TelegramBot

_LOGGER = logging.getLogger(__name__)

# Varios parámetros llegan en ráfaga al encender; esperar un poco para el mensaje.
MQTT_DEBOUNCE_SECONDS = 0.8


class Watchdog:
    def __init__(
        self,
        config: Config,
        hon: HonClient,
        telegram: TelegramBot,
        events: EventLog,
    ) -> None:
        self._config = config
        self._hon = hon
        self._telegram = telegram
        self._events = events
        self._state = WatchdogState()
        self._asking = False
        self._wake = asyncio.Event()
        self._tick_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _on_mqtt_update(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._wake.set)

    async def run_forever(self, stop: asyncio.Event) -> None:
        self._loop = asyncio.get_running_loop()
        self._hon.subscribe_updates(self._on_mqtt_update)
        _LOGGER.info(
            "Watchdog activo (mqtt=inmediato, poll=%ss, timeout=%ss, dry_run=%s, log=%s)",
            self._config.poll_interval_seconds,
            self._config.confirm_timeout_seconds,
            self._config.dry_run,
            self._events.path,
        )
        self._events.write(
            "watchdog_start",
            {
                "poll_interval_seconds": self._config.poll_interval_seconds,
                "confirm_timeout_seconds": self._config.confirm_timeout_seconds,
                "dry_run": self._config.dry_run,
            },
        )
        await self._safe_tick(use_cache=False, trigger="startup")
        try:
            while not stop.is_set():
                reason = await self._wait_wake_or_poll(stop)
                if reason == "stop" or stop.is_set():
                    break
                if reason == "mqtt":
                    await asyncio.sleep(MQTT_DEBOUNCE_SECONDS)
                    self._wake.clear()
                    await self._safe_tick(use_cache=True, trigger="mqtt")
                else:
                    await self._safe_tick(use_cache=False, trigger="http_poll")
        finally:
            self._events.write("watchdog_stop", {})

    async def _wait_wake_or_poll(self, stop: asyncio.Event) -> str:
        if stop.is_set():
            return "stop"
        if self._wake.is_set():
            return "mqtt"
        stop_task = asyncio.create_task(stop.wait())
        wake_task = asyncio.create_task(self._wake.wait())
        try:
            done, pending = await asyncio.wait(
                {stop_task, wake_task},
                timeout=self._config.poll_interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if stop.is_set():
                return "stop"
            if wake_task in done:
                return "mqtt"
            return "poll"
        finally:
            for task in (stop_task, wake_task):
                if not task.done():
                    task.cancel()

    async def _safe_tick(self, *, use_cache: bool, trigger: str) -> None:
        try:
            await self._tick(use_cache=use_cache, trigger=trigger)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error en el ciclo de vigilancia")

    async def _tick(self, *, use_cache: bool, trigger: str) -> None:
        async with self._tick_lock:
            if self._asking:
                return
            if use_cache:
                _LOGGER.info("Estado por MQTT")
                status = self._hon.read_cached_status()
            else:
                _LOGGER.info("Estado por HTTP (poll)")
                status = await self._hon.fetch_status()
            was_on = self._state.last_was_on
            new_state, should_ask = apply_poll(self._state, status.is_on)
            rising = status.is_on and was_on is not True
            falling = was_on is True and not status.is_on
            self._state = new_state
            if falling:
                self._log_off(status, trigger)
            event_id: str | None = None
            guess: dict | None = None
            if rising:
                event_id = uuid.uuid4().hex
                guess = await self._log_turn_on(
                    event_id=event_id, trigger=trigger, status=status
                )
            if not status.is_on:
                _LOGGER.info("%s apagado", status.name)
                return
            if self._state.authorized_until_off:
                _LOGGER.info("%s encendido (autorizado hasta que se apague)", status.name)
                return
            if not should_ask:
                _LOGGER.info("%s encendido (ya preguntado en este ciclo)", status.name)
                return
            _LOGGER.info("%s encendido inesperado; se pregunta por Telegram", status.name)
            await self._ask_and_act(status, event_id, guess=guess)

    def _log_off(self, status: AcStatus, trigger: str) -> None:
        _LOGGER.info("%s flanco apagado (%s)", status.name, trigger)
        self._events.write(
            "ac_off",
            {
                "trigger": trigger,
                "status": _status_payload(status),
            },
        )

    async def _log_turn_on(
        self, *, event_id: str, trigger: str, status: AcStatus
    ) -> dict:
        try:
            snapshot = self._hon.snapshot()
        except Exception:
            _LOGGER.exception("Snapshot local falló")
            snapshot = {}
        mqtt_events = mqtt_capture.recent(mac=status.mac, seconds=60)
        mqtt_params = mqtt_changed_params(mqtt_events)
        cloud: dict = {}
        try:
            cloud = await self._hon.fetch_cloud_diagnostics()
        except Exception as exc:
            _LOGGER.exception("Diagnóstico nube falló")
            cloud = {"errors": {"fetch": str(exc)}, "history": [], "last_activity": {}}
        history = cloud.get("history") or []
        tz = self._config.timezone
        recent = recent_cloud_commands(history, timezone_name=tz)
        activity = cloud.get("last_activity") or {}
        operation_source = _to_int(status.raw.get("operationSource"))
        if operation_source is None:
            operation_source = _to_int(
                (snapshot.get("key_params") or {})
                .get("operationSource", {})
                .get("value")
            )
        guess = guess_source(
            operation_source=operation_source,
            last_activity=activity if isinstance(activity, dict) else {},
            recent_commands=recent,
            mqtt_params=mqtt_params,
        )
        self._events.write(
            "ac_on",
            {
                "event_id": event_id,
                "trigger": trigger,
                "guess": guess,
                "status": _status_payload(status),
                "snapshot": snapshot,
                "mqtt": mqtt_events,
                "mqtt_params_changed": mqtt_params,
                "command_history_recent": recent,
                "command_history": [
                    slim_history_item(item, tz)
                    for item in history
                    if isinstance(item, dict)
                ],
                "last_activity": activity,
                "cloud_errors": cloud.get("errors") or {},
                "snapshot_after_http": cloud.get("snapshot_after_http"),
            },
        )
        _LOGGER.info(
            "ENCENDIDO event_id=%s origen=%s confianza=%s trigger=%s "
            "indoor=%s outdoor=%s target=%s mqtt_params=%s",
            event_id,
            guess.get("label"),
            guess.get("confidence"),
            trigger,
            status.indoor_temp,
            status.outdoor_temp,
            status.target_temp,
            ",".join(mqtt_params) or "-",
        )
        return guess

    async def _ask_and_act(
        self, status: AcStatus, event_id: str | None, *, guess: dict | None = None
    ) -> None:
        self._asking = True
        try:
            decision = await self._telegram.ask_confirmation(status, guess=guess)
            live = await self._hon.fetch_status()
            if decision == "yes":
                if live.is_on:
                    self._state = WatchdogState(
                        last_was_on=True, authorized_until_off=True
                    )
                    self._log_decision(
                        event_id, "yes", live, action="left_on"
                    )
                    await self._telegram.notify(
                        "De acuerdo: lo dejo encendido. "
                        "No volveré a preguntar hasta que se apague y se encienda otra vez."
                    )
                else:
                    self._state = WatchdogState(
                        last_was_on=False, authorized_until_off=False
                    )
                    self._log_decision(
                        event_id, "yes", live, action="already_off"
                    )
                    await self._telegram.notify(
                        "Ha quedado apagado mientras respondías; no lo autorizo."
                    )
                return

            reason = (
                "Sin respuesta en el tiempo límite."
                if decision is None
                else "Has indicado que no lo has encendido tú."
            )
            decision_label = "timeout" if decision is None else "no"
            if not live.is_on:
                self._state = WatchdogState(
                    last_was_on=False, authorized_until_off=False
                )
                self._log_decision(
                    event_id, decision_label, live, action="already_off"
                )
                await self._telegram.notify(
                    f"{reason} El aire ya estaba apagado; no hago nada."
                )
                return
            try:
                method = await self._hon.turn_off()
            except Exception:
                _LOGGER.exception("Fallo al apagar el aire")
                self._state = WatchdogState(
                    last_was_on=False, authorized_until_off=False
                )
                self._log_decision(
                    event_id, decision_label, live, action="off_failed"
                )
                await self._telegram.notify(
                    f"{reason} No he podido apagarlo. Lo intentaré de nuevo "
                    "si sigue encendido."
                )
                return
            extra = (
                " (simulado, DRY_RUN)"
                if method == "dry_run"
                else ""
            )
            self._state = WatchdogState(
                last_was_on=True, authorized_until_off=False
            )
            self._log_decision(
                event_id,
                decision_label,
                live,
                action="turned_off",
                extra={"method": method},
            )
            await self._telegram.notify(f"{reason} Aire apagado{extra}.")
        finally:
            self._asking = False

    def _log_decision(
        self,
        event_id: str | None,
        decision: str,
        live: AcStatus,
        *,
        action: str,
        extra: dict | None = None,
    ) -> None:
        payload = {
            "event_id": event_id,
            "decision": decision,
            "action": action,
            "still_on": live.is_on,
            "status": _status_payload(live),
        }
        if extra:
            payload.update(extra)
        self._events.write("telegram_decision", payload)


def _status_payload(status: AcStatus) -> dict:
    return {
        "name": status.name,
        "model": status.model,
        "mac": status.mac,
        "is_on": status.is_on,
        "connected": status.connected,
        "mode": status.mode,
        "target_temp": status.target_temp,
        "indoor_temp": status.indoor_temp,
        "outdoor_temp": status.outdoor_temp,
        "fan": status.fan,
        "extras": status.extras,
        "observed_at": status.observed_at.isoformat() if status.observed_at else None,
    }
