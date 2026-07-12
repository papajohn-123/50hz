# 50Hz

Britain's electricity system, alive.

50Hz is a native iOS 18+ SwiftUI app backed by FastAPI and PostgreSQL. It turns
public Elexon Insights and NESO Carbon Intensity data into a live national grid
view, a bounded daily briefing, lower-carbon flexible-use planning, reported
event evidence, a local learning loop, and professional inspection/export. The
first layer is for curious people; precise scope, timing, classification,
coverage, revisions, and provenance remain available underneath.

The product and delivery roadmap is in
[PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md). This README distinguishes the
implemented repository, recorded Railway production evidence, and the remaining
Apple/GitHub release gates.

## Release state

50Hz is an active release candidate, not a TestFlight or App Store release.

| State | What is true |
| --- | --- |
| Production database | Alembic is at `20260712_0009`. The bounded source backfill completed 95 days. History has 92 successful runs only and a clean replay, with 2,185 coverage rows, 2,185 aggregate rows, and 104,880 baseline rows. Forecast verification has 3 successful runs only, 89,481 exact pairs, and all 12 metric/horizon results. Demand and wind statistics are available. Carbon truthfully remains `insufficient_data` at its evidence threshold; no result is `not_computed`. |
| Worker and scheduled jobs | Worker deployment `0003798a-a2f4-4aac-a745-5522dafdc22e` is `SUCCESS`. History cron deployment `332a56ff-f51b-4f90-ab6a-25d63f4e006e` runs `17 4,10 * * *` UTC and its next execution is confirmed. Forecast cron deployment `f317ebe3-0bc0-4d63-a949-d32542d87caf` runs `17 11 * * *` UTC. |
| API and smoke | Final API deployment `817ad899-1cc9-4baa-8900-5e1882e2f05d` is `SUCCESS`. The 21-path inventory passed the safe production smoke: all 19 GET templates, legal pages, dynamic event/history, JSON/CSV export, ETag 304, gzip, request IDs/log hygiene, paid event explanation, and paid Ask. Ask fix `997ba3c` produces the exact evidence-owned generation-leader answer/citations. |
| Source boundary | Source fix `7acb788` exposes exactly nine reviewed canonical sources. All nine are healthy; internal and operational aliases are inactive and absent from public source/status output. `/ready` and current grid evidence are valid. |
| Repository verification | The release checkpoint records 611 passing backend tests and 148 passing native tests. Railway authentication works; the reviewed commits still cannot be pushed because normal GitHub credential access remains blocked. |
| Apple release | The Release simulator build is green and excludes fixture JSON. The local device archive remains blocked by `iOS 26.4 Platform Not Installed`; only development signing identities are installed, and no signed physical-device archive or processed TestFlight build has been verified. |

Public baseline URL:
[50hz-api-production.up.railway.app](https://50hz-api-production.up.railway.app)

The hostname is the production-smoked target. Treat deployment identifiers,
migration revision, job evidence, and the route smoke in
[OPERATIONS.md](docs/OPERATIONS.md) as one release record; an uploaded Railway
artifact is not a substitute for the still-blocked GitHub push.

The native tree builds, installs, and launches on an iPhone 16 Pro simulator.
The release candidate includes the required File Timestamp reason `C617.1` in
`PrivacyInfo.xcprivacy` and a one-shot cold-launch notification handoff so a
notification that launches the app routes only after app state is ready. The
current release totals are 611 backend and 148 native tests. Compileall, diff
checking, a single migration head, offline full-upgrade/`0009` downgrade SQL,
simulator build/run, and privacy-manifest lint also pass. Simulator evidence
does not replace the outstanding disposable live-downgrade, signed-device, or
TestFlight verification.

## Architecture

```text
Elexon Insights + NESO Carbon Intensity (public; no app API keys)
                         |
                         v
               Railway worker service
       polling + normalization + correction-safe writes
       deterministic observed-event processing and retention
                         |
                         v
                 Railway PostgreSQL
 raw payloads + immutable normalized revisions + lifecycle ledgers
 history aggregates + prediction outcomes + forecast verification
                         |
                         v
                 Railway API service
 mobile contracts + bounded export/tools + OpenRouter explanations
                         |
                         v
                    SwiftUI app
 protected cache + abstract Britain map + local preferences/reminders
```

The API and worker use the same Docker image. `SERVICE_ROLE=api` serves the
public API. `SERVICE_ROLE=worker` starts ingestion and raw-payload retention in
the FastAPI lifespan. PostgreSQL advisory locks, overlap windows, and
source-derived conflict keys keep polling/restarts idempotent without Redis.

Historical jobs are separate bounded operator/cron commands. They are not part
of API startup:

- `50hz-history-backfill` fetches at most 95 completed London settlement days,
  in resumable source-specific chunks.
- `50hz-history-materialize` creates immutable coverage/comparison revisions for
  23 explicitly supported national/fuel/interconnector series.
- `50hz-forecast-verify` pairs stored forecast vintages with exact compatible
  outturn timestamps over 28 days by default (31 maximum); it never invents
  historical vintages.

The initial production run is complete: 95 days were backfilled, all 92 history
materialization checkpoints completed and replayed cleanly, and the three
forecast-verification runs produced 12/12 result slots. Demand and wind pass the
display evidence gate; carbon remains explicitly below it rather than exposing
invented statistics.

Railway/PostgreSQL remains the single backend. Supabase is not required for the
account-free first release; revisit it only if a proven account, sync, or
realtime job justifies a second platform.

## API surface in the current tree

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Process/role/database diagnostic; DB loss is HTTP 200 with `status=degraded` |
| GET | `/ready` | Deployment gate; 503 on DB loss and additional worker task/freshness failures |
| GET | `/privacy`, `/support` | Public pre-release legal/support pages, omitted from OpenAPI |
| GET | `/v1/meta` | Environment/role and DB/OpenRouter configuration presence |
| GET | `/v1/grid/current` | National snapshot, per-family delivery/fact state, partial supply boundary, provenance, one-hour changes, and highest-priority reported event |
| GET | `/v1/grid/timeline` | Observed/forecast timeline; maximum 96 hours, 60–7200-second resolution |
| GET | `/v1/briefing/today` | Deterministic finite Today briefing with partial/coverage state |
| GET | `/v1/sources` | Publisher, dataset, attribution/licence links, and expected cadence |
| GET | `/v1/sources/status` | Public-safe source delivery health kept separate from current-fact validity |
| GET | `/v1/metadata/metrics` | Versioned metric boundaries, classifications, timing, exclusions, and sign conventions |
| GET | `/v1/events` | Active latest-revision REMIT and fresh SYSWARN notices |
| GET | `/v1/events/{event_id}` | Active reported-event detail |
| GET | `/v1/events/{event_id}/history` | Up to 100 immutable reported lifecycle revisions, including field deltas/provenance |
| GET | `/v1/events/{event_id}/explanation` | Validated OpenRouter explanation or deterministic fallback |
| GET | `/v1/regions/{postcode}` | Regional now, time-aligned national comparison, and compatibility window |
| GET | `/v1/regions/{postcode}/windows` | Deterministic 30–720-minute continuous-use plan, optionally bounded by `earliest`/`latest` |
| GET | `/v1/game/today` | Deterministic daily mission/prediction definition and evidence availability |
| GET | `/v1/game/{date}/resolution` | Auditable pending/correct/incorrect/void outcome from immutable published evidence |
| GET | `/v1/metadata/export-schema` | Export metric/selector/format allow-list and limits |
| GET | `/v1/export` | Maximum 31-day, 1,488-row half-hour JSON/CSV export with gaps and provenance |
| GET | `/v1/forecasts/verification` | National demand/wind/carbon MAE, bias, WAPE and coverage by forecast horizon when display thresholds pass |
| POST | `/v1/ask` | Bounded, read-only, tool-grounded OpenRouter answer with server-owned citations |

Interactive documentation is available at `/docs` on a service running the
current tree. The reviewed inventory contains 21 OpenAPI paths; OpenAPI remains
the authoritative route inventory for a deployed revision. The public event
list/detail is intentionally authoritative
reported-notice data; the worker's observed-event lifecycle maintenance does not
turn every derived signal into a public outage/event claim.

## Native product in the current tree

- **Live:** abstract Britain `Canvas`, truthful supply/demand/carbon/frequency
  and connector presentation, timeline replay, fuel focus, reported events,
  share card, and contextual Ask.
- **Today:** a native finite briefing rather than client-side timeline/event
  dumping; complete, partial, stale, offline, and forecast-unavailable states
  remain bounded.
- **Local:** explicit region selection, activity presets/custom duration,
  continuous lower-carbon window planning, compatible start-now comparison,
  coverage/capture details, optional start/deadline bounds, and device-local
  reminders. An eligible national-carbon review can add recent MAE only when
  source, method, issue basis, outturn class, horizon, sample, and coverage gates
  match the entire planned window. Permission is requested only after
  `Remind me` is tapped.
- **Notebook:** one exact-lock prediction, mission navigation before separate
  local/unverified completion, learned concepts, and evidence-resolved
  correct/incorrect/void/corrected results. Explicit local reminders can fire 15
  minutes before lock and five minutes after the evidence window closes; the
  latter only asks the user to check and never asserts that a result exists.
  Choices never leave the device.
- **Professional inspection:** metric methodology, exact sortable supply and
  connector tables, source delivery/fact status, immutable event revision
  history, protected local JSON/CSV share artifacts, and a national forecast
  review that withholds MAE/bias/WAPE unless both server and native evidence
  gates pass.
- **Trust:** cache-first ETag-aware networking, protected disk data, explicit
  stale/offline states, dark native launch screen, no account, no location
  permission, and no analytics/advertising/crash SDK.

## HTTP behavior and limits

Stable successful JSON GETs receive representation-derived ETags and
`Cache-Control`; matching `If-None-Match` requests can return 304. Current
`max-age` values are 30 seconds for current/source status, 60 seconds for
timeline/briefing/events/game/Local windows, 300 seconds for region and forecast
verification, and 3,600 seconds for source/metadata contracts. ETags reduce
transfer size; handlers still build the representation before validation.

Responses over 1,000 bytes are gzip-compressed when requested. Every HTTP
response also receives an `X-Request-ID`. The structured access record contains
only bounded method, registered route template/name, status, duration, response
size, service role/version, and request ID. It does not log query strings,
bodies, headers, client addresses, exception text, or unmatched raw paths.

One-minute limits are process-local:

| Endpoint family | Per client | Per process |
| --- | ---: | ---: |
| Ask, export | 6 | 30 |
| Event explanation, prediction resolution, forecast verification | 12 | 60 |
| Region, Today briefing, source status | 30 | 120 |
| Timeline | 60 | 300 |

HTTP 429 includes `Retry-After`. Counters and the default 100-call OpenRouter
daily budget reset on process restart and are not shared across replicas. Keep
one API replica until durable shared rate/budget controls are added. The
OpenRouter account/project spend cap remains the billing boundary.

## Local backend

Requirements: Python 3.12 and PostgreSQL.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --requirement requirements-dev.lock
pip install --no-deps --editable .
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Run the continuous worker against a disposable database in another terminal:

```bash
source .venv/bin/activate
SERVICE_ROLE=worker uvicorn app.main:app --port 8001
```

Representative history operations:

```bash
50hz-history-backfill --days 95 --dry-run
50hz-history-materialize --days 95 --dry-run
50hz-history-materialize --refresh-latest
50hz-forecast-verify --days 28 --dry-run
50hz-forecast-verify --refresh-latest
```

All commands require an explicit `DATABASE_URL`, including dry runs. Review the
source/date/metric allow-lists with `--help` before a real run. Production now
runs history materialization at `17 4,10 * * *` UTC and forecast verification at
`17 11 * * *` UTC as separate bounded Railway cron services. See
[OPERATIONS.md](docs/OPERATIONS.md).

Repository verification:

```bash
pytest -q
python -m compileall -q app tests
DATABASE_URL=postgresql://postgres:postgres@localhost/50hz \
  alembic upgrade head --sql > /tmp/50hz-migrations.sql
plutil -lint ios/50Hz/Resources/PrivacyInfo.xcprivacy
xcodebuild \
  -project ios/50Hz.xcodeproj \
  -scheme 50Hz \
  -configuration Debug \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro' \
  test
```

Offline Alembic generation is a useful compile/order check, not a substitute
for upgrading and downgrading a disposable live PostgreSQL database.

## Railway configuration

Provision PostgreSQL plus API and worker services from this repository:

| Variable | API | Worker | Notes |
| --- | --- | --- | --- |
| `DATABASE_URL` | Required | Required | Railway PostgreSQL reference; never commit it |
| `APP_ENV` | `production` | `production` | Environment label |
| `SERVICE_ROLE` | `api` | `worker` | Selects runtime behavior |
| `OPENROUTER_API_KEY` | Required only for AI | Not required | Server-side only; rotate the temporary development key before release |
| `OPENROUTER_MODEL` | Optional | Not required | Defaults to `openai/gpt-5.4-mini` |
| `OPENROUTER_DAILY_CALL_LIMIT` | Optional | Not required | Process-local; default 100 |
| `OPENROUTER_TIMEOUT_SECONDS` | Optional | Not required | Default 20 seconds |
| `PUBLIC_BASE_URL` | Recommended | Not required | OpenRouter application header/context |
| `ELEXON_BASE_URL`, `CARBON_INTENSITY_BASE_URL` | Optional | Optional | Public upstream overrides; no credentials required |
| `WORKER_POLL_SECONDS` | Not required | Optional | Scheduler tick; default 60 seconds |
| `RAW_PAYLOAD_RETENTION_HOURS` | Not required | Optional | Default 72, minimum 49 |
| `RAW_PAYLOAD_CLEANUP_INTERVAL_SECONDS` | Not required | Optional | Default 3,600 seconds |

`railway.toml` runs `alembic upgrade head` before API/worker deployment and gates
on `/ready`. Deploy API and worker sequentially so their pre-deploy migrations do
not overlap. `railway.history.json` and `railway.forecast.json` explicitly clear
the web healthcheck and migration command, select the bounded CLI entry point,
and use restart policy `NEVER`. Those services receive a PostgreSQL reference
but not the OpenRouter key.

## Trust and privacy boundaries

- Observed, estimated, derived, reported, and forecast facts remain distinct.
- Operational timestamps use UTC; GB settlement logic handles 46-, 48-, and
  50-period London days.
- Positive connector flow means import into Britain; negative means export.
- Generation movement is never called an outage. Outage/unavailability language
  requires authoritative reported evidence.
- LLM tools are bounded/read-only. The server owns citations and rejects
  unsupported numeric/causal output; core browsing does not depend on AI.
- Raw upstream JSON is pruned after 72 hours by default. Normalized immutable
  evidence, revisions, aggregates, and provenance remain.
- A full postcode may be entered transiently, but submission immediately
  normalizes it; only the validated outward code is saved, displayed, and sent
  to regional endpoints. No Location Services permission is requested.
- Ask sends the question, selected time, and optional outward region through the
  API/OpenRouter only after a first-use disclosure. OpenRouter zero-data-
  retention handling is requested, not guaranteed by this repository.
- Local and Notebook reminder metadata plus Notebook participation/choice state
  remain on the device. Permission is requested only from an explicit reminder
  action; refresh never prompts. Notification taps route to Local or Notebook,
  including a one-shot handoff when a tap cold-launches the app. No APNs backend,
  account, leaderboard, prize, or server-side choice submission exists.
- App Store privacy answers still require owner review of Railway/provider logs,
  retention, and terms.

## Remaining release prerequisites

Engineering still needs owner-controlled access or decisions for:

1. Restore normal GitHub credential access and push the reviewed clean commits;
   Railway authentication already works. Record how the pushed commit maps to
   the uploaded production deployment artifacts.
2. Rotate the temporary OpenRouter key and confirm the production spend cap and
   retention-eligible routing.
3. Validate migrations against disposable live PostgreSQL. The installed
   Docker.app is incomplete/missing its executable, so only offline migration
   checks and production forward migration have been possible for the newest
   schema; the disposable live downgrade remains open.
4. Confirm Apple team `VKMJPS7WP4`, register/confirm
   `com.papajohn.50hz`, create the App Store Connect record/group, and grant
   signing/upload authority.
5. Approve privacy/support contact, App Privacy answers, metadata, screenshots,
   age/content-rights/export-compliance answers, and testers.
6. Install/repair the Xcode iOS 26.4 device platform (the current archive attempt
   stops during LaunchScreen compilation), obtain an appropriate distribution
   signing identity, then create a signed archive, install on physical iPhones,
   complete accessibility/offline/performance/battery QA, upload, wait for processing,
   install from TestFlight, and retest against the recorded backend deployment.

See [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the technical
baseline, [OPERATIONS.md](docs/OPERATIONS.md) for deployment/data jobs,
[UI_DIRECTION.md](docs/UI_DIRECTION.md) for the native design system, and
[APP_STORE_RELEASE.md](docs/APP_STORE_RELEASE.md) for the Apple handoff.
