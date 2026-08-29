"""Lista los aparatos hOn y vuelca el estado del aire seleccionado."""

from __future__ import annotations

import asyncio
import logging
import sys

from haier_watchdog.config import load_config
from haier_watchdog.hon_client import (
    HonClient,
    appliance_parameters,
    match_appliance,
    read_status,
)
from pyhon.exceptions import HonAuthenticationError


def _print_appliance(appliance) -> None:
    print(f"- {_safe(appliance.nick_name)} [{appliance.appliance_type}]")
    print(f"    modelo: {appliance.model_name}")
    print(f"    mac:    {appliance.mac_address}")
    print(f"    id:     {appliance.unique_id}")
    print(f"    comandos: {', '.join(sorted(appliance.commands)) or '(ninguno)'}")


def _safe(value: object) -> str:
    return str(value or "")


async def async_main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    try:
        config = load_config(require_hon=True, require_telegram=False)
    except ValueError as exc:
        print(f"Configuración incompleta: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    hon = HonClient(config)
    try:
        await hon.connect()
    except HonAuthenticationError:
        print(
            "hOn ha rechazado el login (401 AUTH-INV-PARAM).\n"
            f"Email usado: {config.hon_email}\n"
            "Eso lo devuelve la nube de Haier, no un fallo del script.\n"
            "\n"
            "Prueba esto, en este orden:\n"
            "  1. Espera 15-30 min si has reintentado varias veces: hOn bloquea\n"
            "     la cuenta y sigue diciendo usuario/contraseña incorrectos.\n"
            "  2. En la app hOn, entra con email y contraseña (no 'Google').\n"
            "  3. Cambia la contraseña en hOn, pégala en .env entre comillas:\n"
            "       HON_PASSWORD='tu-clave-nueva'\n"
            "  4. Si la cuenta tiene verificación en dos pasos, esta API no\n"
            "     puede completar el login todavía.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except Exception as exc:
        print(f"No se ha podido conectar con hOn: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    try:
        appliances = hon.list_appliances()
        print(f"Aparatos en la cuenta ({len(appliances)}):")
        for appliance in appliances:
            _print_appliance(appliance)
        print()
        selected = match_appliance(appliances, config.hon_appliance)
        print(f"Seleccionado para el watchdog: {selected.nick_name}")
        await selected.update(force=True)
        status = read_status(selected, config.timezone)
        print()
        print("Estado actual:")
        print(f"  encendido: {status.is_on}")
        print(f"  conectado: {status.connected}")
        print(f"  modo:      {status.mode}")
        print(f"  objetivo:  {status.target_temp}")
        print(f"  ambiente:  {status.indoor_temp}")
        print(f"  ventilador:{status.fan}")
        print()
        print("Parámetros brutos:")
        for key, value in sorted(appliance_parameters(selected).items()):
            print(f"  {key}: {value}")
        settings = selected.settings
        interesting = [
            name
            for name in settings
            if name.startswith("settings.")
            and any(
                token in name.lower()
                for token in (
                    "onoff",
                    "machmode",
                    "temp",
                    "wind",
                    "eco",
                    "mute",
                    "human",
                    "sleep",
                    "rapid",
                    "health",
                )
            )
        ]
        if interesting:
            print()
            print("Ajustes relevantes:")
            for name in interesting:
                setting = settings[name]
                print(f"  {name} = {setting.value}")
    finally:
        await hon.close()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
