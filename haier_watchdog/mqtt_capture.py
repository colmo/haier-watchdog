"""Captura los MQTT de hOn (pyhon no los pasa al callback de subscribe)."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from pyhon.connection.mqtt import MQTTClient

_LOGGER = logging.getLogger(__name__)
_PATCHED = False
_MAX = 250
_events: deque[dict[str, Any]] = deque(maxlen=_MAX)
_orig = MQTTClient._on_publish_received


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return

    def _on_publish_received(self: MQTTClient, data: Any) -> None:
        try:
            packet = getattr(data, "publish_packet", None)
            payload_raw = getattr(packet, "payload", None) if packet else None
            if payload_raw:
                payload = json.loads(payload_raw.decode())
                _events.append(
                    {
                        "ts_utc": datetime.now(timezone.utc).isoformat(),
                        "topic": getattr(packet, "topic", None),
                        "payload": payload,
                    }
                )
        except Exception:
            _LOGGER.debug("No se pudo capturar el MQTT", exc_info=True)
        return _orig(self, data)

    MQTTClient._on_publish_received = _on_publish_received  # type: ignore[method-assign]
    _PATCHED = True


def recent(*, mac: str | None = None, seconds: float = 45) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    out: list[dict[str, Any]] = []
    for event in _events:
        if not _in_window(event, cutoff):
            continue
        if mac and not _matches_mac(event, mac):
            continue
        out.append(event)
    return out


def _in_window(event: dict[str, Any], cutoff: datetime) -> bool:
    raw = event.get("ts_utc")
    if not raw:
        return True
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def _matches_mac(event: dict[str, Any], mac: str) -> bool:
    needle = mac.lower().replace(":", "-")
    payload = event.get("payload") or {}
    if isinstance(payload, dict):
        got = str(payload.get("macAddress") or "").lower().replace(":", "-")
        if got:
            return got == needle
    topic = str(event.get("topic") or "").lower().replace(":", "-")
    if needle in topic:
        return True
    return not topic.startswith("haier/things/") and "presence" not in topic
