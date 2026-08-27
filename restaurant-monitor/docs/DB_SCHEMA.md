# Data Model — Postgres Schema

Implementation: `storage/models.py` (SQLAlchemy 2.0 async ORM, new this
session). DDL is generated from these models via `storage/database.py::init_db()`
(uses `Base.metadata.create_all` for this project's scale — see note at
bottom on why Alembic was not introduced).

## Entity overview

```
cameras 1───* zones 1───* zone_capacity (embedded as zones.max_capacity)
cameras 1───* tracks
tracks  *───1 identities (global_id — worker_gallery row OR ephemeral session id)
worker_gallery 1───* worker_embeddings
cameras 1───* events (append-only)
events  *───1 zones (nullable — not all events are zone-scoped)
cameras 1───* kpi_snapshots
kpi_snapshots *───1 zones (nullable — some KPIs are zone-scoped, some camera/global)
```

## Tables

### `cameras`
| column | type | notes |
|---|---|---|
| camera_id | text PK | matches `config/camera_config.json` keys, e.g. `camera_01` |
| location | text | |
| camera_type | text | `mixed` \| `staff_only` |
| enabled | boolean | |
| operating_hours_start | time | for `OccupancyRate` denominator |
| operating_hours_end | time | |

### `zones`
| column | type | notes |
|---|---|---|
| zone_id | text PK | e.g. `cam01_table_1`, matches `zones_config.json` |
| camera_id | text FK → cameras | |
| zone_type | text | raw type, e.g. `table`, `table_area`, `staff_area` |
| zone_type_normalized | text | `table` \| `staff` \| `walk` |
| zone_name | text | |
| parent_zone_id | text FK → zones, nullable | table→table_area nesting |
| polygon | jsonb | list of [x,y] points, pixel space (matches existing config format) |
| max_capacity | integer, nullable | for `ZoneDensity`; null = capacity not configured, KPI omitted for that zone |

Zones are also kept in `config/zones_config.json` as the source of truth for
the annotator tool; a startup sync (`zone_manager.sync_to_db()`, new) upserts
that JSON into this table so SQL joins/reporting don't need to parse JSON at
query time. The JSON file is not removed — the annotator writes JSON, DB is
a read-optimized mirror for the API/KPI layer.

### `identities` (worker OR customer, permanent or ephemeral)
| column | type | notes |
|---|---|---|
| global_id | text PK | `worker_<uuid8>` or `session_<uuid8>`, matches existing gallery ID scheme |
| kind | text | `worker` \| `customer_session` |
| display_name | text, nullable | set on manual staff enrollment |
| first_seen_at | timestamptz | |
| last_seen_at | timestamptz | |

### `worker_gallery`
| column | type | notes |
|---|---|---|
| global_id | text PK FK → identities | |
| embedding | vector(512) or float4[512] | see note below on pgvector |
| enrolled_via | text | `auto_promotion` \| `manual` |
| enrolled_at | timestamptz | |

Stores **only the embedding vector**, never a raw photo — matches the
existing in-memory `ReIDGallery` design and the brief's biometric-storage
guidance (§ARCHITECTURE.md Security). Uses the `pgvector` extension
(`CREATE EXTENSION vector`) for `embedding vector(512)` with cosine index if
available; falls back to `float4[]` + application-side FAISS (current
approach, unchanged) if `pgvector` isn't installed on the target Postgres —
`storage/database.py` detects this at startup and logs which mode is active.
FAISS remains the **runtime** matcher (already fast, already tuned); Postgres
is the persistence/audit layer so the gallery survives a restart, which it
currently does not (`ReIDGallery.save()`/`.load()` exist but are never
called by `live_pipeline.py` — noted as a pre-existing gap, now closed by
wiring gallery load/save into the new backend's startup/shutdown hooks).

### `tracks` (per-camera, per-session track continuity — debugging/audit trail)
| column | type | notes |
|---|---|---|
| id | bigserial PK | |
| camera_id | text FK → cameras | |
| track_id | integer | DeepSort's local track id (not globally unique) |
| global_id | text FK → identities, nullable | |
| started_at | timestamptz | |
| ended_at | timestamptz, nullable | |

Not written every frame (would be 2 rows/sec/camera forever) — one row per
track *lifetime*, closed when the track ages out. Used for track-fragmentation
debugging (`changes.txt` already documents track-continuity bugs), not for
KPI computation (events are).

### `events` (append-only — the event-sourcing log, §EVENT_ENGINE.md)
| column | type | notes |
|---|---|---|
| id | bigserial PK | |
| ts | timestamptz | event time (not insert time — matters for backfill/replay) |
| level | smallint | 1, 2, or 3 |
| event_type | text | e.g. `CustomerSeated`, `WaitingEnded` |
| camera_id | text FK → cameras | |
| zone_id | text FK → zones, nullable | |
| global_id | text FK → identities, nullable | |
| track_id | integer, nullable | |
| payload | jsonb | event-specific fields (e.g. `wait_seconds`, `role`, `confidence`) |

Indexes: `(camera_id, ts)`, `(zone_id, ts)`, `(global_id, ts)`,
`(event_type, ts)` — these four are the actual query patterns the KPI Engine
and REST history endpoints use.

### `kpi_snapshots` (materialized rolling aggregates)
| column | type | notes |
|---|---|---|
| id | bigserial PK | |
| computed_at | timestamptz | |
| window_start | timestamptz | |
| window_end | timestamptz | |
| kpi_name | text | one of the 8 KPIs, see KPI Engine |
| camera_id | text FK → cameras, nullable | |
| zone_id | text FK → zones, nullable | |
| global_id | text FK → identities, nullable | for `StaffUtilization` per worker |
| value | double precision | |
| unit | text | `percent` \| `seconds` \| `count` |

This is a cache, not a source of truth — always reconstructible from
`events` by re-running the KPI Engine's aggregation over
`[window_start, window_end)`. Historical report ranges that aren't already
snapshotted are computed on-demand from `events` and optionally backfilled
into this table.

## Why no Alembic migration chain this session

The brief asks for "structured DB... for event logs, KPI aggregates, staff
gallery metadata, zone definitions" — it does not ask for a multi-environment
migration history. Introducing Alembic with a single initial revision when
there is no prior schema to migrate *from* is process overhead without
payoff at this stage; `Base.metadata.create_all()` is the right tool for "one
canonical schema, no prod data yet." If/when this schema needs to evolve
against a live production database with real data, that's the point to add
Alembic — noted as a deliberate scope decision, not an oversight.
