import SwiftUI

/// The Study Plan has two everyday destinations. Lifecycle and audit actions
/// stay behind More so the primary navigation does not read like a toolbar.
struct PlanNavigationPill: View {
    enum Selection {
        case week, timeline
    }

    let selection: Selection
    let weekLabel: String
    let onWeek: () -> Void
    let onTimeline: () -> Void
    let onPlans: () -> Void
    let onUpdates: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            destination(
                title: weekLabel,
                systemImage: "checklist",
                selected: selection == .week,
                action: onWeek
            )
            destination(
                title: "Timeline",
                systemImage: "point.topleft.down.to.point.bottomright.curvepath",
                selected: selection == .timeline,
                action: onTimeline
            )

            Menu {
                Button("Plans", action: onPlans)
                Button("Updates", action: onUpdates)
            } label: {
                VStack(spacing: 3) {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 13, weight: .semibold))
                    Text("More")
                        .font(WCFont.sans(11.5, weight: 500))
                }
                .foregroundStyle(Theme.textMuted)
                .frame(maxWidth: .infinity)
                .frame(height: 44)
                .contentShape(Rectangle())
            }
            .accessibilityHint("Opens plan and update options.")
        }
        .padding(4)
        .background(Theme.surface, in: Capsule())
        .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
    }

    private func destination(
        title: String,
        systemImage: String,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Image(systemName: systemImage)
                    .font(.system(size: 12, weight: .semibold))
                Text(title)
                    .font(WCFont.sans(11.5, weight: 500))
                    .lineLimit(1)
            }
            .foregroundStyle(selected ? Theme.text : Theme.textMuted)
            .frame(maxWidth: .infinity)
            .frame(height: 44)
            .background(
                selected ? Theme.bubble : Color.clear,
                in: Capsule()
            )
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
