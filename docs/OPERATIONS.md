# 50Hz Railway operations runbook

This runbook covers the Railway API service, worker service, and shared
PostgreSQL database. Replace placeholders with the exact names or IDs returned
by Railway. The known public API is
`https://50hz-api-production.up.railway.app`; the worker service identity still
needs confirmation after Railway CLI login.

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

Use `railway open` to cross-check the selected project and production
environment. Then set local labels from confirmed values:

```bash
export API_SERVICE='50hz-api'
export WORKER_SERVICE='replace-with-confirmed-worker-service'
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
prove source freshness, OpenRouter functionality, or every route contract. The
worker readiness check uses aggregate required-data freshness; there is not yet
a per-source health endpoint. Raw-payload cleanup is a separate maintenance task:
its failures are logged/retried and intentionally do not fail ingestion
readiness.

Quick checks:

```bash
curl -fsS "$API_BASE/health"
curl -fsS "$API_BASE/ready"
curl -fsS "$API_BASE/v1/meta"
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

A running task alone does not prove every upstream source is advancing. Inspect
the source `observedAt`/`retrievedAt` timestamps and freshness in
`/v1/grid/current`, compare them with `/v1/sources` cadence, and check worker
logs for repeated source failures.

## 3. Pre-deploy gate

From the reviewed release-candidate checkout:

```bash
git status --short
git diff --check
source .venv/bin/activate
pytest -q
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
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/50hz-derived \
  CODE_SIGNING_ALLOWED=NO \
  build-for-testing
```

`build-for-testing` compiles the app and XCTest bundle; it does not execute the
tests, sign a Release archive, or prove physical-device/TestFlight behavior.
Review migration SQL whenever schema changes. Confirm a usable Railway database
backup/restore point before destructive or long-running migrations.

Record the exact commit being deployed:

```bash
git rev-parse HEAD
git status --short
```

Production source deployments should use a pushed, reviewed commit. A dirty
local tree must not be treated as the deployed artifact.

Worker retention defaults should also be explicit in Railway:

| Variable | Default | Valid range | Purpose |
| --- | ---: | ---: | --- |
| `RAW_PAYLOAD_RETENTION_HOURS` | 72 | 49–720 hours | Keep raw JSON beyond the 48-hour reconciliation window, then prune it |
| `RAW_PAYLOAD_CLEANUP_INTERVAL_SECONDS` | 3600 | 300–86400 seconds | Maintenance-task cadence |

Each default cleanup run deletes at most eight locked batches of 25 expired
rows. Normalized observations, forecasts, notices, and provenance columns
survive through existing `ON DELETE SET NULL` relationships. If the limit is
reached, remaining expired rows wait for a later run.

## 4. Deploy API and worker

`railway.toml` runs `alembic upgrade head` as a pre-deploy command for each
service. Deploy sequentially so the two services do not start migrations at the
same time.

Deploy the API from its configured GitHub source:

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

Expected OpenAPI paths are `/health`, `/ready`, `/v1/meta`, current, timeline,
sources, events/list detail/explanation, region, game, and Ask. `/privacy` and
`/support` are intentionally hidden from OpenAPI, so smoke them separately.

Smoke deterministic routes:

```bash
curl -fsS "$API_BASE/health" > /tmp/50hz-health.json
curl -fsS "$API_BASE/ready" > /tmp/50hz-ready.json
curl -fsS "$API_BASE/privacy" > /tmp/50hz-privacy.html
curl -fsS "$API_BASE/support" > /tmp/50hz-support.html
curl -fsS "$API_BASE/v1/meta" > /tmp/50hz-meta.json
curl -fsS "$API_BASE/v1/grid/current" > /tmp/50hz-current.json
curl -fsS "$API_BASE/v1/grid/timeline?resolution=1800" > /tmp/50hz-timeline.json
curl -fsS "$API_BASE/v1/sources" > /tmp/50hz-sources.json
curl -fsS "$API_BASE/v1/events" > /tmp/50hz-events.json
curl -fsS "$API_BASE/v1/regions/SW1A" > /tmp/50hz-region.json
curl -fsS "$API_BASE/v1/game/today" > /tmp/50hz-game.json
```

Do not stop at status codes. Validate that:

- current contains non-empty generation, demand, carbon, source references, and
  plausible non-negative freshness age
- ordinary REMIT unavailability is not automatically marked critical; severity
  and planned/ended status match the notice evidence
- one-hour fuel changes are not all permanently zero when history exists
- timeline observed/forecast classification and now boundary are coherent
- region returns an outward code, current or explicitly delayed regional period,
  and a valid national forecast window
- game availability corresponds to current freshness, forecasts, and events
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
| `GET /v1/events/{id}/explanation` | 12 | 60 |
| `GET /v1/regions/{postcode}` | 30 | 120 |
| `GET /v1/grid/timeline` | 60 | 300 |

HTTP 429 must include `Retry-After`. Do not hammer production to prove limits:
use unit tests or a dedicated staging service. Counters key the right-most
`X-Forwarded-For` value supplied by Railway, fall back to the socket address,
live only in one process, and reset on restart. They are burst protection, not
authentication or a distributed abuse-control system.

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

Normal deployment runs:

```bash
alembic upgrade head
```

Inspect the production database revision without printing variables:

```bash
railway run \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --no-local \
  alembic current
```

Run an approved migration manually only when needed:

```bash
railway run \
  --service "$API_SERVICE" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --no-local \
  alembic upgrade head
```

Flags must precede the child command. Never run `railway run env`, `printenv`, or
similar commands in a captured/shared session because they can print secrets.
Do not automatically downgrade a production database after a failed release.
Prefer a reviewed forward fix. Stop and confirm restore options before any
destructive DDL or data restoration.

## 9. Restart, rollback, and incident triage

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
| Ask 429 | `Retry-After`, client request loop | Respect backoff; do not retry immediately |
| Ask 503 | key configured, budget, OpenRouter response, evidence validation | Keep deterministic grid browsing available; diagnose before spending repeated calls |
| Explanation repeatedly calls model | Event kind/ID, evidence/revision key, fallback flag, cache rows | Validated detected and reported-notice output should cache; deterministic fallback is intentionally retried |
| Raw-payload cleanup warning/error | Worker maintenance log, configured retention/interval, database load | Cleanup retries independently; diagnose backlog without stopping ingestion |
| Migration failure | Generated SQL, pre-deploy log, DB revision/backup | Stop rollout and prepare a reviewed forward/restore plan |

## 10. Secret rotation

The key supplied during development is temporary and must be rotated before the
release candidate.

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

Prefer Railway service references and managed rotation for PostgreSQL. Both API
and worker consume `DATABASE_URL`, so coordinate and verify them sequentially.
Never copy the connection string into documentation.

## 11. Current operational and TestFlight gaps

Before calling the service release-ready:

- deploy the current commit and close the known regional/Ask production smoke
  failures
- confirm the worker service identity, role, `/ready` state, and advancing source
  data
- add per-source last-success/failure/lag/record-count observability and alerts
- approve the default 72-hour raw-payload policy and define retention for HTTP
  logs, questions, detected events, and generated explanations
- keep one API process or introduce shared durable rate/budget controls
- complete backup/restore validation
- rotate the temporary OpenRouter key
- obtain Apple Team/signing access, final bundle registration, App Store Connect
  record/testers, and owner approval of the hosted privacy/support pages (or
  replacement URLs/contact details)
- finish App Privacy answers, signed physical-device QA, archive/upload, and
  processed TestFlight install

The privacy manifest and `/ready` endpoint are necessary release inputs, but
neither substitutes for accurate privacy disclosures or end-to-end production
verification.
