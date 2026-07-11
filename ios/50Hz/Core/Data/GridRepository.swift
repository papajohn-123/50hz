import Foundation

protocol GridRepository: Sendable {
    func currentSnapshot() async throws -> GridSnapshot
    func timeline() async throws -> GridTimeline
}

enum GridRepositoryError: LocalizedError {
    case missingFixture(String)
    case invalidFixture(String, Error)

    var errorDescription: String? {
        switch self {
        case .missingFixture(let name):
            "The bundled fixture \(name) could not be found."
        case .invalidFixture(let name, let error):
            "The bundled fixture \(name) is invalid: \(error.localizedDescription)"
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

