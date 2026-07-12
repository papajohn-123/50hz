import Foundation
import SwiftUI
import XCTest
@testable import FiftyHz

final class InspectionContractTests: XCTestCase {
    func testSourceStatusDecodesSeparateDeliveryAndFactStates() throws {
        let response = try InspectionFixture.sourceStatus()

        XCTAssertEqual(response.sourceCount, 2)
        XCTAssertEqual(response.sources[0].deliveryState, .healthy)
        XCTAssertEqual(response.sources[0].factState, .live)
        XCTAssertEqual(response.sources[1].deliveryState, .delayed)
        XCTAssertEqual(response.sources[1].factState, .notApplicable)
        XCTAssertEqual(response.sources[0].factFamilies, ["generation", "interconnectors"])
        XCTAssertEqual(
            response.definitions["deliveryState"],
            "Whether 50Hz receives the source on schedule."
        )
    }

    func testSourceStatusUnknownStatesAreBoundedAndAdditiveFieldsAreIgnored() throws {
        let data = Data(
            String(decoding: InspectionFixture.sourceStatusData(), as: UTF8.self)
                .replacingOccurrences(of: #""deliveryState":"healthy""#, with: #""deliveryState":"recovering""#)
                .replacingOccurrences(of: #""factState":"live""#, with: #""factState":"provisional""#)
                .replacingOccurrences(of: #""note":"Delivery""#, with: #""newServerField":true,"note":"Delivery""#)
                .utf8
        )

        let response = try GridJSON.decoder.decode(SourceStatusResponse.self, from: data)

        XCTAssertEqual(response.sources[0].deliveryState, .unknown)
        XCTAssertEqual(response.sources[0].factState, .unknown)
        XCTAssertEqual(InspectionPresentation.deliveryLabel(.unknown), "Unknown")
        XCTAssertEqual(InspectionPresentation.factLabel(.unknown), "Unknown")
    }

    func testSourceCountMismatchIsRejected() {
        let data = Data(
            String(decoding: InspectionFixture.sourceStatusData(), as: UTF8.self)
                .replacingOccurrences(of: #""sourceCount":2"#, with: #""sourceCount":3"#)
                .utf8
        )

        XCTAssertThrowsError(try GridJSON.decoder.decode(SourceStatusResponse.self, from: data))
    }

    func testEventHistoryDecodesNewestFirstStateDeltasAndProvenance() throws {
        let history = try InspectionFixture.eventHistory()

        XCTAssertEqual(history.lifecycleStatus, .resolved)
        XCTAssertEqual(history.revisions.map(\.revisionNumber), [3, 2, 1])
        XCTAssertEqual(history.revisions[0].changes[0].field, .status)
        XCTAssertEqual(history.revisions[0].changes[0].before, .text("updated"))
        XCTAssertEqual(history.revisions[1].changes[0].before, .number(500))
        XCTAssertEqual(history.revisions[1].changes[0].after, .number(350))
        XCTAssertEqual(history.revisions[0].sourceIDs, ["elexon.remit"])
        XCTAssertEqual(history.revisions[0].sourceRecordIDs, ["elexon:REMIT:example:r3"])
        XCTAssertEqual(history.revisions[0].evidenceChecksum.count, 64)
    }

    func testEventHistoryRejectsWrongIdentityAndNonDescendingRevisions() {
        let wrongID = Data(
            String(decoding: InspectionFixture.eventHistoryData(), as: UTF8.self)
                .replacingOccurrences(of: InspectionFixture.eventID, with: "not-an-event")
                .utf8
        )
        let wrongOrder = Data(
            String(decoding: InspectionFixture.eventHistoryData(), as: UTF8.self)
                .replacingOccurrences(of: #""revisionNumber":3"#, with: #""revisionNumber":1"#, options: [], range: nil)
                .utf8
        )

        XCTAssertThrowsError(try GridJSON.decoder.decode(EventHistoryResponse.self, from: wrongID))
        XCTAssertThrowsError(try GridJSON.decoder.decode(EventHistoryResponse.self, from: wrongOrder))
    }

    func testEventUnknownEnumsRemainFinite() throws {
        let data = Data(
            String(decoding: InspectionFixture.eventHistoryData(), as: UTF8.self)
                .replacingOccurrences(of: #""lifecycleStatus":"resolved""#, with: #""lifecycleStatus":"archived""#)
                .replacingOccurrences(of: #""status":"resolved""#, with: #""status":"archived""#)
                .replacingOccurrences(of: #""authority":"authoritative_notice""#, with: #""authority":"future_authority""#)
                .replacingOccurrences(of: #""field":"status""#, with: #""field":"future_field""#)
                .utf8
        )

        let history = try GridJSON.decoder.decode(EventHistoryResponse.self, from: data)

        XCTAssertEqual(history.lifecycleStatus, .unknown)
        XCTAssertEqual(history.revisions[0].status, .unknown)
        XCTAssertEqual(history.revisions[0].authority, .unknown)
        XCTAssertEqual(history.revisions[0].changes[0].field, .unknown)
        XCTAssertEqual(EventHistoryPresentation.statusLabel(.unknown), "Unknown")
    }

    func testExportSchemaIsClosedToKnownMetricsAndFormats() throws {
        let schema = try InspectionFixture.exportSchema()

        XCTAssertEqual(schema.maxWindowDays, 31)
        XCTAssertEqual(schema.maxRowCount, 1_488)
        XCTAssertEqual(schema.resolutionsSeconds, [1_800])
        XCTAssertEqual(schema.formats, [.json, .csv])
        XCTAssertEqual(schema.metrics.map(\.metric), [.nationalCarbon, .nationalDemand, .generationFuel, .interconnectorFlow])
        XCTAssertTrue(schema.metrics[2].selectorRequired)
        XCTAssertEqual(schema.metrics[2].allowedSelectors, ["CCGT", "WIND"])
    }

    func testRecentExportRequestUsesExactUTCBoundsAndBoundedRows() throws {
        let now = Date(timeIntervalSince1970: 1_783_825_937) // between half-hours
        let request = try ExportRequestSpec.recent(
            metric: .nationalCarbon,
            selector: nil,
            days: 31,
            format: .csv,
            now: now
        )

        XCTAssertTrue(ExportRequestSpec.isHalfHourBoundary(request.from))
        XCTAssertTrue(ExportRequestSpec.isHalfHourBoundary(request.to))
        XCTAssertLessThanOrEqual(request.to, now)
        XCTAssertEqual(request.expectedRowCount, 1_488)
        XCTAssertEqual(request.to.timeIntervalSince(request.from), 31 * 24 * 3_600)
        XCTAssertThrowsError(
            try ExportRequestSpec.recent(
                metric: .nationalCarbon,
                selector: nil,
                days: 32,
                format: .json,
                now: now
            )
        )
    }

    func testExportURLUsesOnlyFixedRouteAndAllowlistedQueryFields() throws {
        let request = try ExportRequestSpec.recent(
            metric: .generationFuel,
            selector: "WIND",
            days: 7,
            format: .json,
            now: Date(timeIntervalSince1970: 1_783_825_200)
        )
        let url = try HTTPInspectionClient.exportURL(
            baseURL: URL(string: "https://unit.test")!,
            request: request
        )
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })

        XCTAssertEqual(components.path, "/v1/export")
        XCTAssertEqual(query["metric"]!, "generation.transmission_visible_by_fuel")
        XCTAssertEqual(query["selector"]!, "WIND")
        XCTAssertEqual(query["resolution"]!, "1800")
        XCTAssertEqual(query["format"]!, "json")
        XCTAssertEqual(Set(query.keys), ["metric", "selector", "from", "to", "resolution", "format"])
    }

    func testSourceLinksAreRestrictedToKnownHTTPSPublisherHosts() {
        XCTAssertNotNil(InspectionPresentation.approvedSourceURL("https://bmrs.elexon.co.uk/api-documentation"))
        XCTAssertNotNil(InspectionPresentation.approvedSourceURL("https://www.elexon.co.uk/about/copyright/"))
        XCTAssertNotNil(InspectionPresentation.approvedSourceURL("https://carbonintensity.org.uk/"))
        XCTAssertNil(InspectionPresentation.approvedSourceURL("http://bmrs.elexon.co.uk"))
        XCTAssertNil(InspectionPresentation.approvedSourceURL("https://elexon.co.uk@example.com"))
        XCTAssertNil(InspectionPresentation.approvedSourceURL("https://example.com/redirect"))
    }

    func testEventIDValidationRequiresNonReversibleStableShape() {
        XCTAssertTrue(InspectionEndpoint.isValidEventID(InspectionFixture.eventID))
        XCTAssertFalse(InspectionEndpoint.isValidEventID("evt_ABCDEF0123456789ABCD"))
        XCTAssertFalse(InspectionEndpoint.isValidEventID("evt_123"))
        XCTAssertFalse(InspectionEndpoint.isValidEventID("../v1/export"))
    }
}

@MainActor
final class InspectionViewModelTests: XCTestCase {
    func testSourceStatusRetainsCacheWhenRefreshFails() async throws {
        let cached = try InspectionFixture.sourceStatus()
        let client = InspectionFakeClient(
            cachedSource: cached,
            sourceResult: .failure(GridAPIError.transport(.notConnectedToInternet))
        )
        let model = SourceStatusViewModel(client: client)

        await model.load()

        XCTAssertEqual(model.response, cached)
        XCTAssertTrue(model.isFromCache)
        XCTAssertNotNil(model.errorMessage)
        XCTAssertFalse(model.isLoading)
    }

    func testEventHistoryRetainsCacheAndRejectsInvalidIDBeforeClientCall() async throws {
        let cached = try InspectionFixture.eventHistory()
        let client = InspectionFakeClient(cachedHistory: cached)
        let model = EventHistoryViewModel(eventID: InspectionFixture.eventID, client: client)

        await model.load()
        XCTAssertEqual(model.history, cached)
        XCTAssertTrue(model.isFromCache)

        let invalid = EventHistoryViewModel(eventID: "not-an-event", client: client)
        await invalid.load()
        XCTAssertNil(invalid.history)
        XCTAssertEqual(invalid.errorMessage, InspectionRequestError.invalidEventID.localizedDescription)
        let historyCalls = await client.eventHistoryCallCount
        XCTAssertEqual(historyCalls, 1)
    }

    func testExportViewModelEnforcesSelectorAndBuildsPreparedArtifact() async throws {
        let schema = try InspectionFixture.exportSchema()
        let url = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("test.csv")
        let artifact = PreparedExport(
            url: url,
            format: .csv,
            metric: .generationFuel,
            requestedFrom: Date(timeIntervalSince1970: 1_783_738_800),
            requestedTo: Date(timeIntervalSince1970: 1_783_825_200),
            expectedRows: 48,
            missingRows: 1,
            preparedAt: Date(timeIntervalSince1970: 1_783_825_210)
        )
        let client = InspectionFakeClient(schemaResult: .success(schema), preparedArtifact: artifact)
        let model = DataExportViewModel(client: client)

        await model.loadSchema()
        model.selectMetric(.generationFuel)

        XCTAssertEqual(model.selectedSelector, "CCGT")
        XCTAssertEqual(try model.request(now: Date(timeIntervalSince1970: 1_783_825_200)).expectedRowCount, 48)

        model.selectedSelector = "WIND"
        await model.prepare(now: Date(timeIntervalSince1970: 1_783_825_200))

        XCTAssertEqual(model.artifact, artifact)
        XCTAssertNil(model.preparationError)
        XCTAssertFalse(model.isPreparing)
        let lastRequest = await client.lastExportRequest
        XCTAssertEqual(lastRequest?.selector, "WIND")
    }
}

actor InspectionFakeClient: InspectionDataProviding {
    let cachedSource: SourceStatusResponse?
    let sourceResult: Result<SourceStatusResponse, Error>
    let cachedHistory: EventHistoryResponse?
    let historyResult: Result<EventHistoryResponse, Error>
    let cachedSchema: ExportSchemaResponse?
    let schemaResult: Result<ExportSchemaResponse, Error>
    let preparedArtifact: PreparedExport?
    private(set) var eventHistoryCallCount = 0
    private(set) var lastExportRequest: ExportRequestSpec?

    init(
        cachedSource: SourceStatusResponse? = nil,
        sourceResult: Result<SourceStatusResponse, Error> = .failure(GridAPIError.transport(.notConnectedToInternet)),
        cachedHistory: EventHistoryResponse? = nil,
        historyResult: Result<EventHistoryResponse, Error> = .failure(GridAPIError.transport(.notConnectedToInternet)),
        cachedSchema: ExportSchemaResponse? = nil,
        schemaResult: Result<ExportSchemaResponse, Error> = .failure(GridAPIError.transport(.notConnectedToInternet)),
        preparedArtifact: PreparedExport? = nil
    ) {
        self.cachedSource = cachedSource
        self.sourceResult = sourceResult
        self.cachedHistory = cachedHistory
        self.historyResult = historyResult
        self.cachedSchema = cachedSchema
        self.schemaResult = schemaResult
        self.preparedArtifact = preparedArtifact
    }

    func cachedSourceStatus() -> SourceStatusResponse? { cachedSource }
    func sourceStatus() throws -> SourceStatusResponse { try sourceResult.get() }
    func cachedEventHistory(eventID: String) -> EventHistoryResponse? { cachedHistory }
    func eventHistory(eventID: String) throws -> EventHistoryResponse {
        eventHistoryCallCount += 1
        return try historyResult.get()
    }
    func cachedExportSchema() -> ExportSchemaResponse? { cachedSchema }
    func exportSchema() throws -> ExportSchemaResponse { try schemaResult.get() }
    func prepareExport(_ request: ExportRequestSpec) throws -> PreparedExport {
        lastExportRequest = request
        guard let preparedArtifact else { throw GridAPIError.invalidResponse }
        return preparedArtifact
    }
}

enum InspectionFixture {
    static let eventID = "evt_bbbbbbbbbbbbbbbbbbbb"
    static let timestamp = Date(timeIntervalSince1970: 1_783_825_200)

    static func sourceStatus() throws -> SourceStatusResponse {
        try GridJSON.decoder.decode(SourceStatusResponse.self, from: sourceStatusData())
    }

    static func sourceStatusData() -> Data {
        Data(
            """
            {
              "schemaVersion":"1.0","evaluatedAt":"2026-07-12T03:00:00Z","sourceCount":2,
              "sources":[
                {
                  "sourceID":"elexon.fuelinst","publisher":"Elexon","dataset":"FUELINST","displayName":"Elexon generation and interconnectors",
                  "documentationURL":"https://bmrs.elexon.co.uk/api-documentation","licenceURL":"https://www.elexon.co.uk/about/copyright/","attribution":"Data supplied by Elexon Limited.",
                  "expectedFactCadenceSeconds":300,"deliveryState":"healthy","deliveryLagSeconds":120,"lastAttemptedAt":"2026-07-12T02:59:30Z","lastAttemptState":"succeeded","lastSucceededAt":"2026-07-12T02:58:00Z",
                  "factState":"live","factFamilies":["generation","interconnectors"],"observedAt":"2026-07-12T02:55:00Z","validTo":null,"factAgeSeconds":300,"note":"Delivery and fact are evaluated separately."
                },
                {
                  "sourceID":"elexon.remit","publisher":"Elexon","dataset":"REMIT","displayName":"Elexon REMIT",
                  "documentationURL":"https://bmrs.elexon.co.uk/api-documentation","licenceURL":null,"attribution":"Data supplied by Elexon Limited.",
                  "expectedFactCadenceSeconds":300,"deliveryState":"delayed","deliveryLagSeconds":700,"lastAttemptedAt":"2026-07-12T02:59:00Z","lastAttemptState":"failed","lastSucceededAt":"2026-07-12T02:48:20Z",
                  "factState":"not_applicable","factFamilies":[],"observedAt":null,"validTo":null,"factAgeSeconds":null,"note":"This source does not directly supply a fact in the current grid view."
                }
              ],
              "definitions":{"deliveryState":"Whether 50Hz receives the source on schedule.","factState":"Whether a source fact covers the live grid view."}
            }
            """.utf8
        )
    }

    static func eventHistory() throws -> EventHistoryResponse {
        try GridJSON.decoder.decode(EventHistoryResponse.self, from: eventHistoryData())
    }

    static func eventHistoryData() -> Data {
        let checksum1 = String(repeating: "1", count: 64)
        let checksum2 = String(repeating: "2", count: 64)
        let checksum3 = String(repeating: "3", count: 64)
        return Data(
            """
            {
              "schemaVersion":"1.0","eventID":"\(eventID)","lifecycleStatus":"resolved","revisionOrder":"newestFirst","revisionCount":3,"returnedRevisionCount":3,"isTruncated":false,
              "firstPublishedAt":"2026-07-10T08:00:00Z","latestPublishedAt":"2026-07-10T10:00:00Z",
              "revisions":[
                {"revisionNumber":3,"status":"resolved","authority":"authoritative_notice","evidenceClass":"reported","publishedAt":"2026-07-10T10:00:00Z","effectiveWindow":{"start":"2026-07-11T08:00:00Z","end":"2026-07-12T08:00:00Z"},"reportedAsset":{"assetID":"10X1001A1001A001","name":"Example Unit 1","identityReliable":true},"reportedCapacity":{"unavailableMW":350,"normalCapacityMW":600},"planned":false,"reportedCause":"Equipment repair work.","materialReason":"Source revised status","supersededByEventID":null,"sourceIDs":["elexon.remit"],"sourceRecordIDs":["elexon:REMIT:example:r3"],"evidenceChecksum":"\(checksum3)","changes":[{"field":"status","before":"updated","after":"resolved"}]},
                {"revisionNumber":2,"status":"updated","authority":"authoritative_notice","evidenceClass":"reported","publishedAt":"2026-07-10T09:00:00Z","effectiveWindow":{"start":"2026-07-11T08:00:00Z","end":"2026-07-12T08:00:00Z"},"reportedAsset":{"assetID":"10X1001A1001A001","name":"Example Unit 1","identityReliable":true},"reportedCapacity":{"unavailableMW":350,"normalCapacityMW":600},"planned":false,"reportedCause":"Equipment repair work.","materialReason":"Capacity revised","supersededByEventID":null,"sourceIDs":["elexon.remit"],"sourceRecordIDs":["elexon:REMIT:example:r2"],"evidenceChecksum":"\(checksum2)","changes":[{"field":"unavailableMW","before":500,"after":350}]},
                {"revisionNumber":1,"status":"open","authority":"authoritative_notice","evidenceClass":"reported","publishedAt":"2026-07-10T08:00:00Z","effectiveWindow":{"start":"2026-07-11T08:00:00Z","end":"2026-07-12T08:00:00Z"},"reportedAsset":{"assetID":"10X1001A1001A001","name":"Example Unit 1","identityReliable":true},"reportedCapacity":{"unavailableMW":500,"normalCapacityMW":600},"planned":false,"reportedCause":"Equipment repair work.","materialReason":null,"supersededByEventID":null,"sourceIDs":["elexon.remit"],"sourceRecordIDs":["elexon:REMIT:example:r1"],"evidenceChecksum":"\(checksum1)","changes":[]}
              ]
            }
            """.utf8
        )
    }

    static func exportSchema() throws -> ExportSchemaResponse {
        try GridJSON.decoder.decode(ExportSchemaResponse.self, from: exportSchemaData())
    }

    static func exportSchemaData() -> Data {
        Data(
            """
            {
              "schemaVersion":"1.0","maxWindowDays":31,"maxRowCount":1488,"resolutionsSeconds":[1800],"formats":["json","csv"],
              "timestampPolicy":"Bounds and rows are timezone-aware UTC instants on exact half-hour boundaries.",
              "missingDataPolicy":"Missing intervals are emitted as insufficient_data rows with no value.",
              "metrics":[
                {"metric":"carbon.intensity.national","selectorRequired":false,"allowedSelectors":[]},
                {"metric":"demand.national_outturn","selectorRequired":false,"allowedSelectors":[]},
                {"metric":"generation.transmission_visible_by_fuel","selectorRequired":true,"allowedSelectors":["CCGT","WIND"]},
                {"metric":"interconnector.flow","selectorRequired":true,"allowedSelectors":["IFA","NEMO"]}
              ]
            }
            """.utf8
        )
    }
}
