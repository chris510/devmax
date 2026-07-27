import SwiftUI

/// Every color token from the design handoff. No literal hex anywhere else in the app.
///
/// Dark set only — light mode is in scope for the product but not designed yet.
enum Theme {
    // Surfaces
    static let bg = Color(hex: 0x0D0F11)          // app background (near-black, never pure)
    static let surface = Color(hex: 0x14171A)     // sheets
    static let bubble = Color(hex: 0x171B1E)      // user answer bubble fill
    static let bubbleBorder = Color(hex: 0x1F2427)

    // Lines
    static let hairline = Color(hex: 0x191D20)    // row dividers, section rules
    static let border = Color(hex: 0x21262A)      // secondary button / control borders
    static let borderStrong = Color(hex: 0x23282C)
    static let borderHover = Color(hex: 0x3A4248)

    // Loading placeholders — static blocks, no shimmer.
    static let skeleton1 = Color(hex: 0x1B1F22)
    static let skeleton2 = Color(hex: 0x171A1D)
    static let skeleton3 = Color(hex: 0x14171A)

    // Text
    static let text = Color(hex: 0xE8EAEC)
    static let textStrong = Color(hex: 0xF2F4F5)      // question text
    static let textSerif = Color(hex: 0xDFE3E5)       // feedback / mastery summary
    static let textSecondary = Color(hex: 0xCFD4D8)   // answer bubbles, sheet body, errors
    static let textMuted = Color(hex: 0xA8AFB5)       // live transcript — AA on bg, don't darken
    static let textDim = Color(hex: 0x949BA1)         // row mastery summary
    static let meta = Color(hex: 0x8B9299)
    static let metaAlt = Color(hex: 0x7C848B)
    static let metaDim = Color(hex: 0x6B7378)
    static let metaDimAlt = Color(hex: 0x5E666C)
    static let metaFaint = Color(hex: 0x4E565B)
    static let metaFaintAlt = Color(hex: 0x545C61)

    /// Primary buttons, score indicators, recording ring, caret. **Nothing else.**
    static let accent = Color(hex: 0x57B6C2)
    static let accentHover = Color(hex: 0x6EC6D1)
    static let accentInk = Color(hex: 0x06232A)       // text on accent fill
    static let accentWash = Color(hex: 0x57B6C2).opacity(0.10)
    static let accentLine = Color(hex: 0x26343A)      // resume / inline-error border
    static let accentSurface = Color(hex: 0x10171A)   // resume / inline-error fill
    static let accentSelectedText = Color(hex: 0xCFE9ED)
    /// The second line of a selected category chip on Review Sprint Setup.
    static let accentChipNote = Color(hex: 0x8FC7CF)

    // Score colors are never the only signal — the numeral is always present.
    static let scoreLow = Color(hex: 0xC0705A)   // 0-1
    static let scoreMid = Color(hex: 0xC2A35A)   // 2-3
    static let scoreHigh = Color(hex: 0x6FA77F)  // 4-5
    static let scoreNone = Color(hex: 0x6B7378)  // renders as "—"

    static let scrim = Color(hex: 0x040506).opacity(0.72)
    static let inputFill = Color(hex: 0x0F1214)
    static let toggleKnobOff = Color(hex: 0x4A5257)
    static let dottedUnderline = Color(hex: 0x2C3238)
}

/// Design-handoff spacing and radii, named so screens read as the spec does.
enum Metrics {
    static let screenPadding: CGFloat = 22
    static let conversationPadding: CGFloat = 24
    static let listContainerPadding: CGFloat = 14
    static let rowInset: CGFloat = 8
    static let statusBarHeight: CGFloat = 44
    static let bottomSafeArea: CGFloat = 30

    static let rowTopPadding: CGFloat = 15
    static let rowBottomPadding: CGFloat = 16
    static let scoreColumnGap: CGFloat = 14
    static let scoreColumnWidth: CGFloat = 34

    static let primaryRadius: CGFloat = 14
    static let secondaryRadius: CGFloat = 11
    static let sheetRadius: CGFloat = 20
    static let bubbleRadius: CGFloat = 14
    static let inlineRadius: CGFloat = 12
    static let inputRadius: CGFloat = 12
    static let chipRadius: CGFloat = 8

    static let micDiameter: CGFloat = 76
    static let micHitArea: CGFloat = 84
    /// All bottom actions are at least this tall.
    static let minTapTarget: CGFloat = 44
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}
