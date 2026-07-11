# 50Hz implementation plan

Last reconciled with the repository and the pre-release production smoke test on
11 July 2026.

## Product objective

50Hz makes Britain's electricity system feel alive without making the data less
truthful. A curious user should understand the grid at a glance; a professional
should be able to inspect source, time, classification, and limitations.

The governing rule is:

> Deterministic code establishes the facts. The LLM may explain validated
> evidence, but it does not decide what happened.

## Confirmed decisions

- Product name: **50Hz**
- Audience: curious public first, professional detail underneath
- Initial price: free
- Distribution: internal TestFlight, then the iOS App Store
- Platform: iOS 18+
- Native stack: SwiftUI, Observation, structured concurrency, Swift Charts
- Visual direction: authored zoomed-out Britain visualization, not a street map
- Default region: Central London, while the primary map remains national
- Backend: FastAPI, Railway PostgreSQL, API and worker roles
- LLM gateway: OpenRouter, called server-side only
- Default configured model: `openai/gpt-5.4-mini`
- Accounts: none in the first release
- Preferences and future game progress: local device storage
- Location: manually entered postcode; no location permission
- Core browsing must continue when OpenRouter is unavailable

The supplied Claude UI archive has been translated into the native direction in
[UI_DIRECTION.md](UI_DIRECTION.md): Live, Today, Mine, and Log tabs; an abstract
Britain canvas; cyan observed data; violet forecast/replay data; and Ask the Grid
as a contextual inspector rather than a generic chat tab.

## Delivery-state definitions

- **Implemented:** present in the current shared tree and exercised by repository
  tests or build checks.
- **Production verified:** observed on the public Railway deployment after the
  relevant commit was deployed.
- **Release verified:** exercised through a signed build on a physical iPhone and
  a processed TestFlight install.

The current tree is implemented but not release verified. A read-only production
check at 14:12 UTC on 11 July still observed an older deployment: current,
timeline, sources, events, and game succeeded; regional lookup still returned
503; and `/ready`, `/privacy`, and `/support` were not present. ETag/304 and gzip
behavior were verified on that older deployment. Ask previously returned 503
with its older citation handling and was not re-spent after the local fix. Worker
state was not inspected because Railway CLI authentication failed.

## Implemented architecture

```text
Elexon Insights                 NESO Carbon Intensity
       \                              /
        \                            /
         v                          v
               Railway worker role
       schedules + adapters + normalization
      overlap windows + PostgreSQL advisory locks
                         |
                         v
                Railway PostgreSQL
     raw payloads, source runs, observations,
  forecasts, notices, event/explanation caches
                         |
                         v
                  Railway API role
    mobile contracts + evidence tools + OpenRouter
                         |
                         v
                    SwiftUI app
  protected cache + timeline + abstract Britain canvas
```

One Docker image serves both roles:

- `SERVICE_ROLE=api` starts the public API without ingestion.
- `SERVICE_ROLE=worker` supervises the polling loop from FastAPI lifespan.
- PostgreSQL is required by both roles.
- Overlapping source windows plus source-derived uniqueness make writes
  idempotent.
- PostgreSQL advisory locks prevent duplicate concurrent source jobs.

Redis, accounts, authentication, push notifications, weather integration, and
third-party analytics/crash SDKs are not implemented.

## Implemented data platform

### Time, persistence, and provenance

- Async SQLAlchemy sessions and Alembic migrations.
- Separate observed, published, retrieved, valid, and issued timestamps.
- UTC persistence and explicit `Europe/London` settlement conversion.
- Tests for 46-, 48-, and 50-period daylight-saving days.
- Positive interconnector flow means import into Britain; negative means export.
- Raw upstream payloads are checksummed for replay/audit and pruned after 72
  hours by default; normalized facts and provenance survive cleanup.
- Normalized observations and forecast/notice revisions are written
  idempotently.

The schema includes source metadata/runs/raw payloads, assets, national
generation/demand/frequency/interconnector/carbon observations, forecast
observations, grid snapshots, reported notices, detected events, detected-event
explanations, and reported-notice explanation revisions. The public current
endpoint currently composes from normalized
latest reads; a separate production snapshot materializer is not wired. A
bounded worker maintenance task deletes at most 200 expired raw-payload rows per
hourly run by default and retries later if more remain.

### Source schedules

| Job | Content | Poll cadence | Overlap/reconciliation |
| --- | --- | ---: | --- |
| `elexon.fuelinst` | Generation by fuel | 2 min | 10 min; prior 48 h hourly |
| `elexon.indo` | National demand | 5 min | 1 h; prior 48 h hourly |
| `elexon.freq` | System frequency | 1 min | 10 min; prior 48 h hourly |
| `elexon.interconnectors` | Interconnector flows | 2 min | 10 min; prior 48 h hourly |
| `neso.carbon.national.current` | Current GB carbon | 15 min | 5 min |
| `neso.carbon.regional.london` | London carbon | 15 min | 5 min |
| `neso.carbon.national.forecast` | 48-hour GB carbon forecast | 30 min | 5 min |
| `elexon.ndf` | National demand forecast | 15 min | 2 h; prior 48 h every 6 h |
| `elexon.windfor` | Wind forecast | 30 min | 12 h; prior 48 h every 12 h |
| `elexon.remit.unavailability` | Reported unavailability revisions | 2 min | 30 min; prior 48 h hourly |
| `elexon.syswarn` | System warnings | 5 min | 1 h; prior 48 h every 6 h |

The scheduler tick defaults to 60 seconds; individual job cadence is independent
of that tick. Postcodes are not continuously ingested. The region route first
uses stored regional/London data where possible, then performs a bounded public
NESO lookup. That on-demand response is not persisted by the route.

## Implemented API and runtime behavior

| Method | Path | Current tree behavior |
| --- | --- | --- |
| GET | `/health` | Always responds for a running process and reports `ok`/`degraded`, role, and database reachability |
| GET | `/ready` | 503 on DB failure; worker also checks task state and required data freshness after five minutes |
| GET | `/privacy` | Public pre-release policy describing optional postcode/Ask flows and providers; hidden from OpenAPI |
| GET | `/support` | Public support and safety-caveat page with the GitHub issue tracker; hidden from OpenAPI |
| GET | `/v1/meta` | Environment, role, and DB/OpenRouter configuration presence |
| GET | `/v1/grid/current` | Required generation/demand/carbon, optional frequency/flows, source-aware freshness, one-hour change, and active reported event |
| GET | `/v1/grid/timeline` | Up to 96 hours at 60–7200-second resolution, with observed and supported forecast metrics |
| GET | `/v1/sources` | Attribution, licence/documentation links, and expected cadence |
| GET | `/v1/events` | Active latest REMIT and fresh SYSWARN notices |
| GET | `/v1/events/{id}` | Active public-event detail |
| GET | `/v1/regions/{postcode}` | Validated outward code, regional carbon, GB comparison, and charging window |
| GET | `/v1/game/today` | Deterministic definitions with real source/forecast/event availability flags |
| POST | `/v1/ask` | Bounded read-only tool loop and server-owned citations |
| GET | `/v1/events/{id}/explanation` | Grounded explanation/fallback for persisted detected events and reported notice IDs |

HTTP hardening in the current tree:

- Content ETags and `Cache-Control` for successful stable JSON GETs.
- HTTP 304 when `If-None-Match` equals the current representation.
- Gzip for responses of at least 1,000 bytes when supported by the client.
- One-minute process-local burst limits on Ask, explanation, regions, and
  timeline; HTTP 429 includes `Retry-After`.
- Railway deploy health uses `/ready`, not `/health`.

ETags are transfer validators, not a server data/query cache: the route still
builds the response before its hash is compared. The limiter is intentionally
single-process and the API is unauthenticated. It must be replaced with a shared
durable mechanism before scaling beyond one API process.

## Events and evidence

Pure event rules exist for generation-leader changes, renewable-share
milestones, sustained import/export reversals, frequency excursions, and
reported unavailability. Event lifecycle/storage tests exist, but the production
worker does not yet invoke and persist the deterministic event processor after
every relevant ingestion write.

The currently user-visible event list is still useful and authoritative: it
maps active latest-revision REMIT and fresh SYSWARN notices directly to stable
public IDs. Those public notice IDs resolve through the detail and explanation
flows, and successfully validated explanations are cached by public ID and
notice revision. Remaining event work is historical/resolved listing and
production persistence of derived-event lifecycle.

Code-level evidence rules include:

- A generation/output change is not called an outage.
- Outage/unavailability language requires reported evidence.
- REMIT and SYSWARN revisions are retained.
- Forecast frequency is forbidden by the mobile contract.
- Public citations come from server-owned source metadata.
- Unsupported event model output falls back to deterministic evidence copy.

## Grounded intelligence

Ask the Grid exposes five bounded read-only tools: current grid state, metric
history, active events, event evidence, and the cleanest forecast window. The
client allows at most four tool rounds and six total calls. History is capped at
48 hours; clean-window duration at 0.5–12 hours; event identifiers and metrics
are allow-listed. There is no arbitrary SQL, URL, filesystem, or environment
access.

Validation enforces evidence-backed numbers and rejects unsupported causal
language. The server, not the model, selects citations from gathered evidence.
Invalid Ask output returns a bounded 503 rather than an ungrounded answer. Event
explanation failures return deterministic copy. Successful detected-event
explanations are cached by evidence checksum, provider, configured model, prompt
version, and locale. Reported-notice explanations have a parallel cache keyed by
stable public ID and source revision. Deterministic fallback copy is never cached.

OpenRouter calls request provider zero-data-retention handling. The in-process
daily call counter defaults to 100 upstream requests and resets on restart; it is
not a billing-grade or multi-replica budget. The OpenRouter account/project cap
remains the hard spend control.

## Native iOS implementation

The current tree contains:

- Live, Today, Mine, and Log tabs.
- Custom abstract Britain `Canvas` with deterministic batched particles.
- Cache-first repository with protected on-disk response cache, ETags, and 304
  reuse.
- Current/timeline polling, cancellation, shared in-flight requests, scrubbing,
  material-gap handling, and Resume Live.
- Generation mix/fuel focus, demand, frequency, carbon, and interconnector views.
- Active-event list/detail and explanation presentation.
- Today forecast/event presentation.
- Backend-defined daily missions and prediction in Log, with ETag-backed
  same-day offline reuse and prior-day cache rejection.
- Mine postcode entry, regional comparison, and charging guidance.
- Contextual Ask the Grid inspector.
- In-app links to the production privacy and support pages.
- Structured share-card rendering.
- Bundled contract fixtures, XCTest sources, app icons, and a privacy manifest.

The app stores a user's entered postcode in `AppStorage` but strips a valid
three-character inward suffix before putting the outward code in the regional
URL. It requests no location permission. Ask question text, selected time, and
optional region leave the device through the backend/OpenRouter.

Known native/release gaps:

- Mission completion, prediction choice, and streak are local participation
  notes. There is intentionally no account, server submission, result
  verification, leaderboard, prize, or prediction-resolution/scoring flow yet.
- No Apple Development Team is configured.
- Bundle ID `com.papajohn.50hz` is present but not confirmed as registered.
- No signed archive, physical-device install, TestFlight processing, or App Store
  Connect record has been verified.
- Physical-device battery/thermal/performance, VoiceOver, Dynamic Type, Reduce
  Motion, offline, and failure-state QA remain.
- CI executes XCTest on an available simulator and creates an unsigned Release
  archive. Its latest remote result still needs confirmation for the release
  commit.
- No production crash/freshness telemetry or alerting is installed.

## Verification record

Workspace verification commands:

```bash
source .venv/bin/activate
pytest -q
DATABASE_URL=postgresql://postgres:postgres@localhost/50hz \
  alembic upgrade head --sql > /tmp/50hz-migrations.sql
plutil -lint ios/50Hz/Resources/PrivacyInfo.xcprivacy
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

The latest backend run completed with **159 passed and one dependency
deprecation warning**. Offline migration SQL generation through
`20260711_0003` and privacy-manifest lint also passed. An unsigned iOS
`build-for-testing` code/test compile passed with the asset catalogue excluded
because local simulator tooling was unavailable; a later Release archive attempt
was blocked by the same local CoreSimulator/assets failure. Neither result
verified signing or TestFlight readiness. Before the release commit, rerun every
command from the final clean checkout and record the exact commit and outputs.

## Next milestones

### 1. Freeze, verify, and deploy this release candidate

1. Reconcile all shared-tree edits and run backend, migration, privacy-manifest,
   iOS compile, and `git diff --check` gates.
2. Commit and push one reviewed release-candidate revision.
3. Re-authenticate Railway CLI or use the confirmed production dashboard.
4. Confirm API and worker service IDs/roles and production variable references.
5. Deploy API first, wait for `/ready`, then deploy worker and wait for `/ready`.
6. Smoke every route, including one authorized OpenRouter Ask and one event
   explanation.
7. Verify ETag/304, gzip, 429 headers in staging or bounded checks, source
   freshness, and iOS behavior against that exact deployment.

Exit condition: the production API matches native contracts at the release
commit and both roles show current required data, not merely running processes.

### 2. Complete Apple/TestFlight setup

Inputs required from the owner:

- Apple Developer Team ID and permission to sign/upload.
- Confirmation or registration of `com.papajohn.50hz`.
- App Store Connect app record and internal tester group.
- Approval of the hosted `/privacy` and `/support` pages/contact route, or
  replacement public HTTPS URLs and support contact details.
- Final App Privacy answers, approval of the default 72-hour raw-payload policy,
  and retention/logging decisions for the remaining user/operational data.
- App name/subtitle/description/keywords, screenshots, age rating, copyright,
  review contact, and export-compliance answer.
- A rotated production OpenRouter key with the desired spend cap.

Engineering steps:

1. Set the Development Team and automatic/manual signing intentionally.
2. Verify privacy manifest inclusion and complete App Store privacy metadata.
3. Create a signed Release archive and validate it in Organizer.
4. Install on a physical iPhone and complete smoke, offline/stale, accessibility,
   and battery/thermal QA.
5. Upload, wait for processing, install from TestFlight, and retest the production
   build.

### 3. Hardening after the first internal build

- Add per-source last-success/failure/lag/record-count observability and alerts.
- Confirm the 72-hour raw-payload policy and set request-log, event, question,
  and explanation retention.
- Add a backup/restore drill.
- Add a signed archive/export workflow when credentials are ready; CI already
  executes XCTest and compiles an unsigned Release archive.
- Wire the deterministic event processor into worker persistence and expose
  resolved/historical events.
- Connect `/v1/game/today` to a native game loop with deterministic resolution
  and void-on-missing-data rules.
- Add a shared durable limiter/budget before additional API replicas.

## Internal TestFlight definition of done

50Hz is ready for internal TestFlight only when:

- API and worker are deployed from the recorded release commit and `/ready` is
  healthy for both roles.
- Required data timestamps are within their documented source cadences.
- Every native route succeeds against production, with graceful OpenRouter and
  upstream failure behavior.
- ETag/304 reuse, gzip, retry-after handling, cache-first launch, and stale/offline
  labeling are verified.
- Regional requests send only the outward postcode code.
- Event/outage claims and explanations remain traceable to authoritative evidence.
- A signed Release archive installs on a physical iPhone.
- Privacy, attribution, support, accessibility, screenshots, and App Store
  metadata are complete.
- The uploaded build processes and installs through the intended internal
  TestFlight group.
