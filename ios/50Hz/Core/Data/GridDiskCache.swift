import Foundation

struct GridCacheKey: Hashable, Sendable {
    let rawValue: String

    static let current = GridCacheKey(rawValue: "current")
    static let timeline = GridCacheKey(rawValue: "timeline")
    static let events = GridCacheKey(rawValue: "events")
    static let dailyGame = GridCacheKey(rawValue: "game-today")

    static func region(_ postcode: String) -> GridCacheKey {
        let outward = PostcodePrivacy.outwardCode(from: postcode).lowercased()
        return GridCacheKey(rawValue: "region-\(outward)")
    }

    static func localWindows(postcode: String, durationMinutes: Int) -> GridCacheKey {
        let outward = PostcodePrivacy.outwardCode(from: postcode).lowercased()
        return GridCacheKey(rawValue: "local-windows-\(outward)-\(durationMinutes)")
    }
}

struct GridCacheEntry: Sendable {
    let data: Data
    let etag: String?
    let lastModified: String?
    let savedAt: Date
}

actor GridDiskCache {
    private struct Metadata: Codable {
        let etag: String?
        let lastModified: String?
        let savedAt: Date
    }

    private let directory: URL
    private let fileManager: FileManager
    private let maximumEntryBytes = 2_000_000

    init(directory: URL, fileManager: FileManager = .default) {
        self.directory = directory
        self.fileManager = fileManager
    }

    static func production() -> GridDiskCache {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return GridDiskCache(directory: base.appendingPathComponent("50Hz/API", isDirectory: true))
    }

    func entry(for key: GridCacheKey) -> GridCacheEntry? {
        do {
            let data = try Data(contentsOf: dataURL(for: key), options: [.mappedIfSafe])
            guard !data.isEmpty, data.count <= maximumEntryBytes else {
                remove(key)
                return nil
            }
            let metadataData = try Data(contentsOf: metadataURL(for: key))
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            let metadata = try decoder.decode(Metadata.self, from: metadataData)
            return GridCacheEntry(
                data: data,
                etag: metadata.etag,
                lastModified: metadata.lastModified,
                savedAt: metadata.savedAt
            )
        } catch {
            return nil
        }
    }

    func store(
        _ data: Data,
        for key: GridCacheKey,
        etag: String?,
        lastModified: String?,
        savedAt: Date = Date()
    ) throws {
        guard !data.isEmpty, data.count <= maximumEntryBytes else {
            throw GridAPIError.responseTooLarge
        }

        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        try data.write(to: dataURL(for: key), options: [.atomic, .completeFileProtectionUnlessOpen])

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let metadata = Metadata(etag: etag, lastModified: lastModified, savedAt: savedAt)
        try encoder.encode(metadata).write(to: metadataURL(for: key), options: [.atomic, .completeFileProtectionUnlessOpen])
    }

    func remove(_ key: GridCacheKey) {
        try? fileManager.removeItem(at: dataURL(for: key))
        try? fileManager.removeItem(at: metadataURL(for: key))
    }

    private func dataURL(for key: GridCacheKey) -> URL {
        directory.appendingPathComponent("grid-\(key.rawValue).json")
    }

    private func metadataURL(for key: GridCacheKey) -> URL {
        directory.appendingPathComponent("grid-\(key.rawValue).metadata.json")
    }
}
