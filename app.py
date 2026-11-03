"""Flask application entry point for the QualiTrack Edge Service.

This module wires together the Flask application, registers the IAM
bounded-context Blueprint and configures the SQLite database.

Typical usage::

    flask --app app run
    # or
    python app.py
"""

from pathlib import Path

from flask import Flask

from config import load_config
from iam.application.services import AuthApplicationService
from iam.interfaces.services import iam_api
from shared.infrastructure.database import configure_database, db, init_db

def create_app() -> Flask:
    """Create and initialize the Flask application."""
    app = Flask(__name__)
    app.config.from_mapping(load_config(Path(__file__).resolve().parent))
    configure_database(app.config["DATABASE_PATH"])
    init_db()

    app.register_blueprint(iam_api)
    iam_authentication_service = AuthApplicationService()
    app.extensions["iam_authentication_service"] = iam_authentication_service

    if app.config["SEED_TEST_DEVICE"]:
        iam_authentication_service.get_or_create_test_device(
            app.config["TEST_DEVICE_ID"],
            app.config["TEST_DEVICE_API_KEY"],
        )

    @app.teardown_appcontext
    def close_database(_exception: BaseException | None) -> None:
        if not db.is_closed():
            db.close()

    return app
app = create_app()

if __name__ == "__main__":
    if app.config["APP_ENV"] != "development":
        raise RuntimeError(
            "The built-in Flask server is development-only; use a production WSGI server"
        )
    app.run(
        debug=app.config["DEBUG"],
        host=app.config["HOST"],
        port=app.config["PORT"],
    )