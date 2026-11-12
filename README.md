# QualiTrack Edge Service

`qualitrack-edge-service` is the Edge Application of QualiTrack. It runs on the
local station of each pharmaceutical facility (a Raspberry Pi 4) and sits
between the ESP32 nodes and the QualiTrack Cloud REST API. It follows a
Domain-Driven Design (DDD) approach.

The QualiTrack nodes are:

- the **environmental device** (`ENVIRONMENTAL_DEVICE`), which monitors the air
  quality and motion of an environment;
- the **container monitor** (`CONTAINER_MONITOR`), which monitors the
  temperature, humidity, luminosity and RFID access of a container and acts
  on its fan and servo.

## Current Scope

- **Implemented**
  - sign-in of the environmental device and the container monitor with their
    `device_id` and `X-API-Key` at `POST /api/v1/auth/sign-in` (TS53, TS54)
  - `flask devices` commands to register the nodes with a random API key,
    rotate their key, revoke them and list them
  - API keys stored as salted PBKDF2 verifiers
  - optional registration of a development test node
  - connection with the QualiTrack Cloud REST API as an Edge Gateway, with
    its identifier and its API key in the `X-API-Key` header
  - local copy of the cloud registry of the nodes the cloud assigns to this
    Edge Gateway (environment and cloud identifiers of each node), refreshed
    by a background thread and by `flask cloud sync`
  - reception of the air quality and motion of the environmental device
    (TS55), of the temperature, humidity, luminosity and RFID tags of the
    container monitor (TS56), and of the actions of the container monitor
    (TS57), validated with the rules of the cloud
  - local storage of every reading and action as `PENDING` until the cloud
    accepts it (TS60); a retry of the same observation is not stored twice
  - synchronization of the pending readings of the environmental device (TS61)
    and the container monitors (TS62) and of the actions (TS63) with the
    cloud, oldest first, by the background thread and by `flask cloud sync`
  - local copy of the environmental profile of each node, read from the cloud
    (TS64), and its delivery to the environmental device (TS58) and the
    container monitor (TS59) at `GET /api/v1/configurations/{deviceId}`
  - SQLite persistence through Peewee
  - deployment on the local station with Docker Compose and Gunicorn, with the
    database in the `qualitrack-data` volume (report 4.1.3.4)

These cover the Edge stories of the QualiTrack Report (epic EP07, TS53–TS64).

## Bounded Contexts

### IAM (Identity and Access Management)

Identifies the ESP32 nodes and validates their credentials.

- **Core concept**: `Device`
- **Device identity**: `device_id` is the identifier with which the cloud
  registers the node for the Edge (`sensorExternalId`, for example `ENV-001`).

### Tracking

Receives the telemetry and actions of the nodes, keeps them until the cloud
accepts them, and hands each node the configuration it applies. It is the Edge
counterpart of the Tracking & Telemetry bounded context of the cloud platform.

- **Core concepts**: `TelemetryReading` (one metric a node measured at a
  moment) and `ActuationEvent` (an action of a container monitor), each with
  its synchronization status; `EnvironmentalProfile`, the thresholds and
  actuation rules a node applies, with the version of the cloud.
- **Anti-corruption layer**: `IamAntiCorruptionLayer` translates IAM's
  authentication into Tracking's own `ReportingDevice`, so Tracking does not
  depend on IAM's model.

### Sync

Exchanges data with the cloud: it reads the device registry and the profiles
and sends the readings and actions Tracking keeps pending. It uses the model
of Tracking directly, and Tracking does not depend on it (ADR-0015).

- **Core concept**: `MonitoredDevice`, a node of this Edge as the cloud
  registers it, with the cloud identifiers its telemetry is synchronized to.
- **Anti-corruption layer**: its own `IamAntiCorruptionLayer` reports the nodes
  registered in this Edge.
- **Cloud gateways**: `CloudDeviceRegistryGateway` reads the equipment of the
  laboratory, `CloudProfileGateway` the profile of each node and
  `CloudTelemetryGateway` sends the pending readings and actions. Each one
  translates the resources of the cloud into Edge concepts.
- **Commands**: `flask cloud sync`, `pending`, `devices` and `profiles`.
- **Enrollment**: `POST /api/v1/devices/enroll` gives a node the cached cloud
  registry authorizes its local API key (development only).

### Shared

Infrastructure shared by the bounded contexts: the SQLite database, the
authenticated session with the cloud and the background synchronization
thread.

## Layered Architecture

Each bounded context follows the same structure:

- **Domain**: entities, value objects, repository contracts and business rules,
  without framework or ORM concerns.
- **Application**: use cases that coordinate repositories and domain objects.
- **Integration**: the anti-corruption layer a context uses to reach another
  one.
- **Infrastructure**: Peewee models, repository implementations and cloud
  gateways.
- **Interfaces**: Flask HTTP endpoints with request validation in Pydantic, and
  Flask commands.

```text
qualitrack-edge-service/
├── app.py               # application factory and composition
├── config.py            # environment settings
├── wsgi.py              # entry point of Gunicorn
├── gunicorn.conf.py
├── Dockerfile
├── docker-compose.yml   # service qualitrack-edge and volume qualitrack-data
├── iam/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   └── interfaces/
├── tracking/
│   ├── domain/
│   ├── application/
│   ├── integration/
│   ├── infrastructure/
│   └── interfaces/
├── sync/
│   ├── domain/
│   ├── application/
│   ├── integration/
│   ├── infrastructure/
│   └── interfaces/
├── shared/
│   └── infrastructure/
├── docs/                # technical stories, class diagram, ADRs
└── tests/
```

## Technical Stories

The technical stories are the Edge stories of the QualiTrack Report (epic
EP07). They describe the contract of the Edge with the nodes and the cloud from
the perspective of the developer who integrates them, for unattended
node-to-Edge and Edge-to-cloud interactions.

- Source of truth: [`docs/user-stories.md`](docs/user-stories.md)
- Story style: `As a ... I want to ... so that ...` with Gherkin acceptance
  criteria (`Given/When/Then`) and a Requirements Traceability Matrix to the
  implementation
- Focus: observable Edge behavior (authentication, validation, local storage,
  synchronization, configuration), not user interface behavior

### Story-to-Context Mapping

- **IAM bounded context**: `TS53`, `TS54`, and the authentication of `TS55`–`TS59`
- **Tracking bounded context**: `TS55`, `TS56`, `TS57`, `TS58`, `TS59`, `TS60`
- **Sync bounded context**: `TS61`, `TS62`, `TS63`, `TS64`, and the
  `flask cloud pending` command of `TS60`
- **Shared infrastructure**: the cloud session and the background
  synchronization of `TS61`–`TS64`

## Technology Stack

- Python 3.13+
- Flask
- Peewee
- Pydantic
- python-dotenv
- Requests
- SQLite
- Gunicorn
- Docker Compose (Raspberry Pi 4 with Raspberry Pi OS Lite on the local station)

Exact Python dependencies are declared in [`requirements.txt`](requirements.txt).

## Getting Started

### 1. Create a virtual environment

```sh
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```sh
pip install -r requirements.txt
```

### 3. Configure the environment

Copy [`.env.example`](.env.example) to `.env.development` and adjust the values.
`APP_ENV` selects the file (`.env.development` or `.env.production`), and
process environment variables override its values. `config.py` validates the
settings and does not supply defaults for missing ones.

| Variable | Description |
|---|---|
| `APP_ENV` | `development` or `production` |
| `DEBUG` | Flask debug mode; must be `false` in production |
| `DATABASE_PATH` | Path of the SQLite database, relative to the project root |
| `SEED_TEST_DEVICE` | Registers a development test node on startup; must be `false` in production |
| `TEST_DEVICE_ID` | Identifier of the test node, as the cloud registers it (`sensorExternalId`) |
| `TEST_DEVICE_API_KEY` | Local-only API key of the test node; required when seeding is enabled |
| `HOST` | Address the service listens on; `0.0.0.0` lets the nodes of the local network reach it |
| `PORT` | Port the service listens on |
| `CLOUD_SYNC_ENABLED` | Connects the Edge with the QualiTrack Cloud REST API |
| `CLOUD_API_URL` | Base URL of the cloud, without `/api/v1`; it must use `https` in production |
| `CLOUD_DEVICE_ID` | Identifier of this Edge Gateway in the cloud (its `sensorExternalId`) |
| `CLOUD_API_KEY` | API key the cloud issued for this Edge Gateway; keep it out of version control |
| `CLOUD_SYNC_INTERVAL_SECONDS` | Seconds between two synchronizations of the background thread |
| `SYNCED_RETENTION_DAYS` | Optional, 7 by default. Days the Edge keeps a reading or action after the cloud accepted it; then it deletes it |
| `REJECTED_RETENTION_DAYS` | Optional, 90 by default. Days the Edge keeps a reading or action the cloud refused for good; then it deletes it |

The cloud settings other than `CLOUD_SYNC_ENABLED` are required only when the
synchronization is enabled.

While the synchronization is enabled, the background thread also deletes the
old `SYNCED` and `REJECTED` readings and actions (at most once an hour, in
short batches). Pending records are never deleted, so the database still grows
if the cloud cannot take them; `flask --app app cloud pending` shows how many
there are.

### 4. Connect the Edge with the cloud

The Edge authenticates in the cloud as an Edge Gateway (ADR-0014). In
QualiTrack, register the Edge Gateway, place it in the environment it serves
and generate its API key, which the cloud shows only once. Then set
`CLOUD_DEVICE_ID` (the identifier of the Gateway) and `CLOUD_API_KEY` with the
other `CLOUD_*` settings and check the connection:

```sh
flask --app app cloud sync
flask --app app cloud devices
flask --app app cloud profiles
```

`cloud sync` runs now what the background thread runs every
`CLOUD_SYNC_INTERVAL_SECONDS` while the service is up:

1. It reads the IoT devices the cloud assigns to this Edge Gateway
   (`/edge-gateways/{deviceId}/iot-devices`): environmental devices and
   container monitors of the environment of the Gateway, 15 at most. When the
   environment has more devices, the cloud distributes them among its
   Gateways, so the Edge neither chooses nor filters environments. `cloud
   devices` lists this local copy with the environment and cloud identifier of
   each node, and `cloud sync` names the nodes registered in this Edge that the
   cloud does not assign to it, for example because of a different
   `sensorExternalId`.
2. It copies the environmental profile of every node placed in an
   environment (`/edge-gateways/{deviceId}/iot-devices/{id}/environmental-profile`):
   the profile of the environment for the environmental device, and the
   profile of the container monitor for a container monitor. A node without a
   profile in the cloud has none in the Edge. The copy changes only when every
   profile was read, so the nodes keep the last one while the cloud is
   unreachable. `cloud profiles` lists it.
3. It sends the pending readings and actions, oldest first, to the cloud route
   of their node (`/edge-gateways/{deviceId}/iot-devices/{id}/telemetry-measurements`,
   and `.../actuation-events` for the actions of a container monitor).
   - `201` or `200` (the cloud already had it): the record becomes `SYNCED`.
   - `400`, `404`, `409` or `422`: the cloud refuses it for good, so it becomes
     `REJECTED` and the reason is logged.
   - A node the registry does not place in an environment yet: its records
     stay `PENDING` until it is.
   - Cloud not reachable, a server error or a rejected API key: the
     synchronization stops, the records keep their state and the next cycle
     retries them.
4. It deletes the records that became `SYNCED` more than
   `SYNCED_RETENTION_DAYS` ago and the `REJECTED` ones older than
   `REJECTED_RETENTION_DAYS`, counted from the moment the Edge received them.

### 5. Register the nodes

Register each ESP32 with the identifier the cloud registers for it
(`sensorExternalId`) and its type. The command generates a random API key and
shows it only once: write it to the firmware of the node. The Edge keeps only
its one-way verifier.

```sh
flask --app app devices register ENV-001 ENVIRONMENTAL_DEVICE
flask --app app devices register CNT-001 CONTAINER_MONITOR
flask --app app devices list
```

`flask --app app devices rotate-key <device_id>` gives a node a new key and
invalidates the previous one. `flask --app app devices revoke <device_id>`
stops a node from signing in. These operations are not exposed over HTTP.

### 6. Run the service

```sh
APP_ENV=development python app.py
```

`python app.py` also starts the background synchronization with the cloud;
`flask` commands never start it. The built-in Flask server is for development
only; the local station runs Gunicorn (see [Deployment](#deployment-on-the-local-station)).

`flask --app app cloud pending` counts the stored readings and actions by
synchronization status (`PENDING`, `SYNCED`, `REJECTED`).

### 7. Run the tests

```sh
python -m unittest -v
```

## Deployment on the Local Station

The Edge runs on the local station of the facility, a Raspberry Pi 4 with
Raspberry Pi OS Lite and Docker Engine (report 4.1.3.4). Docker Compose runs
the service `qualitrack-edge`: the Edge REST API on Gunicorn with one worker
process, as SQLite and the synchronization thread expect, which also starts the
background synchronization with the cloud. The named volume `qualitrack-data`
keeps the SQLite database, so the readings survive a restart or the recreation
of the container. The container restarts with the station.

### 1. Configure the station

```sh
cp .env.example .env.production
```

In `.env.production`, set `TEST_DEVICE_ID` (required by the settings even
without the test node) and the `CLOUD_*` settings of the Edge Gateway, with
an `https` URL. `docker-compose.yml` fixes the production values: `APP_ENV`,
`DEBUG=false`, `SEED_TEST_DEVICE=false`, `DATABASE_PATH=/data/edge.db`, `HOST`
and `PORT`. Keep `.env.production` out of version control.

### 2. Start the service

```sh
docker compose up -d --build
docker compose logs -f qualitrack-edge
```

The Edge listens on port `5000` of the station; the nodes use
`http://<station address>:5000/api/v1`.

### 3. Register the nodes and check the cloud

The `flask` commands run inside the container:

```sh
docker compose exec qualitrack-edge flask --app app devices register ENV-001 ENVIRONMENTAL_DEVICE
docker compose exec qualitrack-edge flask --app app devices register CNT-001 CONTAINER_MONITOR
docker compose exec qualitrack-edge flask --app app cloud sync
docker compose exec qualitrack-edge flask --app app cloud pending
```

### 4. Update the service

```sh
git pull
docker compose up -d --build
```

`docker compose down` stops the service and keeps the volume; only
`docker compose down -v` deletes the database. The CI builds the image for
`linux/arm64`, the architecture of the Raspberry Pi 4, and runs the compose
file to check the service, the commands and the volume.

## API Contract

### Sign in a node

`POST /api/v1/auth/sign-in`

Checks that the environmental device or container monitor is registered,
active, and presents its matching API key (TS53, TS54).

#### Required headers

- `Content-Type: application/json`
- `X-API-Key: <node api key>`

#### Request body

```json
{
  "device_id": "ENV-001"
}
```

#### Responses

- `200 OK`: credentials are valid.
- `400 Bad Request`: invalid JSON, an empty `device_id` or unexpected fields.
- `401 Unauthorized`: missing or invalid API key, or unknown or revoked node.
- `415 Unsupported Media Type`: the request is not JSON.

#### Success response

```json
{
  "device_id": "ENV-001",
  "device_type": "ENVIRONMENTAL_DEVICE",
  "authenticated": true
}
```

### Send readings and actions

The three endpoints below share these rules:

- Headers: `Content-Type: application/json` and `X-API-Key: <node api key>`;
  the body carries the `device_id` of the node.
- Times are ISO 8601 with an offset, supplied by the node and normalized to
  UTC. They cannot be more than 5 minutes in the future, as in the cloud.
- `profile_version` (optional) is the version of the environmental profile the
  node applied.
- A node, metric and measurement time identify a reading, and a node, action
  and time identify an action, as in the cloud. Sending the same observation
  again returns the stored one with `200 OK` instead of storing it twice;
  sending it with different data returns `409 Conflict` and stores nothing of
  that request.
- Every stored reading and action starts as `PENDING` until the cloud accepts
  it.

| Response | When |
|---|---|
| `201 Created` | something new was stored |
| `200 OK` | identical retry |
| `400 Bad Request` | invalid JSON, unknown fields, a missing or naive time, a future time, a non-finite value, motion other than `true`/`false`, a tag longer than 120 characters, an incomplete cause, or no metric at all |
| `401 Unauthorized` | missing `device_id`, missing or invalid API key, unknown or revoked node |
| `403 Forbidden` | the node type does not send that telemetry |
| `409 Conflict` | the observation was already sent with different data |
| `415 Unsupported Media Type` | the request is not JSON |

#### Environmental device readings (TS55)

`POST /api/v1/monitoring/environment-readings`

```json
{
  "device_id": "ENV-001",
  "measured_at": "2026-11-03T10:15:00-05:00",
  "profile_version": 3,
  "air_quality": 412.5,
  "motion": true
}
```

`air_quality` is in ppm and `motion` is `true` when motion is detected and
`false` when the zone is quiet again. Each one is optional, but a reading needs
at least one.

```json
{
  "device_id": "ENV-001",
  "measured_at": "2026-11-03T15:15:00Z",
  "profile_version": 3,
  "readings": [
    {"id": 1, "metric": "AIR_QUALITY", "value": 412.5, "text_value": null, "sync_status": "PENDING"},
    {"id": 2, "metric": "MOTION", "value": 1.0, "text_value": null, "sync_status": "PENDING"}
  ]
}
```

#### Container monitor readings (TS56)

`POST /api/v1/monitoring/container-readings`

```json
{
  "device_id": "CNT-001",
  "measured_at": "2026-11-03T10:15:00-05:00",
  "profile_version": 3,
  "temperature": 21.5,
  "humidity": 48,
  "luminosity": 120,
  "rfid_tag": "E200-3412-0001"
}
```

`temperature` is in °C, `humidity` in %, `luminosity` in lux and `rfid_tag` is
the tag read on the container. Each one is optional, but a reading needs at
least one. The response has the same shape as the environmental one.

#### Container monitor actions (TS57)

`POST /api/v1/control/actuation-events`

```json
{
  "device_id": "CNT-001",
  "action": "VENTILATION_ON",
  "trigger_metric": "TEMPERATURE",
  "trigger_state": "WARNING",
  "result": "EXECUTED",
  "occurred_at": "2026-11-03T10:15:05-05:00",
  "profile_version": 3
}
```

- `action`: `VENTILATION_ON`, `VENTILATION_OFF`, `COOLING_ON`, `COOLING_OFF`,
  `SERVO_OPEN` or `SERVO_CLOSE`.
- `trigger_metric` and `trigger_state` (optional, together): the metric with
  thresholds (`TEMPERATURE`, `HUMIDITY` or `LUMINOSITY`) and the state
  (`WARNING`, `CRITICAL` or `NORMAL`) that caused the action. An action opened
  by an RFID tag has no cause.
- `result` (optional): `EXECUTED` (default) or `FAILED`.

The response is the stored action with its `id` and `sync_status`.

### Read the configuration of a node (TS58, TS59)

`GET /api/v1/configurations/{deviceId}`

Header: `X-API-Key: <node api key>`. The node reads its own configuration: the
profile of its environment for the environmental device, or its own profile
for a container monitor, as the Edge last copied it from the cloud. The node
applies the thresholds and rules and sends `profile_version` back with its
readings and actions.

| Response | When |
|---|---|
| `200 OK` | the configuration of the node |
| `401 Unauthorized` | missing or invalid API key, or unknown or revoked node |
| `404 Not Found` | the Edge has no profile of the cloud for the node yet |

```json
{
  "device_id": "CNT-001",
  "device_type": "CONTAINER_MONITOR",
  "profile_version": 3,
  "thresholds": [
    {"metric": "TEMPERATURE", "unit": "°C", "normal_min": 15.0, "normal_max": 25.0,
     "critical_min": 8.0, "critical_max": 30.0}
  ],
  "actuation_rules": [
    {"metric": "TEMPERATURE", "state": "WARNING", "action": "VENTILATION_ON"}
  ],
  "updated_at": "2026-11-03T14:00:00Z",
  "synced_at": "2026-11-03T15:00:00Z"
}
```

A value inside `normal_min`..`normal_max` is `NORMAL`, outside it but inside
`critical_min`..`critical_max` is `WARNING`, and beyond it is `CRITICAL`; a
`null` side has no limit. `updated_at` is the last change in the cloud and
`synced_at` when the Edge copied it.

## Documentation

Additional documentation is available in [`docs/`](docs):

- [`docs/user-stories.md`](docs/user-stories.md): technical stories TS53–TS64
  with their acceptance criteria and the Requirements Traceability Matrix.
- [`docs/class-diagram.puml`](docs/class-diagram.puml): PlantUML diagram of the
  bounded contexts, their layers and relationships, and the composition of the
  application.
- [`docs/adrs.md`](docs/adrs.md): architecture decisions and their
  consequences.

## Changelog

Project history is tracked in [`CHANGELOG.md`](CHANGELOG.md) using the Keep a
Changelog format.

## License

This project is licensed under the QualiTrack Proprietary Software License. See
[`LICENSE.md`](LICENSE.md) for details.
