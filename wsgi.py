"""WSGI entry point of the QualiTrack Edge Service for production servers.

Gunicorn imports this module in its worker process (report 4.1.3.4): it
creates the application and starts the background cloud synchronization in
that process. Typical usage::

    gunicorn --config gunicorn.conf.py wsgi:app
"""
import logging

from app import app, start_cloud_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
start_cloud_sync(app)
