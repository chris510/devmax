import SwiftUI

/// "What am I studying this week?"
///
/// The header carries exactly two facts and one capacity affordance. Everything
/// that used to sit up here — what retrieval blocks, where reviews happen — now
/// lives under the Retrieve section, next to the thing it describes.
struct PlanWeekScreen: View {
    let planID: UUID
    let index: Int
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch plan.weekLoad {
                    case .loading, .idle:
                        LoadingList(label: "LOADING WEEK", inset: 0, separator: .overlay)
                    case .error:
                        LoadFailure { Task { await plan.loadWeek(planID, index: index) } }
                    case .ready:
                        if let week = plan.week {
                            ForEach(week.sections) { section in
                                sectionView(section)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 20)
            }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task { await plan.loadWeek(planID, index: index) }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { state.path.removeLast() } label: {
                Text("← Study plan")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            VStack(alignment: .leading, spacing: 5) {
                Text("Week \(index)")
                    .font(TypeRole.screenTitle)
                    .tracking(-0.6)
                    .foregroundStyle(Theme.text)
                    .accessibilityAddTraits(.isHeader)

                if let week = plan.week {
                    Text(week.displayTitle)
                        .font(WCFont.sans(15))
                        .foregroundStyle(Theme.textSecondary)
                        .accessibilityLabel(week.fullTitle)

                    // Two facts. Not four.
                    Text(week.coreLine)
                        .font(WCFont.sans(13.5))
                        .foregroundStyle(Theme.textMuted)

                    Button { state.sheet = .planCapacity } label: {
                        HStack(spacing: 6) {
                            Text(week.capacityLine)
                                .font(WCFont.sans(13.5))
                                .foregroundStyle(Theme.textMuted)
                            Text("→")
                                .font(WCFont.mono(11))
                                .foregroundStyle(Theme.accent)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    // The negative margin keeps the line optically aligned with
                    // the two above it while still clearing 44px.
                    .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                    .padding(.vertical, -12)
                    .accessibilityLabel("\(week.capacityLine). Opens the capacity editor.")
                }
            }
            .padding(.top, 4)
            .padding(.bottom, 16)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
    }

    private func sectionView(_ section: WeekSection) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                MetaText(text: section.label, font: WCFont.mono(10), tracking: 1.2,
                         color: Theme.meta)
                Text(section.aside)
                    .font(WCFont.sans(12.5))
                    .foregroundStyle(Theme.textDim)
            }
            .padding(.top, 18)
            .padding(.bottom, 4)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)

            ForEach(section.rows) { row in
                Hairline()
                ItemRow(row: row) {
                    state.path.append(.planItem(planID, row.id))
                }
            }
            Hairline()

            // Relocated from the week header: it describes the boundary between
            // this section and the review queue, so it belongs beside it.
            if !section.note.isEmpty {
                Text(section.note)
                    .font(WCFont.sans(12.5))
                    .foregroundStyle(Theme.textDim)
                    .lineSpacing(12.5 * 1.45 - 12.5 * 1.2)
                    .padding(.top, 10)
            }
        }
    }
}

private struct ItemRow: View {
    let row: WeekItemRow
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(alignment: .top, spacing: 12) {
                // Decoration, not a control: the row is the button, so there is
                // no nested target and nothing for VoiceOver to land on twice.
                RoundedRectangle(cornerRadius: 2)
                    .fill(row.complete ? Theme.accent : Color.clear)
                    .overlay(
                        RoundedRectangle(cornerRadius: 2)
                            .strokeBorder(
                                row.complete ? Color.clear : Theme.borderStrong, lineWidth: 1
                            )
                    )
                    .frame(width: 11, height: 11)
                    .padding(.top, 4)
                    .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 3) {
                    Text(row.title)
                        .font(WCFont.sans(15))
                        .foregroundStyle(row.complete ? Theme.textMuted : Theme.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    MetaText(text: row.metaLine.uppercased(), font: WCFont.mono(10),
                             tracking: 0.6, color: Theme.metaFaint)
                    if let blockedBy = row.blockedBy {
                        MetaText(text: "AFTER · \(blockedBy.uppercased())",
                                 font: WCFont.mono(10), tracking: 0.6,
                                 color: Theme.metaFaintAlt)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(minHeight: Metrics.minTapTarget)
        .accessibilityLabel(
            "\(row.complete ? "Complete" : "Not complete"). \(row.title). \(row.metaLine)."
        )
        .accessibilityHint("Opens item detail.")
    }
}
