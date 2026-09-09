import SwiftUI
import UIKit

/// The three type families from the handoff, with the exact size / leading /
/// tracking pairs the designs call for.
///
/// Resolve the bundled variable-font families through UIKit instead of hardcoding
/// their inconsistent PostScript instance names. SwiftUI owns Dynamic Type and
/// the requested weight; the point sizes remain the default-size design tokens.
enum Typeface {
    static let serif = "Newsreader"
    static let serifItalic = "Newsreader"
    static let sans = "IBM Plex Sans"
    static let mono = "IBM Plex Mono"
}

enum WCFont {
    /// Resolve the bundled face, then let SwiftUI scale it with the reader's
    /// Dynamic Type setting. A fixed UIFont loses that environment behavior.
    static func serif(_ size: CGFloat, weight: CGFloat = 400, italic: Bool = false) -> Font {
        custom(Typeface.serif, size: size, weight: weight, italic: italic)
    }

    static func sans(_ size: CGFloat, weight: CGFloat = 400) -> Font {
        custom(Typeface.sans, size: size, weight: weight)
    }

    static func mono(_ size: CGFloat, weight: CGFloat = 400) -> Font {
        let name: String
        switch weight {
        case ..<450: name = "IBMPlexMono-Regular"
        case ..<550: name = "IBMPlexMono-Medium"
        default: name = "IBMPlexMono-SemiBold"
        }
        return .custom(name, size: size, relativeTo: .body)
    }

    private static func custom(
        _ family: String, size: CGFloat, weight: CGFloat, italic: Bool = false
    ) -> Font {
        var attributes: [UIFontDescriptor.AttributeName: Any] = [.family: family]
        if italic {
            attributes[.traits] = [
                UIFontDescriptor.TraitKey.symbolic: UIFontDescriptor.SymbolicTraits.traitItalic.rawValue
            ]
        }
        let face = UIFont(descriptor: UIFontDescriptor(fontAttributes: attributes), size: size)
        let font = Font.custom(face.fontName, size: size, relativeTo: .body)
        switch weight {
        case ..<450: return font.weight(.regular)
        case ..<550: return font.weight(.medium)
        default: return font.weight(.semibold)
        }
    }
}

// MARK: - Named roles

extension View {
    /// Applies a font plus the design's line height and letter spacing together —
    /// SwiftUI has no line-height modifier, so leading is expressed as the extra
    /// space beyond the font's natural line height.
    func wcType(_ font: Font, lineHeight: CGFloat? = nil, size: CGFloat, tracking: CGFloat = 0) -> some View {
        let spacing = lineHeight.map { ($0 * size) - size * 1.2 } ?? 0
        return self
            .font(font)
            .tracking(tracking)
            .lineSpacing(max(spacing, 0))
    }
}

enum TypeRole {
    // Question voice (serif)
    static let question = WCFont.serif(25, weight: 400)
    static let followUp = WCFont.serif(21, weight: 400)
    static let emptyQueue = WCFont.serif(22, weight: 400)
    static let masterySummary = WCFont.serif(19, weight: 400)
    static let scoreFeedback = WCFont.serif(18.5, weight: 400)
    static let sheetTitleSerif = WCFont.serif(20, weight: 400)
    static let historyTranscriptQuestion = WCFont.serif(17, weight: 400)

    // UI (sans)
    static let screenTitle = WCFont.sans(30, weight: 600)
    static let historyTitle = WCFont.sans(24, weight: 600)
    static let rowTopic = WCFont.sans(16.5, weight: 500)
    static let body = WCFont.sans(15, weight: 400)
    static let bodyLarge = WCFont.sans(15.5, weight: 400)
    static let button = WCFont.sans(15.5, weight: 500)
    static let rowSummary = WCFont.sans(13.5, weight: 400)
    static let secondaryAction = WCFont.sans(13, weight: 400)
    static let sheetTitle = WCFont.sans(17, weight: 600)
    static let scoreNumeral = WCFont.sans(17, weight: 600)
    static let bigScoreNumeral = WCFont.sans(46, weight: 600)
    static let historyScoreNumeral = WCFont.sans(15.5, weight: 600)
    static let historyNote = WCFont.sans(14, weight: 400)
    static let historyAnswer = WCFont.sans(14.5, weight: 400)

    // Metadata (mono, uppercase)
    static let metaLarge = WCFont.mono(12.5, weight: 400)
    static let metaBody = WCFont.mono(12, weight: 400)
    static let metaStatus = WCFont.mono(11, weight: 400)
    static let metaRow = WCFont.mono(10.5, weight: 400)
    static let metaTag = WCFont.mono(10, weight: 400)
    static let metaLabel = WCFont.mono(9.5, weight: 500)
}
