# 50Hz iOS

Native SwiftUI iOS 18+ application with no third-party dependencies.

## Visual thesis

A calm scientific instrument observing a living national system: one continuous graphite canvas, warm editorial type, cyan observed energy and violet forecast treatment.

The Live map is the primary workspace. Today is a chronological field log, Mine provides London-first regional context, and Log turns observation into lightweight missions and predictions. Motion is limited to deterministic energy particles, a restrained frequency breathe, and timeline/fuel-focus transitions; Reduce Motion provides still directional paths.

## Project structure

- `50Hz/App`: lifecycle, navigation and shared app state.
- `50Hz/Core`: contracts, fixture repository, timeline sampling, design tokens and shared components.
- `50Hz/Features`: Live, Today, Mine and Log feature surfaces.
- `50Hz/Resources/Fixtures`: API-shaped snapshot and timeline fixtures bundled with the app.
- `50HzTests`: decoding, interpolation, gap and observed/forecast-boundary tests.

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

