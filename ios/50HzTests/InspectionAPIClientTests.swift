import Foundation
import XCTest
@testable import FiftyHz

final class InspectionAPIClientTests: XCTestCase {
    override func tearDown() {
        InspectionURLProtocol.handler = nil
        super.tearDown()
    }

    func testSourceStatusDeduplicatesETagRequestAndUsesProtectedCache() async throws {
        let directory = temporaryDirectory("source")
        defer { try? FileManager.default.removeItem(at: directory) }
        let recorder = InspectionRequestRecorder()
        InspectionURLProtocol.handler = { request in
            let count = recorder.record(request)
            if count == 1 {
                Thread.sleep(forTimeInterval: 0.04)
                return (
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: "HTTP/1.1",
                        headerFields: ["ETag": "\"source-v1\""]
                    )!,
                    InspectionFixture.sourceStatusData()
                )
            }
            return (
                HTTPURLResponse(url: request.url!, statusCode: 304, httpVersion: "HTTP/1.1", headerFields: nil)!,
                Data()
            )
        }
        let client = makeClient(directory: directory)

        async let first = client.sourceStatus()
        async let duplicate = client.sourceStatus()
        let (one, two) = try await (first, duplicate)
        let notModified = try await client.sourceStatus()
        let cached = await client.cachedSourceStatus()

        XCTAssertEqual(one, two)
        XCTAssertEqual(notModified, one)
        XCTAssertEqual(cached, one)
        XCTAssertEqual(recorder.requestCount, 2)
        XCTAssertEqual(recorder.paths, ["/v1/sources/status", "/v1/sources/status"])
        XCTAssertEqual(recorder.lastETag, "\"source-v1\"")
        XCTAssertTrue(
            FileManager.default.fileExists(
                atPath: directory.appendingPathComponent("inspection-source-status.json").path
            )
        )
    }

    func testEventHistoryUsesStablePathAndRejectsMismatchedPayload() async throws {
        let directory = temporaryDirectory("event")
        defer { try? FileManager.default.removeItem(at: directory) }
        let recorder = InspectionRequestRecorder()
        InspectionURLProtocol.handler = { request in
            _ = recorder.record(request)
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                InspectionFixture.eventHistoryData()
            )
        }
        let client = makeClient(directory: directory)

        let history = try await client.eventHistory(eventID: InspectionFixture.eventID)
        XCTAssertEqual(history.eventID, InspectionFixture.eventID)
        XCTAssertEqual(recorder.paths, ["/v1/events/\(InspectionFixture.eventID)/history"])

        do {
            _ = try await client.eventHistory(eventID: "../export")
            XCTFail("Expected invalid event identity")
        } catch {
            XCTAssertEqual(error.localizedDescription, InspectionRequestError.invalidEventID.localizedDescription)
        }
        XCTAssertEqual(recorder.requestCount, 1)

        let otherID = "evt_cccccccccccccccccccc"
        do {
            _ = try await client.eventHistory(eventID: otherID)
            XCTFail("Expected mismatched event response")
        } catch {
            XCTAssertEqual(error.localizedDescription, GridAPIError.invalidResponse.localizedDescription)
        }
        let wrongCache = await client.cachedEventHistory(eventID: otherID)
        XCTAssertNil(wrongCache)
    }

    func testCSVExportBuildsOnlyPublishedQueryAndProtectedShareArtifact() async throws {
        let directory = temporaryDirectory("export-cache")
        let exports = temporaryDirectory("export-files")
        defer {
            try? FileManager.default.removeItem(at: directory)
            try? FileManager.default.removeItem(at: exports)
        }
        let recorder = InspectionRequestRecorder()
        let header = "start,end,status,value,unit,classification,metric_id,geography,source_id,source_record_ids,source_methodology_version,materialization_methodology_version,coverage_fraction"
        let rows = (0..<48).map { index in
            "2026-07-11T03:00:00Z,2026-07-11T03:30:00Z,available,\(90 + index).0,gCO2/kWh,estimated,carbon.intensity.national,GB,neso.carbon-intensity-national,carbon:\(index),neso-v1,50hz-v1,1.0"
        }
        let csv = Data(([header] + rows).joined(separator: "\r\n").appending("\r\n").utf8)
        InspectionURLProtocol.handler = { request in
            _ = recorder.record(request)
            if request.url?.path == "/v1/metadata/export-schema" {
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["ETag": "\"schema-v1\""])!,
                    InspectionFixture.exportSchemaData()
                )
            }
            return (
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: [
                        "Content-Type": "text/csv; charset=utf-8",
                        "X-50Hz-Expected-Rows": "48",
                        "X-50Hz-Missing-Rows": "2",
                        "Content-Disposition": "attachment; filename=../../unsafe.csv"
                    ]
                )!,
                csv
            )
        }
        let client = HTTPInspectionClient(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: InspectionDiskCache(directory: directory),
            artifactStore: ExportArtifactStore(directory: exports)
        )
        let request = try ExportRequestSpec.recent(
            metric: .nationalCarbon,
            selector: nil,
            days: 1,
            format: .csv,
            now: InspectionFixture.timestamp
        )

        let artifact = try await client.prepareExport(request)
        let exportURL = try XCTUnwrap(recorder.urls.first(where: { $0.path == "/v1/export" }))
        let components = try XCTUnwrap(URLComponents(url: exportURL, resolvingAgainstBaseURL: false))
        let queryNames = Set((components.queryItems ?? []).map(\.name))

        XCTAssertEqual(queryNames, ["metric", "from", "to", "resolution", "format"])
        XCTAssertEqual(artifact.expectedRows, 48)
        XCTAssertEqual(artifact.missingRows, 2)
        XCTAssertEqual(artifact.url.deletingLastPathComponent(), exports)
        XCTAssertTrue(artifact.url.lastPathComponent.hasPrefix("50Hz-carbon-intensity-national-"))
        XCTAssertFalse(artifact.url.path.contains("unsafe"))
        XCTAssertEqual(try Data(contentsOf: artifact.url), csv)
    }

    func testJSONExportRejectsCoverageOrRequestedBoundsThatDoNotMatchRequest() async throws {
        let directory = temporaryDirectory("json-cache")
        let exports = temporaryDirectory("json-files")
        defer {
            try? FileManager.default.removeItem(at: directory)
            try? FileManager.default.removeItem(at: exports)
        }
        InspectionURLProtocol.handler = { request in
            if request.url?.path == "/v1/metadata/export-schema" {
                return (
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    InspectionFixture.exportSchemaData()
                )
            }
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!,
                Self.mismatchedExportData()
            )
        }
        let client = HTTPInspectionClient(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: InspectionDiskCache(directory: directory),
            artifactStore: ExportArtifactStore(directory: exports)
        )
        let request = try ExportRequestSpec.recent(
            metric: .nationalCarbon,
            selector: nil,
            days: 1,
            format: .json,
            now: InspectionFixture.timestamp
        )

        do {
            _ = try await client.prepareExport(request)
            XCTFail("Expected response/request mismatch")
        } catch {
            XCTAssertEqual(error.localizedDescription, GridAPIError.invalidResponse.localizedDescription)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: exports.path))
    }

    func testUnknownOrUnpublishedSelectorNeverReachesExportRoute() async throws {
        let directory = temporaryDirectory("selector-cache")
        defer { try? FileManager.default.removeItem(at: directory) }
        let recorder = InspectionRequestRecorder()
        InspectionURLProtocol.handler = { request in
            _ = recorder.record(request)
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                InspectionFixture.exportSchemaData()
            )
        }
        let client = makeClient(directory: directory)
        let request = try ExportRequestSpec.recent(
            metric: .generationFuel,
            selector: "../../SECRET",
            days: 1,
            format: .csv,
            now: InspectionFixture.timestamp
        )

        do {
            _ = try await client.prepareExport(request)
            XCTFail("Expected selector allowlist failure")
        } catch {
            XCTAssertEqual(error.localizedDescription, InspectionRequestError.unsupportedSelection.localizedDescription)
        }
        XCTAssertEqual(recorder.paths, ["/v1/metadata/export-schema"])
    }

    func testInspectionCacheRejectsOversizedOrCorruptEntries() async throws {
        let directory = temporaryDirectory("bounded")
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = InspectionDiskCache(directory: directory, maximumEntryBytes: 32)

        do {
            try await cache.store(
                Data(repeating: 0x41, count: 33),
                for: .sourceStatus,
                etag: nil,
                lastModified: nil
            )
            XCTFail("Expected cache size limit")
        } catch {
            XCTAssertEqual(error.localizedDescription, GridAPIError.responseTooLarge.localizedDescription)
        }
        let entry = await cache.entry(for: .sourceStatus)
        XCTAssertNil(entry)
    }

    private func makeClient(directory: URL) -> HTTPInspectionClient {
        HTTPInspectionClient(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: InspectionDiskCache(directory: directory),
            artifactStore: ExportArtifactStore(directory: temporaryDirectory("unused-exports"))
        )
    }

    private func stubSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [InspectionURLProtocol.self]
        configuration.urlCache = nil
        return URLSession(configuration: configuration)
    }

    private func temporaryDirectory(_ name: String) -> URL {
        URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("50HzInspection-\(name)-\(UUID().uuidString)", isDirectory: true)
    }

    private static func mismatchedExportData() -> Data {
        Data(
            """
            {
              "schemaVersion":"1.0","generatedAt":"2026-07-12T03:00:10Z","requestedFrom":"2026-07-11T03:00:00Z","requestedTo":"2026-07-12T03:00:00Z","resolutionSeconds":1800,
              "metricID":"carbon.intensity.national","geography":"GB","unit":"gCO2/kWh","classification":"estimated","sourceID":"neso.carbon-intensity-national","sourceMethodologyVersion":"neso-v1","materializationMethodologyVersion":"50hz-v1",
              "coverage":{"expectedIntervalCount":47,"availableIntervalCount":0,"missingIntervalCount":47,"coverageFraction":0,"isComplete":false},
              "rows":[]
            }
            """.utf8
        )
    }
}

private final class InspectionURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            if !data.isEmpty { client?.urlProtocol(self, didLoad: data) }
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private final class InspectionRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var requests: [URLRequest] = []

    func record(_ request: URLRequest) -> Int {
        lock.withLock {
            requests.append(request)
            return requests.count
        }
    }

    var requestCount: Int { lock.withLock { requests.count } }
    var urls: [URL] { lock.withLock { requests.compactMap(\.url) } }
    var paths: [String] { urls.map(\.path) }
    var lastETag: String? {
        lock.withLock { requests.last?.value(forHTTPHeaderField: "If-None-Match") }
    }
}
