import SwiftUI

/// Reachable only from Today — not a destination.
struct SettingsSheet: View {
    @EnvironmentObject private var state: AppState
    @State private var draft: AppSettings = .placeholder
    // Device-local, so it stays out of `AppSettings` and off the server: which
    // phone reads questions aloud is not something the scheduler needs to know.
    @AppStorage(Preferences.readAloudKey) private var readAloud = true

    var body: some View {
        SheetChrome(title: "Settings", height: 412) {
            VStack(alignment: .leading, spacing: 22) {
                reviewsPerDay
                readAloudRow
                windows
            }
        }
        .onAppear { draft = state.settings }
        .onDisappear { state.saveSettings(draft) }
    }

    private var readAloudRow: some View {
        HStack(spacing: 12) {
            Toggle34(isOn: $readAloud)
            VStack(alignment: .leading, spacing: 4) {
                Text("Read questions aloud")
                    .font(TypeRole.body)
                    .foregroundStyle(Theme.text)
                Text("Speaks the question when a card opens")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            Spacer()
        }
    }

    private var reviewsPerDay: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Reviews per day")
                    .font(TypeRole.body)
                    .foregroundStyle(Theme.text)
                Text(subline)
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            Spacer()
            StepperControl(
                value: draft.reviewsPerDay,
                decrement: { draft.reviewsPerDay = max(1, draft.reviewsPerDay - 1) },
                increment: { draft.reviewsPerDay = min(6, draft.reviewsPerDay + 1) }
            )
        }
    }

    private var subline: String {
        let enabled = draft.windows.filter(\.on).count
        let pushes = draft.reviewsPerDay == 1 ? "1 push" : "\(draft.reviewsPerDay) pushes"
        return enabled > 1 ? "\(pushes), spread across windows" : "\(pushes), in one window"
    }

    private var windows: some View {
        VStack(alignment: .leading, spacing: 16) {
            MetaText(text: "NOTIFICATION WINDOWS", font: TypeRole.metaBody, tracking: 1.2, color: Theme.metaFaint)

            ForEach($draft.windows) { $window in
                HStack(spacing: 12) {
                    Toggle34(isOn: $window.on)
                    Text(window.label)
                        .font(TypeRole.body)
                        .foregroundStyle(Theme.text)
                    Spacer()
                    TimeChip(time: $window.from)
                    Text("–")
                        .font(TypeRole.metaBody)
                        .foregroundStyle(Theme.metaDim)
                    TimeChip(time: $window.to)
                }
            }

            Text("Tap a time to shift the window. Reviews are pushed inside these ranges only.")
                .font(TypeRole.secondaryAction)
                .foregroundStyle(Theme.metaAlt)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// 34×20 track — accent fill and knob when on, `#4A5257` knob when off.
struct Toggle34: View {
    @Binding var isOn: Bool

    var body: some View {
        Button { isOn.toggle() } label: {
            Capsule()
                .fill(isOn ? Theme.accent : Theme.border)
                .frame(width: 34, height: 20)
                .overlay(alignment: isOn ? .trailing : .leading) {
                    Circle()
                        .fill(isOn ? Theme.accentInk : Theme.toggleKnobOff)
                        .frame(width: 14, height: 14)
                        .padding(.horizontal, 3)
                }
        }
        .buttonStyle(.plain)
        .frame(minWidth: Metrics.minTapTarget, minHeight: Metrics.minTapTarget, alignment: .leading)
    }
}

/// Tapping advances through the allowed times; there is no picker.
struct TimeChip: View {
    @Binding var time: String

    var body: some View {
        Button {
            let times = AppSettings.allowedTimes
            let index = times.firstIndex(of: time) ?? 0
            time = times[(index + 1) % times.count]
        } label: {
            Text(time)
                .font(TypeRole.metaBody)
                .tracking(0.6)
                .foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .overlay(
                    RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.borderStrong, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
    }
}

/// Bottom sheet over a scrim: `#14171A` fill, 20px top radius, 22px padding,
/// 30px bottom. Tapping the scrim closes.
struct SheetChrome<Content: View>: View {
    let title: String
    var serifTitle: Bool = false
    /// Overrides the designed height. Settings needs it — the read-aloud toggle
    /// is a control the handoff never drew, and at the designed 340 the extra
    /// row pushed the title and Close out through the top of the sheet.
    var height: CGFloat? = nil
    @ViewBuilder let content: Content
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(spacing: 22) {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(serifTitle ? TypeRole.sheetTitleSerif : TypeRole.sheetTitle)
                    .foregroundStyle(serifTitle ? Theme.textSerif : Theme.text)
                Spacer()
                Button { state.sheet = nil } label: {
                    Text("Close")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.meta)
                }
                .buttonStyle(.plain)
            }
            content
        }
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.top, Metrics.screenPadding)
        .padding(.bottom, Metrics.bottomSafeArea)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface)
        .presentationDetents([.height(sheetHeight)])
        .presentationDragIndicator(.hidden)
        .presentationBackground(Theme.surface)
        .presentationCornerRadius(Metrics.sheetRadius)
    }

    private var sheetHeight: CGFloat { height ?? (serifTitle ? 380 : 340) }
}
