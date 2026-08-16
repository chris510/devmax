import SwiftUI

/// Reachable only from Today, not a destination.
struct SettingsSheet: View {
    @EnvironmentObject private var state: AppState
    @State private var draft: AppSettings = .placeholder
    @AppStorage(Preferences.readAloudKey) private var readAloud = true
    @State private var draftReadAloud = true

    var body: some View {
        SheetChrome(title: "Settings", height: 392) {
            VStack(alignment: .leading, spacing: 14) {
                QuietPanel {
                    readAloudRow
                    Hairline()
                    destinationRow(
                        "Review reminders",
                        value: SettingsValidation.reminderValue(for: draft),
                        screen: .reviewReminders
                    )
                }

                PrimaryButton(
                    title: "Save changes",
                    enabled: isDirty
                ) {
                    save()
                }

                destinationRow("More settings", value: "", screen: .fullSettings)
            }
        }
        .onAppear {
            draft = state.settings
            draftReadAloud = readAloud
        }
        .interactiveDismissDisabled(isDirty)
    }

    private var readAloudRow: some View {
        HStack(spacing: 12) {
            Toggle34(
                isOn: $draftReadAloud,
                accessibilityLabel: "Read questions aloud"
            )
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
        .frame(minHeight: 62)
    }

    private var isDirty: Bool {
        draftReadAloud != readAloud
    }

    private func destinationRow(_ title: String, value: String, screen: AppState.Screen) -> some View {
        Button { open(screen) } label: {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(title)
                    .font(WCFont.sans(14.5, weight: 500))
                    .foregroundStyle(Theme.text)
                Spacer(minLength: 8)
                if !value.isEmpty {
                    Text(value)
                        .font(WCFont.mono(10.5))
                        .foregroundStyle(Theme.metaAlt)
                }
                Text("›")
                    .font(WCFont.sans(17))
                    .foregroundStyle(Theme.metaFaint)
            }
            .frame(minHeight: 48)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func open(_ screen: AppState.Screen) {
        state.sheet = nil
        state.path.append(screen)
    }

    private func save() {
        readAloud = draftReadAloud
        state.sheet = nil
    }
}

enum SettingsValidation {
    static let minimumWindowMinutes = 30

    static func message(for settings: AppSettings) -> String? {
        guard (1...6).contains(settings.reviewsPerDay) else {
            return "Reviews per day must be between 1 and 6."
        }
        for window in settings.windows {
            if let issue = windowMessage(window) { return "\(window.label): \(issue)" }
        }
        return nil
    }

    static func windowMessage(_ window: NotificationWindow) -> String? {
        guard let start = minutes(window.from), let end = minutes(window.to) else {
            return "Choose a valid start and end time."
        }
        guard end - start >= minimumWindowMinutes else {
            return "End must be at least 30 minutes after start."
        }
        return nil
    }

    static func normalizedReminderSettings(_ settings: AppSettings) -> AppSettings {
        var value = settings
        value.reviewsPerDay = max(1, settings.windows.filter(\.on).count)
        return value
    }

    static func reminderValue(for settings: AppSettings) -> String {
        switch settings.windows.filter(\.on).count {
        case 0: "Off"
        case 1: "Up to 1"
        case let count: "Up to \(count)"
        }
    }

    static func dateBinding(for value: Binding<String>) -> Binding<Date> {
        Binding(
            get: { date(from: value.wrappedValue) },
            set: { value.wrappedValue = string(from: $0) }
        )
    }

    private static func minutes(_ text: String) -> Int? {
        let parts = text.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2,
              let hour = Int(parts[0]), let minute = Int(parts[1]),
              (0...23).contains(hour), (0...59).contains(minute)
        else { return nil }
        return hour * 60 + minute
    }

    private static func date(from text: String) -> Date {
        let total = minutes(text) ?? 8 * 60
        let start = Calendar.current.startOfDay(for: Date())
        return Calendar.current.date(byAdding: .minute, value: total, to: start) ?? start
    }

    private static func string(from date: Date) -> String {
        let components = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", components.hour ?? 0, components.minute ?? 0)
    }
}

enum SettingsNavigation {
    static func reviewRemindersBackLabel(for path: [AppState.Screen]) -> String {
        guard path.count > 1, path[path.count - 2] == .fullSettings else {
            return "← Today"
        }
        return "← Settings"
    }

    @discardableResult
    static func popReviewRemindersIfPresented(from path: inout [AppState.Screen]) -> Bool {
        guard path.last == .reviewReminders else { return false }
        path.removeLast()
        return true
    }
}

/// 34×20 track with accent fill and knob when on, `#4A5257` knob when off.
struct Toggle34: View {
    @Binding var isOn: Bool
    let accessibilityLabel: String

    init(isOn: Binding<Bool>, accessibilityLabel: String = "Toggle") {
        _isOn = isOn
        self.accessibilityLabel = accessibilityLabel
    }

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
        .accessibilityLabel(Text(accessibilityLabel))
        .accessibilityValue(Text(isOn ? "On" : "Off"))
        .accessibilityRepresentation {
            Toggle(isOn: $isOn) {
                Text(accessibilityLabel)
            }
            .accessibilityValue(Text(isOn ? "On" : "Off"))
        }
    }
}

/// Bottom sheet over a scrim: `#14171A` fill, 20px top radius, 22px padding,
/// 30px bottom. Tapping the scrim closes.
struct SheetChrome<Content: View>: View {
    let title: String
    var serifTitle: Bool = false
    /// Overrides the designed height. Settings needs it because the read-aloud toggle
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
