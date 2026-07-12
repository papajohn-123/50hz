# 50Hz product roadmap

- **Status:** product strategy plus implementation snapshot after the full native/backend build
- **Product:** 50Hz — Britain's electricity system, alive
- **Audience:** curious public first, professional trust underneath
- **Commercial position:** free during validation

Implementation snapshot, 12 July 2026:

- M1–M5 are substantially implemented in the current repository: truthful
  metric/freshness contracts, finite Today briefing, Local planner/reminders,
  evidence-resolved Notebook, source/event inspection, exact tables/export,
  history materialization, and forecast-verification foundations.
- The current tree is ahead of the public Railway deployment. “Implemented” in
  this roadmap does not mean “deployed” or “TestFlight verified.”
- The critical path is now release reconciliation: final shared-tree tests,
  repair of the incomplete local Docker/Xcode device-platform installs,
  disposable live-PostgreSQL migration validation, GitHub/Railway
  re-authentication, push/deploy/data jobs, production smoke, Apple signing, and
  physical-device/TestFlight QA.
- Widget/App Group, saved regions, remote event alerts, accounts, and scale-out
  infrastructure remain deferred.

## 1. Executive decision

50Hz should not try to win by displaying the most electricity data. It should
win by making the system understandable, personally useful, and unusually
trustworthy.

The product loop is:

1. **Hook — see it:** the living national map makes the grid tangible.
2. **Understand — explain it:** the headline, timeline, evidence, and Ask turn
   measurements into a truthful story.
3. **Act — use it:** Local tells someone when flexible electricity use is likely
   to be cleaner.
4. **Return — learn it:** Notebook predictions and missions build intuition, then
   resolve against published evidence.
5. **Trust — inspect it:** every important claim can reveal timing, method,
   classification, source, coverage, and revision history.

The recommended product wedge is therefore:

> Open 50Hz to understand Britain's grid now and find a better time for flexible
> electricity use.

The map is the emotional hook. The regional clean-window planner is the everyday
utility. The daily briefing and prediction result create a return loop. Exact
provenance is the professional differentiator.

## 2. Definition of useful

50Hz is useful when all four statements are true:

- A new user can explain the national position within fifteen seconds.
- A household user can identify a defensible cleaner window for a flexible load.
- A returning user can learn whether a prior prediction was correct and why.
- A professional can inspect the source boundary, timestamps, classification,
  coverage, methodology, and revision behind a claim.

The product is not useful merely because it renders live numbers, an attractive
map, or an AI answer.

### Five-second, thirty-second, five-minute layers

| Layer | User question | Product response |
| --- | --- | --- |
| Five seconds | What is happening? | Lower/typical/higher carbon, demand trend, net import/export, freshness |
| Thirty seconds | Why is that true? | Supply breakdown, one-hour movement, source timing, reported event |
| Five minutes | What can I do or inspect? | Timeline, Local planner, event evidence, Ask, Notebook mission, exact data |

Every advanced feature must sit behind progressive disclosure. Professional
depth must not make the five-second layer harder to read.

## 3. Priority audiences and jobs

### 3.1 Curious observer — primary

**Situation:** sees energy news, renewable records, outages, or unusual weather
and wants to understand the grid without prior market knowledge.

**Jobs:**

- Tell me what Britain is using for electricity now.
- Tell me whether Britain is importing or exporting.
- Tell me whether carbon is relatively low or high.
- Show what changed and what may happen next.
- Explain unfamiliar terms without talking down to me.

**Success moment:** “I understand the system well enough to explain it to
someone else.”

### 3.2 Flexible-energy household — primary utility segment

This includes EV owners, home-battery users, heat-pump households, and people
who can shift a dishwasher, washing machine, or dryer.

**Jobs:**

- Show whether my region is cleaner or dirtier than Britain overall.
- Find the cleanest usable window before a deadline.
- Tell me how much difference moving the activity is expected to make.
- Remind me at the chosen time without requiring an account.
- Be honest when the forecast is stale, national-only, or not meaningfully
  different.

**Success moment:** “I know when to run something flexible and understand the
forecast limitation.”

### 3.3 Energy enthusiast, journalist, or educator — secondary

**Jobs:**

- Revisit a moment on the timeline.
- Compare now with yesterday, last week, and a recent typical range.
- Understand a reported unit event and its revisions.
- Share an accurate, source-labelled visual.
- Find exact source and timing detail quickly.

**Success moment:** “I can use the app's output in a conversation or story
without first reverse-engineering its definitions.”

### 3.4 Energy professional — secondary trust segment

50Hz is informational, not an operational or trading system.

**Jobs:**

- Inspect precise values, signs, boundaries, settlement periods, and coverage.
- Separate observations, estimates, forecasts, reports, and derived context.
- Inspect source delivery health independently of fact validity.
- Follow notice revisions and lifecycle state.
- Export a bounded dataset with provenance.

**Success moment:** “The public-facing summary is simplified, but not misleading,
and I can inspect exactly how it was produced.”

## 4. Product principles

1. **Deterministic facts, assisted language.** Code establishes facts and
   relevance; the LLM may explain validated evidence.
2. **Classification is visible.** Observed, estimated, derived, reported, and
   forecast values are never collapsed into one visual truth state.
3. **Freshness follows validity, not a single clock.** A current half-hour carbon
   period can be valid even when its start time is twenty-nine minutes old.
4. **Polling is not fact resolution.** UI and documentation never confuse how
   often 50Hz checks with how often the publisher creates a new fact.
5. **No false precision.** Regional carbon, forecast issue time, coverage, and
   typical error carry their real limitations.
6. **Partial failure preserves value.** An AI, forecast, carbon, event, or
   regional failure does not erase unrelated confirmed facts.
7. **The map is illustrative.** It never implies literal network topology,
   power-flow physics, or household outage coverage.
8. **Every comparison has coverage.** No record, percentile, or “unusual” claim
   is made without a defined window and sufficient observations.
9. **Game mechanics teach.** Predictions and missions improve grid intuition;
   they do not create fake competition or anxiety.
10. **No permission before intent.** Location is not requested. Notification
    permission is requested only after someone taps a reminder action.
11. **No account without a proven account job.** Local participation is enough
    for 1.0.
12. **Professional detail is not a paywall during validation.** It is a local
    presentation preference or inspector layer.

### 4.1 Visual thesis

50Hz should feel like a calm scientific instrument looking across Britain at
night: dark, spatial, alive, and precise. The abstract Britain map remains the
dominant visual plane. It creates wonder, but every label and interaction around
it behaves like serious utility software.

The visual system should preserve:

- Near-black/navy surfaces rather than decorative gradients.
- Cyan for observed/current electricity, violet for forecast/replay, and a
  restrained warm colour only for attention that genuinely matters.
- Large tabular figures, short utility labels, and generous separation between
  the map and supporting evidence.
- Thin dividers and plain layout before containers. A card is used only when the
  whole surface is an interaction, such as an event, mission, or selected
  planner result.
- One dominant idea per screen: national state on Live, the day on Today,
  personal timing on Local, and learning on Notebook.

Avoid turning the app into a dashboard-card mosaic. The map, timeline, and
typography should do most of the compositional work. Shadows, glows, pills, and
status colours are functional signals, not decoration.

### 4.2 Content plan

Each surface has one headline question and a controlled disclosure path:

| Surface | Headline question | First layer | Deeper layer | Primary action |
| --- | --- | --- | --- | --- |
| Live | What is Britain doing now? | Map, status, carbon/demand/net position | Supply, timeline, evidence, sources | Scrub or inspect |
| Today | What matters today? | Now and best window | Changes, outlook, reported events | Open a moment |
| Local | When should I use flexible power? | Region and recommended window | Activity, duration, comparison, uncertainty | Set reminder |
| Notebook | What am I learning? | Prediction and missions | Result evidence and learned concepts | Make/resolve prediction |

Any content that does not answer that screen's question moves behind detail,
into the Info sheet, or out of the product.

### 4.3 Interaction thesis

Motion should make the system feel alive and clarify time, not add spectacle.
The signature interactions are:

1. **Grid breathing:** restrained map particles/flows respond to the selected
   moment and move continuously only while Live is current.
2. **Time travel:** scrubbing the timeline crossfades figures, map intensity,
   and observed/forecast colour as one coordinated transition; Resume Live
   visibly returns the instrument to the present.
3. **Material change:** a genuinely relevant event or prediction result enters
   with one restrained pulse/reveal, then becomes still.

All three interactions need a fully static Reduce Motion alternative. Haptics
are reserved for snapping to now, selecting a prediction, completing a mission,
and scheduling a reminder. Routine refreshes never vibrate.

### 4.4 Cross-app design and copy rules

- Use a single spacing scale and align all numeric columns to a stable grid.
- Use tabular numerals for measurements and countdowns.
- Keep primary type readable in one glance; professional metadata can be dense
  but never at the expense of legibility.
- Use sentence case and utility language. Labels say scope, state, timing, or
  action rather than marketing metaphors.
- Standardise units: `MW`, `GW`, `GWh`, `gCO₂/kWh`, and `Hz`; conversions must
  be deterministic and preserve an inspectable raw value.
- Put units next to the value, not in a distant legend.
- Never use colour alone for observed/forecast, good/bad, live/delayed, or event
  severity.
- Every loading surface has a bounded skeleton or progress state, every empty
  state explains whether there is no data or no event, and every recoverable
  error has a retry action.
- Share output must include the metric scope, as-of time, forecast label where
  relevant, and 50Hz/source attribution. It must not export a beautiful but
  context-free number.
- Preserve scroll position when opening and dismissing inspectors. Deep links
  return to their originating mission or briefing context.
- Destructive/reset actions require confirmation; selection, filters, and
  replay do not.
- Navigation animations use one consistent duration/easing family and complete
  quickly enough that repeat inspection never feels theatrical.

## 5. Explicit non-goals

The following are not part of the first useful public product:

- Household or local distribution-network power-cut reporting.
- A literal transmission-line map or constraint-flow model.
- Operational, trading, safety, or emergency decision support.
- Automated EV, battery, heating, or appliance control.
- Tariff optimisation before carbon-window usefulness is validated.
- User accounts, cloud sync, social feeds, leaderboards, prizes, or monetary
  predictions.
- A generic chatbot or a fifth Ask tab.
- Broad causal claims based on weather or temporal correlation.
- Proprietary ML forecasting before publisher forecasts are verified.
- Watch, iPad, landscape, Live Activities, or continuous background refresh.
- Remote outage notifications before event relevance precision is measured.
- Redis, multiple API replicas, asynchronous exports, or a warehouse before
  measured traffic requires them.

## 6. Current-state audit

The current tree is a credible release candidate, but production still serves an
older baseline. The table distinguishes implemented value from the remaining
release/usefulness gap.

| Surface | Implemented current-tree strength | Remaining gap before wider use |
| --- | --- | --- |
| Live | Distinctive map, supply boundary, per-family fact/delivery state, timeline/fuel replay, exact tables, event/Ask entry points | Final small-screen/AX/VoiceOver/thermal pass and production contract smoke |
| Today | Server-defined finite briefing with maximum-three changes, next moments, and events plus partial/offline states | Validate ranking/copy with real event-heavy production days and testers |
| Local | Explicit region, activity/duration planner, coverage/vintage, start/deadline bounds, outward-only privacy, local reminder lifecycle, and strictly matched national-carbon MAE qualification | Production deploy, physical notification QA and measured usefulness; regional forecast error remains unavailable and must not be inferred |
| Notebook | Exact-lock local choice, real mission destinations, local/unverified completion, learned concepts, immutable evidence resolution/void/correction, and explicit local lock/result-check reminders | Date-rollover/reinstall/physical-device cohort validation; no remote push or result assertion |
| Ask | Context scope, first-use data disclosure, cancellation, grounded citations/qualification, bounded failure | Production latency/cost/helpfulness validation and owner confirmation of provider retention |
| Events | Authoritative reported list/detail/explanation plus immutable revision history; failure-isolated worker persistence for coherent observed-event rules | Deploy/validate relevance precision and decide whether/when resolved derived events become public |
| Pro inspection | Public-safe source status, exact supply/connectors, event deltas, maximum-31-day JSON/CSV export, and national forecast MAE/bias/WAPE review by horizon | Test with professionals and validate exported/verification production values after history jobs |
| Data layer | Immutable corrections, versioned registry, 95%-coverage history materialization, prediction ledger, forecast verification | Live PostgreSQL migration/data-job run, ongoing crons/alerts, enough real samples for eligible verification statistics |

No current-tree feature should be described as production verified until the
release commit is pushed, deployed to both roles, migrated/materialized, and
smoked. The 11 July run proves only the older baseline.

## 7. P0 semantic corrections

These are correctness rules, not copy polish. The current tree implements the
contract and primary native copy; production reconciliation and tester
comprehension remain the acceptance gate.

### 7.1 “Generation mix” versus imports

Imports are currently presented within a generation-style mix. Imports are
electricity entering Britain, not domestic generation. FUELINST is also not a
complete description of all GB generation, including embedded generation and
boundary differences.

Implemented/current rule:

- Rename the public total to **GB supply mix** when imports are included.
- Separately expose domestic transmission-visible generation, gross imports,
  gross exports, net interconnector position, storage generation, and storage
  charging.
- Never say “imports are the largest generation source.” Say “imports are the
  largest displayed supply component.”
- Publish the boundary and exclusions in a tappable definition.
- Explain why displayed supply, demand, and interconnector values may not
  reconcile exactly at one timestamp.

### 7.2 “Balanced” and “tightening”

Electricity supply and demand must be balanced continuously. A broad label such
as “Balanced” can be read as a formal system condition when it is currently an
interpretation of limited measurements.

Recommended public headline:

- Carbon: `Lower carbon`, `Typical carbon`, or `Higher carbon`.
- Demand: `Demand rising`, `Demand steady`, or `Demand falling`.
- Position: `Net importing`, `Near balanced`, or `Net exporting`.

Do not use a formal balancing-state label without a defensible source and
methodology.

### 7.3 “Clean”

“Clean” is concise but absolute. Prefer `Lower carbon` when making a comparison,
and expose the baseline. Before history is sufficient, use a documented fixed
band and say so. After backfill, use a rolling recent percentile only when
coverage passes the threshold.

### 7.4 “Outage”

The app covers reported generation/interconnector availability and system
notices, not household power cuts. Default copy should say `reported
unavailability` or `reported grid event`. “Outage” is allowed only when the
authoritative notice supports that language and the context cannot be mistaken
for a local power cut.

### 7.5 Regional “now” and national forecast

The Local experience can pair a regional current estimate with a national
forecast window. The current UI keeps the geography prominent above the value:

- `Regional now — London estimate`
- `Best GB charging window — national forecast`

Do not imply a regional forecast saving until the backend supplies a compatible
regional forecast and calculates its average/delta.

## 8. Information architecture decision

Keep four tabs and contextual Ask. Do not add a Settings tab.

| Former concept name | Current tree name | Job |
| --- | --- | --- |
| Live | Live | Present state, map, replay, event, source inspector |
| Today | Today | Curated national briefing and outlook |
| Mine | Local | Region, flexible-use planner, reminder |
| Log | Notebook | Prediction, missions, results, learned concepts |

Put methodology, Data & AI, sources, privacy, support, version, reset, and replay
onboarding in a small global Info sheet.

Acceptance criteria:

- A first-time tester can state what each tab does after sixty seconds.
- No primary content is duplicated between Today and Notebook.
- Tab names match screen titles and VoiceOver labels.
- Ask remains scoped to the selected grid state instead of becoming generic
  chat.

## 9. Delivery map

| Milestone | Outcome | Current status | Remaining hard dependency |
| --- | --- | --- | --- |
| M0 | Signed baseline in real hands | Blocked outside code | Xcode iOS 26.4 device platform, Apple access/App Store record, rotated key, pushed/deployed release commit |
| M1 | Truthful and understandable | Implemented in current tree | Production/physical accessibility verification |
| M2 | Useful daily briefing/history | Implemented in current tree | Live PostgreSQL data jobs, production ranking/coverage validation |
| M3 | Personally actionable Local planner | Implemented in current tree | Production route deploy and physical reminder/usefulness QA |
| M4 | Completed learning/prediction loop | Implemented in current tree | Production evidence resolution and date/reinstall device QA |
| M5 | Professional inspection/export | Implemented in current tree | Production history/source/event data validation with professionals |
| M6 | Widget and broader notifications | Local clean-window reminder only | Apple App Group/signing for widget; result/remote alert product work |
| M7 | Scale only where measured | Deferred | Observed traffic/SLO trigger |

The unusual ordering reflects parallel implementation: M1–M5 code is now ahead
of M0 release access. Internal TestFlight is still the next product milestone;
do not add more broad features while push/deploy/signing/physical QA remains the
critical path.

### 9.1 Platform and service decision

Keep the current stack for the complete 1.0 roadmap:

| Concern | Decision | Reason |
| --- | --- | --- |
| iOS | Native SwiftUI/iOS 18+ | Existing app, best system accessibility/widgets/notifications |
| API | FastAPI on Railway | Implemented; older baseline deployed, current tree pending reconciliation |
| Ingestion | Separate Railway worker from the same image | Independent polling lifecycle without a second codebase |
| Database | Railway PostgreSQL | Existing normalized history and migrations; no account/realtime need |
| LLM | OpenRouter from API only | One bounded server-side gateway with spend control |
| Public grid data | Elexon Insights and NESO | Authoritative public sources; currently no API keys required |
| Reminders | Local iOS notifications | No account, APNs service, or remote token storage required |
| Release | App Store Connect/TestFlight | Required distribution path |

Do not add Supabase now. It would split database ownership and operational
responsibility without solving a current product problem. Reconsider it only if
accounts, cross-device sync, or a specific Supabase capability becomes a proven
requirement; even then, compare migration cost with adding those capabilities to
the existing Railway/PostgreSQL stack.

The only private application credential currently required is the server-side
OpenRouter key. TestFlight additionally requires Apple Developer/App Store
Connect authority, signing certificates/profiles managed through Xcode, and the
registered bundle identifier. Elexon and NESO feeds used here are public and do
not currently require an app key.

### 9.2 Parallel build lanes

Work proceeds in narrow, mergeable vertical slices. Up to three subagents can
run beside the integrating agent:

| Lane | Typical ownership | Deliverable |
| --- | --- | --- |
| Data/backend | Schema, adapters, deterministic contracts, migrations, tests | Versioned response with fixtures and provenance |
| Native/product | Swift models, state handling, screens, accessibility, UI tests | Complete user path against fixtures and production |
| Verification/release | Contract audit, failure matrix, simulator/device QA, docs | Independent evidence that the slice works and is releasable |
| Integration | Scope, cross-lane contract, conflict resolution, commits, deploy smoke | One reviewed and reversible vertical increment |

Agents should not edit the same files concurrently. The integration contract is
written first, backend and fixture work can then run beside native composition,
and verification starts as soon as the first stable seam exists. Each slice is
committed separately, pushed, and production-smoked when it changes deployed
behavior.

### 9.3 Working increments

Every increment must be small enough to understand in review and complete all
of the following before the next dependent increment:

1. State the user question and the incorrect interpretation being prevented.
2. Define/add the server contract and classification/freshness semantics.
3. Add deterministic fixtures for success, partial, stale, empty, malformed,
   and offline/cached states.
4. Implement the native state and progressive disclosure path.
5. Add backend, decoding, state-model, and critical interaction tests.
6. Run formatting/static checks, backend tests, simulator tests/build, and
   accessibility checks proportional to the change.
7. Deploy API/worker changes in dependency order and smoke the exact public
   response.
8. Record the tested commit, update docs, commit, and push.

### 9.4 Indicative execution sequence

Blocks 1–6 and the professional inspector/export part of Block 8 have now been
implemented in parallel in the current tree. The active sequence is:

1. Finish and independently verify the remaining forecast/event-runtime and
   Local-bounds slices; reconcile docs and final test totals.
2. Validate the complete migration chain against disposable live PostgreSQL.
3. Re-authenticate GitHub/Railway, push, deploy API then worker, run bounded data
   jobs/crons, and smoke the current contract.
4. Complete Block 0 Apple signing, physical-device, and internal TestFlight.
5. Run Block 7 cohort/accessibility/performance hardening from real feedback.
6. Reassess widget/App Group and saved-region work only after the first cohort.

## 10. M0 — signed internal TestFlight baseline

### Outcome

Put the current working product into the hands of a small, diverse internal
group while preserving a clean baseline for comparison.

### Engineering work

- Confirm Apple team `VKMJPS7WP4` is the intended paid Developer Program team.
- Confirm/register `com.papajohn.50hz` and create the App Store Connect record.
- Rotate the temporary OpenRouter key, keep it API-only, and confirm the final
  spend cap and eligible data-retention routing.
- Produce and validate a signed archive.
- Install on at least one physical small-screen and one current large-screen
  iPhone if available.
- Run offline, background/foreground, slow network, dark appearance,
  accessibility, battery, and thermal checks.
- Invite an initial group containing novices, an EV/flexible-energy user, an
  energy enthusiast, and a professional.
- Use structured interviews and a short tester form; do not add an analytics SDK
  merely to measure the first cohort.

### M0 acceptance criteria

- Processed TestFlight build installs and launches against production.
- No secret or development endpoint is present in the bundle.
- Live, Today, Local, Notebook, event detail/explanation, Ask, privacy, and
  support paths work on a physical device.
- Core browsing survives OpenRouter failure.
- No critical VoiceOver, Dynamic Type, Reduce Motion, offline, battery, or crash
  blocker is found.
- The tested commit, backend deployments, device, OS, and pass/fail result are
  recorded.

## 11. M1 — truthful and understandable

**Current status:** implemented in the current tree, including metric registry,
partial supply semantics, per-family status, Data Details, onboarding, and dark
launch screen. Production and full physical accessibility verification remain.

### 11.1 Backend truth contract

Create a versioned metric registry containing:

- Metric ID and public name.
- Unit.
- Geographic/system boundary.
- Observed, estimated, derived, reported, or forecast classification.
- Fact resolution.
- Expected publication lag.
- Methodology version.
- Known exclusions.
- Compatible forecast/outturn pair.

Add per-family status for generation, demand, frequency, interconnectors,
carbon, forecast, REMIT, and SYSWARN.

Each status must distinguish:

- `deliveryState`: whether the worker is successfully receiving the source.
- `factState`: whether the fact validly covers the requested time.

Implemented additive endpoint/fields:

- `GET /v1/metadata/metrics`
- Public-safe `GET /v1/sources/status`
- `sourceStatuses[]`, `coverage`, `publishedAt`, `validTo`,
  `methodologyVersion`, and `revisionID`/watermark in relevant public responses.

### 11.2 Initial freshness policy to validate

These are starting hypotheses and must be tested against real publisher
behavior. They belong in versioned server configuration, not Swift.

| Family | Source fact behavior | Worker poll | Live hypothesis | Delayed | Stale |
| --- | --- | ---: | --- | --- | --- |
| Frequency | High-frequency observations delivered in files | 1 min | Observation/success ≤3 min | ≤10 min | >10 min |
| Generation | 5-minute fact | 2 min | Observation ≤10 min, success ≤5 min | ≤15 min | >15 min |
| Interconnectors | 5-minute fact | 2 min | Same as generation | Same | Same |
| Demand | Half-hour fact published after interval | 5 min | Latest compatible interval, age ≤45 min | ≤65 min | >65 min |
| Carbon current | Half-hour validity period | 5 min | Point covers now, success ≤10 min | Previous period briefly | No valid coverage |
| Carbon forecast | Half-hour points/48h horizon | 30 min | Continuous coverage, issue age ≤60 min | Partial/≤2h | Otherwise |
| NDF | Day/day-ahead demand forecast | 15 min | Target horizon covered in publication window | Late/limited gap | Missing target |
| WINDFOR | Irregular forecast batches | 30 min | Latest expected issue and coverage | One issue late | No usable horizon |
| REMIT | Event-driven | 2 min | Last success ≤5 min | ≤15 min | >15 min |
| SYSWARN | Event-driven | 5 min | Last success ≤10 min | ≤20 min | >20 min |

### 11.3 Native comprehension work

- Replace the unexplained `LIVE · Xm` capsule with `Current`, `Delayed`, or
  `Offline` and a separate `Observed Xm ago`/period label.
- Make the status capsule open a Data Details sheet with per-family source,
  observed/valid/published/retrieved time, cadence, classification, and stale
  reason.
- Add one lightweight, skippable first-run sheet over Live:
  - the map is illustrative and uses public grid data;
  - cyan is observed/current, violet is forecast;
  - Ask sends text to the 50Hz backend/model;
  - no account or location permission is required.
- Add at most two one-time coach marks: timeline scrub and Ask.
- Add contextual metric definitions and a replayable Help entry.
- Correct supply, carbon, demand, import/export, regional, and event language
  described in P0.
- Replace generic one-error loading behavior with partial states.

### 11.4 Live layout and accessibility

- Give the fixed timeline/tab dock an explicit reserved safe-area height; no
  source, generation, event, or Ask content may sit beneath it.
- Audit the timeline accessibility value so it never announces `nan`; it must
  announce date/time, observed/forecast, and adjustable increments.
- Move VoiceOver focus correctly after Today/Notebook deep links.
- Keep every control at least 44 by 44 points, including Share.
- Raise tertiary body/caption contrast to a verified 4.5:1 on every surface.
- Reflow metrics, prediction controls, Local facts, and event cards through AX5.
- Expose map summary and event action as separate accessibility elements.
- Stop particles, pulses, numeric transitions, and nonessential movement under
  Reduce Motion.
- Test 4.7-, 6.1-, and 6.9-inch iPhones, Bold Text, Increase Contrast, Reduce
  Transparency, VoiceOver, and AX5.
- Reassess the icon at 29/40/60 points; simplify details that look like literal
  network topology or become noise.
- Add a dark launch screen with no white flash.

### M1 acceptance criteria

- A user can correctly identify current versus forecast and import versus export
  without help.
- Every displayed metric can open a definition and provenance path.
- With one source removed, only affected facts become delayed/stale; unrelated
  facts remain usable.
- No valid current carbon period becomes stale merely because its start time is
  old.
- Supply semantics do not call imports domestic generation.
- Accessibility Inspector has no serious issue and the full manual VoiceOver
  path is complete.
- No important text clips or overlaps at AX5 on the smallest supported device.

## 12. M2 — deterministic daily briefing

**Current status:** the deterministic briefing, native Today surface, immutable
event lifecycle ledger, history foundations/backfill/materialization, coverage
rules, and bounded post-success observed-event worker action are implemented.
The production database jobs, deploy, and relevance smoke remain.

### Outcome

Today becomes a bounded editorial product generated from facts, not a client-side
dump of every active notice.

### 12.1 History foundation

Backfill at least ninety days of compatible half-hour history for:

- Generation/supply components.
- INDO demand.
- Signed interconnector flows.
- National carbon estimate/outturn.
- Forecast vintages where obtainable.
- REMIT and SYSWARN revisions.

Retain high-frequency frequency/generation detail for a shorter operational
window and create bounded aggregates.

Add coverage-aware structures such as:

- `metric_definitions`
- `observation_coverage_daily`
- `metric_aggregates`
- `comparison_baselines`

Initial comparisons:

- Previous settlement period.
- Same period yesterday and seven days earlier.
- Rolling 28-day median/interquartile range.
- Rolling percentile only with adequate history.

Daily comparisons require at least 95% expected coverage. Missing facts produce
`insufficientData`, never zero or an invented record. All aggregates must pass
46/48/50-period DST tests.

### 12.2 Event relevance and lifecycle

Persist event states:

- Open.
- Updated.
- Resolved.
- Superseded.
- Withdrawn.

Retain revision deltas for capacity, start/end, status, cause, evidence checksum,
and material update reason.

Rank events deterministically using:

- Authoritative warning versus ordinary notice.
- Unavailable MW and percentage of normal capacity where known.
- Duration and current/near-future relevance.
- Planned/unplanned classification when reported.
- Novelty and material revision.
- System-warning status.

Do not attribute a simultaneous movement in gas, imports, demand, or frequency
to an event. It may appear as separate “what changed around the same time”
context.

### 12.3 Briefing contract

The implemented deterministic `GET /v1/briefing/today` contract contains:

- `now`: concise current position and evidence.
- `changes`: maximum three meaningful observed changes.
- `next`: maximum three forecast moments.
- `reportedEvents`: maximum three relevant events plus total count.
- `bestWindow`: cleanest supported forecast period.
- Coverage, source statuses, comparison windows, revision watermark, and ETag.

The LLM may rewrite an already-selected briefing, but deterministic copy must
always exist and the model cannot select the facts.

### 12.4 Today UI

Use sections:

1. **Now** — one sentence and three meaningful values.
2. **Best window** — actionable forecast with geography and issue time.
3. **What changed** — no more than three evidence-backed movements.
4. **Coming next** — no more than three forecast moments.
5. **Reported events** — maximum three, then See all.

Requirements:

- If timeline fails but snapshot works, show Now and a bounded forecast error.
- If forecast is unavailable, show confirmed observations rather than an endless
  loading state.
- All times use Europe/London policy; cross-day values show weekday/date.
- Group/deduplicate similar unit notices after asset identity is reliable.
- Do not repeat Today's event list in Notebook.

### M2 acceptance criteria

- A user can consume the primary briefing in under one minute.
- Event-heavy fixtures never create an unbounded main screen.
- No duplicate unit/station event is shown as separate national incidents after
  grouping is enabled.
- Every comparison carries its period and coverage.
- Empty, partial, offline, observed-only, and event-heavy fixtures all produce a
  useful finite screen.
- Typical current/history API p95 is below 500 ms and the native 48-hour timeline
  p95 below one second at TestFlight load.

## 13. M3 — Local flexible-use planner

**Current status:** implemented in the current tree, including activity presets,
custom duration, optional earliest/deadline bounds, coverage/vintage detail,
outward-only transmission, schedule/update/cancel for an exact local
notification, and eligible exact-match national-carbon MAE qualification.
Production and physical-device notification QA remain; Local never infers
regional error from the national review.

### Outcome

Local becomes the everyday utility: choose an activity and get a defensible
cleaner window for the selected region or clearly learn that there is no
meaningful benefit.

### 13.1 Local IA

- Keep the implemented Local name and explicit region editing.
- Put the region selector directly below the title; do not bury editing below
  the results.
- Explain once that London is the default, not a detected location.
- Accept a full or outward postcode, validate locally, strip a valid inward
  suffix before transmission, and display only the outward code after use.
- Provide `Use Central London` and, later, at most two or three local saved
  regions.
- Preserve the last confirmed regional result offline with a prominent age.

### 13.2 Planner inputs

- Activity: EV, dishwasher, washer, dryer, home battery, heat pump, or custom.
- Required duration: half-hour increments.
- Earliest start.
- Latest finish.
- Region.
- Interruptible or continuous, only if the algorithm supports it.

### 13.3 Planner output

- Recommended start/end.
- Forecast geography and issue time.
- Expected average intensity.
- Difference from starting now, only with compatible values.
- Whether the difference is meaningful.
- Coverage/gaps and typical recent forecast error when available.
- `Remind me` using a local notification.

Prefer a compatible regional forecast. If only national forecast is available,
say `Best GB window`; do not describe it as a London forecast.

Implemented backend contract:

`GET /v1/regions/{postcode}/windows?durationMinutes=&earliest=&latest=&continuous=`

The algorithm must be deterministic, bounded, gap-aware, and versioned.

### 13.4 Local reminder

- Ask for notification permission only after `Remind me` is tapped.
- Schedule locally; no account or APNs backend is needed.
- Include activity, date/time, region/GB scope, and forecast nature.
- Reschedule or warn when a materially changed forecast is seen on refresh.
- Provide a clear denied-permission path to system Settings.

### M3 acceptance criteria

- Region edit is discoverable without scrolling.
- Full postcodes never enter the URL, backend logs, UI after submission, or
  analytics.
- Regional-now and national-forecast values cannot be mistaken for one
  geography.
- Missing forecast intervals invalidate a window rather than count as zero.
- Moving an activity displays a saving/delta only when calculations are
  compatible and coverage is adequate.
- Local reminders work without an account and identify the forecast issue time.

## 14. M4 — complete Notebook and prediction loop

**Current status:** implemented in the current tree. The backend persists an
immutable evidence result/void/correction independently of the user's choice;
the choice, completion, and learned state remain local. Production resolution
and physical date/reinstall QA remain. Device-local reminders are available 15
minutes before lock and five minutes after the evidence window; the second only
asks the user to check for a published result.

### Outcome

Notebook teaches grid intuition and gives a reason to return. It does not pretend
local checkmarks are a competitive game.

### 14.1 Notebook IA

- Keep the implemented Notebook name and remove any residual Log terminology.
- Prediction at top.
- Three bounded missions.
- Recent prediction result and explanation.
- Learned concepts/history.
- Remove `Moments in view`, which duplicates Today.
- Hide `0 day streak`; use `Start today` until participation exists.

### 14.2 Mission semantics

Current mission rows must not use chevrons and immediately toggle completion.

Required flow:

1. Tap the mission.
2. Deep-link to the relevant Live/Today/Local/event context.
3. Infer completion when the required action is observable, or expose a distinct
   `Mark done` checkbox after return.
4. Label manual completion as local/unverified.

Unavailable missions state why and cannot complete.

Mission examples:

- Identify the largest displayed supply component.
- Determine net importing/exporting.
- Find the cleanest forecast window.
- Open evidence for a reported event.
- Find the observed/forecast boundary.
- Compare Local and national carbon.

Each completed mission can reveal one short deterministic learning card.

### 14.3 Prediction resolution

The immutable daily outcome contract contains:

- Prediction ID/date.
- Choices.
- Exact lock time.
- Evidence start/end.
- Rule version.
- Observed result and value.
- Source IDs/times.
- Coverage.
- Resolution state: correct, incorrect, or void.

Implemented endpoint:

`GET /v1/game/{date}/resolution`

Rules:

- Resolve only from compatible published observations.
- Insufficient or delayed evidence produces `void`, never a guess.
- A corrected publisher value creates an explicit corrected result.
- Store choice/result/history locally for 1.0.
- The LLM may explain the result after deterministic resolution.

### 14.4 Ethical retention design

- Reward days explored, not compulsive opens.
- No punitive red badges or “you lost your streak” copy.
- Allow recovery days if streaks become meaningful.
- No prizes, public ranks, or competitive integrity claims.
- Prediction countdowns reflect real lock time only.

### M4 acceptance criteria

- Every mission has a functional destination and explicit completion state.
- VoiceOver announces mission availability and completion.
- Prediction disables at the exact backend lock time while preserving choice.
- The next result is correct, incorrect, or void from published evidence.
- Date rollover, reinstall, offline reuse, corrected evidence, and insufficient
  coverage are tested.
- Notebook never implies server scoring, global competition, or prizes.

## 15. Grounded intelligence roadmap

Ask remains a contextual inspector, not the centre of navigation.

### P0/P1 improvements

- Show scope at top: `Live now`, selected date/time, and optional region.
- On first use, state that question text goes to the 50Hz API and OpenRouter.
- Structure every answer as:
  1. Direct answer.
  2. Evidence.
  3. Qualification.
  4. Human-readable citations.
  5. Suggested next questions.
- Let metrics, forecast moments, and events provide `Ask about this` actions.
- Preserve deterministic answers for sign/direction and other exact simple
  questions.
- Show as-of/freshness even when the wording is model-generated.
- Turn opaque IDs into publisher/dataset/time/source links; retain IDs only in
  technical detail.
- Make cancellation, rate limit, budget exhaustion, model failure, validation
  failure, and fallback explicit without blocking core data.

### Validation requirements

- Every number validates against gathered evidence with unit awareness.
- Percent/MW/GW and import/export sign cannot be swapped.
- Unsupported causality is rejected.
- A generation movement cannot become an outage claim.
- Model-created source IDs are impossible.
- Forecasts cannot be described as observations.
- Regional flow precision cannot be invented.
- Prompt, model, provider, evidence checksum, validation version, locale, and
  fallback state are auditable.

### Privacy/cost

- Do not persist Ask bodies or question history without an explicit product and
  retention decision.
- Log latency, status, validation result, cache hit, token/cost estimate, and
  route without question text.
- Keep an external account/project spend cap as the hard boundary.
- Make budget counters durable before running multiple API replicas.

## 16. M5 — professional inspection and export

**Current status:** implemented in the current tree: public-safe source status,
exact supply/connector tables, reported revision UI, and bounded protected
JSON/CSV export. Production history/source data validation with professionals
remains.

Professional detail should be an inspector layer or local preference, not a
different app and not a paywall during validation.

### 16.1 Data inspector

Every major value can reveal:

- Exact MW/signed-flow value.
- Raw and normalized code.
- Source record ID.
- Observed, published, retrieved, valid, and issued times.
- Settlement date/period.
- Classification.
- Metric boundary and exclusions.
- Methodology version.
- Coverage and gaps.
- Forecast vintage and measured typical error.

### 16.2 Source health

Provide a public-safe source status view with:

- Dataset and publisher.
- Expected fact resolution and publication behavior.
- Worker poll cadence.
- Last success.
- Current fact validity.
- Delivery state.
- Lag.
- Coverage.
- Attribution/licence.

Do not expose raw errors, infrastructure identifiers, or internal secrets.

### 16.3 Event revision history

Event detail should show:

- Current lifecycle state.
- First publication and latest revision.
- Capacity/start/end/cause changes.
- Withdrawn/superseded state.
- Evidence facts and source links.
- Reported cause separated from simultaneous grid context.

### 16.4 Exact tables and export

The current native inspector includes sortable exact supply and interconnector
tables plus source health and event revision views. Broader filtering remains a
post-cohort refinement.

The implemented export is bounded synchronous CSV/JSON:

- Maximum 31 days.
- Metric allow-list.
- Fixed resolutions.
- Maximum row count/response size.
- Stable columns and UTF-8.
- Classification, timestamps, coverage, source IDs, and methodology version.
- Separate rate limit.

Implemented endpoints:

- `GET /v1/export?metric=&from=&to=&resolution=&format=`
- `GET /v1/metadata/export-schema`

### M5 acceptance criteria

- Exported values round-trip to API values within documented aggregation rules.
- Every export carries provenance and coverage.
- Oversized exports fail with a safe 413/422 without degrading Live.
- Professional detail remains usable with VoiceOver and Dynamic Type.
- No private infrastructure identifier or secret appears in an export.

## 17. Forecast verification

**Current status:** migration `20260712_0009`, a bounded/resumable CLI, immutable
pair/result storage, public endpoint, cache/rate policy, and tests are present in
the current tree. Native Data Details provides a protected-cache national review
and Local can qualify a compatible national-carbon plan without blocking it.
No Railway migration/job/route smoke or eligible production statistics have
been verified yet.

Do not display invented confidence percentages. Build measured quality by
matching compatible forecast vintages to outturns.

Initial pairs:

- NDF to the compatible national-demand outturn.
- Wind forecast to its documented wind outturn boundary.
- National carbon forecast to compatible national carbon estimate/outturn.

Do not call regional forecast error “accuracy” without a compatible regional
outturn.

Calculate by horizon bucket:

- 0–3 hours.
- 3–12 hours.
- 12–24 hours.
- 24–48 hours.

Retain MAE, bias, safe-denominator WAPE, sample count, coverage, verification
window, issue-time basis, and methodology version.

Only show `typical recent error` when there are at least 100 verified points and
at least 90% required coverage. Forecast revisions never overwrite earlier
vintages.

## 18. M6 — widget and notifications

**Current status:** Local clean-window reminders plus Notebook lock and
result-check reminders are implemented on-device after an explicit permission
action. Notification taps route to their real tab. Widget/App Group and all
remote notifications remain deferred.

### 18.1 Widget before remote push

Small widget:

- Carbon estimate.
- Net import/export.
- Updated time and state.

Medium widget:

- Leading supply component.
- Demand.
- Carbon.
- Net imports.
- Next supported clean window.

Requirements:

- Explicit updated/as-of time; never an unqualified `LIVE` claim.
- Stale/offline labeling.
- Deep links to Live or Local.
- Accessible/tinted rendering.
- Shared sanitized cache through an App Group.
- Best-effort refresh consistent with iOS scheduling; no minute-by-minute claim.

### 18.2 Contextual notifications

Implemented local reminders:

- User-selected clean window.
- Prediction lock/result reminder.

The lock reminder fires 15 minutes before the backend lock. After a local
choice, the optional result-check reminder fires five minutes after the evidence
window closes; it says evidence may still be pending and does not claim a
result. Refresh never requests notification permission. Both reminder types can
be cancelled, expose a denied-permission path to Settings, and deep-link to
Local or Notebook.

Remote event alerts are deferred until event persistence, relevance precision,
APNs token lifecycle, subscriptions, dedupe, quiet hours, retention, and privacy
review exist.

No notification should turn routine planned unavailability into alarmist copy.

## 19. Reliability, security, and operations

### Before wider testing

- Rotate the temporary OpenRouter key and verify selected routing/retention
  eligibility.
- Keep secrets on the API role only.
- Split database privileges into API read-oriented, worker write, and
  migration/admin roles.
- Confirm Railway/PostgreSQL backups and exercise restoration to a disposable
  database.
- Keep the committed Python lock files current and add reviewed vulnerability
  update automation.
- Keep the existing CI gitleaks scan, backend/migration/image gates, simulator
  XCTest, unsigned archive compile, and Swift production-contract tests.
- Keep `/v1/sources/status` public-safe: no raw error, internal identifier,
  credential, or infrastructure detail.
- Preserve application access logging that omits bodies, raw/query paths,
  headers, client addresses, and exceptions; separately verify/redact Railway
  platform logs and unsafe upstream text.
- Define retention for HTTP logs, normalized facts, event revisions,
  explanations, and operational diagnostics.

### Observability

Measure without storing user questions or postcode paths:

- Route count, latency, status, response size, and request ID.
- DB pool and slow queries.
- Per-source attempt/success/failure/lag/records/parse warnings.
- Forecast horizon coverage.
- Reconciliation and cleanup backlog.
- Regional cache/fallback performance.
- OpenRouter calls, latency, cache/fallback/validation results, and estimated
  cost.
- Deployed app/API version and methodology versions.

Initial SLO hypotheses:

- Core API availability: 99.5% during TestFlight.
- Current snapshot p95: below 500 ms.
- Native 48-hour timeline p95: below one second.
- Stored/cached regional p95: below 500 ms.
- Upstream regional fallback: below five seconds.
- Required family freshness inside its published threshold: at least 99%.
- Ask remains optional and cannot reduce core browsing availability.

### Scaling triggers

Keep one API replica, ETags/gzip, process-local burst limits, explanation cache,
and external spend cap during initial TestFlight.

Add shared caching/rate limits only on a trigger:

- A second API replica.
- Sustained traffic above roughly ten requests per second.
- Database p95 outside the SLO.
- Measured repeated expensive-query pressure.

Then add watermark-based snapshot/timeline caches, region validity-period cache,
single-flight expensive requests, durable cost budget, and an atomic shared
rate-limit store.

## 20. Research and success measurement

### 20.1 Test cohort

Recruit a small but deliberately mixed group:

- At least five electricity-system novices.
- At least three flexible-energy/EV users.
- At least three enthusiasts, educators, or journalists.
- At least three energy professionals.
- At least two people who regularly use accessibility features where feasible.

### 20.2 Core tasks

Ask participants to:

- State whether Britain is net importing or exporting.
- Identify the largest displayed supply component.
- Explain how old the relevant data is.
- Distinguish observed from forecast.
- Find a cleaner upcoming period.
- Change the Local region and explain what leaves the device.
- Open evidence for a reported event.
- Make and later inspect a prediction.
- Find the exact source and timestamp for one metric.

### 20.3 Initial success targets

These are product hypotheses, not claims of achieved performance:

- 80% identify import/export and leading supply inside fifteen seconds.
- 80% correctly distinguish observed and forecast after first-use guidance.
- 70% find a supported clean window without assistance.
- 80% understand that map flows are illustrative.
- 90% of critical tasks succeed with VoiceOver after remediation.
- No participant interprets a generation notice as a household power cut.
- No participant interprets a national forecast as regional after Local redesign.
- Crash-free sessions exceed 99.5% during the expanded beta.
- Ask helpfulness exceeds 70% in structured tester feedback, with every reported
  numeric error treated as a release issue.

### 20.4 Measurement policy

Use interviews, task completion, TestFlight feedback, App Store crash reports,
and aggregate server reliability first. If product analytics are later added:

- Publish the event list and retention.
- Never log full postcodes or question bodies by default.
- Avoid shadow identities and cross-app tracking.
- Capture only events tied to explicit product questions.

The north-star behavior is **meaningful weekly explorations**: sessions in which
a user understands a current fact, selects a useful window, inspects evidence,
or resolves a prediction. Raw session length is not the goal.

## 21. QA matrix

Every milestone must cover:

### Data contracts

- Whole and fractional ISO 8601 timestamps on the actual iOS runtime.
- Unknown enums and extra fields.
- Missing optional fields.
- Null values and empty arrays.
- Maximum response size.
- Source-specific stale/partial states.
- DST settlement days.
- Forecast corrections and notice revisions.

### Native states

- First launch online/offline.
- Cached launch then refresh.
- One source delayed.
- API unavailable.
- Forecast unavailable.
- Events unavailable versus confirmed empty.
- OpenRouter unavailable/rate-limited/budget exhausted.
- Region invalid/unavailable/delayed.
- Date rollover and prediction lock/result.
- Background/foreground and network transition.
- Small/large iPhone, AX5, VoiceOver, Reduce Motion, Increase Contrast, Bold Text,
  Reduce Transparency, Low Power Mode.

### Production preflight

- Exact clean commit.
- Backend tests, compile check, migration SQL, and disposable live PostgreSQL
  upgrade/downgrade.
- Hosted simulator XCTest.
- Unsigned archive compile.
- Signed physical-device archive/install.
- Public API/worker readiness.
- Current OpenAPI inventory; Live/Today/Local/Notebook/pro routes; bounded data
  jobs; ETag, gzip, rate limit, request ID, regional, Ask, and event smoke.
- Secret scan and final OpenRouter key rotation.

## 22. Prioritised backlog

### P0 — do next

1. Freeze the shared tree; run final backend/native/compile/privacy/diff/secret
   gates and record exact totals.
2. Validate upgrade/downgrade through migration `20260712_0009` on disposable
   live PostgreSQL after repairing Docker or providing another safe instance.
3. Re-authenticate GitHub/Railway, push the reviewed commit, deploy API then
   worker, and verify the database revision.
4. Run/inspect the bounded 95-day history backfill/materialization and separate
   28-day (maximum 31-day) forecast verification, then configure approved crons.
5. Smoke all current routes and the native app against the exact deployment;
   verify source freshness, ETag/gzip/429/request IDs and AI fallback/cost.
6. Rotate the temporary OpenRouter key and approve privacy/retention/support
   answers.
7. Install the missing Xcode iOS 26.4 device platform, complete Apple
   signing/App Store record, signed physical-device QA, upload, processed
   internal TestFlight install, and the first mixed tester cohort.

### P1 — make it useful enough for public 1.0

1. Close all first-cohort comprehension and high-risk terminology findings.
2. Complete smallest/large-device AX5, VoiceOver, Reduce Motion, contrast,
   offline, thermal, battery, and launch/performance release gates.
3. Validate Today ranking and event relevance on real heavy/empty/partial days;
   finish observed-event worker/public lifecycle policy.
4. Validate Local recommendation/reminder usefulness and geography understanding;
   show typical error only after verified thresholds pass.
5. Validate Notebook lock/date/reinstall/correction/void and local reminder
   behavior on production evidence; measure whether the reminders add value.
6. Add backup/restore drill, external source/job alerts, database role separation,
   and final retention policy.

### P2 — professional trust and repeat access

1. Test source status, event revisions, exact tables, and export with
   professionals; improve only from observed task failures.
2. Surface eligible national forecast-error context where it improves decisions,
   never as generic confidence.
3. Small/medium widget after App Group/signing/cache behavior is available.
4. Saved regions and deeper links if the cohort demonstrates repeat-access need.
5. Additional exact filters/comparisons only when the existing inspector cannot
   answer a validated job.

### P3 — only after evidence of demand

- Remote material-event notifications.
- Tariff/price concepts with an explicitly chosen market boundary.
- Smart-device integrations.
- Accounts/cloud sync.
- More platforms.
- Multi-replica/distributed infrastructure.

## 23. Dependency order

1. A reconciled, clean, tested commit unlocks push/deploy.
2. GitHub and Railway authentication unlocks production reconciliation.
3. Migration `0009` plus backfill/materialization unlocks production export,
   comparisons, resolution history, and forecast verification.
4. Enough exact compatible samples unlocks forecast-error display; code presence
   alone does not.
5. Apple access, bundle registration, App Store record, rotated production key,
   and owner privacy metadata unlock a signed TestFlight build.
6. Physical-device/accessibility/performance validation unlocks a credible
   internal cohort.
7. Cohort comprehension/usefulness evidence unlocks public 1.0 and prioritises
   remaining UI work.
8. Proven event relevance unlocks remote event notifications.
9. Stable production contracts plus an Apple App Group unlock a widget.
10. Measured traffic, not anticipation, unlocks shared scaling infrastructure.

Do not reverse these dependencies. In particular, do not ship confidence before
eligible verification, notifications before relevance, comparisons before
coverage, or leaderboards before secure scoring/accounts.

## 24. Owner decisions and inputs

Engineering can proceed with the recommended defaults, but the owner must
ultimately confirm:

- Apple team, bundle registration, App Store Connect access, testers, and review
  contact.
- Re-authentication of the normal GitHub credential helper and Railway
  CLI/dashboard so engineering can push and deploy the reviewed tree.
- Replacement OpenRouter key and final spend cap.
- Privacy/provider-retention disclosures and support contact.
- Confirmation that the implemented tab names `Live / Today / Local / Notebook`
  and evidence-resolved prediction should remain in public 1.0.
- Confirmation that carbon-first flexible-use planning remains the primary
  practical utility. Recommendation: yes; defer prices and device control.
- Whether professional detail remains free during validation. Recommendation:
  yes.

## 25. Definition of done for public 1.0

Public 1.0 is ready only when:

- Live tells the truth about supply boundary, imports, timing, and
  classifications.
- Today is a bounded deterministic briefing, not an event dump.
- Local provides a clearly scoped, useful clean-window plan.
- Notebook either resolves its prediction from evidence or does not display one.
- Ask remains optional, grounded, cited, limited, and privacy-disclosed.
- Partial failures preserve confirmed data and clearly state what is unknown.
- Critical tasks pass physical-device accessibility and performance testing.
- Production source health, backups, alerts, privacy, key rotation, and release
  operations are complete.
- Internal testers demonstrate comprehension and no repeated high-risk
  misunderstanding remains.
- The signed build is processed, installed from TestFlight, and reverified
  against the exact production deployment.

The roadmap should be reviewed after every TestFlight cohort. Features may move
later when research disproves their value, but truth, accessibility, and complete
interaction loops do not move later.
