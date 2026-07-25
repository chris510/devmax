import CoreText
import SwiftUI
import UIKit

/// The three type families from the handoff, with the exact size / leading /
/// tracking pairs the designs call for.
///
/// Newsreader and IBM Plex Sans ship as variable fonts, so weights are selected
/// by setting the `wght` axis rather than by PostScript name — the named
/// instances are inconsistently named upstream (`Newsreader16pt-Regular` next to
/// `NewsreaderRoman-Medium`) and would be fragile to hard-code.
enum Typeface {
    static let serif = "Newsreader"
    static let serifItalic = "Newsreader"
    static let sans = "IBM Plex Sans"
    static let mono = "IBM Plex Mono"
}

private enum Axis {
    static let weight: UInt32 = 0x77676874  // 'wght'
    static let opticalSize: UInt32 = 0x6F70737A  // 'opsz'
}

enum WCFont {
    /// Serif — the "question voice". Optical size tracks the point size, which is
    /// what a browser does automatically and what the prototype was rendered with.
    static func serif(_ size: CGFloat, weight: CGFloat = 400, italic: Bool = false) -> Font {
        let family = italic ? Typeface.serifItalic : Typeface.serif
        return Font(
            variableFont(
                family: family,
                size: size,
                axes: [Axis.weight: weight, Axis.opticalSize: min(max(size, 6), 72)],
                italic: italic
            )
        )
    }

    static func sans(_ size: CGFloat, weight: CGFloat = 400) -> Font {
        Font(variableFont(family: Typeface.sans, size: size, axes: [Axis.weight: weight]))
    }

    /// Mono is shipped as static instances, so it selects by PostScript name.
    static func mono(_ size: CGFloat, weight: CGFloat = 400) -> Font {
        let name: String
        switch weight {
        case ..<450: name = "IBMPlexMono-Regular"
        case ..<550: name = "IBMPlexMono-Medium"
        default: name = "IBMPlexMono-SemiBold"
        }
        return Font(UIFont(name: name, size: size) ?? .monospacedSystemFont(ofSize: size, weight: .regular))
    }

    private static func variableFont(
        family: String,
        size: CGFloat,
        axes: [UInt32: CGFloat],
        italic: Bool = false
    ) -> UIFont {
        var attributes: [UIFontDescriptor.AttributeName: Any] = [
            .family: family,
            UIFontDescriptor.AttributeName(rawValue: kCTFontVariationAttribute as String): axes,
        ]
        if italic {
            attributes[.traits] = [
                UIFontDescriptor.TraitKey.symbolic: UIFontDescriptor.SymbolicTraits.traitItalic.rawValue
            ]
        }
        let descriptor = UIFontDescriptor(fontAttributes: attributes)
        return UIFont(descriptor: descriptor, size: size)
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
