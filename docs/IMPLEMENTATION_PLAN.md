# 50Hz implementation baseline

Last reconciled with the shared repository and Railway production on 12 July
2026. This document records what is implemented, what has production evidence,
and what still needs final route or Apple release verification. Product
priorities and future choices live in
[PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md).

## Product objective and governing rule

50Hz makes Britain's electricity system feel alive without making the evidence
less truthful. A curious person should understand the national position in
seconds; a professional should be able to inspect scope, classification,
timestamps, coverage, methods, sources, and revisions.

> Deterministic code establishes the facts. The LLM may explain validated
> evidence, but it does not decide what happened.

Confirmed decisions:

- Native SwiftUI app for iPhone on iOS 18+.
- Four tabs: Live, Today, Local, Notebook; Ask remains contextual.
- Abstract national Britain visualization, not a street/network map.
- Central London is the default Local region; no location permission.
- FastAPI plus one Railway PostgreSQL database, with separate API/worker roles.
- OpenRouter is server-side and optional for core browsing.
- Elexon Insights and NESO Carbon Intensity feeds in use are public and require
  no application API key.
- No account, advertising, analytics, leaderboard, prize, cloud sync, or remote
  notification service in the first release.
- Railway/PostgreSQL remains the backend. Adding Supabase would currently split
  ownership without solving a proven product need.

## Delivery-state vocabulary

- **Implemented:** code exists in the current tree and has repository-level
  tests/build evidence.
- **Deployed:** the named Railway deployment is recorded and its migrations/jobs
  have run. A deployment uploaded from a local clean tree still needs a later
  GitHub push and commit-to-artifact reconciliation.
- **Production verified:** the deployed artifact passed the bounded smoke in
  [OPERATIONS.md](OPERATIONS.md).
- **Release verified:** the same contract passed on a signed physical-device and
  processed TestFlight install.

Railway authentication now works. Production PostgreSQL is at migration
`20260712_0009`; the 95-day backfill, all 92 history-materialization checkpoints
plus a clean replay, and all three forecast-verification runs are complete. The
worker deployment `0003798a-a2f4-4aac-a745-5522dafdc22e` and both bounded cron
services are deployed successfully. Final API deployment
`817ad899-1cc9-4baa-8900-5e1882e2f05d` includes Ask grounding fix `997ba3c` and
source-boundary fix `7acb788`; it is `SUCCESS` and passed the full production
smoke. Normal GitHub credential access remains blocked, so the reviewed commits
are not yet pushed. Apple signing, physical-device, and TestFlight gates remain
unverified.

## Implemented service architecture

```text
Elexon Insights                 NESO Carbon Intensity
       \                              /
        \                            /
         v                          v
               Railway worker role
 polling schedules -> adapters -> immutable normalized revisions
 retention + deterministic post-ingest event maintenance
 overlap windows + PostgreSQL advisory locks
                         |
                         v
                Railway PostgreSQL
 source runs/raw payloads, observations/forecasts/notices,
 event and prediction ledgers, history materialization,
 explanation caches, forecast-verification evidence/results
                         |
                         v
                  Railway API role
 mobile presentation + bounded exports/tools + OpenRouter
                         |
                         v
                    SwiftUI app
 protected cache + abstract map + local preferences/reminders
```

One image serves both always-on roles:

- `SERVICE_ROLE=api` exposes the public API and does not poll upstreams.
- `SERVICE_ROLE=worker` supervises continuous ingestion plus raw-payload
  retention inside the application lifespan.
- `DATABASE_URL` is required for both roles.
- Overlap windows plus source-derived conflict keys make normalized writes
  idempotent.
- PostgreSQL advisory locks prevent concurrent work on the same ingestion,
  history, verification, or event-maintenance scope.

Bounded historical work runs as explicit commands or Railway cron services, not
inside API requests:

- `50hz-history-backfill`
- `50hz-history-materialize`
- `50hz-forecast-verify`

## Data and evidence platform

### Time, identity, and corrections

- UTC storage with explicit `Europe/London` settlement conversion.
- Separate observed, valid, issued, published, and retrieved timestamps where
  the source supplies them.
- Tests cover 46-, 48-, and 50-period settlement days.
- Positive interconnector values import into Britain; negative values export.
- Stable source-record identities and content checksums preserve provenance.
- Normalized corrections append immutable revisions instead of overwriting prior
  observations/forecast vintages.
- Raw upstream payloads are retained for 72 hours by default and pruned in
  bounded batches; normalized evidence and provenance survive.

The migration chain currently runs from `20260711_0001` through
`20260712_0009`. It includes normalized grid/source tables, reported notice and
explanation revisions, history/coverage foundations, event lifecycle,
prediction resolution, immutable forecast revisions, history materialization,
and forecast verification. Production forward migration is recorded at `0009`,
and the chain has offline Alembic generation coverage. A disposable live
PostgreSQL downgrade still needs validation because the installed Docker.app is
incomplete and its executable is missing.

### Continuous source schedules

| Job | Content | Normal poll | Reconciliation |
| --- | --- | ---: | --- |
| `elexon.fuelinst` | Transmission-visible generation by fuel | 2 min | 10 min; previous 48 h hourly |
| `elexon.indo` | National demand | 5 min | 1 h; previous 48 h hourly |
| `elexon.freq` | System frequency | 1 min | 10 min; previous 48 h hourly |
| `elexon.interconnectors` | Signed connector flows | 2 min | 10 min; previous 48 h hourly |
| `elexon.ndf` | National demand forecast vintages | 15 min | 2 h; previous 48 h every 6 h |
| `elexon.windfor` | Wind forecast vintages | 30 min | 12 h; previous 48 h every 12 h |
| `elexon.remit.unavailability` | Reported availability revisions | 2 min | 30 min; previous 48 h hourly |
| `elexon.syswarn` | System warning revisions | 5 min | 1 h; previous 48 h every 6 h |
| `neso.carbon.national.current` | Current GB carbon period | 5 min | 5 min |
| `neso.carbon.regional.london` | Stored London carbon period | 5 min | 5 min |
| `neso.carbon.national.forecast` | Captured 48-hour national carbon forecast | 30 min | 5 min |

The worker scheduler ticks every 60 seconds by default, independently of each
job's cadence. Postcodes are not continuously ingested. Regional requests use a
validated outward code and a bounded NESO fallback; that on-demand response is
not persisted by the route.

The public source/status boundary is an explicit nine-ID allow-list:
`elexon.freq`, `elexon.fuelinst`, `elexon.indo`, `elexon.ndf`, `elexon.remit`,
`elexon.syswarn`, `elexon.windfor`, `neso.carbon-intensity-national`, and
`neso.carbon-intensity-regional`. Final production smoke found all nine healthy.
Operational job aliases remain inactive metadata and cannot appear merely
because an ingestion/backfill row exists; this regression is fixed in `7acb788`.

### History backfill and materialization

`50hz-history-backfill` is a maximum-95-day, resumable, source-allow-listed
operator job. It uses publisher ranges, London settlement-day boundaries,
advisory locks, safe checkpoints, and the same idempotent ingestion repository
as the worker. Historical carbon range data is estimate-only because that
source does not expose historical issue timestamps; frequency detail is
deliberately excluded from a 90-day backfill.

`50hz-history-materialize` reads normalized evidence in maximum-30-day chunks
with a 28-day comparison lookback and appends immutable derived revisions. Its
explicit registry contains national carbon, national demand, 11 supported fuel
selectors, and 10 supported connector selectors: 23 series total. It calculates
half-hour/daily coverage and previous-period, yesterday, seven-day, and rolling
28-day comparisons. Daily and rolling outputs require at least 95% compatible
coverage. Missing data never becomes zero. Frequency and invented historical
forecast vintages are excluded.

`--refresh-latest` re-evaluates only the last completed London day with force and
the required read-only lookback. The recommended Railway materialization cron is
`17 4,10 * * *` UTC, after the ingestion/backfill contract has been validated in
the target database.

Production evidence is now recorded: the source backfill completed the full 95
days; materialization has 92 successful runs only and a clean replay, producing
2,185 coverage rows, 2,185 aggregate rows, and 104,880 baseline rows. Scheduled
deployment `332a56ff-f51b-4f90-ab6a-25d63f4e006e` runs
`50hz-history-materialize --refresh-latest` at `17 4,10 * * *` UTC, and its next
execution has been confirmed.

### Forecast verification

The current tree includes an immutable forecast-verification migration, job,
and public contract for exactly reviewed national pairs:

- Elexon NDF to Elexon INDO demand.
- Elexon WINDFOR to Elexon FUELINST wind.
- NESO national carbon forecast to NESO national carbon estimate.

The reviewed carbon forecast method is
`50hz.neso-carbon-intensity.national-forecast.v1`. NESO does not publish a
source issue timestamp for this feed, so each public carbon result states
`issueTimeBasis=source_does_not_publish_issue_time` and
`effectiveVintageTimeBasis=retrieved_at`; 50Hz uses capture/retrieval time and
does not relabel it as a publisher issue time.

For horizons 0–3, 3–12, 12–24, and 24–48 hours, the job selects an exact stored
forecast vintage and exact compatible outturn timestamp. It stores MAE, signed
bias, safe-denominator WAPE, sample count, coverage, verification window,
issue-time basis, evidence checksum, source watermark, and methodology versions.
No vintage, timestamp, interpolation, or regional accuracy is synthesized.
Statistics remain `insufficient_data` until there are at least 100 verified
samples and 90% compatible coverage. A normal run defaults to 28 completed
London days and cannot exceed 31. `--refresh-latest` rechecks the latest seven
completed London days for a bounded daily cron.

Production migration and the initial verification run are complete. Three
successful metric runs with no failed run produced 89,481 exact compatible pairs
and all 12 reviewed metric/horizon rows: demand and wind statistics are
available; carbon remains truthfully `insufficient_data` at its evidence
threshold. No row is `not_computed`. Scheduled deployment
`f317ebe3-0bc0-4d63-a949-d32542d87caf` runs
`50hz-forecast-verify --refresh-latest` at `17 11 * * *` UTC. The passing public
route smoke is recorded against the API deployment separately from the job result.

The native Data Details inspector also implements a protected, ETag-aware
Forecast review. It presents national-only MAE, signed bias, WAPE, horizon,
pairs, coverage, verification window, source mapping, issue/effective-vintage
basis, and method/revision. Numbers remain hidden when the server status/reason,
100-sample/90%-coverage gates, native coverage math, evidence/method contract, or
uniqueness check fails. Local can show only national-carbon MAE for a complete
plan that remains inside one reviewed horizon and exactly matches source,
method, basis, and outturn class. Otherwise the review is silent and never
blocks planning; it never infers regional error or calls the metric confidence.

## Current API contracts

The complete 21-path OpenAPI route table and limits are in
[README.md](../README.md). The final safe smoke covered all 19 GET templates,
legal pages, dynamic event/history, JSON/CSV export, ETag 304, gzip, request IDs
and log hygiene, plus paid event explanation and paid grounded Ask. Important
contracts in the release candidate are:

- `/v1/metadata/metrics` for versioned definitions and supply/sign boundaries.
- `/v1/briefing/today` for finite deterministic Today content.
- `/v1/regions/{postcode}/windows` for gap-aware continuous Local planning,
  duration, and optional start/deadline bounds.
- `/v1/game/{date}/resolution` for auditable pending/correct/incorrect/void
  prediction outcomes and evidence corrections.
- `/v1/sources/status` for public-safe source delivery versus fact validity.
- `/v1/events/{event_id}/history` for immutable reported lifecycle revisions.
- `/v1/metadata/export-schema` and `/v1/export` for bounded half-hour JSON/CSV.
- `/v1/forecasts/verification` for evidence-qualified national forecast error.

HTTP behavior in the current tree includes:

- representation-derived ETags/304 and explicit `Cache-Control` on stable JSON;
- gzip above 1,000 response bytes;
- process-local route burst limits with `Retry-After`;
- privacy-bounded structured access records and `X-Request-ID` on responses;
- no query string, request body, header, IP/client address, exception message,
  or unmatched raw path in the application access record.

The API is unauthenticated and intended to run as one process during validation.
ETags are transfer validators, not a database query cache. A durable shared
limiter/budget is required before horizontal scaling.

## Events and grounded intelligence

### Reported events

Active public events map authoritative latest REMIT and recent SYSWARN revisions
to stable opaque IDs. Detail and explanation remain active-only. History is
independently resolvable for terminal reported events and returns at most 100
newest-first immutable lifecycle revisions, field changes, evidence checksums,
and source-record provenance.

Reported-event explanations are cached by stable event ID and notice revision.
Detected-event explanations are cached by evidence checksum, configured
provider/model, prompt version, and locale. Validated output may cache;
deterministic fallback copy never does.

The worker also runs one failure-isolated `ObservedEventMaintenanceAction` after
a successful generation, connector, or frequency job, including reconciliation
runs. It takes the advisory lock `50hz:maintenance:observed-events:v1`, performs
exactly three bounded normalized reads (45-minute lookback, at most 512 rows per
family), and independently constructs coherent evidence windows:

- generation: latest two complete snapshots with at least four series, no more
  than ten minutes apart and twenty minutes old;
- connectors: latest three complete snapshots under the versioned connector
  registry, no more than ten minutes apart and twenty minutes old; a signed
  reversal requires two sustained samples at least 100 MW from zero;
- frequency: latest GB sample no more than five minutes old.

All evidence timestamps/revisions are at or before one cutoff, but the families
are never combined at a synthetic cross-family instant. Rules produce derived/
observed copy only; they never infer an outage or cause. Deterministic keys and
checksums make replay idempotent, corrections increment the evidence revision,
and an exact-time correction can resolve a removed moment event. Rule-owned
expiry is ten minutes for frequency, twenty for renewable state, and thirty for
leader/reversal. Persisted rule IDs are versioned under
`observed.window-v1.<rule>.v1`. Resolution/expiry is restricted to
`observed.%`, bounded to 256 rows per scope/pass with one ID read plus one set
update, so reported-notice lifecycle is untouched and there is no per-row N+1.

An in-process fingerprint avoids unchanged evaluation; database idempotence
still handles restarts/replicas, and expiry runs after relevant successes. If all
three relevant collectors fail simultaneously, expiry waits for a future
relevant success. The derived-event row retains the current evidence payload
with incrementing version/checksum, not a full immutable history of every prior
derived payload; reported-event history is a separate immutable contract. There
is no operator cron, key, or new configuration. This is deployed in the final
worker artifact, and current public event list/detail remains the
authoritative reported-notice flow rather than a claim that every derived event
is public.

### Ask the Grid

Ask exposes five bounded read-only tools: current state, metric history, active
events, event evidence, and cleanest forecast window. Tool rounds/calls,
history, duration, metrics, and event IDs are bounded or allow-listed. There is
no arbitrary SQL, URL, filesystem, environment, or write access.

Validation checks evidence-backed numbers/units, import/export signs,
classification, and unsupported causality. The server chooses citations.
Invalid Ask output returns a bounded error instead of an ungrounded answer;
event explanation has deterministic fallback copy. OpenRouter calls request
zero-data-retention routing. The process-local daily counter is a guardrail, not
a billing or distributed quota; the OpenRouter project/account cap is the hard
spend boundary.

For a simple current-generation-leader question, the server now owns the answer
directly: it selects the largest compatible MW generation fact from the latest
evidence snapshot and supplies its source citation. Explanatory `why` questions
remain model-authored and pass the normal validation boundary. This grounding
regression is fixed in commit `997ba3c`.

## Native iOS implementation

The app now contains:

- **Live:** cache-first national snapshot/timeline, illustrative Britain canvas,
  fuel focus, demand/frequency/carbon/connector detail, replay/Resume Live,
  reported-event list/detail/history/explanation, share card, and contextual
  Ask.
- **Today:** server-defined bounded briefing with Now, best window, maximum-three
  changes/upcoming moments/reported events, London-day cache identity, and
  explicit partial/offline states.
- **Local:** Central London default, explicit region edit, full/outward postcode
  normalization, multiple flexible activities/custom duration, deterministic
  continuous window presentation, coverage/gaps/vintage, optional start/deadline
  bounds, eligible exact-match national-carbon MAE qualification, and
  device-local reminder schedule/update/cancel. The full postcode entry is
  transient; only the outward code is persisted/displayed/sent. Notification
  permission is requested only from an explicit reminder tap.
- **Notebook:** backend daily definition, exact lock enforced at display and tap
  time, local choice persistence, evidence resolution, correction/void handling,
  mission navigation before local/unverified completion, and learned concepts.
  Explicit reminders can fire 15 minutes before lock and, after a local choice,
  five minutes after the evidence window closes. The second is a check prompt,
  not a claim that a result has been published.
- **Data Details/pro tools:** metric/source methodology, per-family state,
  public source delivery/fact health, exact sortable supply/connector tables,
  reported event revision deltas, and maximum-31-day JSON/CSV sharing through a
  protected local artifact. Forecast review shows eligible national MAE, bias,
  and WAPE by horizon while withholding absent, ambiguous, incompatible, or
  below-threshold rows.
- **System behavior:** protected ETag cache, in-flight deduplication, cancellation
  and race protection, dark launch screen, first-run disclosure/onboarding,
  notification deep links to Local/Notebook including a one-shot cold-launch
  handoff after app state is ready, privacy/support links, a privacy manifest
  with required File Timestamp reason `C617.1`, and no third-party iOS packages.

Prediction choices, mission completion, outward-code preference, reminder
metadata, and learned state remain local. There is no server choice submission
or scoring.
The evidence result is computed by the backend independently of a user's choice;
the native app derives correct/incorrect locally only when the method/source
contract is supported. Notification authorization is read/requested only after
an explicit scheduling action; ordinary refresh does not prompt.

## Verification record

Repository gates for the exact release commit are:

```bash
source .venv/bin/activate
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
git diff --check
```

The current release checkpoint records 611 passing backend tests and 148 passing
native tests. Compileall, diff checking, a single Alembic head, offline full-upgrade
SQL, and offline `0009` downgrade SQL pass. The Release simulator build excludes
fixture JSON. `PrivacyInfo.xcprivacy` passes `plutil` lint and now includes File
Timestamp reason `C617.1`; regression coverage includes one-shot notification
routing when a tap cold-launches the app. The device archive remains blocked
during LaunchScreen compilation because this Xcode install reports `iOS 26.4
Platform Not Installed`, and only development signing identities are installed.
The newest migrations still require a disposable live PostgreSQL downgrade;
production forward migration and offline SQL do not prove downgrade behavior.

## Deployment and release sequence

### 1. Freeze and verify

1. Reconcile the shared tree and review every uncommitted file.
2. Preserve the recorded 611 backend and 148 native tests plus migration, privacy,
   compile, diff, and secret-scan gates.
3. Repair/install Docker or use another disposable live PostgreSQL instance,
   then validate downgrade through `20260712_0009`; production forward migration
   is already applied.
4. Commit the documentation with the final route/migration/test inventory.

### 2. Push and deploy

1. Owner restores normal GitHub credential access and pushes the clean reviewed
   commits; Railway authentication already works.
2. Record worker deployment `0003798a-a2f4-4aac-a745-5522dafdc22e`, history cron
   `332a56ff-f51b-4f90-ab6a-25d63f4e006e`, forecast cron
   `f317ebe3-0bc0-4d63-a949-d32542d87caf`, and the final API deployment as one
   release unit.
3. Record successful API deployment `817ad899-1cc9-4baa-8900-5e1882e2f05d`, its
   exact deterministic generation-leader answer/citations, all nine healthy
   canonical sources with no aliases, and the complete route smoke.
4. Preserve the completed 95-day backfill, 92/92 clean materialization, and 3/3
   verification evidence; confirm both cron next-run records.
5. Preserve the passing 21-path/AI/ETag/gzip/request-ID/log-hygiene evidence and
   verify the iOS app against that exact deployment.

### 3. TestFlight

Owner-controlled prerequisites:

- Confirm Apple team `VKMJPS7WP4`, signing/upload roles, and paid membership.
- Confirm/register bundle ID `com.papajohn.50hz`.
- Create the App Store Connect record and internal tester group.
- Approve privacy/support contact, provider retention, App Privacy answers,
  screenshots, metadata, rights, rating, and export-compliance answers.
- Rotate the temporary OpenRouter key and confirm its production spend cap.

Then create/validate a signed archive, install on a physical iPhone, complete
offline/accessibility/performance/battery QA, upload, wait for processing,
install from TestFlight, and rerun the complete production flow. See
[APP_STORE_RELEASE.md](APP_STORE_RELEASE.md).

The current host first needs the missing iOS 26.4 device platform installed in
Xcode; the unsigned archive compile stops at LaunchScreen compilation without
it. This is separate from Apple signing authority.

## Internal TestFlight definition of done

50Hz is ready for internal TestFlight only when:

- API and worker are deployed from the recorded pushed commit and both roles are
  ready with current required evidence.
- New migrations and bounded data jobs completed or produced an explicitly
  accepted insufficient-data state.
- Every native route succeeds against that deployment, including graceful AI,
  upstream, stale, partial, and offline behavior.
- Outward-only postcode transmission, protected caches/artifacts, local choices,
  and explicit notification permission are verified.
- Event/outage and forecast-quality language remains traceable to compatible
  evidence and display thresholds.
- A signed Release archive installs on a physical iPhone and passes the manual
  accessibility/performance matrix.
- Privacy, support, attribution, metadata, screenshots, and owner approvals are
  complete.
- The uploaded build processes and installs through the intended internal
  TestFlight group.
