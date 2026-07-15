import XCTest
@testable import FiftyHz

final class LiveTruthPresentationTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_784_112_000)

    func testFreshnessSummaryKeepsMixedFamilyStatesDistinct() {
        let snapshot = makeSnapshot()

        let summary = LiveFreshnessPresentation.make(snapshot: snapshot, at: now)

        XCTAssertEqual(summary.currentCount, 1)
        XCTAssertEqual(summary.delayedCount, 1)
        XCTAssertEqual(summary.staleCount, 1)
        XCTAssertEqual(summary.unavailableCount, 1)
        XCTAssertEqual(summary.compactLabel, "1 current · 1 delayed · 1 stale · 1 unavailable")
        XCTAssertTrue(summary.hasConcern)
        XCTAssertFalse(summary.isLegacy)
    }

    func testEachDisplayedMetricRetainsOwnFactSourceAndObservationTime() throws {
        let metrics = LiveMetricPresentation.make(snapshot: makeSnapshot(), mode: "LIVE", at: now)
        let frequency = try XCTUnwrap(metrics.first { $0.id == .frequency })
        let demand = try XCTUnwrap(metrics.first { $0.id == .demand })
        let carbon = try XCTUnwrap(metrics.first { $0.id == .carbon })

        XCTAssertEqual(frequency.factLabel, "OBSERVED")
        XCTAssertEqual(frequency.sourceLabel, "Elexon Insights · FREQ")
        XCTAssertEqual(frequency.observedAt, now.addingTimeInterval(-10))
        XCTAssertEqual(frequency.state, .current)

        XCTAssertEqual(demand.factLabel, "OBSERVED")
        XCTAssertEqual(demand.sourceLabel, "Elexon Insights · INDO")
        XCTAssertEqual(demand.observedAt, now.addingTimeInterval(-500))
        XCTAssertEqual(demand.state, .delayed)

        XCTAssertEqual(carbon.factLabel, "ESTIMATED")
        XCTAssertEqual(carbon.sourceLabel, "NESO · CARBON")
        XCTAssertEqual(carbon.observedAt, now.addingTimeInterval(-2_000))
        XCTAssertEqual(carbon.state, .stale)
    }

    func testReplayUsesSelectedFrameRatherThanCurrentSourceTimestamp() throws {
        var snapshot = makeSnapshot()
        snapshot.timestamp = now.addingTimeInterval(-86_400)

        let metrics = LiveMetricPresentation.make(snapshot: snapshot, mode: "REPLAY", at: now)
        let demand = try XCTUnwrap(metrics.first { $0.id == .demand })

        XCTAssertEqual(demand.sourceLabel, "50Hz timeline")
        XCTAssertEqual(demand.observedAt, snapshot.timestamp)
        XCTAssertEqual(demand.timePrefix, "Frame")
        XCTAssertEqual(demand.state, .current)
    }

    func testInterconnectorSignBecomesExplicitHumanDirection() {
        let importing = LiveInterconnectorPresentation.make(
            flow: InterconnectorFlow(id: "ifa", name: "IFA", countryCode: "FR", megawatts: 1_250, factClass: .observed)
        )
        let exporting = LiveInterconnectorPresentation.make(
            flow: InterconnectorFlow(id: "nemo", name: "Nemo Link", countryCode: "BE", megawatts: -725, factClass: .observed)
        )

        XCTAssertEqual(importing.direction, "IMPORT")
        XCTAssertEqual(importing.flowDescription, "FR → GB")
        XCTAssertEqual(importing.magnitude, "1,250 MW")
        XCTAssertEqual(exporting.direction, "EXPORT")
        XCTAssertEqual(exporting.flowDescription, "GB → BE")
        XCTAssertEqual(exporting.magnitude, "725 MW")
    }

    func testAssetOverlayRejectsMissingSourceAndImplausibleCoordinates() {
        let valid = LiveMapAsset(
            id: "asset-1",
            name: "Example station",
            fuel: .gas,
            latitude: 52.4,
            longitude: -1.7,
            capacityMW: 800,
            sourceID: "elexon.asset-registry",
            observedAt: now
        )
        let unsourced = LiveMapAsset(
            id: "asset-2",
            name: "Unsourced station",
            fuel: .wind,
            latitude: 54.0,
            longitude: -2.0,
            capacityMW: 200,
            sourceID: "",
            observedAt: now
        )
        let outsideGB = LiveMapAsset(
            id: "asset-3",
            name: "Outside bounds",
            fuel: .other,
            latitude: 10,
            longitude: 20,
            capacityMW: nil,
            sourceID: "publisher.asset-registry",
            observedAt: now
        )

        XCTAssertTrue(valid.hasAuthoritativeCoordinate)
        XCTAssertFalse(unsourced.hasAuthoritativeCoordinate)
        XCTAssertFalse(outsideGB.hasAuthoritativeCoordinate)
        XCTAssertEqual(LiveTruthCopy.supplyTitle, "Transmission-visible supply")
        XCTAssertTrue(LiveTruthCopy.mapDisclosure.contains("do not locate"))
    }

    func testAssetClusteringReducesNationalMarkersAndSeparatesThemAtHighZoom() {
        let west = LiveMapAsset(
            id: "west",
            name: "West site",
            fuel: .wind,
            latitude: 52.40,
            longitude: -1.70,
            capacityMW: 300,
            sourceID: "desnz.repd",
            observedAt: now
        )
        let east = LiveMapAsset(
            id: "east",
            name: "East site",
            fuel: .solar,
            latitude: 52.45,
            longitude: -1.30,
            capacityMW: 100,
            sourceID: "desnz.repd",
            observedAt: now
        )
        let unsourced = LiveMapAsset(
            id: "unsourced",
            name: "Not drawable",
            fuel: .gas,
            latitude: 52.42,
            longitude: -1.50,
            capacityMW: 900,
            sourceID: "",
            observedAt: now
        )

        let national = LiveAssetClustering.clusters(
            assets: [west, east, unsourced],
            zoomScale: 1
        )
        let local = LiveAssetClustering.clusters(
            assets: [west, east, unsourced],
            zoomScale: 8
        )

        XCTAssertEqual(national.count, 1)
        XCTAssertEqual(national[0].count, 2)
        XCTAssertEqual(national[0].totalCapacityMW, 400)
        XCTAssertEqual(local.count, 2)
        XCTAssertTrue(local.allSatisfy { $0.singleAsset != nil })
    }

    func testAssetViewportCullsOffscreenMarkersAndCoarsensDenseAreas() {
        let center = LiveMapAsset(
            id: "center",
            name: "Center site",
            fuel: .wind,
            latitude: 55.15,
            longitude: -4.0,
            capacityMW: 20,
            sourceID: "desnz.repd",
            observedAt: now
        )
        let farAway = LiveMapAsset(
            id: "far-away",
            name: "Far site",
            fuel: .solar,
            latitude: 50.0,
            longitude: 2.5,
            capacityMW: 20,
            sourceID: "desnz.repd",
            observedAt: now
        )
        let viewport = CGSize(width: 390, height: 340)
        let sparseSelection = LiveAssetClustering.visibleClusters(
            in: LiveAssetClustering.Index(assets: [center, farAway]),
            zoomScale: 8,
            offset: .zero,
            viewportSize: viewport
        )

        XCTAssertTrue(sparseSelection.clusters.flatMap(\.assets).contains(center))
        XCTAssertFalse(sparseSelection.clusters.flatMap(\.assets).contains(farAway))

        var denseAssets: [LiveMapAsset] = []
        for latitudeIndex in 0..<17 {
            for longitudeIndex in 0..<19 {
                denseAssets.append(
                    LiveMapAsset(
                        id: "dense-\(latitudeIndex)-\(longitudeIndex)",
                        name: "Dense site",
                        fuel: .wind,
                        latitude: 54.11 + Double(latitudeIndex) * 0.13,
                        longitude: -5.53 + Double(longitudeIndex) * 0.17,
                        capacityMW: 1,
                        sourceID: "desnz.repd",
                        observedAt: now
                    )
                )
            }
        }
        let denseSelection = LiveAssetClustering.visibleClusters(
            in: LiveAssetClustering.Index(assets: denseAssets),
            zoomScale: 8,
            offset: .zero,
            viewportSize: viewport,
            maximumMarkerCount: 20
        )

        XCTAssertTrue(denseSelection.isCoarsened)
        XCTAssertLessThanOrEqual(denseSelection.clusters.count, 20)
    }

    func testClusterRecenteringPlacesTappedCoordinateAtViewportCenter() {
        let viewport = CGSize(width: 390, height: 340)
        let point = LiveAssetMapProjection.position(
            latitude: 52.5,
            longitude: -1.5,
            viewportSize: viewport
        )
        let offset = LiveAssetMapProjection.centeredOffset(
            for: point,
            viewportSize: viewport,
            zoomScale: 4
        )
        let screenPosition = LiveAssetMapProjection.screenPosition(
            point,
            viewportSize: viewport,
            zoomScale: 4,
            offset: offset
        )

        XCTAssertEqual(screenPosition.x, viewport.width / 2, accuracy: 0.001)
        XCTAssertEqual(screenPosition.y, viewport.height / 2, accuracy: 0.001)
    }

    func testAssetEvidenceLabelsExposeSignAndDirection() {
        XCTAssertEqual(
            AssetEvidencePresentation.signedMeasurement(125, unit: "MW", direction: "export"),
            "+125 MW · export"
        )
        XCTAssertEqual(
            AssetEvidencePresentation.signedMeasurement(-80, unit: "MW average", direction: "import"),
            "−80 MW average · import"
        )
        XCTAssertEqual(
            AssetEvidencePresentation.signedMeasurement(0, unit: "MW", direction: "idle"),
            "0 MW · idle"
        )
    }

    private func makeSnapshot() -> GridSnapshot {
        let sources = [
            source(id: "freq", name: "Elexon Insights", dataset: "FREQ", age: 10),
            source(id: "demand", name: "Elexon Insights", dataset: "INDO", age: 500),
            source(id: "carbon", name: "NESO", dataset: "CARBON", age: 2_000)
        ]
        return GridSnapshot(
            timestamp: now.addingTimeInterval(-10),
            retrievedAt: now.addingTimeInterval(-5),
            freshness: .live,
            freshnessAgeSeconds: 10,
            headline: ConditionHeadline(
                cleanliness: "Typical carbon",
                balance: "Comfortable",
                energyPosition: "Importing",
                interpretation: "Wind leads."
            ),
            frequency: GridMetric(value: 50.01, unit: "Hz", factClass: .observed, sourceID: "freq"),
            demand: GridMetric(value: 30_400, unit: "MW", factClass: .observed, sourceID: "demand"),
            carbonIntensity: GridMetric(value: 142, unit: "gCO₂/kWh", factClass: .estimated, sourceID: "carbon"),
            generation: [],
            interconnectors: [
                InterconnectorFlow(id: "ifa", name: "IFA", countryCode: "FR", megawatts: 1_250, factClass: .observed)
            ],
            activeEvent: nil,
            sources: sources,
            dataStatus: [
                status(family: .frequency, sourceID: "freq", age: 10, seriesCount: 1),
                status(family: .demand, sourceID: "demand", age: 500, seriesCount: 1),
                status(family: .carbon, sourceID: "carbon", age: 2_000, seriesCount: 1),
                status(family: .interconnectors, sourceID: "connector", age: 0, seriesCount: 0)
            ],
            supply: nil
        )
    }

    private func source(id: String, name: String, dataset: String, age: TimeInterval) -> SourceReference {
        SourceReference(
            id: id,
            name: name,
            dataset: dataset,
            observedAt: now.addingTimeInterval(-age),
            retrievedAt: now.addingTimeInterval(-5),
            cadenceSeconds: 60
        )
    }

    private func status(
        family: GridDataFamily,
        sourceID: String,
        age: Int,
        seriesCount: Int
    ) -> DataFamilyStatus {
        DataFamilyStatus(
            family: family,
            metricIDs: [family.rawValue],
            sourceIDs: [sourceID],
            sourceRecordIDs: ["record-\(sourceID)"],
            requiredForSnapshot: true,
            evaluatedAt: now,
            deliveryState: seriesCount == 0 ? .unavailable : .healthy,
            factState: seriesCount == 0 ? .unavailable : (age > 900 ? .stale : (age > 120 ? .delayed : .live)),
            observedAt: seriesCount == 0 ? nil : now.addingTimeInterval(TimeInterval(-age)),
            publishedAt: nil,
            retrievedAt: seriesCount == 0 ? nil : now.addingTimeInterval(-5),
            validTo: nil,
            observationAgeSeconds: seriesCount == 0 ? nil : age,
            retrievalAgeSeconds: seriesCount == 0 ? nil : 5,
            expectedCadenceSeconds: 60,
            deliveryHealthySeconds: 120,
            deliveryStaleSeconds: 300,
            factLiveSeconds: 120,
            factStaleSeconds: 900,
            seriesCount: seriesCount
        )
    }
}
