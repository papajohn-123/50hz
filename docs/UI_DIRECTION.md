# 50Hz UI direction

This document translates the Claude UI concept archive `50Hz_ Britain's Grid Alive.zip` into an implementation brief for the native SwiftUI app. The archive is the visual north star; its HTML and canvas code are reference material, not production code.

Implementation status, 12 July 2026: the four-tab structure, finite Today,
activity-based Local planner/reminders, evidence-resolved Notebook, contextual
Ask disclosure/cancellation, Data Details/source status/event history/export,
Notebook lock/result-check reminders, notification deep links, and dark native
launch screen are implemented in the current tree. Data Details also contains a
national forecast review; Local shows a typical recent MAE only on an exact,
eligible contract match. Production worker/data jobs/crons are deployed, and API
deployment `817ad899-1cc9-4baa-8900-5e1882e2f05d` passed the complete production
smoke. Notification deep links now use a one-shot pending
handoff so a tap that cold-launches the app routes only after shared app state is
ready. Physical-device accessibility/performance, distribution signing, and
TestFlight verification remain. The rules below are both design guardrails and
manual release acceptance criteria.

## Design thesis

50Hz should feel like a calm scientific instrument observing a living national system. It is not a dashboard made from interchangeable cards and it is not a neon environmental infographic.

The Live screen is the product icon. Every other screen inherits its typography, colour, motion, evidence language and sense of restraint.

## Information architecture

Adopt the four-tab structure from the concept:

1. **Live** — national map, grid state, measurements, fuel filters and timeline.
2. **Today** — a finite national briefing: now, best window, changes, outlook,
   and reported events.
3. **Local** — postcode context and activity-duration planning.
4. **Notebook** — one evidence-resolved prediction, missions, and learned
   concepts.

**Ask the Grid** is a contextual inspector, not a fifth tab and not a generic
chatbot. It opens from the current map, event, or time context and can link its
evidence back to the relevant asset and timestamp.

## Live screen anatomy

Preserve this hierarchy:

1. 50Hz identity and freshness state.
2. A truthful three-part condition headline, such as
   `Lower carbon · Demand steady · Net exporting`.
3. One plain-language interpretation.
4. Frequency, demand and carbon measurements.
5. The Britain visualization as the continuous canvas behind the information.
6. Generation mix bar and selectable fuel chips.
7. Observed/forecast timeline as the bottom anchor.
8. Four-tab navigation.

The screen should not become a stack of opaque panels. Surfaces are used only for selected details, events and controls that require separation from the map.

### Fuel selection

Selecting a fuel:

- Emphasizes its nodes, particles and mix segment.
- Dims other energy sources without completely removing system context.
- Promotes current output, share, one-hour change and rank.
- Adds a compact 24-hour trace.
- Offers a clear way to return to the full grid.

### Timeline states

The timeline remains visible and full width across supported devices.

- Observed history uses the live cyan language.
- The forecast region uses a restrained violet tint and patterned/hatched treatment.
- `NOW` is a fixed semantic boundary.
- Scrubbing displays the selected clock time and observed/forecast classification.
- Moving away from now changes the live badge to replaying or forecast.
- Returning to live is an explicit action with subtle sensory feedback.

Smooth interpolation must not imply extra measurement precision. The UI exposes the source resolution and stops interpolating across material gaps.

## Screen states from the concept

### Live default

- National overview.
- Current three-part headline.
- Interpretation, measurements and mix.
- All sources visible.

### Fuel selected

- Selected energy source becomes the subject.
- Other sources dim.
- Output history and focused statistics appear.

### Forecast scrubbed

- Violet forecast treatment.
- Future timestamp and `FORECAST` label are prominent.
- The headline and explanation are calculated for the selected future frame.
- Frequency is not presented as a forecast measurement unless a defensible source is added. Use demand, carbon and forecast margin/position instead.

### Event selected

- Map highlights the relevant asset, connector or region.
- A medium/large native sheet shows reported facts, observed changes, evidence and qualification.
- Interpretation is visually distinct from an official source statement.

### Today

- A bounded daily briefing rather than a client-side dump of timeline points or
  every active notice.
- Now and the best supported GB window receive hierarchy.
- At most three observed changes, three upcoming moments, and three reported
  events remain visibly classified and time-scoped.
- Empty, partial, offline, observed-only, and event-heavy states remain finite
  and useful.
- Each time-bound entry can move the Live map to its time and subject.

### Ask the Grid

- Presented as an analysis inspector.
- States the selected scope/time and, on first use, that question text reaches
  the 50Hz API and OpenRouter.
- Shows the question, bounded answer, evidence, qualification, human-readable
  sources and follow-up prompts; no model self-confidence score is presented as
  measured certainty.
- Evidence rows are interactive and return to map context.
- The input remains available at the bottom without dominating the analysis.
- In-flight work has a visible cancel action and cancellation/failure does not
  erase deterministic grid content.

### Local

- Defaults to Central London without requesting device location.
- Accepts a full or outward postcode locally, then sends and displays only its
  validated outward code.
- Keeps `Regional now` separate from the national forecast used for planning.
- Plans a continuous lower-carbon window for an activity and required duration,
  with coverage, gaps, forecast capture time, and a compatible start-now
  comparison only when defensible.
- Shows a national `Typical recent error` MAE only when the entire recommended
  window fits one reviewed horizon and source, methodology, issue/effective
  vintage basis, outturn class, sample, coverage, and evidence gates all match.
  It stays silent rather than implying regional accuracy or confidence.
- Requests notification permission only after an explicit reminder action.
- Preferences stay on device for the account-free MVP.

### Notebook

- `Field notebook` framing.
- One active daily prediction with exact lock and evidence times.
- A recent result resolves as correct, incorrect, or void from published
  evidence; publisher corrections remain visible.
- An explicit on-device reminder can precede lock by 15 minutes. After a local
  choice, a separate result-check reminder can follow the evidence window by
  five minutes; its copy must say evidence may still be pending.
- Three deterministic missions navigate to real app context before an explicit
  local/unverified completion action appears.
- Learned concepts replace content duplicated from Today.
- Participation language rewards exploration without punitive streak mechanics,
  prizes, ranks, or server-scoring claims.
- Reminder permission is never requested on refresh. Tapping a reminder returns
  to Notebook; Local reminders return to Local. If the tap cold-launches the app,
  its allow-listed destination is retained until shared navigation state is ready
  and consumed exactly once.

## Loading and failure states

These are part of the first Live milestone, not later polish.

### Loading

- Calm breathing placeholders using the same page geometry as loaded content.
- `Connecting to the grid…` status.
- No fabricated measurements.

### Stale

- Amber status, exact age and last confirmed timestamp.
- Keep the last snapshot visible but lower its emphasis.
- Label every held measurement as stale.
- Explain that automatic retry is in progress and provide a manual retry.

### Offline

- Show the last cached snapshot time when one exists.
- Explain that the story will resume on reconnect.
- Do not label cached data live.

### Critical event

- Red is reserved for a validated material system event.
- A thin red edge and restrained field tint are sufficient; do not turn the whole screen into an alarm.
- A detected frequency deviation must not claim that a unit tripped unless authoritative evidence reports that cause.
- Source cadence must be accurate. Do not say `LIVE 1s` when the available feed publishes less frequently.

## Design tokens adopted from the concept

### Foundation

| Token | Value | Use |
| --- | --- | --- |
| `gridBackground` | `#070A10` | Graphite-blue app background |
| `gridSurface` | `#10141C` | Sheets and restrained raised surfaces |
| `textPrimary` | `#F2EFE9` | Warm off-white primary text |
| `textSecondary` | `#9AA6B4` | Explanations and secondary values |
| `textTertiary` | `#66707E` | Metadata and source labels |
| `hairline` | blue-grey at about 12% | Dividers and boundaries |
| `trueWarning` | `#E05A4D` | Validated material events only |
| `staleAmber` | `#E0A24D` | Delayed or stale feeds |
| `forecastViolet` | `#A98BD6` | Forecast/replay distinction |

### Energy sources

| Source | Colour |
| --- | --- |
| Wind | `#7FE3E0` pale cyan |
| Solar | `#F2C14E` warm gold |
| Nuclear | `#5B9DFF` electric blue |
| Gas | `#D98A4B` burnt orange |
| Biomass | `#7FAE72` muted green |
| Hydro | `#4FB3B3` deep aqua |
| Imports | `#DFE6EE` cool white |
| Storage | `#A98BD6` violet |

Energy colour appears primarily as emitted light: particles, glows, strokes and thin bars. Avoid large saturated cards.

### Typography and spacing

- SF Pro for interface and editorial text.
- SF Mono/monospaced digits for measurements, timestamps and source metadata.
- Reference hierarchy: 30 pt headline, 22 pt detail title, 13–14 pt body and 19 pt measurements.
- Use semantic Dynamic Type styles rather than locking the web prototype's pixel sizes.
- Four-point spacing grid, with 22 pt reference horizontal content padding.
- All controls retain at least a 44-point interactive hit target even when their visual chip is smaller.

## Native component map

| Concept | SwiftUI implementation |
| --- | --- |
| `GridMap` | `Canvas`/optional Metal renderer driven by `TimelineView` |
| `ConditionHeadline` | Three domain enums rendered as an accessible text group |
| `MeasurementRow` | Monospaced values with fact class and freshness labels |
| `FuelFilter` | Selectable chips with 44-point hit areas |
| `MixBar` | Geometry-driven stacked bar using absolute MW-derived proportions |
| `GridTimeline` | Drag gesture, fixed NOW boundary and sensory feedback |
| `EventSheet` | Native sheet with medium and large detents |
| `AskInspector` | Bounded asynchronous analysis sheet with evidence navigation and cancellation |
| `StatusLabel` | Live/estimated/forecast/stale metadata component |
| Data/pro inspector | Native sheets for methodology, exact tables, source delivery/fact status, event revisions, national forecast review and protected export sharing |
| Navigation | Native four-tab container; Ask remains contextual |

## Map translation rules

- Re-author the Britain silhouette as a clean stylized vector. The approximate HTML point list is not a production geographic asset.
- Use normalized scene coordinates so the composition adapts across iPhone sizes.
- Preserve the concept's quiet topographic field, sparse stars/particles and luminous nodes.
- Particle speed may represent magnitude only within a clamped range.
- The global breathe can respond subtly to frequency deviation, but never literally animate at 50 cycles per second.
- Interconnector paths show direction and magnitude and are labelled illustrative.
- Every ambient animation respects Reduce Motion and has a meaningful still state.

## Responsive behavior

- The reference composition is approximately a 393-point-wide iPhone.
- On compact devices, preserve the timeline and controls; crop the map more tightly and reflow measurements.
- On taller devices, give extra height to the map or one additional Today moment, not larger controls.
- Landscape is deferred for the first TestFlight.
- Dynamic Type can move selected details into sheets rather than overlapping the map.

## Prototype claims that must be replaced by source-backed behavior

The concept contains illustrative values and language. They are not requirements for the data engine.

- `Frequency · FCST 49.98 Hz`: do not forecast frequency in v1.
- `A large unit tripped`: show only when an authoritative notice supports it.
- `NGESO frequency telemetry · LIVE 1s`: use the actual source and publication cadence.
- Regional generation-mix percentages: label them estimated and expose the model/source.
- Confidence bars: confidence comes from deterministic evidence rules, not LLM self-assessment.
- Carbon savings: state battery size, charge energy, assumed efficiency and comparison window.
- “Record” language: use bounded history such as `highest in the last 30 days` until the database supports stronger claims.

## Visual acceptance criteria

- The default Live screen reads first as one living system, not a dashboard.
- Observed, estimated, forecast and stale values are distinguishable without relying only on colour.
- Fuel selection, timeline scrubbing and event selection each produce a clear visual state.
- Loading, stale, offline and critical states work before TestFlight.
- Ask the Grid always exposes evidence and qualification.
- Forecast-review numbers appear only for unique eligible national rows; failed,
  ambiguous, incompatible, or below-threshold rows say `NOT SHOWN` and reveal no
  MAE/bias/WAPE values.
- Red appears only for validated material events.
- The launch transition remains dark and visually continuous with Live; no
  white/default flash appears.
- All screenshots pass Dynamic Type, Reduce Motion, increased contrast and VoiceOver review.
- Physical-device profiling confirms acceptable battery, thermal, memory and frame-time behavior.
