# 50Hz

Britain's electricity system, alive.

50Hz is a native iOS 18+ SwiftUI app backed by FastAPI and PostgreSQL. It
turns Elexon and NESO data into a live national grid view, a 24-hour timeline,
reported system events, regional carbon guidance, and evidence-grounded
explanations. The first layer is designed for curious people; source timing and
provenance remain visible for professional users.

The execution-grade product, UX, data, release, and validation roadmap is in
[PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md). It is the source of truth for what
we build next; this README describes the current implemented baseline.

The repository is a release candidate, not a TestFlight release. The native app,
Railway API/worker, source adapters, persistence, public routes, and OpenRouter
integration are implemented and production-smoked. Apple signing, a signed
physical-device archive, and TestFlight release verification remain.

## Production status

Public API:
[50hz-api-production.up.railway.app](https://50hz-api-production.up.railway.app)

The release-candidate production smoke was completed on 11 July 2026:

- `/health`, `/ready`, `/privacy`, `/support`, and every public `/v1` route used
  by the app returned its expected successful response.
- The national snapshot reported live data; the 48-hour timeline contained both
  observed and forecast samples, and all three daily missions were available.
- `/v1/regions/SW1A` returned Central London data and explicitly labelled the
  bounded upstream delay instead of failing the request.
- Ask correctly answered that Britain was importing from the signed net-flow
  evidence, with server-owned citations and model-generated follow-up prompts.
- A current reported-unavailability event produced a validated OpenRouter
  explanation, then reused its revision-keyed cache on the second request.
- ETag/HTTP 304 reuse on `/v1/sources` and gzip on the timeline were verified.
- The worker's private `/ready` endpoint returned ready after its first-deploy
  grace period, with PostgreSQL reachable and ingestion data advancing.

This proves the deployed engineering baseline; it does not replace signed-device
or processed-TestFlight verification.

The native client has also been run on an iPhone 16 Pro / iOS 18.6 simulator
against production. Live, Today, Mine, Log, reported-event explanation, and a
grounded Ask answer were exercised successfully. That run caught and fixed iOS
18's stricter handling of fractional-second API timestamps.

## Architecture

```text
Elexon Insights + NESO Carbon Intensity
                  |
                  v
        Railway worker service
 adapters -> normalization -> idempotent writes
                  |
                  v
          Railway PostgreSQL
 raw payloads + observations + forecasts + notices
                  |
                  v
          Railway API service
 REST presentation + bounded tools + OpenRouter
                  |
                  v
             SwiftUI app
 protected cache + timeline + abstract Britain map
```

The API and worker use the same Docker image. `SERVICE_ROLE=api` serves the
public API. `SERVICE_ROLE=worker` starts the continuous ingestion loop in the
FastAPI lifespan. PostgreSQL advisory locks and source-derived conflict keys make
overlapping polling and restarts idempotent without Redis.

## API surface

| Method | Path | Implemented behavior |
| --- | --- | --- |
| GET | `/health` | Process status, role, and database reachability; it still returns HTTP 200 with `status=degraded` when the database is down |
| GET | `/ready` | Deployment gate; returns 503 for database loss and, on the worker, a stopped task or stale required data after a five-minute grace period |
| GET | `/privacy` | Public pre-release privacy policy for App Store metadata; intentionally omitted from OpenAPI |
| GET | `/support` | Public support/contact page for App Store metadata; intentionally omitted from OpenAPI |
| GET | `/v1/meta` | Environment, role, and whether database/OpenRouter configuration exists |
| GET | `/v1/grid/current` | National snapshot, freshness, provenance, one-hour generation changes, and highest-priority reported event |
| GET | `/v1/grid/timeline` | Observed/forecast samples for a maximum 96-hour window and 60–7200-second resolution |
| GET | `/v1/sources` | Publisher, data set, attribution, licence links, and expected cadence |
| GET | `/v1/events` | Active latest-revision REMIT and fresh SYSWARN notices |
| GET | `/v1/events/{event_id}` | Detail for an active public event |
| GET | `/v1/regions/{postcode}` | Regional carbon, GB comparison, and forecast charging window using a validated outward postcode |
| GET | `/v1/game/today` | Deterministic mission/prediction definitions with availability derived from current evidence |
| POST | `/v1/ask` | Bounded, tool-grounded OpenRouter answer with server-owned citations |
| GET | `/v1/events/{event_id}/explanation` | Validated model explanation or deterministic fallback for detected and reported event IDs |

Interactive API documentation is at `/docs` on a running service.

The native Log consumes `/v1/game/today`, accepts only the backend-defined
current date, and can reuse a same-day ETag-backed plan offline with a warning.
Mission completion, prediction choice, and streak stay local and are explicitly
unsubmitted/unscored; there is no account or leaderboard. Successfully validated
detected-event explanations are cached by evidence checksum, while reported
notice explanations are cached by stable public ID and notice revision. Fallback
copy is not cached. Historical/resolved public events and push delivery are not
implemented.

## HTTP behavior and limits

Successful JSON GETs use representation-derived ETags and may return HTTP 304:

| Route family | `max-age` |
| --- | ---: |
| `/v1/grid/current` | 30 seconds |
| timeline, events, event detail, game | 60 seconds |
| regions | 300 seconds |
| sources and metadata | 3600 seconds |

Responses larger than 1,000 bytes are gzip-compressed when the client supports
it. ETags reduce transfer size; they are not a database query cache because the
handler still produces the representation before the validator is checked.

The following one-minute burst limits are process-local:

| Endpoint | Per client | Per process |
| --- | ---: | ---: |
| `POST /v1/ask` | 6 | 30 |
| event explanation | 12 | 60 |
| regional lookup | 30 | 120 |
| timeline | 60 | 300 |

Exceeded limits return HTTP 429 with `Retry-After`. These counters and the
default 100-call OpenRouter daily budget reset on process restart and are not
shared across replicas. Keep one API replica or add a shared durable limiter and
budget before horizontal scaling. The OpenRouter account/project spend cap is
the billing boundary.

## Local backend

Requirements: Python 3.12 and PostgreSQL.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Set `DATABASE_URL` in `.env` to a disposable local database, then migrate and
serve the API:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

Run the ingestion worker in a second terminal. It contacts public upstream APIs,
so use a disposable database:

```bash
source .venv/bin/activate
SERVICE_ROLE=worker uvicorn app.main:app --port 8001
```

Repository verification:

```bash
pytest -q
DATABASE_URL=postgresql://postgres:postgres@localhost/50hz \
  alembic upgrade head --sql > /tmp/50hz-migrations.sql
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
plutil -lint ios/50Hz/Resources/PrivacyInfo.xcprivacy
```

## Local iOS build

Open [50Hz.xcodeproj](ios/50Hz.xcodeproj) and select the `50Hz` scheme. The app
targets iOS 18 and has no third-party iOS dependencies.

```bash
xcodebuild \
  -project ios/50Hz.xcodeproj \
  -scheme 50Hz \
  -configuration Debug \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/50hz-derived \
  CODE_SIGNING_ALLOWED=NO \
  build-for-testing
```

The GitHub workflow selects an available simulator, executes the XCTest suite,
and builds an unsigned Release archive. A signed archive, physical-device
install, and TestFlight processing remain separate release gates.

## Railway configuration

Provision PostgreSQL plus API and worker services from this repository:

| Variable | API | Worker | Notes |
| --- | --- | --- | --- |
| `DATABASE_URL` | Required | Required | Railway PostgreSQL reference; never commit it |
| `APP_ENV` | `production` | `production` | Environment metadata |
| `SERVICE_ROLE` | `api` | `worker` | Selects runtime behavior |
| `OPENROUTER_API_KEY` | Required for AI calls | Not required | Server-side only |
| `OPENROUTER_MODEL` | Optional | Not required | Defaults to `openai/gpt-5.4-mini` |
| `OPENROUTER_DAILY_CALL_LIMIT` | Optional | Not required | Process-local; defaults to 100 |
| `OPENROUTER_TIMEOUT_SECONDS` | Optional | Not required | Defaults to 20 seconds |
| `PUBLIC_BASE_URL` | Recommended | Not required | OpenRouter application header |
| `ELEXON_BASE_URL` | Optional | Optional | Public upstream override |
| `CARBON_INTENSITY_BASE_URL` | Optional | Optional | Public upstream override |
| `WORKER_POLL_SECONDS` | Not required | Optional | Scheduler tick; defaults to 60 seconds |
| `RAW_PAYLOAD_RETENTION_HOURS` | Not required | Optional | Raw JSON retention; defaults to 72 hours and cannot be set below 49 |
| `RAW_PAYLOAD_CLEANUP_INTERVAL_SECONDS` | Not required | Optional | Cleanup cadence; defaults to 3600 seconds |

`railway.toml` runs `alembic upgrade head` before deployment and uses `/ready`
as its health check. Deploy API and worker sequentially so two pre-deploy
migrations are not started together. Never put OpenRouter or database
credentials in the iOS bundle.

## Trust and privacy

- Observed, estimated, derived, reported, and forecast facts remain distinct.
- Operational timestamps use UTC; GB settlement conversion handles 46-, 48-,
  and 50-period days.
- Positive interconnector flow means import into Britain.
- Generation change is never called an outage. Outage language requires an
  authoritative reported notice such as REMIT.
- LLM tools are bounded and read-only. Citations come from server-owned source
  metadata, and unsupported event output falls back to deterministic copy.
- Raw upstream JSON is pruned by the worker after 72 hours by default; normalized
  observations, forecasts, notices, and their provenance fields remain.
- OpenRouter requests ask providers for zero-data-retention handling. This is a
  request to the provider, not a claim that user content stays on the device.
- No account, advertising, analytics, or crash-reporting SDK is present.
- A full postcode may be saved locally, but the iOS client removes a valid inward
  suffix and sends only the outward code in the regional URL.
- Ask sends the question, selected map time, and optional region code to the
  backend and OpenRouter. Railway/OpenRouter may also process network metadata.
- The privacy manifest declares the UserDefaults required-reason API, no
  tracking, and conservative Other Diagnostic Data processing for App
  Functionality that is not linked to identity. App Store privacy answers and
  retention claims still require owner review against production logging and
  provider terms.

## Inputs still required for TestFlight

The owner needs to provide or confirm:

1. Confirmation that the locally selected Apple team `VKMJPS7WP4` is the
   intended paid Developer Program team and has signing/upload access.
2. Registration/ownership of bundle ID `com.papajohn.50hz` or a replacement.
3. App Store Connect app record, internal tester group, and required roles.
4. Approval of the hosted `/privacy` and `/support` pages, including the public
   GitHub issue-tracker contact route, or replacement HTTPS URLs/contact details.
5. App Privacy answers plus approval of the 72-hour raw-payload policy and
   retention decisions for request logs, questions, event explanations, and
   operational diagnostics.
6. App Store metadata, screenshots, age rating, copyright, and export-compliance
   answers.
7. A rotated production OpenRouter key and final account/project spend cap.

After the owner inputs are supplied, engineering must configure signing, create a
signed Release archive, install it on a physical iPhone, complete
accessibility/offline/battery QA, upload it, and verify the processed TestFlight
build. See [PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md) for the product and
delivery sequence, [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the
implemented technical baseline,
[OPERATIONS.md](docs/OPERATIONS.md) for deployment checks, and
[APP_STORE_RELEASE.md](docs/APP_STORE_RELEASE.md) for ready-to-paste metadata and
the Apple handoff.
