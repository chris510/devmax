import Foundation

enum LocalJSONStore {
    private static func url(for filename: String) -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(filename)
    }

    static func read<Value: Decodable>(_ type: Value.Type, from filename: String) -> Value? {
        guard let data = try? Data(contentsOf: url(for: filename)) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    static func save<Value: Encodable>(_ value: Value, to filename: String) {
        let destination = url(for: filename)
        try? FileManager.default.createDirectory(
            at: destination.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try? JSONEncoder().encode(value).write(to: destination, options: .atomic)
    }

    static func clear(_ filename: String) {
        try? FileManager.default.removeItem(at: url(for: filename))
    }
}
