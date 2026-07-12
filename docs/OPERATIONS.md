# 50Hz Railway operations runbook

This runbook covers the Railway API, worker, history cron, forecast cron, and
shared PostgreSQL services. The public API is
`https://50hz-api-production.up.railway.app`; the deployed service names are
`50hz-api`, `50hz-worker`, `50hz-history-materialize`, and
`50hz-forecast-verify`.

Production PostgreSQL is at `20260712_0009`. The 95-day source backfill, 92/92
history materialization plus clean replay, and 3/3 forecast verification runs
are complete. Worker deployment `0003798a-a2f4-4aac-a745-5522dafdc22e`, history
cron deployment `332a56ff-f51b-4f90-ab6a-25d63f4e006e`, and forecast cron
deployment `f317ebe3-0bc0-4d63-a949-d32542d87caf` are recorded. Final API
deployment `817ad899-1cc9-4baa-8900-5e1882e2f05d` is `SUCCESS`. The complete
production smoke passes, including the exact evidence-owned generation-leader
Ask answer/citations and all nine healthy canonical sources with no operational
aliases. Record the eventually pushed commit, all four deployment artifacts,
database revision, job evidence, and route smoke as one release unit.

Never paste secrets into tickets, chat, command arguments, screenshots, or
shared logs. Railway variable output can contain raw credentials. Regional
paths contain outward postcode codes, and Ask request bodies may contain user
text; redact both when sharing logs.

## 1. Access and orientation

The commands below were checked against Railway CLI 4.58.0. Confirm identity,
project, environment, and services before any state-changing command:

```bash
railway --version
railway whoami
railway status
railway service list
```

If authentication fails:

```bash
railway login
railway status
```

Railway CLI authentication works at this handoff. Local GitHub Keychain access
still blocks the push. Do not work around that by pasting a token into a command,
captured terminal, or repository URL. Restore the normal GitHub credential
helper, confirm `git ls-remote origin`, and reconcile the clean pushed commit to
the uploaded Railway artifacts.

Use `railway open` to cross-check the selected project and production
environment. Then set local labels from confirmed values:

```bash
export API_SERVICE='50hz-api'
export WORKER_SERVICE='50hz-worker'
export HISTORY_SERVICE='50hz-history-materialize'
export FORECAST_SERVICE='50hz-forecast-verify'
export RAILWAY_ENVIRONMENT='production'
export API_BASE='https://50hz-api-production.up.railway.app'
```

Prefer immutable service IDs in automation when names could collide.

## 2. Health and readiness semantics

Railway uses `/ready` from `railway.toml`.

### `/health`

`GET /health` is diagnostic. A running process returns HTTP 200 with:

- `status=ok` when the database probe succeeds
- `status=degraded` and `database=false` when it fails
- the selected `role`, service label, and timestamp

Because database failure still produces HTTP 200, `/health` is not a deployment
gate.

### `/ready`

`GET /ready` is the deployment gate:

- both roles return 503 when PostgreSQL is unavailable
- the worker returns 503 when its ingestion task is missing or stopped
- after a five-minute first-deploy grace period, the worker also returns 503
  when required grid data cannot be presented or is stale
- a successful response is HTTP 200 with `status=ready`

The API role's `/ready` currently proves database connectivity only. It does not
prove source freshness, OpenRouter functionality, history materialization,
forecast verification, or every route contract. The worker readiness check uses
aggregate required-data freshness. `/v1/sources/status` separately exposes
public-safe delivery and current-fact state after the current tree is deployed;
it is diagnostic and is not itself the Railway health gate. Raw-payload cleanup
and observed-event maintenance are isolated post-ingest work: their failures are
logged/retried without stopping successful source ingestion.

Quick checks:

```bash
curl -fsS "$API_BASE/health"
curl -fsS "$API_BASE/ready"
curl -fsS "$API_BASE/v1/meta"
curl -fsS "$API_BASE/v1/sources/status"
```

The worker may not have a public domain. Inspect its Railway health state and
logs instead:

```bash
railway service status \
  --service "$WORKER_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT"

railway logs \
  --service "$WORKER_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --since 30m \
  --lines 200
```

A running task alone does not prove every upstream source is advancing. Compare
`/v1/sources/status`, `/v1/grid/current`, `/v1/sources`, and worker logs. Delivery
health and fact validity answer different questions and must not be collapsed.

## 3. Pre-deploy gate

From the reviewed release-candidate checkout:

```bash
git status --short
git diff --check
source .venv/bin/activate
pytest -q
python -m compileall -q app tests
DATABASE_URL=postgresql://postgres:postgres@localhost/50hz \
  alembic upgrade head --sql > /tmp/50hz-migrations.sql
plutil -lint ios/50Hz/Resources/PrivacyInfo.xcprivacy
```

On a Mac with a healthy iOS Simulator runtime:

```bash
xcodebuild \
  -project ios/50Hz.xcodeproj \
  -scheme 50Hz \
  -configuration Debug \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro' \
  -derivedDataPath /tmp/50hz-derived \
  test
```

Use an installed simulator name/runtime on the host. The current release
checkpoint records 611 passing backend and 148 passing native tests.
`test` executes XCTest but does not sign a Release archive or prove
physical-device/TestFlight behavior.
Review migration SQL whenever schema changes. Offline SQL confirms ordering and
compilation only. Production forward migration is recorded at `0009`; validate
its downgrade against disposable live PostgreSQL before release. That live check
remains outstanding because the installed Docker.app is incomplete/missing its
executable. Confirm a usable Railway database backup/restore point before
production DDL or long-running data jobs.

The Release simulator suite/build/run succeeds and excludes fixture JSON. The
privacy manifest includes required File Timestamp reason `C617.1`, and native
regression coverage verifies one-shot notification routing when a tap
cold-launches the app. The device archive still fails during LaunchScreen
compilation with `iOS 26.4 Platform Not Installed`. Install/repair that Xcode
device platform before using an archive result as a release compile gate. Only
development signing identities are installed, so distribution signing remains a
separate owner-controlled gate.

Record the exact commit being deployed:

```bash
git rev-parse HEAD
git status --short
```

Production source deployments should use a pushed, reviewed commit. A dirty
local tree must not be treated as the deployed artifact.

Before deploying, verify no secret was introduced:

```bash
git grep -n -E 'OPENROUTER_API_KEY=.+|postgres(ql)?://[^[:space:]]+:[^[:space:]]+@' -- . \
  ':(exclude).env.example' || true
```

Review every match rather than treating a zero/non-zero exit code as a complete
secret audit. Rotate any credential that has entered chat, logs, shell history,
or a commit.

Worker retention defaults should also be explicit in Railway:

| Variable | Default | Valid range | Purpose |
| --- | ---: | ---: | --- |
| `RAW_PAYLOAD_RETENTION_HOURS` | 72 | 49–720 hours | Keep raw JSON beyond the 48-hour reconciliation window, then prune it |
| `RAW_PAYLOAD_CLEANUP_INTERVAL_SECONDS` | 3600 | 300–86400 seconds | Maintenance-task cadence |

Each default cleanup run deletes at most eight locked batches of 25 expired
rows. Normalized observations, forecasts, notices, and provenance columns
survive through existing `ON DELETE SET NULL` relationships. If the limit is
reached, remaining expired rows wait for a later run.

The worker also owns a failure-isolated observed-event maintenance action. It
runs only after a successful `elexon.fuelinst`, `elexon.interconnectors`, or
`elexon.freq` job (including reconciliation), under advisory lock
`50hz:maintenance:observed-events:v1`. It makes three bounded normalized reads,
uses independent coherent evidence windows, and never infers outage or cause.
Deterministic keys/checksums make replay safe; corrections append evidence
versions and can resolve removed exact-time events. Rule-owned expiry is 10–30
minutes and touches only versioned `observed.%` events, never reported notices.
Resolution/expiry is limited to 256 IDs per scope/pass and uses set updates,
rather than an unbounded or per-row write loop.

There is no cron, variable, or OpenRouter key for this action. A failure is
logged without invalidating the successful ingestion job. Expiry waits for the
next relevant successful source run, so a simultaneous failure of all three
inputs can delay expiry. Strict completeness intentionally suppresses partial or
unknown generation/connector snapshots rather than fabricating an event.

## 4. Deploy API and worker

`railway.toml` runs `alembic upgrade head` as a pre-deploy command for the API and
worker. Deploy those roles sequentially so they do not start migrations at the
same time. `railway.history.json` and `railway.forecast.json` explicitly clear
the migration command and `/ready` healthcheck for the short-lived cron jobs.

After normal GitHub credentials are restored, deploy the API from its configured
source. The current candidate was uploaded directly and must later be reconciled
to the pushed commit:

```bash
railway redeploy \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --from-source \
  --yes

railway service status \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT"
```

Wait for the deployment to become healthy, verify `$API_BASE/ready`, and inspect
deployment logs. Then deploy the worker:

```bash
railway redeploy \
  --service "$WORKER_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --from-source \
  --yes

railway service status \
  --service "$WORKER_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT"
```

An explicit emergency upload of the local directory is possible only after
reviewing `git status`:

```bash
railway up \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --detach \
  --message 'emergency deploy: describe the approved change'
```

A successful `redeploy`/`up` invocation means deployment started, not that it
became ready. Poll service state and inspect the latest deployment logs.

## 5. Post-deploy route smoke

The final 12 July record for API deployment
`817ad899-1cc9-4baa-8900-5e1882e2f05d` passed this smoke: all 19 GET templates,
legal pages, dynamic event/detail/history, JSON and CSV export, ETag 304, gzip,
request IDs and application-log hygiene, paid event explanation, and paid Ask.
The final worker deployment is `0003798a-a2f4-4aac-a745-5522dafdc22e`.

First verify the route surface:

```bash
curl -fsS "$API_BASE/openapi.json" > /tmp/50hz-openapi.json
.venv/bin/python - <<'PY'
import json
from pathlib import Path

document = json.loads(Path("/tmp/50hz-openapi.json").read_text())
print("\n".join(sorted(document["paths"])))
PY
```

For the current tree, require exactly the reviewed 21 OpenAPI paths, including
health/readiness/meta,
current/timeline/briefing, sources/source status/metric metadata, reported event
list/detail/history/explanation, region/Local windows, daily game/prediction
resolution, export schema/export, forecast verification, and Ask. `/privacy`
and `/support` are intentionally hidden from OpenAPI, so smoke them separately.
Diff this inventory against the reviewed commit; do not use a hard-coded list to
approve an unexpected route.

Smoke deterministic routes:

```bash
curl -fsS "$API_BASE/health" > /tmp/50hz-health.json
curl -fsS "$API_BASE/ready" > /tmp/50hz-ready.json
curl -fsS "$API_BASE/privacy" > /tmp/50hz-privacy.html
curl -fsS "$API_BASE/support" > /tmp/50hz-support.html
curl -fsS "$API_BASE/v1/meta" > /tmp/50hz-meta.json
curl -fsS "$API_BASE/v1/grid/current" > /tmp/50hz-current.json
curl -fsS "$API_BASE/v1/grid/timeline?resolution=1800" > /tmp/50hz-timeline.json
curl -fsS "$API_BASE/v1/briefing/today" > /tmp/50hz-briefing.json
curl -fsS "$API_BASE/v1/sources" > /tmp/50hz-sources.json
curl -fsS "$API_BASE/v1/sources/status" > /tmp/50hz-source-status.json
curl -fsS "$API_BASE/v1/metadata/metrics" > /tmp/50hz-metrics.json
curl -fsS "$API_BASE/v1/metadata/export-schema" > /tmp/50hz-export-schema.json
curl -fsS "$API_BASE/v1/events" > /tmp/50hz-events.json
curl -fsS "$API_BASE/v1/regions/SW1A" > /tmp/50hz-region.json
curl -fsS \
  "$API_BASE/v1/regions/SW1A/windows?durationMinutes=120" \
  > /tmp/50hz-windows.json
curl -fsS "$API_BASE/v1/game/today" > /tmp/50hz-game.json
curl -fsS "$API_BASE/v1/forecasts/verification" \
  > /tmp/50hz-forecast-verification.json
```

Resolve a completed London prediction date from the game contract or choose a
date within the route's 31-day bound, then smoke resolution. `pending`, `void`,
and an evidence result can all be valid depending on time and coverage:

```bash
PREDICTION_DATE='YYYY-MM-DD'
curl -fsS "$API_BASE/v1/game/$PREDICTION_DATE/resolution" \
  > /tmp/50hz-resolution.json
```

Smoke a minimal bounded JSON export on exact UTC half-hour boundaries after
choosing a metric from the published schema. Never assume missing intervals are
omitted; the contract emits explicit `insufficient_data` rows:

```bash
EXPORT_FROM='YYYY-MM-DDTHH:00:00Z'
EXPORT_TO='YYYY-MM-DDTHH:30:00Z'
curl -fsS --get \
  --data-urlencode 'metric=carbon.intensity.national' \
  --data-urlencode "from=$EXPORT_FROM" \
  --data-urlencode "to=$EXPORT_TO" \
  --data-urlencode 'resolution=1800' \
  --data-urlencode 'format=json' \
  "$API_BASE/v1/export" > /tmp/50hz-export.json
```

Do not stop at status codes. Validate that:

- current contains non-empty generation, demand, carbon, source references, and
  plausible non-negative freshness age
- ordinary REMIT unavailability is not automatically marked critical; severity
  and planned/ended status match the notice evidence
- one-hour fuel changes are not all permanently zero when history exists
- timeline observed/forecast classification and now boundary are coherent
- briefing is finite, carries the London local date, coverage/revision fields,
  and no more than three changes, next moments, or reported events
- source status separates worker delivery from fact validity and exposes no raw
  error/infrastructure identifier; it contains exactly the nine reviewed source
  IDs and no operational/backfill aliases
- region returns an outward code, current or explicitly delayed regional period,
  and a clearly scoped national forecast window
- Local uses one compatible forecast capture, exact continuous half-hours,
  explicit coverage/gaps, and matching requested duration/bounds
- game availability corresponds to current freshness, forecasts, and events;
  resolution never guesses when evidence is insufficient
- event history is immutable/newest-first and remains available for a known
  terminal reported event even though active detail is active-only
- export schema and output enforce the 31-day/1,488-row/1,800-second bounds and
  retain missing rows, classification, methods, and source-record provenance
- forecast verification returns all requested national metric/horizon slots;
  MAE/bias/WAPE appear only after 100 samples and 90% coverage; carbon declares
  `source_does_not_publish_issue_time` with effective retrieval-time vintage
  rather than inventing a publisher timestamp
- source observation/retrieval timestamps fall within expected cadence or are
  explicitly stale
- privacy/support pages are publicly reachable over HTTPS, contain current copy,
  and expose an owner-approved contact route before their URLs enter App Store
  Connect

### OpenRouter spend smoke

Only run this after explicit authorization to spend from the configured key:

```bash
curl -fsS \
  -X POST \
  -H 'Content-Type: application/json' \
  --data '{"question":"What is powering Britain right now?"}' \
  "$API_BASE/v1/ask" > /tmp/50hz-ask.json
```

The answer must contain server-resolved citations and no unsupported numerical
or causal claim. A grounded 503 is preferable to an invented answer, but a 503
for the app's default question blocks the release flow.

If an active event exists, verify detail and explanation:

```bash
EVENT_ID=$(
  .venv/bin/python - <<'PY'
import json
from pathlib import Path

events = json.loads(Path("/tmp/50hz-events.json").read_text())
print(events[0]["id"] if events else "")
PY
)

if [ -n "$EVENT_ID" ]; then
  curl -fsS "$API_BASE/v1/events/$EVENT_ID" > /tmp/50hz-event.json
  curl -fsS "$API_BASE/v1/events/$EVENT_ID/history" \
    > /tmp/50hz-event-history.json
  curl -fsS "$API_BASE/v1/events/$EVENT_ID/explanation" \
    > /tmp/50hz-explanation.json
fi
```

The explanation may set `usedFallback=true`; it must still cite only supplied
evidence. Successfully validated detected-event and reported-notice explanations
are database-cached; deterministic fallbacks are not. A new reported-notice
revision deliberately creates a new cache entry.

## 6. ETag, gzip, and rate-limit checks

Use a stable representation such as `/v1/sources` to verify conditional GET:

```bash
curl -fsS -D /tmp/50hz-source-headers \
  -o /tmp/50hz-sources.json \
  "$API_BASE/v1/sources"

ETAG=$(awk 'BEGIN { IGNORECASE=1 } /^etag:/ { gsub("\r", ""); print $2 }' \
  /tmp/50hz-source-headers)
test -n "$ETAG"

curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "If-None-Match: $ETAG" \
  "$API_BASE/v1/sources"
```

The last command should print `304`. ETags are generated after route execution;
this validates transfer behavior, not reduced database work.

Verify compression on a response over 1,000 bytes:

```bash
curl -sS --compressed -D - -o /dev/null \
  -H 'Accept-Encoding: gzip' \
  "$API_BASE/v1/grid/timeline?resolution=1800"
```

Expect `content-encoding: gzip` when the uncompressed representation exceeds the
threshold. Small responses may correctly remain uncompressed.

Current one-minute process-local limits:

| Endpoint | Per client | Per process |
| --- | ---: | ---: |
| `POST /v1/ask` | 6 | 30 |
| `GET /v1/export` | 6 | 30 |
| `GET /v1/events/{id}/explanation` | 12 | 60 |
| `GET /v1/game/{date}/resolution` | 12 | 60 |
| `GET /v1/forecasts/verification` | 12 | 60 |
| `GET /v1/regions/{postcode}` and `/windows` | 30 | 120 |
| `GET /v1/briefing/today` | 30 | 120 |
| `GET /v1/sources/status` | 30 | 120 |
| `GET /v1/grid/timeline` | 60 | 300 |

HTTP 429 must include `Retry-After`. Do not hammer production to prove limits:
use unit tests or a dedicated staging service. Counters prefer Railway's
proxy-owned `X-Real-IP`, then the left-most `X-Forwarded-For` value for non-Railway
or local proxy setups, and finally the socket address. They live only in one
process and reset on restart. They are burst protection, not authentication or a
distributed abuse-control system.

Current stable JSON cache policy:

| Route family | `max-age` |
| --- | ---: |
| Current snapshot and source status | 30 seconds |
| Timeline, briefing, event list/detail/history, Local windows, game/resolution | 60 seconds |
| Region and forecast verification | 300 seconds |
| Sources and metric/export metadata | 3,600 seconds |

Every response should also include `X-Request-ID`. The API's application access
record is deliberately privacy-bounded: registered route template/name, method,
status, duration, response size, request ID, and service role/version only. It
does not emit query strings, request bodies, headers, IP/client addresses,
exception messages, or unmatched raw paths. Railway platform logs have separate
behavior and retention that the owner must verify.

## 7. Logs and freshness

Recent API errors:

```bash
railway logs \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --since 1h \
  --lines 200 \
  --filter '@level:error'
```

HTTP failures:

```bash
railway logs \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --http \
  --status '>=400' \
  --since 1h \
  --lines 200
```

Worker failures:

```bash
railway logs \
  --service "$WORKER_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --since 2h \
  --lines 300 \
  --filter '@level:error'
```

Latest build/deploy logs:

```bash
railway logs --build --latest --lines 200 \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT"

railway logs --deployment --latest --lines 200 \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT"
```

Redact outward postcodes, IP addresses, request IDs, questions, upstream free
text, and authorization-related material before sharing. Do not use `--json` or
variable-listing modes in a shared terminal unless their raw output has been
reviewed for secrets.

## 8. Migrations

Normal deployment automatically runs this pre-deploy command inside Railway:

```bash
alembic upgrade head
```

Inspect the production database revision inside the API service without printing
variables:

```bash
railway ssh \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  alembic current
```

Run an approved migration manually only when needed:

```bash
railway ssh \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  alembic upgrade head
```

Railway's private PostgreSQL hostname is not generally resolvable from a local
shell, so do not use a local `railway run alembic ...` invocation for this check.
Flags must precede the remote command. Never run `env`, `printenv`, or similar
commands in a captured/shared session because they can print secrets. Do not
automatically downgrade a production database after a failed release. Prefer a
reviewed forward fix. Stop and confirm restore options before any destructive
DDL or data restoration.

## 9. History and verification jobs

These jobs are bounded operator/cron processes using the same image and
PostgreSQL database. They must not be added to the API or ingestion worker
lifespan. Give them `DATABASE_URL`, public upstream base URLs where applicable,
and `APP_ENV`; do not give them `OPENROUTER_API_KEY`.

### Historical source backfill

Start with a dry run, inspect the exact date/source chunks, and then run the
same explicit request without `--dry-run`:

```bash
50hz-history-backfill --days 95 --dry-run
50hz-history-backfill --days 95
```

The maximum is 95 completed `Europe/London` settlement days. `--start` and
exclusive `--end` are alternatives to `--days`; repeat `--source` to narrow the
allow-list. Checkpoints make a retry resumable, and successful chunks skip
unless `--force` is explicitly approved. Historical carbon range responses do
not contain forecast issue timestamps, so this job imports estimates only; it
does not fabricate vintages. High-frequency frequency is also excluded.

### Coverage/comparison materialization

After compatible normalized data exists:

```bash
50hz-history-materialize --days 95 --dry-run
50hz-history-materialize --days 95
50hz-history-materialize --refresh-latest
```

The job handles up to 95 completed London days in maximum-30-day chunks, reads a
28-day baseline lookback, and writes immutable revisions for an explicit
23-series registry. Existing successful chunks skip; `--force` re-evaluates and
appends only changed evidence. `--refresh-latest` force-rechecks yesterday only.
Daily/rolling results require 95% compatible coverage and remain insufficient
instead of filling gaps.

Recommended Railway cron after the initial backfill/materialization is
`17 4,10 * * *` UTC with command:

```bash
50hz-history-materialize --refresh-latest
```

The twice-daily UTC schedule intentionally revisits publisher corrections while
the command itself still touches only the last completed London day.

Recorded production result: the initial 95-day backfill completed; all 92
materialization checkpoints succeeded with no failed run and a clean replay
remained complete. It produced 2,185 coverage rows, 2,185 aggregate rows, and
104,880 comparison-baseline rows.
History cron deployment `332a56ff-f51b-4f90-ab6a-25d63f4e006e` runs the command
above at `17 4,10 * * *` UTC, and its next execution is confirmed.

### Forecast verification

After migration `20260712_0009` and enough immutable forecast/outturn evidence:

```bash
50hz-forecast-verify --days 28 --dry-run
50hz-forecast-verify --days 28
50hz-forecast-verify --refresh-latest
```

Repeat `--metric` to narrow the reviewed national demand, wind, or carbon pairs.
The default window is 28 completed London days and the hard maximum is 31; use
explicit `--start` plus exclusive `--end` only within that bound.
`--refresh-latest` force-rechecks the latest seven completed London days so late
outturns/corrections can append new result revisions. Production has completed
3/3 successful metric runs with no failed run, 89,481 exact compatible pairs,
and exactly 12 metric/horizon results. Demand and wind
statistics are available; all carbon rows remain `insufficient_data` at the
reviewed evidence threshold, and no row is `not_computed`. Forecast cron
deployment `f317ebe3-0bc0-4d63-a949-d32542d87caf` runs the command above daily at
`17 11 * * *` UTC. Statistics remain hidden until at least 100 samples and 90%
coverage.
Evidence/input capacity is conservative and fail-closed: an oversized reviewed
set must fail the run rather than silently truncate samples or improve coverage.

For every real job run, record:

- deployment image/commit and Alembic revision;
- exact command, UTC start/end, and target environment;
- date range, selected sources/metrics, chunk/run summary, and exit code;
- row/revision counts and any insufficient/failed chunks;
- follow-up route smoke and source watermark.

Never use `--force` as a generic retry reflex. First determine whether a safe
checkpoint should resume normally or whether corrected upstream evidence truly
needs re-evaluation.

## 10. Restart, rollback, and incident triage

A restart is appropriate only for a transient process failure and reuses the
same deployment:

```bash
railway restart \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --yes
```

For code rollback, use the Railway dashboard to redeploy a known-good prior
deployment. A code rollback does not reverse database migrations.

| Symptom | First checks | Next action |
| --- | --- | --- |
| API unavailable or `/ready` 503 | API status, DB service, deploy/migration logs | Restore DB connectivity or deploy a forward boot/migration fix |
| Worker `/ready` 503 after grace | Worker task/logs, current data timestamps, upstream errors | Diagnose stale source/adapter; restart only if the task is stuck |
| One source stale | Documented cadence, upstream 429/5xx/schema change | Allow bounded retry or deploy an adapter fix |
| Region 502/503 | Postcode format, NESO response period/status, API logs | Retry later or fix source-period selection; do not label as invalid input unless 422 |
| New route 404 | OpenAPI, deployed commit, latest API deployment | Deploy/reconcile the current tree; do not debug the native decoder against an older contract |
| Source delivery healthy but fact stale | Source status timestamps, valid interval, current composition | Diagnose publisher fact behavior/coverage; polling success alone does not make a fact current |
| Ask 429 | `Retry-After`, client request loop | Respect backoff; do not retry immediately |
| Ask 503 | key configured, budget, OpenRouter response, evidence validation | Keep deterministic grid browsing available; diagnose before spending repeated calls |
| Explanation repeatedly calls model | Event kind/ID, evidence/revision key, fallback flag, cache rows | Validated detected and reported-notice output should cache; deterministic fallback is intentionally retried |
| Raw-payload cleanup warning/error | Worker maintenance log, configured retention/interval, database load | Cleanup retries independently; diagnose backlog without stopping ingestion |
| History job incomplete | Chunk checkpoints, locks, source/date allow-list, database load | Retry normally to resume; approve `--force` only for a real evidence recheck |
| Verification remains insufficient | Stored vintages, compatible outturn timestamps, samples, coverage, source watermark | Continue collecting evidence; never lower thresholds or synthesize vintages to populate the UI |
| Derived event maintenance fails | Post-ingest maintenance log, coherent evidence window, advisory lock/lifecycle write | Keep ingestion running; fix/retry maintenance without inventing or duplicating an event |
| Migration failure | Generated SQL, pre-deploy log, DB revision/backup | Stop rollout and prepare a reviewed forward/restore plan |

## 11. Secret rotation

The key supplied during development is temporary and must be rotated before
TestFlight or public release.

OpenRouter procedure:

1. Create a replacement key with an explicit account/project spend cap.
2. Replace `OPENROUTER_API_KEY` for the API service only.
3. Wait for the variable-triggered deployment or redeploy the API.
4. Verify `/ready` and `openRouterConfigured=true` in `/v1/meta`.
5. If spend is authorized, run one minimal grounded Ask request.
6. Revoke the old key.
7. Remove any old value from local `.env`, shell history, screenshots, issue
   trackers, and logs.

When the dashboard is unavailable, stdin avoids putting the value in command
arguments:

```bash
railway variable set \
  OPENROUTER_API_KEY \
  --stdin \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT"
```

Prefer Railway service references and managed rotation for PostgreSQL. API,
worker, history cron, and forecast cron all consume the `DATABASE_URL` reference,
so coordinate and verify them without copying the connection string into
documentation.

## 12. Remaining operational and TestFlight gaps

Before expanding traffic or submitting the signed app:

- restore normal GitHub credential access and push the reviewed clean commits;
  Railway authentication already works
- retain the passing smoke for API deployment
  `817ad899-1cc9-4baa-8900-5e1882e2f05d`, worker deployment
  `0003798a-a2f4-4aac-a745-5522dafdc22e`, the two cron deployments, all nine
  healthy canonical sources, and no public aliases; rerun after any backend change
- retain the recorded history/forecast deployments, confirmed cron
  schedules, migration `20260712_0009`, and completed data-job evidence as part
  of the exact release record
- complete the iOS physical-device production flow against those exact deployments
- validate the `0009` downgrade against disposable live PostgreSQL; the installed
  Docker.app is incomplete/missing its executable, leaving this gate open
- add external alerts for per-source last success/failure/lag/records, history
  backlog, verification watermark, and event-maintenance failures
- approve the 72-hour raw-payload policy and define retention for Railway HTTP
  logs, application access records, questions, event evidence/explanations, and
  operational diagnostics
- keep one API process or introduce shared durable rate/budget controls
- complete PostgreSQL backup/restore validation and role/privilege separation
- rotate the temporary OpenRouter key and verify the final account/project cap
- obtain Apple team/signing authority, final bundle registration, App Store
  Connect record/testers, and owner approval of hosted privacy/support pages (or
  replacement URLs/contact details)
- finish App Privacy/rights/rating/export answers, signed physical-device QA,
  install the missing Xcode iOS 26.4 device platform, then complete
  archive/upload and processed TestFlight installation

The privacy manifest and `/ready` endpoint are necessary release inputs, but
neither substitutes for accurate privacy disclosures or end-to-end production
verification.
