import Foundation

/// Domain vocabulary handed to `SFSpeechRecognitionRequest.contextualStrings`.
///
/// The recognizer's language model is general-purpose, so the terms this
/// curriculum is made of are exactly the ones it gets wrong: real sessions came
/// back with "cash" for *cache*, "TTO" for *TTL*, and "said the signal" for
/// *sent the signal*. `VOICE_TRANSCRIPT_RULE` on the server tells the model to
/// forgive an obvious mis-transcription, and it does — but "TTO" has no
/// plausible reading, and a garbled term the scorer can't recover is a silent
/// hit to a score that is supposed to measure recall.
///
/// Apple recommends keeping this list short; it biases recognition rather than
/// restricting it, and a long list dilutes the bias. So this is not a glossary
/// of the whole curriculum — it is the terms that are both common across cards
/// and acoustically weak (short, homophonous, or initialisms).
enum SpeechVocabulary {
    /// Terms worth biasing on every card, regardless of topic.
    static let standing = [
        // The observed failures.
        "cache", "cached", "caching", "ETag", "ETags", "TTL", "Cache-Control",
        // Initialisms — the weakest class, since each letter is guessed alone.
        "TCP", "TLS", "DNS", "CDN", "RTT", "LRU", "WAL", "API", "SQL", "JWT",
        "gRPC", "REST", "GraphQL", "UUID", "JSON",
        // Short words the recognizer resolves to an everyday homophone.
        "SYN", "ACK", "SYN-ACK", "shard", "sharding", "quorum", "replica",
        "idempotent", "idempotency", "throughput", "latency", "backpressure",
        // Multi-word terms that only cohere if recognized together.
        "consistent hashing", "virtual nodes", "leader election", "write-ahead log",
        "connection pooling", "keep-alive", "round trip", "cache stampede",
        "thundering herd", "eventual consistency", "two-phase commit",
        "exponential backoff", "circuit breaker", "bloom filter", "load balancer",
        "schema migration", "at-least-once", "exactly-once", "dead letter queue",
        "read-through", "write-through", "revalidation", "authoritative nameserver",
    ]

    /// Set once, so the per-tap path only pays a hash lookup per candidate.
    private static let standingSet = Set(standing)

    /// The standing terms plus the words of the card being reviewed, which is the
    /// single best predictor of what is about to be said. Topics are short, so
    /// this stays well inside the length the recognizer handles well.
    ///
    /// The whole topic goes in as one phrase *and* its individual words, so both
    /// "TTL propagation" and a bare "TTL" get the bias.
    static func terms(for topic: String?) -> [String] {
        guard let topic, !topic.isEmpty else { return standing }

        // Splitting on non-alphanumerics rather than spaces: a quarter of the
        // topics carry punctuation ("HTTP caching: ETags, Cache-Control, and
        // revalidation"), and "ETags," is not a term anything will match. The
        // hyphen survives so "write-ahead" and "at-least-once" stay whole.
        let words = topic
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber && $0 != "-" })
            .map(String.init)
            // Function words carry no bias. An initialism is never one, and is
            // the class the recognizer handles worst — so length alone would
            // throw away exactly the terms most worth biasing.
            .filter { $0.count > 3 || $0 == $0.uppercased() }

        // A duplicate spends a slot in a budget that works by staying short —
        // and a one-word topic would otherwise be added as both phrase and word.
        var seen = standingSet
        var additions: [String] = []
        for term in [topic] + words where seen.insert(term).inserted {
            additions.append(term)
        }
        return standing + additions
    }
}
