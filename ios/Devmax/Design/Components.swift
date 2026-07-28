import SwiftUI

/// 44px tall, mono 11px, bottom-aligned. The right slot carries `DEVMAX`
/// normally and `READING ALOUD` while TTS is speaking.
struct StatusBar: View {
    var rightText: String = "DEVMAX"

    var body: some View {
        HStack {
            Text(Self.clock)
            Spacer()
            Text(rightText)
        }
        .font(TypeRole.metaStatus)
        .tracking(0.44)
        .foregroundStyle(Color(hex: 0x616870))
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

/// Bordered − / value / +. Settings' "reviews per day" and Review Sprint's
/// "session size" are the same control at the same geometry in the design, so
/// they are the same component here.
struct StepperControl: View {
    let value: Int
    let decrement: () -> Void
    let increment: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            step("−", action: decrement)
            Text("\(value)")
                .font(WCFont.sans(15, weight: 600))
                .monospacedDigit()
                .foregroundStyle(Theme.text)
                .frame(width: 26)
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

/// The 1px dotted underline on a Today row's topic name — the affordance for
/// "tap for history".
struct DottedUnderline: View {
    var color: Color = Theme.dottedUnderline

    var body: some View {
        Rectangle()
            .fill(.clear)
            .frame(height: 1)
            .overlay(
                Line()
                    .stroke(color, style: StrokeStyle(lineWidth: 1, dash: [1, 2]))
            )
    }
}

private struct Line: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: 0, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.width, y: rect.midY))
        return path
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
            let size = view.sizeThatFits(.unspecified)
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
            let size = view.sizeThatFits(.unspecified)
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
            Text(topic)
                .font(TypeRole.rowTopic)
                .tracking(-0.165)
                .foregroundStyle(Theme.text)
                .fixedSize()
            MetaText(text: category, font: WCFont.mono(10), tracking: 1.0,
                     color: Theme.metaDim, uppercased: true)
                .fixedSize()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
