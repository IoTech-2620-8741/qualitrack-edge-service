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

### Changed

- IAM (iam): the device types and the device identifier follow the cloud registry: `ENVIRONMENTAL_DEVICE` instead of `ENVIRONMENT_MONITOR`, and the `sensorExternalId` of the node (for example `ENV-001`) instead of its MAC address (#1).

- The dependencies file is `requirements.txt` (#1).

### Removed

- The empty Compliance & Alerting (ca) scaffold: the alerts are evaluated in the cloud (#1).

- IAM (iam): `POST /api/v1/iam/authentications`, replaced by `POST /api/v1/auth/sign-in` (#2).
