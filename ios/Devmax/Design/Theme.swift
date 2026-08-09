import SwiftUI
import UIKit

/// Semantic colors shared by both appearances. The dark values remain the
/// original handoff exactly; light mode maps the same roles onto warm paper.
enum Theme {
    // Surfaces
    static let bg = adaptive(light: 0xFCFBF8, dark: 0x0D0F11)
    static let surface = adaptive(light: 0xF4F1EA, dark: 0x14171A)
    static let bubble = adaptive(light: 0xF7F4EE, dark: 0x171B1E)
    static let bubbleBorder = adaptive(light: 0xD8D5CE, dark: 0x1F2427)

    // Lines
    static let hairline = adaptive(light: 0xD8D5CE, dark: 0x191D20)
    static let border = adaptive(light: 0xC4C0B7, dark: 0x21262A)
    static let borderStrong = adaptive(light: 0xB7B2A9, dark: 0x23282C)
    static let borderHover = adaptive(light: 0x8E8A82, dark: 0x3A4248)

    // Loading placeholders — static blocks, no shimmer.
    static let skeleton1 = adaptive(light: 0xE2DED5, dark: 0x1B1F22)
    static let skeleton2 = adaptive(light: 0xE9E5DD, dark: 0x171A1D)
    static let skeleton3 = adaptive(light: 0xF0ECE5, dark: 0x14171A)

    // Text. The light metadata floor is #6E7477: it is the first warm-grey
    // target that clears AA at the app's 10px mono minimum on #FCFBF8.
    static let text = adaptive(light: 0x171A1C, dark: 0xE8EAEC)
    static let textStrong = adaptive(light: 0x171A1C, dark: 0xF2F4F5)
    static let textSerif = adaptive(light: 0x25292B, dark: 0xDFE3E5)
    static let textSecondary = adaptive(light: 0x3F4548, dark: 0xCFD4D8)
    static let textMuted = adaptive(light: 0x5E6467, dark: 0xA8AFB5)
    static let textDim = adaptive(light: 0x626A6D, dark: 0x949BA1)
    static let meta = adaptive(light: 0x6E7477, dark: 0x8B9299)
    static let metaAlt = adaptive(light: 0x5E6467, dark: 0x7C848B)
    static let metaDim = adaptive(light: 0x6E7477, dark: 0x6B7378)
    static let metaDimAlt = adaptive(light: 0x626A6D, dark: 0x5E666C)
    static let metaFaint = adaptive(light: 0x6E7477, dark: 0x4E565B)
    static let metaFaintAlt = adaptive(light: 0x626A6D, dark: 0x545C61)
    static let statusBar = adaptive(light: 0x6E7477, dark: 0x616870)

    /// Primary buttons, score indicators, recording ring, caret. Nothing else.
    static let accent = adaptive(light: 0x267F8B, dark: 0x57B6C2)
    static let accentHover = adaptive(light: 0x1F6E78, dark: 0x6EC6D1)
    static let accentInk = adaptive(light: 0xFCFBF8, dark: 0x06232A)
    static let accentWash = accent.opacity(0.10)
    static let accentLine = adaptive(light: 0x8BB8BE, dark: 0x26343A)
    static let accentSurface = adaptive(light: 0xEEF5F5, dark: 0x10171A)
    static let accentSelectedText = adaptive(light: 0x1E6670, dark: 0xCFE9ED)
    static let accentChipNote = adaptive(light: 0x267F8B, dark: 0x8FC7CF)

    // The numeral and text label always remain present; color is reinforcement.
    static let scoreLow = adaptive(light: 0xA34A32, dark: 0xC0705A)
    static let scoreMid = adaptive(light: 0x8A6A12, dark: 0xC2A35A)
    static let scoreDeveloping = adaptive(light: 0x5E7A2E, dark: 0xC2A35A)
    static let scoreHigh = adaptive(light: 0x2F6B43, dark: 0x6FA77F)
    static let scoreNone = adaptive(light: 0x6E7477, dark: 0x6B7378)

    static let scrim = adaptive(
        light: 0x171A1C, dark: 0x040506, lightOpacity: 0.42, darkOpacity: 0.72
    )
    static let inputFill = adaptive(light: 0xF7F4EE, dark: 0x0F1214)
    static let toggleKnobOff = adaptive(light: 0x777D80, dark: 0x4A5257)
    static let dottedUnderline = adaptive(light: 0x9C9A94, dark: 0x2C3238)

    private static func adaptive(
        light: UInt32,
        dark: UInt32,
        lightOpacity: CGFloat = 1,
        darkOpacity: CGFloat = 1
    ) -> Color {
        Color(uiColor: UIColor { traits in
            let isDark = traits.userInterfaceStyle == .dark
            return UIColor(hex: isDark ? dark : light)
                .withAlphaComponent(isDark ? darkOpacity : lightOpacity)
        })
    }
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
    static let minTapTarget: CGFloat = 44
}

extension Color {
    init(hex: UInt32) {
        self.init(uiColor: UIColor(hex: hex))
    }
}

private extension UIColor {
    convenience init(hex: UInt32) {
        self.init(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}
