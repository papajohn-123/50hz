import Foundation
import XCTest
@testable import FiftyHz

final class GridAssetAPIClientTests: XCTestCase {
    override func tearDown() {
        AssetURLProtocolStub.handler = nil
        super.tearDown()
    }

    func testMapRequestIsBoundedToOperationalAssetsAndDecodesProvenance() async throws {
        AssetURLProtocolStub.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            XCTAssertEqual(components.path, "/v1/assets/map")
            XCTAssertEqual(components.queryItems?.first(where: { $0.name == "lifecycle" })?.value, "operational")
            XCTAssertEqual(components.queryItems?.first(where: { $0.name == "limit" })?.value, "5000")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                Data(Self.mapJSON.utf8)
            )
        }

        let client = HTTPGridAssetClient(
            baseURL: URL(string: "https://example.test")!,
            session: session()
        )
        let response = try await client.mapAssets()

        XCTAssertEqual(response.totalCount, 1)
        XCTAssertEqual(response.validLocatedAssets.count, 1)
        let asset = try XCTUnwrap(response.assets.first)
        XCTAssertEqual(asset.name, "Hornsea Project Two")
        XCTAssertEqual(asset.fuel, .wind)
        XCTAssertEqual(asset.coordinate.source.sourceID, "desnz.repd")
        XCTAssertEqual(asset.coordinate.source.sourceRecordID, "6935")
        XCTAssertFalse(asset.operatingEvidence?.hasLiveMeteredOutput ?? true)
        XCTAssertEqual(asset.operatingEvidence?.participantSubmittedPlan?.evidenceKind, .reportedPlan)
    }

    func testDetailRequestUsesOpaqueAssetIDAsOnePathComponent() async throws {
        AssetURLProtocolStub.handler = { request in
            XCTAssertEqual(request.url?.path, "/v1/assets/site_abcdef123")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!,
                Data("{\"detail\":\"Asset not found\"}".utf8)
            )
        }
        let client = HTTPGridAssetClient(
            baseURL: URL(string: "https://example.test")!,
            session: session()
        )

        do {
            _ = try await client.assetDetail(id: "site_abcdef123")
            XCTFail("Expected HTTP error")
        } catch let GridAPIError.httpStatus(code, message, _) {
            XCTAssertEqual(code, 404)
            XCTAssertEqual(message, "Asset not found")
        }
    }

    private func session() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AssetURLProtocolStub.self]
        return URLSession(configuration: configuration)
    }

    private static let mapJSON = """
    {
      "schemaVersion": "1.0",
      "evaluatedAt": "2026-07-15T11:00:00Z",
      "sourceStatus": {
        "state": "current",
        "lastSuccessfulAt": "2026-07-15T06:00:00Z",
        "assetReferenceCount": 3026,
        "locatedAssetCount": 3098
      },
      "totalCount": 1,
      "returnedCount": 1,
      "isTruncated": false,
      "assets": [{
        "id": "site_abc123",
        "name": "Hornsea Project Two",
        "operatorName": "Orsted",
        "technology": "Offshore Wind",
        "fuelType": "wind",
        "lifecycle": "operational",
        "capacityMW": 1386,
        "region": "Yorkshire and Humber",
        "country": "England",
        "coordinate": {
          "latitude": 53.9,
          "longitude": 1.8,
          "precision": "site_point",
          "source": {
            "sourceID": "desnz.repd",
            "publisher": "Department for Energy Security and Net Zero",
            "dataset": "Renewable Energy Planning Database Q1 2026",
            "sourceRecordID": "6935",
            "retrievedAt": "2026-07-15T06:00:00Z",
            "canonicalURL": "https://www.gov.uk/government/publications/renewable-energy-planning-database-quarterly-extract",
            "licence": "Open Government Licence v3.0",
            "attribution": "Contains public sector information licensed under the Open Government Licence v3.0."
          }
        },
        "linkedBMUnitCount": 2,
        "operatingEvidence": {
          "participantSubmittedPlan": {
            "levelMW": 900,
            "at": "2026-07-15T11:00:00Z",
            "direction": "export",
            "evidenceKind": "reported_plan",
            "sourceID": "elexon.pn",
            "retrievedAt": "2026-07-15T11:01:00Z",
            "settlementDate": "2026-07-15",
            "settlementPeriod": 23,
            "caveat": "Participant-submitted plan, not actual output."
          },
          "latestSettledMetered": null,
          "hasLiveMeteredOutput": false
        }
      }],
      "boundary": "Great Britain renewable and storage sites with source-backed coordinates",
      "disclaimer": "Map positions come from DESNZ REPD. Elexon does not supply generator coordinates."
    }
    """
}

private final class AssetURLProtocolStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

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
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
