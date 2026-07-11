# 50Hz App Store and TestFlight handoff

This is the release worksheet for the iOS 18+ app. The copy is ready to paste
once the owner-only inputs below are complete and the current backend release is
deployed. Do not upload a build that points at an older API: at the last check,
`/ready`, `/privacy`, and `/support` still returned 404 in production.

## Owner-only inputs

Engineering cannot supply or legally decide these values:

- [ ] **Apple Developer Team ID** and a user with signing permission.
- [ ] **App Store Connect access** with an Account Holder, Admin, App Manager, or
  Developer role capable of creating the record/uploading the build. App Privacy
  publishing requires an eligible App Store Connect role.
- [ ] **Bundle registration approval:** confirm `com.papajohn.50hz` belongs to
  the chosen team, or provide the replacement bundle ID.
- [ ] **Legal seller/copyright name** for `2026 <person or entity>`.
- [ ] **App Review contact:** name, monitored email address, and phone number.
- [ ] **Support contact approval:** approve public GitHub issues as the support
  route and add any legally required email/address/phone, or provide a different
  contact destination.
- [ ] **Export-compliance determination:** answer Apple's encryption questions;
  do not infer the legal answer from this document.
- [ ] **App Privacy confirmation:** approve the data-handling answers after
  checking Railway and OpenRouter/model-provider retention terms.
- [ ] **Age-rating questionnaire**, content-rights/licence answers, territories,
  and any Digital Services Act/trader-status answers that apply to the account.
- [ ] **Screenshots:** capture and approve the final production UI with no full
  postcode, private question, secret, or fabricated operational event.
- [ ] **Internal testers:** App Store Connect users/emails and the target group.

## App record values

| Field | Value |
| --- | --- |
| Platform | iOS |
| Name | `50Hz` |
| Primary language | English (UK) |
| Bundle ID | `com.papajohn.50hz` — owner must confirm registration |
| SKU | `50HZ-IOS-001` — owner may replace before record creation |
| Version | `1.0` |
| Build | `1`, increment for every subsequent upload |
| Minimum OS | iOS 18.0 |
| Device family | iPhone |
| Price | Free |
| Primary category | Utilities |
| Secondary category | Education |

The Xcode project currently declares the bundle identifier above, version 1.0,
build 1, iPhone-only support, iOS 18, and the Utilities category.

## Ready-to-paste product-page copy

### Name

```text
50Hz
```

### Subtitle

21 characters; Apple's current limit is 30.

```text
Britain's grid, alive
```

### Promotional text

151 characters; Apple's current limit is 170.

```text
Watch Britain’s electricity system move in near real time, scrub the day, inspect reported events, compare regional carbon, and ask grounded questions.
```

### Description

```text
Britain’s electricity system is always moving. 50Hz turns public grid data into a living, source-aware view that is easy to explore and honest about what is known.

LIVE
See generation, demand, grid frequency, carbon intensity and interconnector flows on an abstract map of Britain. Scrub the timeline to revisit the day, inspect the observed/forecast boundary and return to live data at any time.

TODAY
Explore the current outlook and lightweight daily missions. Mission completion and prediction choices stay on your device; there is no account, leaderboard or server-side score.

MINE
Compare regional carbon with the national picture and find a forecast charging window. 50Hz does not request device location. If you enter a postcode, only its outward code is sent for the regional lookup.

EVENTS AND EXPLANATIONS
When authoritative reports identify an outage or system warning, 50Hz surfaces the notice and its evidence. Ask the Grid can explain validated observations and reported events with source-backed citations. Unsupported AI output is rejected or replaced with deterministic evidence copy.

BUILT FOR TRUST
Every data family has its own source time and cadence. Observed, estimated, derived, reported and forecast values stay distinct. The map is an illustrative national visualization, not a literal transmission-network diagram.

50Hz uses public Elexon and NESO data that can be delayed, corrected or temporarily unavailable. AI features use OpenRouter and may be rate-limited or unavailable without blocking the core grid view. 50Hz is informational and must not be used for operational, trading, safety or emergency decisions.

No account. No advertising. No cross-app tracking. Requires iOS 18 or later.
```

### Keywords

83 ASCII bytes; Apple's current limit is 100 bytes. App/company names are not
duplicated.

```text
electricity,energy,grid,carbon,power,renewables,frequency,demand,wind,solar,Britain
```

### URLs

These routes exist in the current repository but must return HTTP 200 from the
release deployment before being entered in App Store Connect.

| Field | Ready value |
| --- | --- |
| Privacy Policy URL | `https://50hz-api-production.up.railway.app/privacy` |
| Support URL | `https://50hz-api-production.up.railway.app/support` |
| Marketing URL | Leave blank for 1.0, or owner supplies a product site |
| User Privacy Choices URL | Leave blank unless the owner publishes a separate choices page |

The support page currently points users to the public GitHub issue tracker.
Apple says the support destination must provide real contact information as
required by local law. The owner must approve that route and add any required
email, address, or phone before submission.

### Copyright

```text
2026 <OWNER LEGAL NAME OR ENTITY>
```

Apple adds the copyright symbol automatically.

## Ready-to-paste TestFlight copy

### Beta app description

```text
50Hz is a source-aware view of Britain’s electricity system for curious people and energy professionals. Explore near-live generation, demand, frequency, carbon and interconnector data on an abstract national map; scrub the timeline; inspect reported REMIT/SYSWARN events; compare regional carbon; try local daily missions; and ask bounded, evidence-grounded questions. No account or location permission is required. Requires iOS 18 or later.
```

### What to Test

```text
Please test the complete production flow:

• Launch on a clean install, then relaunch in Airplane Mode and confirm cached data is clearly labelled stale/offline.
• Scrub the Live timeline, cross the observed/forecast boundary, select a fuel and Resume Live.
• Open a reported event, inspect its source evidence and request an explanation. An evidence-based fallback is acceptable; invented numbers or causes are not.
• In Today, check that forecasts are withheld when stale and that mission/prediction state stays local. A cached daily plan must not survive into a new date.
• In Mine, enter a UK postcode and confirm only the outward code is shown/sent. Try delayed and failed regional refresh states.
• Ask a suggested grid question and check citations, freshness and limitations. Core grid browsing must still work if Ask is rate-limited or unavailable.
• Check VoiceOver, Dynamic Type, Reduce Motion, contrast, thermal/battery behaviour and all retry controls.

Public data arrives at different cadences and active events may legitimately be absent. Please include the app version/build, device, iOS version, time and affected screen in feedback. Do not include a full postcode, private question or credential.
```

### Feedback email

```text
<OWNER-MONITORED SUPPORT EMAIL>
```

## Ready-to-paste App Review notes

```text
50Hz requires no account, login, subscription or demo credentials. It opens to a national electricity view and defaults regional context to Central London.

The app reads the production API at https://50hz-api-production.up.railway.app. Public data comes from Elexon Insights and NESO Carbon Intensity. Each value is labelled by freshness/classification; source delays or an empty active-event list are valid states, not login failures.

Mine accepts a manually entered UK postcode. The full value is stored only in on-device preferences; a valid inward suffix is removed before the outward code is sent to the regional endpoint. The app does not request Location Services permission.

Ask the Grid and event explanations are optional server-side OpenRouter features. The backend supplies bounded read-only evidence tools and server-owned citations, rejects unsupported numbers/causal claims and can return deterministic explanation copy. AI failure, validation failure, budget exhaustion or rate limiting does not block deterministic grid browsing.

Today’s daily plan is supplied by the backend, but mission completion and prediction choices remain local to the device. There is no user account, leaderboard, prize or server-side scoring.

The abstract Britain map is illustrative rather than a literal transmission diagram. 50Hz is informational and is not intended for operational, trading, safety or emergency decisions.

Privacy policy: https://50hz-api-production.up.railway.app/privacy
Support: https://50hz-api-production.up.railway.app/support
```

App Review contact name, email, and phone are owner-only inputs. Select “Sign-in
required: No.”

## App Privacy worksheet

This worksheet records code facts; the owner remains responsible for the final
App Store Connect answers and third-party-provider terms.

Current facts:

- No account, advertising SDK, analytics SDK, crash SDK, location permission, or
  cross-app tracking.
- Tracking: **No**.
- Railway processes ordinary operational request metadata. The conservative
  privacy-manifest posture is **Other Diagnostic Data**, not linked to identity,
  not used for tracking, purpose **App Functionality**.
- The app stores its postcode preference and cached API responses on-device.
- A valid full postcode is reduced to its outward code before transmission.
- The 50Hz application database does not persist postcode requests or Ask
  question histories.
- Ask text and selected time are transmitted to the 50Hz API and OpenRouter only
  to service the request; provider zero-data-retention processing is requested.
- Raw electricity-source JSON is not user data and is pruned after 72 hours by
  default; normalized public grid evidence remains.

Owner checks before publishing App Privacy answers:

1. Confirm Railway access-log contents and retention, including outward postcode
   paths and IP/network metadata.
2. Confirm OpenRouter and selected model-provider zero-data-retention eligibility
   for the production key/model.
3. Decide whether Apple's current definitions require **Other User Content** for
   service-only Ask text and **Coarse Location** for service-only outward postcode
   handling. Do not select “No data collected” without validating the provider
   and log behavior.
4. Confirm Other Diagnostic Data is not linked to a user, used only for App
   Functionality/security/operations, and not used for tracking.
5. Ensure `/privacy` exactly matches the published answers and update both when
   behavior changes.

`PrivacyInfo.xcprivacy` and the App Store privacy questionnaire are separate
requirements; one does not complete the other.

## Age rating, rights, and export compliance

### Owner decisions

- Complete Apple's current age-rating questionnaire from actual app content.
  Do not hard-code a rating in this repository; App Store Connect calculates it
  from the owner's answers.
- Confirm rights to display and transform Elexon/NESO data, attribution text, and
  links for the chosen territories.
- Complete content-rights and any regulated-information questions. The app's
  review notes must retain the informational/non-trading caveat.
- Complete Apple's export-compliance questions. The app uses HTTPS through
  Apple's networking stack and contains no custom iOS cryptography dependency,
  but the owner is responsible for the legal determination.

The generated Info.plist does not currently set
`ITSAppUsesNonExemptEncryption`. After the owner completes Apple's questions,
set that key to the resulting truthful value if appropriate so future uploads do
not repeatedly enter Missing Compliance.

## Screenshot plan

The app is iPhone-only. Apple currently requires one to ten screenshots. Supply
the highest required 6.9-inch portrait set so App Store Connect can scale it for
smaller displays; accepted current sizes include 1260×2736, 1290×2796, and
1320×2868 pixels. Recheck Apple's specifications at capture time.

Recommended six-frame sequence:

1. **Britain’s grid, alive** — Live abstract map, current data and freshness.
2. **Replay the day** — timeline scrub with observed/forecast boundary visible.
3. **Reported, then explained** — active authoritative event plus citations; use
   a real production notice or omit this frame when none is active.
4. **A cleaner time to charge** — Mine regional comparison and window using only
   outward code `SW1A`.
5. **Today’s grid challenge** — missions/prediction with local-progress copy.
6. **Ask with evidence** — a non-personal suggested question with citations and
   limitations visible.

Owner-only screenshot gate:

- [ ] Capture from the signed release candidate against production.
- [ ] Confirm status bar/time, source values, event wording, and freshness are
  plausible and not fabricated.
- [ ] Remove full postcodes, custom/private Ask text, notifications, debug UI,
  test accounts, and secrets.
- [ ] Check text contrast, Dynamic Type clipping, and device-frame consistency.
- [ ] Approve every final image and localization.

## Exact Xcode archive and upload checklist

### A. Account and signing

1. In Apple Developer, register/confirm App ID `com.papajohn.50hz` under the
   chosen Team.
2. In App Store Connect, create the iOS app record with that exact bundle ID.
3. In Xcode, open `ios/50Hz.xcodeproj`, select target **50Hz**, then **Signing &
   Capabilities**.
4. Select the owner-provided Team and enable automatic signing unless the owner
   has a managed manual-signing policy.
5. Confirm bundle ID, iOS 18.0 minimum, iPhone device family, version 1.0, and a
   unique incrementing build number.
6. Align the Info.plist category with the approved App Store category and apply
   the owner-confirmed export-compliance key if appropriate.

### B. Release preflight

1. Deploy the exact release commit to Railway API and worker.
2. Require HTTP 200 from `/ready`, `/privacy`, `/support`, and all user-visible
   routes; run the full smoke test in [OPERATIONS.md](OPERATIONS.md).
3. Rotate the temporary OpenRouter key and verify one authorized Ask plus one
   event explanation.
4. Run backend tests, migration SQL validation, iOS tests, privacy-manifest lint,
   and a Release archive compile from the clean release commit.
5. Install a signed development/Ad Hoc build on a physical iPhone and complete
   functional, accessibility, offline, battery, and failure-state QA.
6. Confirm the final app icon, privacy manifest, source attribution, privacy and
   support pages are included/available.

### C. Create the signed archive

In Xcode:

1. Select **Any iOS Device (arm64)** or a connected physical iPhone.
2. Choose **Product → Clean Build Folder**.
3. Choose **Product → Archive**.
4. In Organizer, select the new archive and inspect version, build, bundle ID,
   signing team, icon, entitlements, and archive validation warnings.

Equivalent signed CLI archive after Team/signing is configured in Xcode:

```bash
xcodebuild \
  -project ios/50Hz.xcodeproj \
  -scheme 50Hz \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath /tmp/50Hz.xcarchive \
  -allowProvisioningUpdates \
  archive
```

Do not use `CODE_SIGNING_ALLOWED=NO` for the upload archive. CI's unsigned
archive is a compile gate, not the distributable artifact.

### D. Validate and upload

1. In Organizer choose **Distribute App → App Store Connect → Upload**.
2. Keep symbol upload enabled unless a reviewed policy says otherwise.
3. Let Xcode validate signing, capabilities, bundle metadata, and assets; resolve
   every error and review warnings before upload.
4. Upload using an App Store Connect user with an eligible role.
5. In App Store Connect → TestFlight → Build Uploads, wait for processing to
   become Complete and inspect every warning.
6. If the build shows Missing Compliance, the owner answers export-compliance
   questions before distribution. If processing fails, fix the issue and upload
   a new build number when required.

### E. Internal TestFlight

1. In TestFlight, create an **Internal Testing** group.
2. Add the processed build and paste **What to Test** from this document.
3. Add/invite the owner-approved App Store Connect users. Apple currently allows
   up to 100 internal testers; builds are available for up to 90 days.
4. Install from TestFlight on a physical iPhone, not from Xcode.
5. Re-run launch/cache, Live, timeline, event/explanation, Today/game, Mine,
   Ask, accessibility, privacy/support-link, and failure-state checks.
6. Record the tested commit, version/build, API deployment, device/iOS version,
   pass/fail result, and any release blocker.

Internal TestFlight is the current delivery target. External testing and public
App Review should wait until internal feedback is closed.

## App Store submission after TestFlight

1. Create/select iOS version 1.0 and paste approved metadata/screenshots.
2. Publish accurate App Privacy answers and privacy URL.
3. Complete category, age rating, content rights, copyright, availability,
   export compliance, review contact, and regional/account compliance fields.
4. Select the processed build; a Missing Compliance build cannot be submitted
   until the owner answers the required questions.
5. Paste App Review notes, confirm no sign-in is required, select manual release
   for the first version unless the owner chooses otherwise, then submit.
6. Respond to App Review using the monitored owner contact. Do not change backend
   contracts, privacy behavior, or public legal-page copy underneath a submitted
   build without reassessing metadata and review notes.

## Final go/no-go record

- [ ] Owner-only inputs complete.
- [ ] Release commit clean, pushed, and recorded: `<COMMIT>`.
- [ ] API deployment from that commit: `<DEPLOYMENT>`.
- [ ] Worker deployment from that commit: `<DEPLOYMENT>`.
- [ ] Production smoke and source freshness pass.
- [ ] `/privacy` and `/support` return approved HTTPS pages.
- [ ] Temporary secrets rotated.
- [ ] Signed archive validation passes.
- [ ] Physical-device release QA passes.
- [ ] App Privacy/export/age/content-rights answers approved by owner.
- [ ] Screenshots and metadata approved by owner.
- [ ] Upload processes without unresolved warning/error.
- [ ] Internal TestFlight install and full flow pass.

## Current Apple references

Apple changes App Store Connect requirements, so recheck these official pages at
release time:

- [App information limits](https://developer.apple.com/help/app-store-connect/reference/app-information/app-information/)
- [Platform version metadata and review fields](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information/)
- [Screenshot specifications](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications)
- [Manage App Privacy](https://developer.apple.com/help/app-store-connect/manage-app-information/manage-app-privacy/)
- [Upload builds](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/)
- [Choose a build and resolve compliance](https://developer.apple.com/help/app-store-connect/manage-builds/choose-a-build-to-submit/)
- [Export compliance overview](https://developer.apple.com/help/app-store-connect/manage-app-information/overview-of-export-compliance/)
- [Add internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers)
