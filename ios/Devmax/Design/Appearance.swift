import SwiftUI

enum AppAppearance: String, CaseIterable, Identifiable {
    case light
    case dark
    case system

    var id: String { rawValue }
    var title: String { rawValue.capitalized }

    var colorScheme: ColorScheme? {
        switch self {
        case .light: .light
        case .dark: .dark
        case .system: nil
        }
    }
}

struct AppearanceSelector: View {
    @Binding var selection: String

    var body: some View {
        HStack(spacing: 3) {
            ForEach(AppAppearance.allCases) { option in
                AppearanceOptionButton(
                    option: option,
                    selected: isSelected(option)
                ) {
                    selection = option.rawValue
                }
            }
        }
        .padding(3)
        .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: 11))
        .overlay(RoundedRectangle(cornerRadius: 11).strokeBorder(Theme.borderStrong, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Appearance")
    }

    private func isSelected(_ option: AppAppearance) -> Bool {
        selection == option.rawValue
    }
}

private struct AppearanceOptionButton: View {
    let option: AppAppearance
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(option.title)
                .font(WCFont.sans(13.5, weight: 500))
                .foregroundStyle(selected ? Theme.text : Theme.metaAlt)
                .frame(maxWidth: .infinity, minHeight: Metrics.minTapTarget)
                .background(selected ? Theme.bg : Color.clear)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(selectedBorder)
        }
        .buttonStyle(.plain)
        .accessibilityValue(selected ? "Selected" : "Not selected")
    }

    @ViewBuilder private var selectedBorder: some View {
        if selected {
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(Theme.border, lineWidth: 1)
        }
    }
}
