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
  - SQLite persistence through Peewee
- **Planned** (technical stories of the QualiTrack Report, epic EP07)
  - reception of environmental and container readings and of actuation events
    (TS55, TS56, TS57)
  - local storage of the records that are not synchronized yet (TS60)
  - synchronization of readings and actuation events with the cloud (TS61,
    TS62, TS63)
  - download of the environmental profile of each node from the cloud and
    delivery to the ESP32 (TS64, TS58, TS59)
  - deployment with Docker Compose and Gunicorn on the local station

## Bounded Contexts

### IAM (Identity and Access Management)

Identifies the ESP32 nodes and validates their credentials.

- **Core concept**: `Device`
- **Device identity**: `device_id` is the identifier with which the cloud
  registers the node for the Edge (`sensorExternalId`, for example `ENV-001`).

### Tracking

Will receive the telemetry and actuation events of the nodes, keep them until
they are synchronized with the cloud, and keep the environmental profile each
node applies. It is the Edge counterpart of the Tracking & Telemetry bounded
context of the cloud platform.

### Shared

Infrastructure shared by the bounded contexts, such as the SQLite database.

## Layered Architecture

Each bounded context follows the same structure:

- **Domain**: entities, value objects, repository contracts and business rules,
  without framework or ORM concerns.
- **Application**: use cases that coordinate repositories and domain objects.
- **Infrastructure**: Peewee models and repository implementations.
- **Interfaces**: Flask HTTP endpoints and request validation with Pydantic.

```text
qualitrack-edge-service/
├── app.py
├── config.py
├── iam/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   └── interfaces/
├── tracking/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   └── interfaces/
├── shared/
│   └── infrastructure/
└── tests/
```

## Technology Stack

- Python 3.13+
- Flask
- Peewee
- Pydantic
- python-dotenv
- Requests
- SQLite

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

### 4. Register the nodes

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

### 5. Run the service

```sh
APP_ENV=development python app.py
```

The built-in Flask server is for development only. Production must run behind
a production WSGI server.

### 6. Run the tests

```sh
python -m unittest -v
```

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

## Changelog

Project history is tracked in [`CHANGELOG.md`](CHANGELOG.md) using the Keep a
Changelog format.

## License

This project is licensed under the QualiTrack Proprietary Software License. See
[`LICENSE.md`](LICENSE.md) for details.
