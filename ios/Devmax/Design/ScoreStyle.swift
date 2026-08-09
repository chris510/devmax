import SwiftUI

/// Score → color. The numeral is always rendered; the dot is decorative
/// reinforcement only, never the sole signal.
enum ScoreStyle {
    static func color(for score: Int?) -> Color {
        guard let score else { return Theme.scoreNone }
        switch score {
        case ...1: return Theme.scoreLow
        case 2: return Theme.scoreMid
        case 3: return Theme.scoreDeveloping
        default: return Theme.scoreHigh
        }
    }

    static func label(for score: Int?) -> String {
        score.map(String.init) ?? "—"
    }

    /// The Today mastery-distribution bands, derived client-side from each due
    /// card's last score. Distinct from the backend's `/cards/overview` tiers,
    /// which answer a different question and have no screen in this design.
    enum Band: String, CaseIterable, Identifiable {
        /// Declaration order is display order: shaky, cold, solid, unrated.
        case shaky, cold, solid, unrated

        var id: String { rawValue }

        static func of(_ score: Int?) -> Band {
            guard let score else { return .unrated }
            switch score {
            case ...1: return .cold
            case 2...3: return .shaky
            default: return .solid
            }
        }

        var color: Color {
            switch self {
            case .cold: return Theme.scoreLow
            case .shaky: return Theme.scoreMid
            case .solid: return Theme.scoreHigh
            case .unrated: return Theme.scoreNone
            }
        }
    }

    /// Coverage's five tiers — a *third* vocabulary, and deliberately so.
    ///
    /// `Band` above answers "how is today's queue distributed" in four coarse
    /// buckets; this answers "where does the library need more cards" and splits
    /// the middle so `developing` (a 3) reads differently from `shaky` (a 2). It
    /// shares its names with the backend's `/cards/overview` tiers but not their
    /// definitions — those fold in ease factor and lapse timing, these come from
    /// the last score alone. Do not merge the three.
    enum Tier: String, CaseIterable, Identifiable {
        /// Declaration order is display order.
        case cold, shaky, developing, solid, untested

        var id: String { rawValue }

        static func of(_ score: Int?) -> Tier {
            guard let score else { return .untested }
            switch score {
            case ...1: return .cold
            case 2: return .shaky
            case 3: return .developing
            default: return .solid
            }
        }

        var color: Color {
            switch self {
            case .cold: return Theme.scoreLow
            case .shaky: return Theme.scoreMid
            case .developing: return Theme.scoreDeveloping
            case .solid: return Theme.scoreHigh
            case .untested: return Theme.scoreNone
            }
        }
    }
}

/// The row score column: numeral above, 6px dot below, both in the score color.
struct ScoreColumn: View {
    let score: Int?

    var body: some View {
        VStack(spacing: 5) {
            Text(ScoreStyle.label(for: score))
                .font(TypeRole.scoreNumeral)
                .monospacedDigit()
                .foregroundStyle(ScoreStyle.color(for: score))
            Circle()
                .fill(ScoreStyle.color(for: score))
                .frame(width: 6, height: 6)
        }
        .padding(.top, 2)
        .frame(width: Metrics.scoreColumnWidth, alignment: .center)
    }
}
