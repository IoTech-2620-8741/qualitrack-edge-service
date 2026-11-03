# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project structure with the IAM (iam), Tracking (tracking) and Shared (shared) bounded contexts, a Flask application factory and environment-specific configuration loaded from `.env.development` or `.env.production`.

- IAM (iam): `Device` aggregate with its type (`ENVIRONMENTAL_DEVICE` or `CONTAINER_MONITOR`), API keys stored as salted PBKDF2 verifiers, revocation and rotation, SQLite persistence through Peewee, optional seed of a development test node and `POST /api/v1/iam/authentications`.

- Unit tests of the credential, the device aggregate, the configuration and the authentication endpoint, and a CI workflow that runs them on pull requests (#1).

- README, LICENSE.md with the QualiTrack Proprietary Software License, and this changelog (#1).

- IAM (iam): `POST /api/v1/auth/sign-in` for the environmental device and the container monitor, which returns the device type (TS53, TS54) (#2).

- IAM (iam): `flask devices` commands to register a node with a generated API key shown only once, rotate its key, revoke it and list the nodes (#2).

- Shared (shared): session with the QualiTrack Cloud REST API with the account of an operator, which signs in again when the token expires, and a background thread that runs the cloud synchronization every `CLOUD_SYNC_INTERVAL_SECONDS` (#3).

- Tracking (tracking): local copy of the device registry of the laboratory, with the environment and cloud identifier of each node, read from the equipment of the cloud by the background thread and by `flask cloud sync`, and listed by `flask cloud devices` (#3).

- Tracking (tracking): `POST /api/v1/monitoring/environment-readings` (TS55), `POST /api/v1/monitoring/container-readings` (TS56) and `POST /api/v1/control/actuation-events` (TS57), validated with the rules of the cloud. The nodes are authenticated through the IAM anti-corruption layer (#4).

- Tracking (tracking): every reading and action is kept in SQLite as `PENDING` until the cloud accepts it; a retry of the same observation is not stored twice and a retry with different data is rejected with `409`. `flask cloud pending` counts them by synchronization status (TS60) (#4).

### Changed

- IAM (iam): the device types and the device identifier follow the cloud registry: `ENVIRONMENTAL_DEVICE` instead of `ENVIRONMENT_MONITOR`, and the `sensorExternalId` of the node (for example `ENV-001`) instead of its MAC address (#1).

- The dependencies file is `requirements.txt` (#1).

- The configuration requires `CLOUD_SYNC_ENABLED`, and the other `CLOUD_*` settings when it is true (#3).

### Removed

- The empty Compliance & Alerting (ca) scaffold: the alerts are evaluated in the cloud (#1).

- IAM (iam): `POST /api/v1/iam/authentications`, replaced by `POST /api/v1/auth/sign-in` (#2).
