# Technical Stories — QualiTrack Edge Service

## Overview

This document captures the technical stories of the QualiTrack Edge Service,
the Edge Application that runs on the local station of each pharmaceutical
facility. The two ESP32 nodes of QualiTrack, the environmental device and the
container monitor, talk to it over HTTP without anyone taking part at the
moment of transmission; the Edge keeps what they send and synchronizes it with
the QualiTrack Cloud REST API.

The stories are the Edge stories of the QualiTrack Report (epic EP07,
TS53–TS64), written from the perspective of the developer who integrates the
nodes, the Edge and the cloud. Their acceptance criteria describe the observable
behavior of the implemented Edge: its HTTP contract with the nodes, its
commands, its local storage and its requests to the cloud. They align with the
Domain-Driven Design structure of the project: the IAM and Tracking bounded
contexts with their domain, application, integration, infrastructure and
interface layers, and the shared infrastructure.

## Requirements Traceability Matrix (RTX)

| Story ID | Title | Bounded Context | Related Implementation Elements |
|---|---|---|---|
| TS53 | Authenticate environmental devices in the Edge | IAM | `iam/interfaces/services.py`: `SignInRequest`, `sign_in_device`; `iam/application/services.py`: `AuthApplicationService.authenticated_device`; `iam/domain/entities.py`: `Device.authenticate`; `iam/domain/credentials.py`: `ApiCredential`; `iam/interfaces/cli.py`: `devices_cli` |
| TS54 | Authenticate container monitors in the Edge | IAM | Same elements as TS53, with `DeviceType.CONTAINER_MONITOR` |
| TS55 | Receive the telemetry of the environmental device in the Edge | Tracking; IAM (authentication) | `tracking/interfaces/services.py`: `EnvironmentReadingRequest`, `create_environment_readings`; `tracking/application/services.py`: `TelemetryApplicationService.record_readings`; `tracking/integration/iam_anti_corruption_layer.py`: `IamAntiCorruptionLayer`; `tracking/domain/entities.py`: `TelemetryReading.record`; `tracking/infrastructure/repositories.py`: `TelemetryReadingRepository` |
| TS56 | Receive the telemetry of the container monitor in the Edge | Tracking; IAM (authentication) | `tracking/interfaces/services.py`: `ContainerReadingRequest`, `create_container_readings`; the same application, integration, domain and infrastructure elements as TS55 |
| TS57 | Receive the actuation events of the container in the Edge | Tracking; IAM (authentication) | `tracking/interfaces/services.py`: `ActuationEventRequest`, `create_actuation_event`; `tracking/application/services.py`: `TelemetryApplicationService.record_actuation_event`; `tracking/domain/entities.py`: `ActuationEvent.record`; `tracking/infrastructure/repositories.py`: `ActuationEventRepository` |
| TS58 | Deliver the configuration to the environmental device from the Edge | Tracking; IAM (authentication) | `tracking/interfaces/services.py`: `get_configuration`; `tracking/application/services.py`: `EnvironmentalProfileApplicationService.configuration_for`; `tracking/domain/entities.py`: `EnvironmentalProfile`; `tracking/infrastructure/repositories.py`: `EnvironmentalProfileRepository` |
| TS59 | Deliver the configuration to the container monitor from the Edge | Tracking; IAM (authentication) | Same elements as TS58, with the profile of the container monitor and its `ActuationRule` values |
| TS60 | Keep the pending records in the Edge | Tracking | `tracking/domain/enums.py`: `SyncStatus`; `tracking/infrastructure/models.py`: `TelemetryReading`, `ActuationEvent`; `tracking/infrastructure/repositories.py`: `save_idempotently`, `find_pending`, `count_by_sync_status`; `tracking/interfaces/cli.py`: `show_pending` |
| TS61 | Synchronize the pending environmental telemetry with the cloud | Tracking; Shared (cloud session) | `tracking/application/services.py`: `TelemetrySynchronizationApplicationService.synchronize_pending`; `tracking/infrastructure/cloud_telemetry_gateway.py`: `CloudTelemetryGateway.send_reading`; `shared/infrastructure/cloud_session.py`: `CloudSession`; `shared/infrastructure/cloud_sync_scheduler.py`: `CloudSyncScheduler` |
| TS62 | Synchronize the pending container telemetry with the cloud | Tracking; Shared (cloud session) | Same elements as TS61, with the container monitor route |
| TS63 | Synchronize the pending actuation events with the cloud | Tracking; Shared (cloud session) | `tracking/infrastructure/cloud_telemetry_gateway.py`: `CloudTelemetryGateway.send_actuation_event`; the same application service and shared elements as TS61 |
| TS64 | Synchronize the configurations from the cloud to the Edge | Tracking; Shared (cloud session) | `tracking/application/services.py`: `DeviceRegistryApplicationService.synchronize_registry`, `EnvironmentalProfileApplicationService.synchronize_profiles`; `tracking/infrastructure/cloud_registry_gateway.py`: `CloudDeviceRegistryGateway`; `tracking/infrastructure/cloud_profile_gateway.py`: `CloudProfileGateway`; `tracking/interfaces/cli.py`: `synchronize` |

### TS53 — Authenticate environmental devices in the Edge
As a Developer, I want to validate the identity of each environmental device before accepting its requests in the Edge, so that data from unregistered nodes is not accepted.

Acceptance criteria:
- Scenario: Registered environmental device
  - Given the local station registered the node `ENV-001` as `ENVIRONMENTAL_DEVICE` with `flask devices register`
  - And the command showed its API key once
  - When the node sends `POST /api/v1/auth/sign-in` with `{"device_id": "ENV-001"}` and its key in `X-API-Key`
  - Then the Edge returns `200 OK`
  - And the response includes the `device_id`, `"device_type": "ENVIRONMENTAL_DEVICE"` and `"authenticated": true`

- Scenario: Unknown, revoked or wrong credentials
  - Given a request to `/api/v1/auth/sign-in`
  - And the `X-API-Key` is missing, does not match the node, the node is unknown or it was revoked
  - When the Edge validates the request
  - Then the Edge returns `401 Unauthorized`

- Scenario: Malformed request
  - Given a request to `/api/v1/auth/sign-in` that is not JSON, has invalid JSON, an empty `device_id` or unexpected fields
  - When the Edge validates the request
  - Then the Edge returns `415 Unsupported Media Type` for a non-JSON request, or `400 Bad Request` otherwise

---

### TS54 — Authenticate container monitors in the Edge
As a Developer, I want to validate the identity of each container monitor before accepting its requests in the Edge, so that data from unregistered nodes is not accepted.

Acceptance criteria:
- Scenario: Registered container monitor
  - Given the local station registered the node `CNT-001` as `CONTAINER_MONITOR`
  - When the node sends `POST /api/v1/auth/sign-in` with its `device_id` and `X-API-Key`
  - Then the Edge returns `200 OK` with `"device_type": "CONTAINER_MONITOR"`

- Scenario: Key rotated or node revoked
  - Given the local station ran `flask devices rotate-key CNT-001` or `flask devices revoke CNT-001`
  - When the node signs in with its previous key
  - Then the Edge returns `401 Unauthorized`

- Scenario: Keys are never stored in clear
  - Given a node registered in the Edge
  - When the Edge persists it
  - Then the `devices` table keeps a salted PBKDF2 verifier instead of the API key

---

### TS55 — Receive the telemetry of the environmental device in the Edge
As a Developer, I want to receive the air quality and the motion events from the environmental device through the Edge API, so that they are stored locally.

Acceptance criteria:
- Scenario: Valid reading
  - Given the environmental device sends `POST /api/v1/monitoring/environment-readings` with its `device_id` and `X-API-Key`
  - And the body has a `measured_at` with offset and at least one of `air_quality` (ppm) and `motion` (`true` or `false`)
  - When the Edge validates and stores the reading
  - Then the Edge returns `201 Created`
  - And the response lists one stored reading per metric (`AIR_QUALITY`, `MOTION` as 1 or 0) with `"sync_status": "PENDING"`
  - And `measured_at` is returned in UTC

- Scenario: Retry of the same reading
  - Given the Edge already stored the reading of a metric at a `measured_at`
  - When the node sends the same values again
  - Then the Edge returns `200 OK` with the stored readings and stores nothing new

- Scenario: Retry with different values
  - Given the Edge already stored the reading of a metric at a `measured_at`
  - When the node sends other values for that metric and time
  - Then the Edge returns `409 Conflict` and stores nothing of that request

- Scenario: Invalid reading
  - Given the body has no metric, a naive or missing `measured_at`, a time more than 5 minutes in the future, a non-finite value, a negative `profile_version`, a container metric or unexpected fields
  - When the Edge validates the request
  - Then the Edge returns `400 Bad Request` and stores nothing

- Scenario: Node of another type
  - Given a container monitor sends its credentials to `/api/v1/monitoring/environment-readings`
  - When the Edge authenticates it
  - Then the Edge returns `403 Forbidden`

---

### TS56 — Receive the telemetry of the container monitor in the Edge
As a Developer, I want to receive temperature, humidity, luminosity and the available identification from the container monitor through the Edge API, so that they are stored locally.

Acceptance criteria:
- Scenario: Valid reading
  - Given the container monitor sends `POST /api/v1/monitoring/container-readings` with its `device_id` and `X-API-Key`
  - And the body has a `measured_at` with offset and at least one of `temperature` (°C), `humidity` (%), `luminosity` (lux) and `rfid_tag`
  - When the Edge validates and stores the reading
  - Then the Edge returns `201 Created` with one `PENDING` reading per metric
  - And the RFID tag is kept as the `text_value` of `RFID_TAG`

- Scenario: Invalid reading
  - Given a tag longer than 120 characters, a non-finite value or an environmental metric
  - When the Edge validates the request
  - Then the Edge returns `400 Bad Request` and stores nothing

- Scenario: Retries
  - Given readings already stored for the node and `measured_at`
  - When the node repeats them, or repeats them with other values
  - Then the Edge returns `200 OK`, or `409 Conflict` without storing anything of the request

---

### TS57 — Receive the actuation events of the container in the Edge
As a Developer, I want to receive the ventilation, simulated cooling or servo events through the Edge API, so that the executed actions are kept.

Acceptance criteria:
- Scenario: Valid action
  - Given the container monitor sends `POST /api/v1/control/actuation-events` with its credentials
  - And the body has an `action` (`VENTILATION_ON`, `VENTILATION_OFF`, `COOLING_ON`, `COOLING_OFF`, `SERVO_OPEN`, `SERVO_CLOSE`) and an `occurred_at` with offset
  - And optionally the `trigger_metric` and `trigger_state` that caused it, the `result` (`EXECUTED` by default or `FAILED`) and the `profile_version`
  - When the Edge stores the action
  - Then the Edge returns `201 Created` with the stored action and `"sync_status": "PENDING"`

- Scenario: Retry of the same action
  - Given the Edge already stored the action of the node at an `occurred_at`
  - When the node sends it again with the same data, or with other data
  - Then the Edge returns `200 OK` with the stored action, or `409 Conflict`

- Scenario: Invalid action
  - Given an unknown action, a cause with only the metric or only the state, a cause based on `RFID_TAG`, `MOTION` or `AIR_QUALITY`, or a future time
  - When the Edge validates the request
  - Then the Edge returns `400 Bad Request`

- Scenario: Not a container monitor
  - Given the environmental device sends an action with its credentials
  - When the Edge authenticates it
  - Then the Edge returns `403 Forbidden`

---

### TS58 — Deliver the configuration to the environmental device from the Edge
As a Developer, I want to expose from the Edge the current configuration of the environmental device, so that the ESP32 gets its operating criteria.

Acceptance criteria:
- Scenario: Configuration available
  - Given the Edge copied from the cloud the profile of the environment of `ENV-001`
  - When the node sends `GET /api/v1/configurations/ENV-001` with its `X-API-Key`
  - Then the Edge returns `200 OK`
  - And the response includes the `device_type`, the `profile_version`, the thresholds (`normal_min`, `normal_max`, `critical_min`, `critical_max`, `unit`) and the copy times

- Scenario: No configuration yet
  - Given the Edge has no profile of the cloud for the node
  - When the node requests its configuration
  - Then the Edge returns `404 Not Found`

- Scenario: Another node or no credentials
  - Given a request without `X-API-Key`, or with the key of another node
  - When the Edge authenticates it
  - Then the Edge returns `401 Unauthorized`

---

### TS59 — Deliver the configuration to the container monitor from the Edge
As a Developer, I want to expose from the Edge the current configuration of the container monitor, so that the ESP32 gets its ranges and rules.

Acceptance criteria:
- Scenario: Configuration available
  - Given the Edge copied from the cloud the profile of the container monitor `CNT-001`
  - When the node sends `GET /api/v1/configurations/CNT-001` with its `X-API-Key`
  - Then the Edge returns `200 OK` with its thresholds and its `actuation_rules` (`metric`, `state`, `action`)

- Scenario: Configuration missing or credentials rejected
  - Given the Edge has no profile for the node, or the key does not match
  - When the node requests its configuration
  - Then the Edge returns `404 Not Found`, or `401 Unauthorized`

---

### TS60 — Keep the pending records in the Edge
As a Developer, I want to keep in SQLite the pending records of both types of device, so that no information is lost when the cloud is not available.

Acceptance criteria:
- Scenario: Local persistence
  - Given the Edge receives a reading or an action that it cannot synchronize yet
  - When it stores the record
  - Then the record stays in SQLite with `"sync_status": "PENDING"`
  - And `flask cloud pending` counts it

- Scenario: Later recovery
  - Given pending records in SQLite
  - When the cloud becomes available again, or the container of the Edge is recreated
  - Then the records are still pending and the synchronization sends them later

---

### TS61 — Synchronize the pending environmental telemetry with the cloud
As a Developer, I want to synchronize the pending environmental measurements and events from the Edge to the cloud, so that the information is centralized when there is connectivity.

Acceptance criteria:
- Scenario: Accepted reading
  - Given a pending reading of an environmental device that the cloud registry places in an environment
  - When the synchronization sends it to `POST /api/v1/laboratories/{laboratoryId}/environments/{environmentId}/telemetry-measurements` with the token of the operator account
  - And the cloud answers `201 Created`, or `200 OK` because it already had it
  - Then the reading becomes `SYNCED`

- Scenario: Rejected reading
  - Given the cloud answers `400`, `404`, `409` or `422`
  - When the synchronization receives the answer
  - Then the reading becomes `REJECTED` and the reason is logged

- Scenario: Node without environment or cloud not available
  - Given the registry does not place the node in an environment, or the cloud does not answer
  - When the synchronization runs
  - Then the reading stays `PENDING` and the next cycle retries it

---

### TS62 — Synchronize the pending container telemetry with the cloud
As a Developer, I want to synchronize the pending container measurements from the Edge to the cloud, so that the information is centralized when there is connectivity.

Acceptance criteria:
- Scenario: Accepted reading
  - Given a pending reading of a container monitor placed in an environment
  - When the synchronization sends it to `POST /api/v1/laboratories/{laboratoryId}/environments/{environmentId}/container-monitors/{deviceId}/telemetry-measurements`
  - Then the reading becomes `SYNCED` on `201` or `200`, and `REJECTED` on `400`, `404`, `409` or `422`

- Scenario: Oldest first
  - Given several pending readings
  - When the synchronization runs
  - Then it sends them in the order the Edge received them, in batches, until none is left

---

### TS63 — Synchronize the pending actuation events with the cloud
As a Developer, I want to synchronize the actuation events of the containers from the Edge to the cloud, so that the automatic responses remain traceable.

Acceptance criteria:
- Scenario: Accepted action
  - Given a pending action of a container monitor placed in an environment
  - When the synchronization sends it to `POST /api/v1/laboratories/{laboratoryId}/environments/{environmentId}/container-monitors/{deviceId}/actuation-events`
  - Then the action becomes `SYNCED` on `201` or `200`, and `REJECTED` on `400`, `404`, `409` or `422`

- Scenario: Expired token of the operator account
  - Given the cloud answers `401` because the token expired
  - When the synchronization sends a record
  - Then the Edge signs in again once and repeats the request

---

### TS64 — Synchronize the configurations from the cloud to the Edge
As a Developer, I want to get from the cloud the current configurations of both types of device, so that the Edge keeps an available copy.

Acceptance criteria:
- Scenario: Registry and profiles copied
  - Given the operator account of the Edge can access the laboratory
  - When the synchronization reads `GET /api/v1/laboratories/{laboratoryId}/equipments` and, for each node placed in an environment, `GET /api/v1/laboratories/{laboratoryId}/environments/{environmentId}/devices/{deviceId}/environmental-profile`
  - Then the Edge keeps the cloud identifiers of each node and the profile it applies with its version
  - And `flask cloud devices` and `flask cloud profiles` list them

- Scenario: Node without profile
  - Given the cloud answers `404 Not Found` for the profile of a node
  - When the synchronization finishes
  - Then the Edge has no profile for that node

- Scenario: Cloud not available
  - Given the cloud does not answer while the profiles are read
  - When the synchronization stops
  - Then the Edge keeps the previous copy, so the nodes keep their configuration
