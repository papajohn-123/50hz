import Foundation

protocol GridRepository: Sendable {
    func cachedSnapshot() async -> GridSnapshot?
    func cachedTimeline() async -> GridTimeline?
    func cachedRegion(postcode: String) async -> RegionalGridContext?
    func cachedEvents() async -> [GridEvent]?
    func cachedDailyGame() async -> DailyGame?
    func currentSnapshot() async throws -> GridSnapshot
    func timeline() async throws -> GridTimeline
    func region(postcode: String) async throws -> RegionalGridContext
    func events() async throws -> [GridEvent]
    func dailyGame() async throws -> DailyGame
    func event(id: String) async throws -> GridEvent
    func eventExplanation(id: String) async throws -> EventExplanationResponse
    func ask(_ request: AskGridRequest) async throws -> AskGridAnswer
}

extension GridRepository {
    func cachedSnapshot() async -> GridSnapshot? { nil }
    func cachedTimeline() async -> GridTimeline? { nil }
    func cachedRegion(postcode: String) async -> RegionalGridContext? { nil }
    func cachedEvents() async -> [GridEvent]? { nil }
    func cachedDailyGame() async -> DailyGame? { nil }

    func region(postcode: String) async throws -> RegionalGridContext {
        throw GridRepositoryError.unsupportedFeature("Regional data")
    }

    func events() async throws -> [GridEvent] { [] }

    func dailyGame() async throws -> DailyGame {
        throw GridRepositoryError.unsupportedFeature("Daily game")
    }

    func event(id: String) async throws -> GridEvent {
        throw GridRepositoryError.unsupportedFeature("Event details")
    }

    func eventExplanation(id: String) async throws -> EventExplanationResponse {
        throw GridRepositoryError.unsupportedFeature("Event explanations")
    }

    func ask(_ request: AskGridRequest) async throws -> AskGridAnswer {
        throw GridRepositoryError.unsupportedFeature("Ask the Grid")
    }
}

enum GridRepositoryError: LocalizedError {
    case missingFixture(String)
    case invalidFixture(String, Error)
    case unsupportedFeature(String)

    var errorDescription: String? {
        switch self {
        case .missingFixture(let name):
            "The bundled fixture \(name) could not be found."
        case .invalidFixture(let name, let error):
            "The bundled fixture \(name) is invalid: \(error.localizedDescription)"
        case .unsupportedFeature(let feature):
            "\(feature) is not available from this data source."
        }
    }
}

struct FixtureGridRepository: GridRepository {
    private let bundle: Bundle

    init(bundle: Bundle = .main) {
        self.bundle = bundle
    }

    func currentSnapshot() async throws -> GridSnapshot {
        try load(GridSnapshot.self, named: "grid_snapshot")
    }

    func timeline() async throws -> GridTimeline {
        try load(GridTimeline.self, named: "grid_timeline")
    }

    private func load<Value: Decodable>(_ type: Value.Type, named name: String) throws -> Value {
        guard let url = bundle.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
                ?? bundle.url(forResource: name, withExtension: "json") else {
            throw GridRepositoryError.missingFixture(name)
        }

        do {
            let data = try Data(contentsOf: url)
            return try GridJSON.decoder.decode(type, from: data)
        } catch {
            throw GridRepositoryError.invalidFixture(name, error)
        }
    }
}

enum GridJSON {
    static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }
}
