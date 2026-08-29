"""Cliente de la API no oficial de hOn (pyhon-revived)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pyhon import Hon
from pyhon.appliance import HonAppliance

from haier_watchdog import hon_auth_patch, mqtt_capture
from haier_watchdog.config import Config
from haier_watchdog.diagnose import format_guess_es

hon_auth_patch.install()
mqtt_capture.install()

_LOGGER = logging.getLogger(__name__)

MACH_MODE_ES = {
    0: "Auto",
    1: "Frío",
    2: "Frío",
    3: "Deshumidificar",
    4: "Calor",
    5: "Ventilación",
    6: "Ventilación",
}

FAN_ES = {
    1: "Alto",
    2: "Medio",
    3: "Bajo",
    4: "Auto",
    5: "Auto",
}

HUMAN_SENSE_ES = {
    0: "desactivado",
    1: "evitar a la persona",
    2: "seguir a la persona",
}


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _is_on_value(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in {"0", "", "false", "off", "none"}


def _param_value(raw: Any) -> Any:
    return getattr(raw, "value", raw)


def appliance_parameters(appliance: HonAppliance) -> dict[str, Any]:
    params = appliance.attributes.get("parameters") or {}
    out: dict[str, Any] = {}
    for key, raw in params.items():
        out[str(key)] = _param_value(raw)
    return out


@dataclass
class AcStatus:
    name: str
    model: str
    mac: str
    appliance_type: str
    is_on: bool
    connected: bool
    mode: str | None
    target_temp: float | None
    indoor_temp: float | None
    outdoor_temp: float | None
    fan: str | None
    extras: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime | None = None

    def _stamp(self, timezone: str) -> str:
        when = self.observed_at or datetime.now()
        try:
            local = when.astimezone(ZoneInfo(timezone))
        except Exception:
            local = when.astimezone()
        return local.strftime("%H:%M:%S %d/%m/%Y")

    def format_report(self, *, timezone: str, include_title: bool = True) -> str:
        lines: list[str] = []
        if include_title:
            lines.append(f"<b>{_escape(self.name)}</b>")
        lines.append(f"Hora: {self._stamp(timezone)}")
        if self.model:
            lines.append(f"Modelo: {_escape(self.model)}")
        lines.append(f"Estado: {'encendido' if self.is_on else 'apagado'}")
        if self.mode:
            lines.append(f"Modo: {_escape(self.mode)}")
        if self.target_temp is not None:
            lines.append(f"Temp. objetivo: {self.target_temp:g} °C")
        if self.indoor_temp is not None:
            lines.append(f"Temp. ambiente: {self.indoor_temp:g} °C")
        if self.outdoor_temp is not None:
            lines.append(f"Temp. exterior: {self.outdoor_temp:g} °C")
        if self.fan:
            lines.append(f"Ventilador: {_escape(self.fan)}")
        for label, value in self.extras.items():
            lines.append(f"{label}: {_escape(value)}")
        if not self.connected:
            lines.append("Aviso: hOn marca el aparato como desconectado.")
        return "\n".join(lines)

    def format_message(
        self,
        *,
        timeout_seconds: int,
        timezone: str,
        guess: dict[str, Any] | None = None,
    ) -> str:
        minutes = max(1, round(timeout_seconds / 60))
        parts = [
            f"El aire <b>{_escape(self.name)}</b> se ha encendido.",
            "",
        ]
        guess_line = format_guess_es(guess)
        if guess_line:
            parts.append(_escape(guess_line))
            parts.append("")
        parts.extend(
            [
                self.format_report(timezone=timezone, include_title=False),
                "",
                "¿Has sido tú quien lo ha encendido?",
                f"Si no respondes en {minutes} min, o dices que no, lo apago.",
            ]
        )
        return "\n".join(parts)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def match_appliance(appliances: list[HonAppliance], selector: str) -> HonAppliance:
    acs = [a for a in appliances if (a.appliance_type or "").upper() == "AC"]
    pool = acs or list(appliances)
    if selector:
        needle = selector.strip().lower()
        matched = [
            a
            for a in pool
            if needle
            in {
                (a.nick_name or "").lower(),
                (a.model_name or "").lower(),
                (a.mac_address or "").lower().replace(":", "-"),
                (a.unique_id or "").lower(),
            }
            or needle in (a.nick_name or "").lower()
            or needle in (a.model_name or "").lower()
            or needle in (a.mac_address or "").lower()
        ]
        if len(matched) == 1:
            return matched[0]
        if not matched:
            names = ", ".join(_label(a) for a in pool) or "(ninguno)"
            raise RuntimeError(
                f"Ningún aparato coincide con HON_APPLIANCE={selector!r}. "
                f"Disponibles: {names}"
            )
        names = ", ".join(_label(a) for a in matched)
        raise RuntimeError(
            f"Varios aparatos coinciden con HON_APPLIANCE={selector!r}: {names}"
        )
    if len(acs) == 1:
        return acs[0]
    if len(acs) > 1:
        names = ", ".join(_label(a) for a in acs)
        raise RuntimeError(
            "Hay varios aires. Pon HON_APPLIANCE en .env con el nombre o MAC. "
            f"Encontrados: {names}"
        )
    if len(pool) == 1:
        _LOGGER.warning(
            "No hay un aparato de tipo AC; se usará %s", _label(pool[0])
        )
        return pool[0]
    if not pool:
        raise RuntimeError("La cuenta hOn no tiene aparatos visibles.")
    names = ", ".join(_label(a) for a in pool)
    raise RuntimeError(
        "Hay varios aparatos y ninguno es un AC claro. "
        f"Pon HON_APPLIANCE. Disponibles: {names}"
    )


def _label(appliance: HonAppliance) -> str:
    return (
        f"{appliance.nick_name or appliance.model_name} "
        f"[{appliance.appliance_type}/{appliance.mac_address}]"
    )


def read_status(appliance: HonAppliance, timezone: str) -> AcStatus:
    raw = appliance_parameters(appliance)
    on_raw = appliance.get("onOffStatus")
    is_on = _is_on_value(on_raw) if on_raw is not None else False
    mode_n = _to_int(appliance.get("machMode"))
    fan_n = _to_int(appliance.get("windSpeed"))
    target = _to_int(appliance.get("tempSel"))
    indoor = _to_int(appliance.get("tempIndoor"))
    if indoor is None:
        indoor = _to_int(appliance.get("temp"))
    outdoor = _to_int(appliance.get("tempOutdoor"))

    extras: dict[str, str] = {}
    if _is_on_value(appliance.get("ecoMode")):
        extras["Eco"] = "sí"
    if _is_on_value(appliance.get("muteStatus")):
        extras["Silencio"] = "sí"
    if _is_on_value(appliance.get("silentSleepStatus")):
        extras["Modo noche"] = "sí"
    if _is_on_value(appliance.get("healthMode")):
        extras["Health"] = "sí"
    if _is_on_value(appliance.get("rapidMode")):
        extras["Rapid"] = "sí"
    sense = _to_int(appliance.get("humanSensingStatus"))
    if sense is not None and sense != 0:
        extras["Eco Pilot"] = HUMAN_SENSE_ES.get(sense, str(sense))
    hdir = _to_int(appliance.get("windDirectionHorizontal"))
    vdir = _to_int(appliance.get("windDirectionVertical"))
    swing_bits = []
    if hdir == 7:
        swing_bits.append("horizontal")
    if vdir == 8:
        swing_bits.append("vertical")
    if swing_bits:
        extras["Oscilación"] = " y ".join(swing_bits)

    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        now = datetime.now().astimezone()

    return AcStatus(
        name=appliance.nick_name or appliance.model_name or "Aire acondicionado",
        model=appliance.model_name or "",
        mac=appliance.mac_address or "",
        appliance_type=appliance.appliance_type or "",
        is_on=is_on,
        connected=bool(appliance.connection),
        mode=MACH_MODE_ES.get(mode_n) if mode_n is not None else None,
        target_temp=float(target) if target is not None else None,
        indoor_temp=float(indoor) if indoor is not None else None,
        outdoor_temp=float(outdoor) if outdoor is not None else None,
        fan=FAN_ES.get(fan_n) if fan_n is not None else None,
        extras=extras,
        raw=raw,
        observed_at=now,
    )


class HonClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._hon: Hon | None = None
        self._appliance: HonAppliance | None = None
        self._on_update: Callable[[], None] | None = None

    @property
    def appliance(self) -> HonAppliance:
        if self._appliance is None:
            raise RuntimeError("hOn no está conectado")
        return self._appliance

    def subscribe_updates(self, callback: Callable[[], None]) -> None:
        """Recibe avisos cuando MQTT (appliancestatus/update) cambia el aparato."""
        self._on_update = callback
        self._bind_updates()

    def _bind_updates(self) -> None:
        if self._hon is None or self._on_update is None:
            return
        callback = self._on_update
        self._hon.subscribe_updates(lambda _payload: callback())

    async def connect(self) -> None:
        await self.close()
        hon = Hon(self._config.hon_email, self._config.hon_password)
        try:
            await hon.create()
            self._hon = hon
            self._appliance = match_appliance(
                hon.appliances, self._config.hon_appliance
            )
            self._bind_updates()
            _LOGGER.info("Aparato seleccionado: %s", _label(self._appliance))
        except Exception:
            self._hon = hon
            await self.close()
            raise

    async def close(self) -> None:
        if self._hon is None:
            return
        try:
            await self._hon.close()
        except Exception:
            _LOGGER.debug("Error al cerrar la sesión hOn", exc_info=True)
        self._hon = None
        self._appliance = None

    async def fetch_status(self) -> AcStatus:
        appliance = self.appliance
        try:
            await appliance.update(force=True)
        except Exception:
            _LOGGER.warning("Fallo al actualizar estado; se reintenta el login")
            await self.connect()
            appliance = self.appliance
            await appliance.update(force=True)
        return read_status(appliance, self._config.timezone)

    def read_cached_status(self) -> AcStatus:
        """Estado ya aplicado por MQTT o por el último fetch HTTP."""
        return read_status(self.appliance, self._config.timezone)

    def snapshot(self) -> dict[str, Any]:
        appliance = self.appliance
        params_meta: dict[str, Any] = {}
        for key, raw in (appliance.attributes.get("parameters") or {}).items():
            entry: dict[str, Any] = {"value": _param_value(raw)}
            last_update = getattr(raw, "last_update", None)
            if last_update is not None:
                entry["last_update"] = (
                    last_update.isoformat()
                    if hasattr(last_update, "isoformat")
                    else str(last_update)
                )
            params_meta[str(key)] = entry
        interesting = (
            "onOffStatus",
            "machMode",
            "tempSel",
            "tempIndoor",
            "tempOutdoor",
            "windSpeed",
            "operationSource",
            "intelligenceStatus",
            "smartLinkageStatus",
            "echoStatus",
            "voiceStatus",
            "humanSensingStatus",
            "energySavingStatus",
            "muteStatus",
            "silentSleepStatus",
            "rapidMode",
            "healthMode",
        )
        return {
            "nick": appliance.nick_name,
            "model": appliance.model_name,
            "mac": appliance.mac_address,
            "connected": bool(appliance.connection),
            "lastConnEvent": appliance.attributes.get("lastConnEvent"),
            "key_params": {
                name: params_meta[name] for name in interesting if name in params_meta
            },
            "parameters": {
                name: meta.get("value") for name, meta in params_meta.items()
            },
            "parameters_last_update": {
                name: meta.get("last_update")
                for name, meta in params_meta.items()
                if meta.get("last_update")
            },
        }

    async def fetch_cloud_diagnostics(self) -> dict[str, Any]:
        appliance = self.appliance
        api = appliance.api
        errors: dict[str, str] = {}
        history: list[Any] = []
        last_activity: dict[str, Any] = {}
        try:
            history = await api.load_command_history(appliance)
        except Exception as exc:
            errors["command_history"] = str(exc)
            _LOGGER.warning("No se pudo leer command_history: %s", exc)
        try:
            last_activity = await api.load_last_activity(appliance) or {}
        except Exception as exc:
            errors["last_activity"] = str(exc)
            _LOGGER.warning("No se pudo leer last_activity: %s", exc)
        try:
            await appliance.update(force=True)
        except Exception as exc:
            errors["attributes"] = str(exc)
            _LOGGER.warning("No se pudo refrescar attributes: %s", exc)
        return {
            "history": history if isinstance(history, list) else [],
            "last_activity": last_activity if isinstance(last_activity, dict) else {},
            "errors": errors,
            "snapshot_after_http": self.snapshot(),
        }

    async def turn_off(self) -> str:
        if self._config.dry_run:
            _LOGGER.info("DRY_RUN: no se envía el apagado a hOn")
            return "dry_run"
        appliance = self.appliance
        await appliance.update(force=True)
        if "stopProgram" in appliance.commands:
            _LOGGER.info("Apagando con stopProgram")
            ok = await appliance.commands["stopProgram"].send()
            appliance.sync_command("stopProgram", "settings")
            if ok:
                return "stopProgram"
            _LOGGER.warning("stopProgram no confirmó el envío; se prueba settings")
        setting = appliance.settings.get("settings.onOffStatus")
        if setting is not None:
            _LOGGER.info("Apagando con settings.onOffStatus=0")
            setting.value = "0"
            ok = await appliance.commands["settings"].send()
            if ok:
                return "settings.onOffStatus"
            raise RuntimeError("hOn rechazó el comando settings de apagado")
        raise RuntimeError(
            "Este modelo no expone stopProgram ni settings.onOffStatus"
        )

    def list_appliances(self) -> list[HonAppliance]:
        if self._hon is None:
            raise RuntimeError("hOn no está conectado")
        return list(self._hon.appliances)
