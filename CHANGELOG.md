# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Tracking (tracking): `POST /api/v1/monitoring/environment-readings` accepts `luminosity` (lux, a JSON number of 0 or more), the light of the room that the Environment Monitor measures with its BH1750, alone or with `air_quality` and `motion`. It is stored as a `LUMINOSITY` reading like the one of the container monitor. A node that does not send it works as before. The cloud accepts it from qualitrack-platform 2.1.0; an older one rejects it for good. Update the Edge before the firmware that sends it: 1.0.0 answers `400` to the whole request.
- IAM, Tracking, Sync and Shared: `flask-openapi3` builds the OpenAPI document of the routes from their request and response models and serves it at `/openapi/openapi.json`, with its viewers under `/openapi/` (Swagger UI, ReDoc, RapiDoc, RapiPDF, Scalar and Elements). The routes, status codes and error bodies stay the same: a validation error still answers `400` (or `401` for a telemetry request without `device_id`), never `422`, and `POST /api/v1/devices/enroll` still answers `403` outside development, `403` to a node the cached registry does not authorize, `409` to a registered one and `Cache-Control: no-store` with the API key (#42, #45).

### Changed

- Tracking (tracking): `Metric.device_type` is replaced by `Metric.device_types`, because `LUMINOSITY` is reported by both node types. Only a container monitor still sends temperature, humidity, RFID tags and actions, and only the environmental device sends air quality and motion.

## [1.0.0] - 2026-11-15

First stable version of the Edge Application of QualiTrack. From this version the public API — the HTTP routes under `/api/v1`, the `flask devices` and `flask cloud` commands, the settings and the SQLite database — follows Semantic Versioning: a change that breaks it raises the major version. `POST /api/v1/devices/enroll` stays development only and out of that contract.

## [0.4.0] - 2026-11-15

### Added

- IAM (iam) and Tracking (tracking): the nodes may send their API key as `Authorization: Bearer <key>` when they do not send `X-API-Key`, so a node built on the Modest IoT Nano Framework reaches the Edge with its `HttpGatewayClient`. `X-API-Key` still works and wins when both arrive; another scheme or an empty token is a missing key (ADR-0018).
- Shared (shared): `GET /api/v1/health` reports whether the Edge can serve the nodes. It answers `503` with `unavailable` when the SQLite database cannot be read, and `200` with `ok`, or with `degraded` while the cloud synchronization fails, stops or ends no cycle in three intervals; the nodes are still served then. It needs no API key and shows no identifiers, keys or error messages. `CloudSyncScheduler` keeps the outcome of its last cycle for it (ADR-0017).
- The Docker image checks `GET /api/v1/health` every 30 seconds, so `docker compose ps` shows the container as `healthy` or `unhealthy`; the CI waits on the endpoint and on that state instead of expecting a `401` from the sign-in (ADR-0017).

### Changed

- Sync (sync): the pending readings and actions are sent four at a time instead of one by one, so the Edge keeps up with 15 nodes when the cloud is slow; an outage still stops the synchronization once the records in flight are answered. The records of nodes the registry does not place in an environment are filtered and counted in SQL instead of being read on every cycle.
- Shared (shared): the background synchronization logs how long each cycle and each task took, and warns when a cycle takes longer than `CLOUD_SYNC_INTERVAL_SECONDS`.
- IAM (iam): new API keys are kept as a SHA-256 verifier instead of PBKDF2, so checking the key of a node no longer takes tens of milliseconds of the Raspberry Pi on every request. Verifiers stored before keep working; `flask devices rotate-key` replaces them (ADR-0016).
- Sync (sync): `DeviceRegistryApplicationService` requires its repository from `create_app`, as the other Sync services do (ADR-0015).
- IAM (iam) and Sync (sync): a second registration or enrollment of the same node is refused only by the primary key of the devices table; the lookups made before the insert, which it already covered, are removed. `flask devices register` still answers "already registered" and `POST /api/v1/devices/enroll` still answers `409` (#32).

### Fixed

- IAM (iam): two registrations of the same node at the same time, for example `flask devices register` and `POST /api/v1/devices/enroll`, no longer both answer "Registered" with only the last key working; the second one fails with "already registered".
- Sync (sync): the profile read from the cloud is parsed once.
- Settings: `CLOUD_API_URL` must be an http or https URL with a host and a valid port, and without a user, password, query or fragment; any other value stops the start with "CLOUD_API_URL must be a valid http or https URL" (#34).

### Removed

- `DeviceRegistryApplicationService.find_monitored_device` and `AuthApplicationService.authenticate`, which only the tests used.

## [0.3.1] - 2026-11-13

### Changed

- Sync (sync): the telemetry and profile synchronization receive the repositories of Tracking from `create_app` instead of building them, so Sync uses only the model and contracts of Tracking, as ADR-0015 states; a test keeps it away from the infrastructure of Tracking. The docstrings of the registry describe the devices the cloud assigns to this Edge Gateway (#28).

## [0.3.0] - 2026-11-12

### Added

- Sync (sync): the background synchronization deletes the readings and actions the cloud already answered: the `SYNCED` ones after `SYNCED_RETENTION_DAYS` (7 by default) and the `REJECTED` ones after `REJECTED_RETENTION_DAYS` (90 by default), at most once an hour and in batches of 2,000 records. Pending records are never deleted. It is active by default, so `flask cloud pending` shows fewer `SYNCED` records as time goes by (ADR-0012).

- Settings `SYNCED_RETENTION_DAYS` and `REJECTED_RETENTION_DAYS`, both optional integers from 1 (ADR-0012).

- The Edge authenticates in the cloud as an Edge Gateway, with the settings `CLOUD_DEVICE_ID` and `CLOUD_API_KEY` sent in the `X-API-Key` header, and reads the IoT devices the cloud assigns to it from `/edge-gateways/{deviceId}/iot-devices` (#20, ADR-0014).

- Sync (sync): `POST /api/v1/devices/enroll` lets a node the cloud assigns to this Edge obtain its local API key. Development only (#20).

### Changed

- Sync (sync): the device registry caches every device the cloud sends for this Edge Gateway, also the ones not yet registered in the Edge (#20).

- Sync (sync): new bounded context that owns the exchange with the cloud: the device registry, the copy of the profiles, the sending of the pending readings and actions, their deletion, the `flask cloud` commands and the enrollment of the nodes (`POST /api/v1/devices/enroll`). Tracking keeps the reception of the telemetry and the configuration it hands to each node, and no longer depends on the cloud. The routes, the commands, the settings and the database do not change; the loggers of the cloud gateways are now named `sync.infrastructure.*` (#23, ADR-0015).

### Removed

- Settings `CLOUD_USERNAME` and `CLOUD_PASSWORD`: the Edge no longer signs in with the account of an operator. A station that updates from 0.2.0 with `CLOUD_SYNC_ENABLED=true` must set `CLOUD_DEVICE_ID` and `CLOUD_API_KEY` first, or the service does not start (#20, ADR-0014 supersedes ADR-0005).

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

[Unreleased]: https://github.com/IoTech-2620-8741/qualitrack-edge-service/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/IoTech-2620-8741/qualitrack-edge-service/compare/v0.4.0...v1.0.0
[0.4.0]: https://github.com/IoTech-2620-8741/qualitrack-edge-service/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/IoTech-2620-8741/qualitrack-edge-service/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/IoTech-2620-8741/qualitrack-edge-service/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/IoTech-2620-8741/qualitrack-edge-service/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/IoTech-2620-8741/qualitrack-edge-service/releases/tag/v0.1.0
