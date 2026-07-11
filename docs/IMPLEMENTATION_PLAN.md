# 50Hz implementation plan

## Product objective

Build a cinematic, game-like view of Britain's electricity system that is immediately understandable to curious people and remains trustworthy and useful to professionals.

The opening experience is a zoomed-out, abstract Britain map. It shows the character and magnitude of generation, demand and interconnector flows without pretending to display literal transmission paths. Professional detail, evidence and methodology are always one tap away.

The guiding product rule is:

> Calculations decide what happened. The LLM explains validated facts.

## Confirmed decisions

- Product name: **50Hz**
- Initial audience: curious public, with progressive professional detail
- Distribution: free TestFlight followed by the iOS App Store
- iOS target: iOS 18+
- iOS stack: SwiftUI, Observation, structured concurrency and Swift Charts
- Map: authored abstract Britain visualization, not a street-level MapKit experience
- Default regional context: Central London; the national map remains visible
- Backend: FastAPI on Railway with a separate ingestion worker
- Database: Railway PostgreSQL
- LLM gateway: OpenRouter, called only by the backend
- Initial model: `openai/gpt-5.4-mini`
- Accounts: none in the first version
- User preferences and game progress: stored locally on device
- Core product remains usable when OpenRouter is unavailable

## UI reference integration

The Claude concept archive supplied on 11 July 2026 is adopted as the visual north star. Its detailed native translation, design tokens, screen states and data-trust corrections are recorded in [UI_DIRECTION.md](UI_DIRECTION.md).

Key decisions added to this plan:

- Four primary tabs: Live, Today, Mine and Log.
- Ask the Grid is a contextual analysis inspector rather than a fifth tab or generic chatbot.
- The timeline is the persistent anchor of the Live screen.
- Cyan denotes observed/live energy, while violet plus texture distinguishes forecast/replay.
- Loading, stale, offline and critical-event states belong in the first Live milestone.
- Fuel selection focuses the map and adds a compact history/detail mode.
- Red is reserved for validated material system events.
- The concept's approximate Britain canvas will be re-authored as a native stylized vector.
- Prototype copy and values are illustrative; source cadence, causal claims, regional estimates and forecast availability must come from the data contracts.

## System architecture

```text
Elexon + NESO + weather
          |
          v
50hz-worker on Railway
  adapters -> normalization -> event rules
          |
          v
Railway PostgreSQL
 raw provenance + normalized observations
 snapshots + events + cached explanations
          |
          v
50hz-api on Railway
 versioned REST API + grounded OpenRouter tools
          |
          v
SwiftUI app
 cache + timeline sampler + abstract grid renderer
```

Railway resources:

1. `50hz-api`: public FastAPI service and OpenRouter integration.
2. `50hz-worker`: continuously polls sources and builds snapshots/events. It uses the same image as the API with a different start command.
3. `Postgres`: shared durable store.

Redis is unnecessary initially. PostgreSQL advisory locks, deterministic identifiers and idempotent upserts are sufficient.

## Delivery strategy

We will build vertical slices rather than completing the entire backend before starting iOS. The first slice is:

```text
Elexon generation + demand + frequency
    -> normalized PostgreSQL observations
    -> current grid snapshot API
    -> animated SwiftUI Britain map
```

Every later feature attaches to this proven route.

## Phase 0 — contracts, fixtures and time foundation

This phase lands first because it allows safe parallel development.

### Backend

- Add SQLAlchemy, Alembic and async PostgreSQL access.
- Define versioned Pydantic domain and API schemas.
- Define a source-adapter protocol independent of upstream field names.
- Store representative upstream responses as sanitized test fixtures.
- Establish the internal interconnector convention: positive means import into Britain; negative means export.
- Model observed, published, retrieved and forecast-issued timestamps separately.
- Store operational timestamps in UTC and display them using `Europe/London`.
- Implement settlement-period handling for 46-, 48- and 50-period days.
- Add DST fixtures for both clock changes.

### iOS

- Create the Xcode project and app targets.
- Establish design tokens, navigation and feature folders.
- Define `Codable`, `Sendable` domain models and separate API DTOs.
- Add a deterministic app clock so live, scrubbed and replay modes are testable.
- Check in matching snapshot and timeline JSON fixtures.
- Land the shared design tokens and native component contracts from `UI_DIRECTION.md`.
- Establish the four-tab shell with a contextual Ask inspector route.
- Build explicit loading, stale, offline and critical state fixtures.

### Acceptance gate

- The backend serializes a fixture snapshot.
- The iOS app decodes that same fixture.
- DST and import/export contract tests pass.
- Each fact contains source, timing, freshness and observed/derived/forecast classification.
- The app can render every reference state from fixtures without fabricated data.

## Phase 1 — live national grid

### Source adapters

- Elexon `FUELINST`: generation mix.
- Elexon demand outturn/`INDO`: national demand.
- Elexon `FREQ`: grid frequency.
- Elexon interconnector flows.

Initial collection cadence:

- Frequency: every 60 seconds with a ten-minute overlap.
- Generation, demand and interconnectors: every two minutes.
- Hourly reconciliation: revisit the preceding 48 hours.

Polling windows overlap deliberately. Source-derived unique keys and upserts prevent duplicates and make restarts safe.

### Database

Initial tables:

- `ingestion_runs`
- `raw_payloads`
- `generation_observations`
- `demand_observations`
- `frequency_observations`
- `interconnector_observations`
- `grid_snapshots`
- `assets`
- `source_metadata`

Raw source payloads are retained with checksums so parsing can be reproduced and every visible fact can be audited.

### API

- `GET /health`
- `GET /v1/meta`
- `GET /v1/grid/current`
- `GET /v1/grid/timeline?from=&to=&resolution=`
- `GET /v1/sources`

The current endpoint reads a compact denormalized snapshot rather than performing a large live join. Responses will support ETags, compression, caching and explicit freshness.

### iOS playback prototype

- Load cached data immediately, then refresh.
- Poll the backend at a source-appropriate interval.
- Implement live, scrubbed, replay and Resume Live modes.
- Interpolate continuous MW values between valid samples.
- Keep warnings, outages and other discrete facts stepwise.
- Never interpolate across a material data gap.
- Clearly mark the boundary between observed and forecast data.
- Preserve the concept's cyan observed treatment and violet-plus-texture forecast treatment.
- Replace forecast frequency with metrics that have defensible forecast sources.

### Acceptance gate

- A real Elexon reading travels through the worker, database and API into the simulator.
- Restarting the worker does not duplicate observations.
- The app launches offline from a cached snapshot and labels it stale.
- Scrubbing is deterministic and does not invent precision across gaps.

## Phase 2 — the living Britain map

The map is a custom data visualization using authored normalized coordinates.

Layer order:

1. Dark gradient/noise field.
2. Britain silhouette, topographic texture and coast glow.
3. Regional demand field.
4. Generation nodes.
5. Interconnector paths.
6. Batched directional particles.
7. Event and outage pulses.
8. Labels, selection and freshness state.

Rendering starts with SwiftUI `Canvas` and `TimelineView`. Particles are drawn in batches, not as individual SwiftUI views. Their positions are deterministic from connection ID, seed and playback time so replay is reproducible.

Performance progression:

- Start with capped Canvas particles at 60 fps.
- Reduce to 30 fps and a smaller particle budget in Low Power Mode.
- Cache paths/symbols and avoid allocations in the frame loop.
- Profile on the oldest supported physical device.
- Move only the particle/field layer to Metal if Instruments demonstrates that Canvas misses the budget.

Accessibility is part of the renderer milestone:

- Provide a semantic list alternative to the visual map.
- VoiceOver navigates meaningful assets and events, never particles.
- Reduce Motion replaces continuous particles with directional strokes.
- Shape, texture and labels supplement colour.
- Compact visual chips retain 44-point native hit areas and semantic Dynamic Type styles.

### Acceptance gate

- Generation mix, interconnector direction, demand, frequency pulse and selected time are visibly driven by fixtures and then live data.
- The scene remains responsive during scrubbing.
- Reduce Motion and VoiceOver alternatives are complete.
- The map explicitly identifies flows as illustrative rather than literal transmission routes.
- The Live screen matches the hierarchy and interaction states documented in `UI_DIRECTION.md`.
- Loading, stale, offline and critical-event states pass the same accessibility review as the live state.

## Phase 3 — carbon, forecasts and personal context

### Backend

- NESO national carbon actual and forecast adapter.
- NESO regional carbon and postcode-to-region lookup.
- Elexon wind and demand forecasts.
- Forecast issue/version retention rather than destructive overwrite.
- Cleanest upcoming charging-window calculation.
- Default London regional context.

Additional endpoints:

- `GET /v1/regions/{postcode}`
- `GET /v1/charging-window`

### iOS

- Today view with observed events and forecast moments.
- My Electricity with postcode, regional comparison and clean charging window.
- Progressive professional sheets showing raw MW, issue time, source and methodology.

### Acceptance gate

- The user can move seamlessly from observed history into forecast data.
- Forecast issue time and uncertainty are visible.
- A London default works without requesting location permission.
- Postcode is converted to a region on the server; precise location is not sent to the model.

## Phase 4 — deterministic events and outages

The event engine consists of versioned pure rules operating on normalized time windows. Rules emit candidates; a lifecycle service opens, updates, resolves, supersedes or withdraws events.

Initial event rules:

1. Generation leader changes.
2. Sustained import/export reversals.
3. Renewable-share and low-carbon milestones.
4. Material fuel ramps using both MW floors and historical thresholds.
5. Cleanest/dirtiest observed and forecast windows.
6. Rolling-period records only when sufficient history exists.
7. Reported generating-unit or interconnector unavailability from REMIT.
8. Frequency excursions after feed reliability is proven.
9. Forecast-versus-observed misses for professional users.

Important semantic rule: an observed generation drop is never called an outage. “Outage” requires an authoritative reported notice.

Sources:

- Elexon REMIT revisions and outage profiles.
- Elexon `SYSWARN` operational warnings.

Each event contains deterministic severity, confidence, evidence class, evidence facts, source record IDs and a stable deduplication key. Hysteresis and minimum-duration rules prevent noisy pop-ups.

Additional endpoints:

- `GET /v1/events`
- `GET /v1/events/{event_id}`
- `GET /v1/events/{event_id}/explanation`

### Acceptance gate

- Replaying the same day creates the same event IDs without duplicates.
- Revised and withdrawn REMIT notices update existing events.
- Every event is traceable to evidence.
- No anomaly is mislabeled as an outage.

## Phase 5 — grounded intelligence

### Event explanations

The model receives a small evidence packet containing only validated facts, permitted comparisons, explicit unknowns and server-owned source references.

The response is structured:

- Headline.
- Plain-language explanation.
- Why it matters, when supported.
- Caveat.
- Evidence reference IDs.
- Suggested questions.

Validation rules:

- At least one supplied evidence reference is required.
- Every numerical claim must match an allowed fact.
- The model cannot invent source URLs.
- Causal language is forbidden unless the packet contains an explicitly reported cause.
- Upstream free text is treated as untrusted input.
- Invalid output gets one repair attempt, then a deterministic template fallback.

Explanations are cached by:

```text
event ID + event revision + prompt version + model ID + locale
```

One event revision therefore creates at most one model call shared by all users.

### Ask the Grid

The assistant receives a narrow read-only tool registry, never SQL access:

- Current grid state.
- Metric series and period comparison.
- Active events and event evidence.
- Reported unavailability.
- Interconnector flows.
- Carbon forecast and cleanest window.
- Historical ranking.
- Source metadata.

The native presentation follows the analysis-inspector concept: bounded answer, evidence rows, freshness, qualification, follow-up prompts and links back to the relevant map time/asset.

Limits:

- Maximum four tool rounds.
- Validated/capped time ranges.
- No secret or environment access.
- No unsupported answer when evidence is missing.
- Per-device/IP rate limits and a global daily model budget.
- Circuit breaker for repeated failures or budget exhaustion.
- Deterministic screens remain fully functional without OpenRouter.

For early testing, model usage begins with cached event explanations. Ask the Grid is enabled only after usage accounting and validation are proven.

### Acceptance gate

- Hallucinated figures, URLs and causal claims are rejected in tests.
- Each visible answer shows its as-of time, freshness, evidence and limitations.
- Budget exhaustion produces a useful deterministic fallback.

## Phase 6 — credible game layer

The game rewards observation and understanding, never electricity consumption.

### Grid Log

A local collection of genuinely observed moments. Entries preserve event time, evidence class, source, explanation and later revisions.

### Daily missions

Examples:

- Find today's cleanest half-hour.
- Identify Britain's largest source.
- Inspect the largest interconnector flow.
- Compare the evening peak with yesterday.
- Open the evidence for an active event.

Missions become unavailable, not failed, when required data is stale.

### Predictions

The backend publishes questions with fixed choices, lock time, metric window and versioned resolution rule. Choices remain local for the account-free MVP. Predictions are voided if source coverage is insufficient.

The Operator Streak rewards participation and learning rather than only correct guesses. There are no wagers, leaderboards or claims that one user's action moved the national grid.

Additional endpoints:

- `GET /v1/game/today`
- `GET /v1/predictions`
- `GET /v1/predictions/{id}/result`

### Acceptance gate

- Missions resolve only from deterministic facts.
- Predictions lock and resolve reproducibly.
- Missing data voids rather than guesses a result.
- Revised events remain visible in the Grid Log.

## Phase 7 — TestFlight completion

- WidgetKit current state and clean-window widgets.
- Share cards generated locally from validated structured data.
- Full accessibility and Dynamic Type pass.
- Battery, thermal and memory profiling on physical devices.
- Reconciliation and retention jobs.
- Database indexes and API load testing.
- Source attribution and licence review.
- Privacy copy and App Store disclosures.
- Crash/error/freshness telemetry without invasive tracking.
- TestFlight feedback loop, followed by App Store assets and submission.

## Parallel-agent operating model

Subagents are used for concrete, isolated tasks with distinct file ownership. The main agent owns shared contracts, integration, Railway state, code review and end-to-end verification.

### Wave 1: foundation

- **Database/time agent:** database models, Alembic migrations, repositories, settlement clock and DST tests.
- **Elexon adapter agent:** HTTP client, generation/demand/frequency/interconnector adapters and fixtures.
- **iOS foundation agent:** Xcode project, domain types, deterministic app clock, mock transport and fixture decoding.
- **Main agent:** freezes schemas and fixtures, integrates work, configures Railway worker and verifies the first vertical slice.

### Wave 2: live experience

- **API agent:** snapshot/timeline response models, endpoints, ETags and endpoint tests.
- **Visualization agent:** Britain geometry, Canvas scene, deterministic particles and performance harness.
- **Timeline agent:** sampler, playback state machine, scrubber and gap tests.
- **Main agent:** live feed integration and simulator/physical-device verification.

### Wave 3: intelligence and events

- **REMIT/event agent:** outage ingestion, event rules, lifecycle, replay tests.
- **LLM agent:** OpenRouter client, evidence packets, validators, cache and cost controls.
- **Game agent:** missions, predictions and resolution tests.
- **iOS events agent:** event cards, Ask the Grid UI, evidence drill-down and Grid Log.
- **Main agent:** adversarial evaluation, contract integration and production limits.

Agents do not concurrently edit shared domain/schema files. A contract change is proposed to and landed by the main agent before dependent work proceeds. Each subagent must return tests or fixtures with implementation work.

## How building starts

The first implementation session will do the following:

1. Add the backend package structure, SQLAlchemy, Alembic and test configuration.
2. Land the canonical timestamp, provenance and snapshot schemas.
3. Check in captured Elexon fixtures for generation, demand, frequency and interconnectors.
4. Add settlement/DST and sign-convention tests.
5. Create the iOS Xcode project with matching fixture DTOs, the adopted design tokens, four-tab shell and Live reference states.
6. Start the Railway worker as a second service using the same repository/image.
7. In parallel, implement the first four source adapters and the iOS fixture-driven timeline shell.
8. Integrate one real end-to-end snapshot and verify it from source through the deployed API into the simulator.

The first visible checkpoint is not a collection of disconnected screens. It is one honest, animated Britain map driven by real generation, demand, frequency and interconnector data, with a working 24-hour playback path and explicit freshness.

## Immediate definition of done

The initial build milestone is complete when:

- Railway continuously collects the four live Elexon data families.
- PostgreSQL stores raw and normalized values idempotently.
- `/v1/grid/current` and `/v1/grid/timeline` are deployed and documented.
- The iOS simulator renders the abstract map from the same contract.
- The user can scrub recent history and resume live mode.
- The UI survives stale/missing data without presenting it as live.
- Source and observation times are visible.
- Automated tests cover DST, corrections, gaps and interconnector sign conventions.
- The native Live hierarchy, fuel-selected mode, forecast treatment and failure states conform to `UI_DIRECTION.md`.
