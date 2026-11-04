# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Tracking (tracking): the background synchronization deletes the readings and actions the cloud already answered: the `SYNCED` ones after `SYNCED_RETENTION_DAYS` (7 by default) and the `REJECTED` ones after `REJECTED_RETENTION_DAYS` (90 by default), at most once an hour and in batches of 2,000 records. Pending records are never deleted. It is active by default, so `flask cloud pending` shows fewer `SYNCED` records as time goes by (ADR-0012).

- Settings `SYNCED_RETENTION_DAYS` and `REJECTED_RETENTION_DAYS`, both optional integers from 1 (ADR-0012).

## [0.2.0] - 2026-11-03

### Changed

- Tracking (tracking): the device registry keeps only the nodes registered in this Edge, read from IAM through the anti-corruption layer, so the Edge no longer stores the nodes of other stations of the laboratory nor reads their profiles. `flask cloud sync` names the nodes registered in the Edge that the cloud does not have (#14).

## [0.1.0] - 2026-11-03

First version of the Edge Application of QualiTrack: the Edge stories of the QualiTrack Report (TS53–TS64) and the deployment on the local station.

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

- Tracking (tracking): the pending readings of the environmental device (TS61) and the container monitors (TS62) and the actions (TS63) are sent oldest first to the nested routes of the cloud by the background thread and by `flask cloud sync`. The cloud's acceptance makes them `SYNCED` and its refusal `REJECTED`; records of a node the registry does not place in an environment wait (#5).

- Tracking (tracking): local copy of the environmental profile of each node (version, thresholds and actuation rules), read from the cloud by the background thread and by `flask cloud sync` (TS64) and listed by `flask cloud profiles` (#6).

- Tracking (tracking): `GET /api/v1/configurations/{deviceId}` hands the environmental device (TS58) and the container monitor (TS59) the profile they apply (#6).

- Deployment on the local station with Docker Compose: the service `qualitrack-edge` runs the Edge on Gunicorn (`wsgi.py`, `gunicorn.conf.py`, one worker) and the volume `qualitrack-data` keeps the SQLite database. The image builds for `linux/arm64` and `linux/amd64`, and CI checks the compose service and the arm64 build (#7).

- Documentation in `docs/`: the technical stories TS53–TS64 with Gherkin acceptance criteria and a Requirements Traceability Matrix, the PlantUML class diagram of the bounded contexts, and the architecture decision records ADR-0001 to ADR-0010 (#8).

### Changed

- IAM (iam): the device types and the device identifier follow the cloud registry: `ENVIRONMENTAL_DEVICE` instead of `ENVIRONMENT_MONITOR`, and the `sensorExternalId` of the node (for example `ENV-001`) instead of its MAC address (#1).

- The dependencies file is `requirements.txt` (#1).

- The configuration requires `CLOUD_SYNC_ENABLED`, and the other `CLOUD_*` settings when it is true (#3).

- Tracking (tracking): `flask cloud sync` also sends the pending telemetry after reading the device registry (#5).

- Tracking (tracking): `flask cloud sync` also copies the profiles, between the device registry and the pending telemetry (#6).

### Removed

- The empty Compliance & Alerting (ca) scaffold: the alerts are evaluated in the cloud (#1).

- IAM (iam): `POST /api/v1/iam/authentications`, replaced by `POST /api/v1/auth/sign-in` (#2).
