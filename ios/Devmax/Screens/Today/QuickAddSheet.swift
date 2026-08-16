import SwiftUI

/// Capture is intentionally cheaper than card authoring. It writes an inbox
/// item immediately; source, rubric, question, and schedule come later.
struct QuickAddSheet: View {
    @EnvironmentObject private var state: AppState
    @State private var topic = ""
    @State private var context = ""
    @FocusState private var focusedField: Field?

    private enum Field { case topic, context }

    var body: some View {
        SheetChrome(
            title: state.savedCapture == nil ? "Capture a gap" : "Saved for review.",
            serifTitle: true
        ) {
            if let capture = state.savedCapture {
                confirmation(capture)
            } else {
                form
            }
        }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 20) {
            field("TOPIC", text: $topic, field: .topic, axis: .horizontal)
            field("CONTEXT · OPTIONAL", text: $context, field: .context, axis: .vertical)

            MetaText(
                text: "Not added to your review queue yet.",
                font: TypeRole.metaBody, tracking: 0.7, color: Theme.metaFaint
            )

            if state.addError {
                InlineNotice {
                    Text("Couldn't save. The gap is still here. Try again.")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .wcFade(Motion.fadeFast)
            }

            PrimaryButton(
                title: state.addPending ? "Saving…" : (state.addError ? "Try again" : "Save for review"),
                enabled: !state.addPending
                    && !topic.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ) {
                Task { await state.saveCapture(topic: topic, context: context) }
            }
        }
        .onAppear { focusedField = .topic }
    }

    private func confirmation(_ capture: PendingCapture) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(capture.topic)
                .font(TypeRole.bodyLarge)
                .foregroundStyle(Theme.text)
            Text("A trusted source is still required before this can enter reviews.")
                .font(TypeRole.rowSummary)
                .foregroundStyle(Theme.textDim)
                .fixedSize(horizontal: false, vertical: true)

            PrimaryButton(title: "Review now") { state.openCapture(capture.id) }
            SecondaryButton(title: "Done") {
                state.savedCapture = nil
                state.sheet = nil
            }
        }
    }

    private func field(
        _ label: String, text: Binding<String>, field: Field, axis: Axis
    ) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            MetaText(text: label, font: TypeRole.metaBody, tracking: 1.1, color: Theme.metaFaint)
            TextField("", text: text, axis: axis)
                .font(TypeRole.bodyLarge)
                .foregroundStyle(Theme.text)
                .focused($focusedField, equals: field)
                .lineLimit(axis == .vertical ? 2...4 : 1...1)
                .padding(.horizontal, 14)
                .padding(.vertical, 13)
                .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: Metrics.inputRadius)
                        .strokeBorder(
                            focusedField == field ? Theme.accent : Theme.borderStrong,
                            lineWidth: 1
                        )
                )
        }
    }
}
