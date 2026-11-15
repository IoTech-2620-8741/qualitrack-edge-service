# QualiTrack Edge Service for the local station of the facility (report 4.1.3.4).
# The python image is multi-architecture, so the same Dockerfile builds for the
# Raspberry Pi 4 (linux/arm64) and for development machines (linux/amd64).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DATABASE_PATH=/data/edge.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py wsgi.py gunicorn.conf.py ./
COPY iam ./iam
COPY tracking ./tracking
COPY sync ./sync
COPY shared ./shared

# The service runs without root; the SQLite database lives in the /data volume.
RUN useradd --system --uid 10001 --home-dir /app edge \
    && mkdir /data \
    && chown edge /data
USER edge

VOLUME /data
EXPOSE 5000

# Docker asks the Edge itself whether it works (ADR-0017): GET /api/v1/health
# answers 503 when the database cannot be read. The slim image has no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '5000') + '/api/v1/health', timeout=4)"

CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
