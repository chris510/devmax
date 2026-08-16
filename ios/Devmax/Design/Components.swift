import SwiftUI

/// 44px tall, mono 11px, bottom-aligned. The right slot carries `UNPROMPTED`
/// normally and `READING ALOUD` while TTS is speaking.
struct StatusBar: View {
    var rightText: String = "UNPROMPTED"

    var body: some View {
        HStack {
            Text(Self.clock)
            Spacer()
            Text(rightText)
        }
        .font(TypeRole.metaStatus)
        .tracking(0.44)
        .foregroundStyle(Theme.statusBar)
        .frame(height: Metrics.statusBarHeight, alignment: .bottom)
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.bottom, 6)
    }

    private static var clock: String {
        let f = DateFormatter()
        f.dateFormat = "H:mm"
        return f.string(from: Date())
    }
}

struct Hairline: View {
    var body: some View {
        Rectangle().fill(Theme.hairline).frame(height: 1)
    }
}

/// A low-contrast container for related controls. The panel is selective: it
/// groups a section, while review queues and conversation turns stay flat.
struct QuietPanel<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        VStack(spacing: 0) { content }
            .padding(.horizontal, 15)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(Theme.bubbleBorder, lineWidth: 1)
            )
    }
}

/// Accent fill, `#06232A` text, 17px vertical padding, 14px radius.
struct PrimaryButton: View {
    let title: String
    var enabled: Bool = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(WCFont.sans(16, weight: 600))
                .tracking(-0.16)
                .foregroundStyle(Theme.accentInk)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 17)
                .background(Theme.accent, in: RoundedRectangle(cornerRadius: Metrics.primaryRadius))
        }
        .buttonStyle(.plain)
        .opacity(enabled ? 1 : 0.55)
        .disabled(!enabled)
    }
}

/// 1px `#21262A` border, 11px radius.
struct SecondaryButton: View {
    let title: String
    var fillsWidth: Bool = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textMuted)
                .frame(maxWidth: fillsWidth ? .infinity : nil)
                .padding(.vertical, 11)
                .padding(.horizontal, fillsWidth ? 0 : 18)
                .background(
                    RoundedRectangle(cornerRadius: Metrics.secondaryRadius)
                        .strokeBorder(Theme.border, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .frame(minHeight: Metrics.minTapTarget)
    }
}

/// One quiet mono line of counts, each segment tappable: `2 shaky · 1 cold`.
///
/// Today's mastery-band filter and Coverage's per-category tier line are the same
/// control over different vocabularies — the open segment switches from
/// `meta-alt` to its own colour and gains a 1px underline. The vocabularies stay
/// separate (that invariant is about meaning, not pixels); only the rendering is
/// shared, so the treatment can't drift between the two screens.
///
/// Not a dashboard: no percentages, no bars, no second line.
struct CountSegments: View {
    struct Segment: Identifiable {
        let id: String
        let text: String
        let color: Color
        let isActive: Bool
    }

    let segments: [Segment]
    let onTap: (Segment) -> Void

    var body: some View {
        FlowLayout(horizontalSpacing: 7, verticalSpacing: 3) {
            ForEach(Array(segments.enumerated()), id: \.element.id) { index, segment in
                Button { onTap(segment) } label: {
                    VStack(spacing: 1) {
                        Text(segment.text + (index < segments.count - 1 ? " ·" : ""))
                            .font(WCFont.mono(11))
                            .tracking(0.33)
                            .foregroundStyle(segment.isActive ? segment.color : Theme.metaAlt)
                        Rectangle()
                            .fill(segment.isActive ? segment.color : .clear)
                            .frame(height: 1)
                    }
                    .fixedSize()
                }
                .buttonStyle(.plain)
            }
        }
    }
}

/// Bordered − / value / + used by compact numeric controls such as Review
/// Sprint's session size.
struct StepperControl: View {
    /// The rendered readout, not the raw number. Study Plan's steppers show
    /// units ("12 weeks", "7h · 420 min") where the two original call sites show
    /// a bare count, so the label is the parameter and the Int initializer below
    /// keeps those two unchanged.
    let value: String
    let decrement: () -> Void
    let increment: () -> Void

    init(value: String, decrement: @escaping () -> Void, increment: @escaping () -> Void) {
        self.value = value
        self.decrement = decrement
        self.increment = increment
    }

    init(value: Int, decrement: @escaping () -> Void, increment: @escaping () -> Void) {
        self.init(value: "\(value)", decrement: decrement, increment: increment)
    }

    var body: some View {
        HStack(spacing: 4) {
            step("−", action: decrement)
            Text(value)
                .font(WCFont.sans(15, weight: 600))
                .monospacedDigit()
                .foregroundStyle(Theme.text)
                // A minimum rather than a fixed width: the bare-count call sites
                // keep their geometry and a unit label is not clipped.
                .frame(minWidth: 26)
                .padding(.horizontal, 4)
                // The readout is what changes when a step is tapped.
                .accessibilityAddTraits(.updatesFrequently)
            step("+", action: increment)
        }
        .padding(4)
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Theme.borderStrong, lineWidth: 1))
    }

    private func step(_ glyph: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(glyph)
                .font(WCFont.sans(16))
                .foregroundStyle(Theme.textMuted)
                .frame(width: 32, height: 30)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// The resume banner and the inline submit-failure strip share this treatment —
/// bordered accent-surface, 12px radius. No red, no icon, no toast.
struct InlineNotice<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        content
            .padding(.horizontal, 15)
            .padding(.vertical, 14)
            .background(Theme.accentSurface, in: RoundedRectangle(cornerRadius: Metrics.inlineRadius))
            .overlay(
                RoundedRectangle(cornerRadius: Metrics.inlineRadius)
                    .strokeBorder(Theme.accentLine, lineWidth: 1)
            )
    }
}

/// Mono, wide-tracked — the metadata voice.
///
/// Casing is *not* forced. Only the category tag is uppercased by style; every
/// other mono string is written in the case the design shows it in, and several
/// are deliberately lowercase ("3 days overdue", "2 shaky · 1 cold").
struct MetaText: View {
    let text: String
    var font: Font = TypeRole.metaRow
    var tracking: CGFloat = 0.8
    var color: Color = Theme.metaDim
    var uppercased: Bool = false

    var body: some View {
        Text(uppercased ? text.uppercased() : text)
            .font(font)
            .tracking(tracking)
            .foregroundStyle(color)
    }
}

/// Wrapping mono chips with 8px column / 3px row gaps, each `white-space: nowrap`.
struct WrappingChips: View {
    let chips: [Chip]

    struct Chip: Identifiable {
        let id = UUID()
        let text: String
        var color: Color = Theme.metaDimAlt
    }

    var body: some View {
        FlowLayout(horizontalSpacing: 8, verticalSpacing: 3) {
            ForEach(chips) { chip in
                MetaText(text: chip.text, font: WCFont.mono(10.5), tracking: 0.42, color: chip.color)
                    .fixedSize()
            }
        }
    }
}

/// Minimal flow layout — chips and mastery bands wrap rather than truncate.
struct FlowLayout: Layout {
    var horizontalSpacing: CGFloat = 8
    var verticalSpacing: CGFloat = 3

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for view in subviews {
            // Measured against the row width, not `.unspecified`. An unbounded
            // proposal makes a long `Text` report its full single-line width, and
            // the layout then places it past both edges instead of wrapping it —
            // invisible with the short fixture topics, obvious with real ones.
            let size = view.sizeThatFits(ProposedViewSize(width: width, height: nil))
            if x > 0, x + size.width > width {
                x = 0
                y += rowHeight + verticalSpacing
                rowHeight = 0
            }
            x += size.width + horizontalSpacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: proposal.width ?? x, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(ProposedViewSize(width: bounds.width, height: nil))
            if x > bounds.minX, x + size.width > bounds.maxX {
                x = bounds.minX
                y += rowHeight + verticalSpacing
                rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + horizontalSpacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

/// A row's topic with its category set inline after it, dropping to its own line
/// when the topic is long. Shared by Review Sprint's preview rows and Session
/// Recap's — `sprint-setup-default.png` and `session-recap.png` show both
/// behaviours, and the wrap is why this can't be an `HStack`: that compresses
/// the topic instead, wrapping the title and stranding the category beside it.
///
/// Deliberately takes no styling parameters. A caller that needs different
/// values wants a different component, not a knob on this one.
struct TopicWithCategory: View {
    let topic: String
    let category: String

    var body: some View {
        FlowLayout(horizontalSpacing: 8, verticalSpacing: 2) {
            // No `.fixedSize()` on the topic: it has to be free to wrap when it
            // is wider than the row. The category keeps one, since a two-line
            // category tag is never right.
            Text(topic)
                .font(TypeRole.rowTopic)
                .tracking(-0.165)
                .foregroundStyle(Theme.text)
            MetaText(text: category, font: WCFont.mono(10), tracking: 1.0,
                     color: Theme.metaDim, uppercased: true)
                .fixedSize()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
