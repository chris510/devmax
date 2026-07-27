import SwiftUI

struct QuickAddSheet: View {
    @EnvironmentObject private var state: AppState
    @State private var topic = ""
    @State private var schedule = "now"
    @FocusState private var focused: Bool

    var body: some View {
        SheetChrome(title: "What do you want to be quizzed on?", serifTitle: true) {
            VStack(alignment: .leading, spacing: 20) {
                TextField("", text: $topic)
                    .font(TypeRole.bodyLarge)
                    .foregroundStyle(Theme.text)
                    .focused($focused)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 13)
                    .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
                    .overlay(
                        RoundedRectangle(cornerRadius: Metrics.inputRadius)
                            .strokeBorder(focused ? Theme.accent : Theme.borderStrong, lineWidth: 1)
                    )

                VStack(alignment: .leading, spacing: 10) {
                    MetaText(text: "SCHEDULE", font: TypeRole.metaBody, tracking: 1.2, color: Theme.metaFaint)
                    HStack(spacing: 10) {
                        segment("Now — top of queue", value: "now")
                        segment("Next review", value: "next")
                    }
                }

                MetaText(
                    text: "DELIVERY · CONVERSATIONAL REVIEW",
                    font: TypeRole.metaBody, tracking: 1.0, color: Theme.metaFaint
                )

                if state.addError {
                    // The sheet stays open and the typed topic is untouched.
                    InlineNotice {
                        Text("Couldn't submit — the topic is still here. Try again.")
                            .font(TypeRole.secondaryAction)
                            .foregroundStyle(Theme.textSecondary)
                            .lineSpacing(2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .wcFade(Motion.fadeFast)
                }

                PrimaryButton(
                    title: state.addPending ? "Adding…" : (state.addError ? "Try again" : "Add card"),
                    enabled: !state.addPending && !topic.trimmingCharacters(in: .whitespaces).isEmpty
                ) {
                    Task { await state.addCard(topic: topic, schedule: schedule) }
                }
            }
        }
    }

    private func segment(_ title: String, value: String) -> some View {
        let selected = schedule == value
        return Button { schedule = value } label: {
            Text(title)
                .font(TypeRole.secondaryAction)
                .foregroundStyle(selected ? Theme.accentSelectedText : Theme.textMuted)
                .padding(.horizontal, 14)
                .padding(.vertical, 11)
                .background(
                    RoundedRectangle(cornerRadius: 10)
                        .fill(selected ? Theme.accentWash : .clear)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(selected ? Theme.accent : Theme.border, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
    }
}
