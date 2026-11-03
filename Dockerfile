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
COPY shared ./shared

# The service runs without root; the SQLite database lives in the /data volume.
RUN useradd --system --uid 10001 --home-dir /app edge \
    && mkdir /data \
    && chown edge /data
USER edge

VOLUME /data
EXPOSE 5000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
