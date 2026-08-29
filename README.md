# Watchdog Haier + Telegram

Servicio que vigila el aire acondicionado Haier a través de la app [hOn](https://play.google.com/store/apps/details?id=com.haiereurope.hon) y te avisa por Telegram si se enciende. El aviso llega en cuanto la nube publica `appliancestatus/update` por MQTT; cada 15 minutos hay un poll HTTP de respaldo.

Haier no publica API oficial. Se usa [pyhon-revived](https://github.com/mmalolepszy/pyhon-revived) (comunidad). Puede dejar de funcionar si Haier cambia el login o los endpoints.

## Qué hace

- Reacciona al MQTT de hOn al encender (no espera al poll HTTP). El poll HTTP cada 15 minutos sigue como respaldo si MQTT falla.
- Si pasa de **apagado a encendido** (o ya está encendido al arrancar), manda un mensaje con modo, temperaturas, ventilador y botones.
- Cada encendido se guarda en `logs/events.jsonl` con MQTT, `operationSource`, historial de comandos hOn y una estimación de origen (app / termostato / programación).
- **Sí, fui yo**: lo deja encendido y **no vuelve a preguntar** hasta que se apague y se encienda otra vez.
- **No, apágalo** o **5 minutos sin respuesta**: lo apaga y te lo confirma.

Comandos del bot: `/start` (muestra tu `chat_id`) y `/status` (estado actual).

## Preparación

1. Crea un bot con [@BotFather](https://t.me/BotFather) y copia el token.
2. Ten a mano el email y la contraseña de la app hOn.
3. Copia `.env.example` a `.env` y rellena las variables. **No subas `.env` a git.**

## Docker (recomendado)

La imagen se publica en [GitHub Container Registry](https://github.com/features/packages) como `ghcr.io/colmo/haier-watchdog`. Cada tag git `v1.0.1` genera una imagen con el mismo número, más un tag `sha-…` del commit. En el VPS no hace falta copiar código: Compose, `.env` y el volumen de logs.

### Prueba local

```bash
cp .env.example .env
# edita HON_*, TELEGRAM_* y DRY_RUN=1
docker compose up --build
```

Para `chat_id` y el dump del aparato, el venv local sigue siendo más cómodo (ver más abajo).

### Publicar una versión

1. Sube `__version__` en `haier_watchdog/__init__.py`.
2. Commit y tag:

```bash
git tag v1.0.1
git push origin v1.0.1
```

GitHub Actions construye `linux/amd64` y sube a GHCR:

- `ghcr.io/colmo/haier-watchdog:1.0.1` (el que usa el VPS)
- `ghcr.io/colmo/haier-watchdog:1.0`
- `ghcr.io/colmo/haier-watchdog:sha-<commit>`

Tras el **primer** push, el paquete nace privado aunque el repo sea público. En GitHub: **Packages → haier-watchdog → Package settings → Change visibility → Public**. Así el VPS hace `pull` sin token.

No uses `latest` en producción: pinnea `IMAGE_TAG` (por ejemplo `1.0.1`).

### VPS Debian 13

Instala Docker Engine y el plugin Compose. En `/opt/haier` solo hacen falta Compose y `.env`:

```bash
# copia docker-compose.yml y .env (secretos + IMAGE_TAG=1.0.0)
cd /opt/haier
docker compose pull
docker compose up -d
docker compose logs -f
```

`IMAGE_TAG` en `.env` elige la versión. Los ficheros de log van al volumen Docker `logs` (sobreviven a `compose down` y a cambiar de imagen). Los secretos no van en la imagen.

Actualizar:

```bash
# IMAGE_TAG=1.0.1 en .env
docker compose pull
docker compose up -d
```

Rollback: vuelve a poner `IMAGE_TAG=1.0.0` y repite pull + `up -d`.

Opcional: unidad systemd para `systemctl restart haier-watchdog`:

```bash
sudo cp deploy/haier-watchdog-docker.service /etc/systemd/system/haier-watchdog.service
sudo systemctl daemon-reload
sudo systemctl enable --now haier-watchdog
```

Docker debe arrancar al boot (`sudo systemctl enable docker`). Compose ya trae `restart: unless-stopped`.

## Prueba en el Mac (venv)

Hace falta **Python 3.10+**. En macOS, `/usr/bin/python3` suele ser 3.9: usa el de Homebrew (`/opt/homebrew/bin/python3`).

```bash
cd /ruta/al/repo
/opt/homebrew/bin/python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env`. Luego:

```bash
# 1) chat_id de Telegram (abre el bot y pulsa /start)
python -m haier_watchdog.chat_id

# 2) login hOn y dump del aparato
python -m haier_watchdog.discover

# 3) watchdog sin apagar de verdad
#    DRY_RUN=1 en .env
python -m haier_watchdog
```

Si el login falla con 401, hOn está rechazando email o contraseña (o la cuenta está bloqueada un rato por demasiados intentos). Usa los mismos de la app, no el botón de Google. Si la clave tiene `%`, `&`, `#` o `$`, déjala entre comillas simples en `.env`. Tras varios fallos, espera 15-30 minutos y vuelve a probar.

Enciende el aire en la app hOn y comprueba el mensaje. Prueba **Sí** (no debe repreguntar) y **No** / timeout. Cuando `DRY_RUN=0`, el No/timeout apaga de verdad.

## VPS con venv (legado)

Si no usas Docker, copia el proyecto a `/opt/haier` y usa `deploy/haier-watchdog.service`:

```bash
sudo mkdir -p /opt/haier
sudo rsync -a --exclude .venv ./ /opt/haier/
cd /opt/haier
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env
sudo nano .env
sudo cp deploy/haier-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now haier-watchdog
sudo journalctl -u haier-watchdog -f
```

Ajusta `WorkingDirectory` y `ExecStart` si no usas `/opt/haier`. El proceso carga el `.env` del directorio del proyecto (no hace falta `EnvironmentFile` de systemd, que se lía con contraseñas raras).

## Variables

- `HON_EMAIL` / `HON_PASSWORD`: cuenta de la app hOn
- `HON_APPLIANCE`: nombre, modelo o MAC si hay varios AC
- `TELEGRAM_BOT_TOKEN`: token de BotFather
- `TELEGRAM_CHAT_ID`: solo ese chat puede confirmar o apagar
- `POLL_INTERVAL_SECONDS`: respaldo HTTP si no llega MQTT (por defecto 900, 15 min)
- `CONFIRM_TIMEOUT_SECONDS`: por defecto 300
- `DRY_RUN`: `1` avisa pero no apaga
- `TZ`: por defecto `Europe/Madrid`
- `LOG_DIR`: por defecto `./logs` (en Docker, `/app/logs` en el volumen `logs`)
- `IMAGE_TAG` / `IMAGE_NAME`: solo Compose; pinnean la imagen de GHCR (no uses `latest`)

## Logs

El proceso escribe dos ficheros (no se suben a git):

- `watchdog.log`: traza de ejecución (MQTT, poll, reconexiones), con rotación.
- `events.jsonl`: un JSON por línea. Los eventos `ac_on` son los ricos: hora, temperaturas, payload MQTT, historial de la app, `last_activity` y `guess` (origen estimado). También hay `ac_off`, `telegram_decision`, `watchdog_start` y `watchdog_stop`.

En Docker viven en el volumen `logs`. Para leerlos o copiarlos:

```bash
docker compose exec watchdog ls -l /app/logs
docker compose exec watchdog tail -f /app/logs/events.jsonl
docker compose cp watchdog:/app/logs/events.jsonl ./events.jsonl
```

`docker compose down` no borra el volumen. `docker compose down -v` sí (pierdes el historial).

Tras unos meses, el fichero útil para diagnosticar encendidos es **`events.jsonl`**.

## Limitaciones

- El estado llega por la nube de Haier (MQTT suele ser casi inmediato; el poll HTTP es el plan B).
- No ejecutes dos instancias a la vez.
- Si reinicias el servicio mientras el aire está encendido y lo habías autorizado con «Sí», volverá a preguntar (el autorizado no se guarda en disco).
- Causas típicas de encendido solo: Alexa/Google, otro móvil con la misma cuenta, temporizador físico, Eco Pilot. El dump de `discover` ayuda a ver sensores/modos smart.
