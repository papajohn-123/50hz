# 50Hz iOS

Native SwiftUI iOS 18+ application with no third-party dependencies.

The runtime uses `https://50hz-api-production.up.railway.app`. Bundled fixtures remain isolated to previews and contract tests.

## Visual thesis

A calm scientific instrument observing a living national system: one continuous graphite canvas, warm editorial type, cyan observed energy and violet forecast treatment.

The Live map is the primary workspace. Today is a chronological field log, Mine provides London-first regional context, and Log turns observation into lightweight missions and predictions. Motion is limited to deterministic energy particles, a restrained frequency breathe, and timeline/fuel-focus transitions; Reduce Motion provides still directional paths.

## Project structure

- `50Hz/App`: lifecycle, navigation and shared app state.
- `50Hz/Core`: contracts, fixture repository, timeline sampling, design tokens and shared components.
- `50Hz/Features`: Live, Today, Mine and Log feature surfaces.
- `50Hz/Resources/Fixtures`: API-shaped snapshot and timeline fixtures bundled with the app.
- `50HzTests`: decoding, interpolation, gap and observed/forecast-boundary tests.

## Runtime data behavior

- Cache-first launch from a small protected disk cache; cached values are never labelled live.
- Conditional `If-None-Match` and `If-Modified-Since` requests against current and timeline endpoints.
- Fifteen-second request timeout, cancellation when foreground work stops, and one shared in-flight task per endpoint.
- Current and timeline refresh concurrently every 60 seconds while the app is active.
- Partial, stale, offline, contract and HTTP failures have bounded user-facing messages.
- Share cards are rendered to PNG entirely on-device from validated structured data.

## Build

Open `50Hz.xcodeproj` in Xcode, choose an iPhone simulator and run the `50Hz` scheme.

Verified command-line build:

```sh
xcodebuild -project ios/50Hz.xcodeproj -scheme 50Hz -configuration Debug -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/50hz-derived CODE_SIGNING_ALLOWED=NO ARCHS=arm64 ONLY_ACTIVE_ARCH=YES build
```

Compile the app and unit-test bundle:

```sh
xcodebuild -project ios/50Hz.xcodeproj -scheme 50Hz -configuration Debug -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/50hz-tests CODE_SIGNING_ALLOWED=NO ARCHS=arm64 ONLY_ACTIVE_ARCH=YES build-for-testing
```
