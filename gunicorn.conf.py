"""Gunicorn settings of the QualiTrack Edge Service on the local station.

One worker process, because SQLite and the background cloud synchronization
expect a single process; its threads serve the nodes concurrently. Logs go to
the standard streams, where Docker collects them.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = "info"
