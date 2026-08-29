"""Heurísticas de origen de encendido y recortes para el JSONL."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Mapeo no oficial (dumps de la comunidad hOn). 0 en este modelo suele ser local.
OPERATION_SOURCE_HINT = {
    0: "local_o_desconocido",
    1: "app_hOn",
    2: "interno_o_local",
    3: "nube_dashboard_o_programacion",
}

_HISTORY_DROP = {"PK", "SK", "SK_Secondary"}


def operation_source_hint(value: int | None) -> str:
    if value is None:
        return "sin_dato"
    return OPERATION_SOURCE_HINT.get(value, f"codigo_{value}")


def to_local_iso(value: Any, timezone_name: str) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    if text.isdigit() and len(text) >= 12:
        try:
            dt = datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
            return dt.astimezone(ZoneInfo(timezone_name)).isoformat()
        except (OSError, OverflowError, ValueError):
            pass
    try:
        iso = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(timezone_name)).isoformat()
    except ValueError:
        return text


def slim_history_item(item: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    cmd = item.get("command") or {}
    params = cmd.get("parameters") or {}
    attrs = cmd.get("attributes") or item.get("attributes") or {}
    device = cmd.get("device") or {}
    executed = item.get("timestampExecuted") or item.get("timestampAccepted") or cmd.get("timestamp")
    return {
        "local_time": to_local_iso(executed, timezone_name),
        "timestampExecuted": item.get("timestampExecuted"),
        "timestampAccepted": item.get("timestampAccepted"),
        "commandName": cmd.get("commandName") or item.get("commandName"),
        "programName": cmd.get("programName") or item.get("programName"),
        "channel": attrs.get("channel"),
        "origin": attrs.get("origin"),
        "onOffStatus": params.get("onOffStatus"),
        "machMode": params.get("machMode"),
        "tempSel": params.get("tempSel"),
        "windSpeed": params.get("windSpeed"),
        "device": {
            "mobileOs": device.get("mobileOs") or device.get("os"),
            "deviceModel": device.get("deviceModel"),
            "appVersion": device.get("appVersion"),
            "osVersion": device.get("osVersion"),
            "mobileId": device.get("mobileId"),
        },
    }


def recent_cloud_commands(
    history: list[dict[str, Any]],
    *,
    timezone_name: str,
    minutes: int = 15,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    out: list[dict[str, Any]] = []
    for item in history:
        slim = slim_history_item(item, timezone_name)
        raw = slim.get("timestampExecuted") or slim.get("timestampAccepted")
        local = slim.get("local_time")
        ts = None
        for candidate in (raw, local):
            if not candidate:
                continue
            try:
                ts = datetime.fromisoformat(str(candidate).replace("Z", "+00:00"))
                break
            except ValueError:
                continue
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            out.append(slim)
    return out


def mqtt_changed_params(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for event in events:
        payload = event.get("payload") or {}
        params = payload.get("parameters") if isinstance(payload, dict) else None
        if not isinstance(params, list):
            continue
        for param in params:
            if not isinstance(param, dict):
                continue
            name = str(param.get("parName") or "")
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def guess_source(
    *,
    operation_source: int | None,
    last_activity: dict[str, Any] | None,
    recent_commands: list[dict[str, Any]],
    mqtt_params: list[str],
) -> dict[str, Any]:
    """Estima quién encendió. Nunca es prueba cerrada; va con evidencias."""
    evidence: list[str] = []
    activity = last_activity or {}
    channel = str(activity.get("channel") or "")
    origin = str(activity.get("origin") or "")
    if channel:
        evidence.append(f"last_activity.channel={channel}")
    if origin:
        evidence.append(f"last_activity.origin={origin}")
    if operation_source is not None:
        evidence.append(
            f"operationSource={operation_source} ({operation_source_hint(operation_source)})"
        )
    if mqtt_params:
        evidence.append("mqtt_params=" + ",".join(mqtt_params[:20]))

    app_cmds = [
        c
        for c in recent_commands
        if c.get("commandName") in {"startProgram", "settings"}
        and (c.get("channel") == "mobileApp" or (c.get("device") or {}).get("mobileOs"))
        and str(c.get("onOffStatus")) in {"1", "1.0"}
    ]
    if app_cmds:
        cmd = app_cmds[0]
        device = cmd.get("device") or {}
        evidence.append(
            f"historial_nube_reciente {cmd.get('commandName')} "
            f"{device.get('mobileOs')} {device.get('deviceModel')} {cmd.get('local_time')}"
        )
        return {
            "label": "app_hOn",
            "confidence": "alta",
            "detail": "Hay un comando de la app en el historial de hOn en los últimos minutos.",
            "evidence": evidence,
        }

    if recent_commands:
        evidence.append(f"comandos_nube_recientes={len(recent_commands)}")
    else:
        evidence.append("sin_comando_nube_reciente")

    if operation_source == 1:
        return {
            "label": "app_hOn",
            "confidence": "media",
            "detail": "operationSource=1 suele ser app; el historial no lo confirma del todo.",
            "evidence": evidence,
        }

    if operation_source == 3:
        return {
            "label": "nube_programacion_o_asistente",
            "confidence": "media",
            "detail": "operationSource=3 suele ser nube (programación hOn, dashboard o asistente).",
            "evidence": evidence,
        }

    if channel == "mobileApp":
        return {
            "label": "app_hOn_o_integracion",
            "confidence": "media",
            "detail": "last_activity.channel=mobileApp, pero no hay startProgram fresco.",
            "evidence": evidence,
        }

    if not recent_commands and operation_source in {0, None}:
        return {
            "label": "termostato_o_mando_local",
            "confidence": "media",
            "detail": (
                "La nube no ha registrado startProgram. Encaja con termostato de pared, "
                "IR o arranque local de la unidad. last_activity.origin=dashboard "
                "en este modelo a menudo está desfasado."
            ),
            "evidence": evidence,
        }

    return {
        "label": "desconocido",
        "confidence": "baja",
        "detail": "No hay señales claras de app ni de mando local.",
        "evidence": evidence,
    }
