# Architecture Decision Records

This document records the decisions that shape the QualiTrack Edge Service.
The records are concise and focus on decisions that affect the boundaries of
the service, its contracts with the nodes and the cloud, and its operation on
the local station.

The records were written when the decisions were made, during the
implementation of the Edge stories of the QualiTrack Report (TS53–TS64). New
records are appended rather than rewriting an accepted decision. If a decision
changes, a new ADR supersedes the earlier record and links the two.

## ADR format

Each record includes:

- **Status**: `Accepted`, `Superseded`, or `Deprecated`
- **Date**: the date the decision was made
- **Context**: the problem and constraints
- **Decision**: the chosen approach
- **Consequences**: important benefits and trade-offs
- **Alternatives considered**: credible options that were not selected

## ADR-0001: Organize the Edge in the IAM, Tracking and Shared contexts

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

The Edge identifies the ESP32 nodes, receives their telemetry, keeps it until
the cloud accepts it and hands each node its configuration. The report
evaluates the alerts in the cloud, not in the Edge (5.6). The first scaffold
also had empty Compliance & Alerting and Laboratory folders.

### Decision

Structure the service as two bounded contexts and a shared kernel, following
the course example:

- **IAM** owns the identity and API key of each node.
- **Tracking** owns the readings, the actions, their synchronization, the local
  copy of the cloud registry and the profiles. It is the Edge counterpart of
  the Tracking & Telemetry context of the cloud.
- **Shared** holds infrastructure without domain: the SQLite database, the
  session with the cloud and the synchronization thread.

Each context has domain, application, infrastructure and interface layers;
Tracking also has an integration layer for its anti-corruption layer.

### Consequences

- Responsibilities follow the report and the cloud platform.
- No context exists without behavior; the alerts stay in the cloud.
- Tracking concentrates most of the behavior of the Edge.

### Alternatives considered

- Keeping Compliance & Alerting and Laboratory contexts in the Edge, which
  would have duplicated cloud behavior or stayed empty.
- Contexts named after the routes of the report (monitoring, control,
  configuration), which would have split one model across three contexts.

## ADR-0002: Use SQLite with Peewee for the local store

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

The Edge must keep the registered nodes, the pending telemetry and the copies
of the cloud on a Raspberry Pi, and keep them while the internet connection is
down (report 4.1.3, TS60).

### Decision

Use SQLite as the local database and Peewee as the ORM, as the course example
and the report do. ORM models and repository implementations live in the
infrastructure layer; domain entities and repository protocols do not depend
on them.

### Consequences

- The station needs no database server.
- The repository boundary allows the persistence technology to change.
- SQLite expects one writing process, so the service runs with a single
  worker process (ADR-0010).

### Alternatives considered

- PostgreSQL or MySQL on the station, which adds a server to operate.
- Files or an in-memory queue, which do not give transactions or durability.

## ADR-0003: Authenticate the nodes with an API key on every request

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

The ESP32 nodes send data unattended and must be identified before the Edge
accepts it (TS53, TS54, SP07). The course example authenticates devices with a
paired API key; another reference project issues a token after a sign-in with
the MAC address.

### Decision

Each request carries the `device_id` of the node and its API key in the
`X-API-Key` header. IAM keeps a salted PBKDF2 verifier of the key and checks
it on every request. `POST /api/v1/auth/sign-in`, the route of the report,
reports whether the credentials are valid and the type of the node. The
`device_id` is the identifier with which the cloud registers the node for the
Edge (`sensorExternalId`).

### Consequences

- The firmware sends the same headers on every request and manages no token.
- The API key is a secret that must be provisioned (ADR-0004).
- The node identifier matches the cloud registry without translation.

### Alternatives considered

- Sign-in with the MAC address and a token, where the MAC address is not a
  secret.
- Sign-in with the API key and a token, which adds token renewal to the
  firmware.

## ADR-0004: Register the nodes with Flask commands

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

The cloud does not keep the API keys of the nodes, so the local station must
register them. The course example does not expose registration over HTTP.

### Decision

`flask devices register <device_id> <type>` generates a random API key, shows
it once to write it to the firmware and stores only its verifier.
`rotate-key`, `revoke` and `list` complete the lifecycle. None of these
operations is exposed over HTTP.

### Consequences

- The keys are never stored in clear and never travel over the local network
  except from the node.
- Registering a node needs access to the station.
- A lost key is replaced with `rotate-key`.

### Alternatives considered

- A seed file with the keys in clear.
- A single test node defined in the settings, which serves only development.

## ADR-0005: Connect to the cloud with the account of an operator

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

The cloud accepts the telemetry of the Edge on its REST API with a user token;
the report states that the Edge uses the account of an operator and that
device credentials are pending (5.6).

### Decision

The shared `CloudSession` signs in with `CLOUD_USERNAME` and `CLOUD_PASSWORD`,
sends the JWT as a Bearer token and signs in again once when the cloud answers
`401`. It reports unreachable clouds, rejected credentials and laboratories
that are not ready with their own errors. Production requires `https`.

### Consequences

- The Edge uses the endpoints the cloud already has.
- The operator account must have changed its first password and its
  laboratory must have an active subscription.
- The password lives in the settings of the station, outside version control.

### Alternatives considered

- Device credentials in the cloud, which the cloud does not offer yet.

## ADR-0006: Keep the cloud registry and the profiles in Tracking

- **Status**: Accepted
- **Date**: 2026-11-03
- **Amended by**: ADR-0011, which keeps only the nodes registered in this Edge

### Context

The routes of the cloud need the laboratory, the environment and the
equipment identifier of each node. The nodes also need the profile they apply
while the cloud is unreachable (TS64).

### Decision

Tracking keeps a local copy of the IoT devices of the laboratory
(`MonitoredDevice`) and of the profile of each one (`EnvironmentalProfile`).
Cloud gateways in its infrastructure layer read them and translate the cloud
resources into Tracking concepts. Each copy is replaced only when the cloud
answered completely. IAM keeps only the local identity and key, so a node
signs in even when the cloud is unreachable.

### Consequences

- Tracking builds the routes of the cloud without asking IAM.
- The nodes keep their last configuration while the cloud is unreachable.
- A node that the registry does not place in an environment waits.

### Alternatives considered

- Adding the cloud identifiers to the IAM device and refusing nodes the cloud
  does not know, which stops every node while the cloud has never answered.

## ADR-0007: Synchronize with the cloud in a background thread

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

The registry, the profiles and the pending telemetry must flow between the
Edge and the cloud without anyone running them, also when no node is sending
data.

### Decision

`CloudSyncScheduler` runs, every `CLOUD_SYNC_INTERVAL_SECONDS`, the device
registry, then the profiles, then the pending telemetry, each with its own
database connection. A failed task is logged and retried in the next cycle.
The serving process starts it (`python app.py` or `wsgi.py`); `flask`
commands never start it, and `flask cloud sync` runs the same tasks on demand.

### Consequences

- The synchronization does not depend on the traffic of the nodes.
- The order gives each record the route of its node before it is sent.
- The service runs one process, so only one thread synchronizes.

### Alternatives considered

- Checking the interval before each request, which stops synchronizing when
  no node sends data.
- Manual synchronization only.

## ADR-0008: Receive the metrics of one moment together and identify each reading as the cloud does

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

A container monitor measures temperature, humidity, luminosity and RFID tags
in the same cycle (TS56). The cloud receives one metric per request and
identifies a reading by its device, metric and time, and an action by its
device, action and time (report 5.6).

### Decision

A reading request carries the metrics measured at one moment, each optional
but at least one. The Edge stores one record per metric, with the same
identity as the cloud. A retry with the same data returns the stored records
with `200 OK`; a retry with different data returns `409 Conflict` and stores
nothing of the request, in one SQLite transaction.

### Consequences

- The firmware makes one request per cycle.
- The synchronization maps each record to one cloud request.
- The node supplies the measurement time, which must be reliable (NTP).

### Alternatives considered

- One metric per request, which multiplies the requests of the container
  monitor.
- A device-generated event identifier, which the cloud does not use.

## ADR-0009: Apply the rules of the cloud before storing

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

A record the cloud rejects after the Edge accepted it can only be marked as
rejected. The node should learn about invalid data when it sends it.

### Decision

The Tracking domain applies the rules of the Tracking & Telemetry context of
the cloud: the node type reports the metric, numeric values are finite, motion
is 0 or 1, tags have up to 120 characters, actions have a complete cause based
on a metric with thresholds, times are timezone-aware and at most 5 minutes in
the future, and profiles have valid thresholds and rules. Pydantic validates
the shape of each request at the HTTP boundary.

### Consequences

- Invalid data is answered with `400 Bad Request` at the moment it is sent.
- The rules must follow the cloud when the cloud changes them.

### Alternatives considered

- Storing everything and letting the cloud reject it during the
  synchronization.

## ADR-0010: Deploy with Docker Compose and Gunicorn

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

The report deploys the Edge on a Raspberry Pi 4 with Docker Compose: the
service `qualitrack-edge` runs the Edge REST API on Gunicorn and the volume
`qualitrack-data` keeps the local database (4.1.3.4).

### Decision

A multi-architecture `python:3.13-slim` image runs Gunicorn without root with
one worker process and several threads. `docker-compose.yml` fixes the
production values, reads the rest from `.env.production`, keeps the database
in the named volume `qualitrack-data`, restarts the service with the station
and rotates its logs. CI runs the compose file and builds the image for
`linux/arm64`.

### Consequences

- The readings survive a restart or the recreation of the container.
- The same image runs on the station and on development machines.
- One worker serves the nodes; threads handle concurrent requests.

### Alternatives considered

- Running Gunicorn directly on the operating system with systemd, which
  diverges from the report.
- Several worker processes, which SQLite and the synchronization thread do not
  support.

## ADR-0011: Keep only the nodes registered in this Edge

- **Status**: Accepted
- **Date**: 2026-11-03

### Context

The cloud lists the equipment of the whole laboratory, and the laboratory may
have nodes in environments that another local station serves. Copying them
made the Edge read one profile per node of the laboratory every cycle, a load
the Raspberry Pi of the station does not need. The cloud has no list of
equipment per environment.

### Decision

The Edge keeps only the nodes registered in it with `flask devices register`.
Tracking asks IAM for their identifiers through its anti-corruption layer
(`registered_device_ids`), keeps from the cloud registry only those nodes and
reads the profiles of those nodes only. Revoked nodes stay, so the records
they sent before can still be synchronized. `flask cloud sync` names the nodes
registered in the Edge that the cloud does not have.

### Consequences

- The station reads one profile per own node, whatever the size of the
  laboratory.
- No setting or cloud change is needed, and a node keeps working when the
  cloud moves it to another environment.
- The list of equipment is still downloaded whole, in one request per cycle.
- A node registered with an identifier the cloud does not have waits until
  the identifiers match.

### Alternatives considered

- Environment identifiers in the settings of the station, which must be
  looked up and kept by hand and lose a node that changes environment.
- A list of equipment per environment in the cloud, which needs a change and
  a deployment of the cloud and still needs the environments of each station.

## ADR-0012: Delete the telemetry the cloud answered long ago

- **Status**: Accepted
- **Date**: 2026-11-04

### Context

The Edge stored every reading and action in SQLite and kept it after the cloud
answered. Nothing deleted a record, so the database of the Raspberry Pi grew
with every reading until the SD card was full. With 15 nodes that send every
10 seconds that is about 390,000 records a day, and a full card makes the Edge
lose the readings it receives.

### Decision

The background synchronization deletes the records the cloud already answered:
`SYNCED` ones after `SYNCED_RETENTION_DAYS` (7 by default) and `REJECTED` ones
after `REJECTED_RETENTION_DAYS` (90 by default), counted from the moment the
Edge received them. `PENDING` records are never deleted. It runs after the
synchronization of the pending records, at most once an hour, and deletes in
batches of 2,000 records, each in its own short transaction with a pause of
50 ms, so the nodes that send readings wait for the single SQLite writer for
a moment only. It is active by default.

### Consequences

- The database stops growing with the synchronized telemetry. SQLite reuses
  the freed pages, so the file keeps its size instead of shrinking.
- The cloud is the only copy of a reading after 7 days. If it accepted a
  reading and lost it, the Edge cannot send it again.
- A retry of the same observation after the retention period would be stored
  as new; the nodes retry within minutes, so this does not happen.
- `PENDING` records have no expiry, so a node the registry never places in an
  environment, or a cloud that never accepts a record, still makes the
  database grow. `flask cloud pending` shows the count.
- The first purge of a station with a large backlog takes longer, in many
  batches.

### Alternatives considered

- Deleting each record when the cloud accepts it, which leaves no local copy
  for diagnosing a cloud problem.
- One `DELETE` for everything old, which holds the SQLite writer for seconds
  and stops the nodes from sending.
- A size limit on the database instead of an age, which would delete
  `PENDING` records or need a rule to choose which ones.

