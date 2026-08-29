FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_DIR=/app/logs

WORKDIR /app

RUN adduser --disabled-password --gecos "" --uid 1000 app \
    && mkdir -p /app/logs \
    && chown app:app /app/logs

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY haier_watchdog ./haier_watchdog

USER app

CMD ["python", "-m", "haier_watchdog"]
