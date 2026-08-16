import SwiftUI

struct AIConsentScreen: View {
    @EnvironmentObject private var auth: AuthState
    @State private var working = false

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: "PRIVACY CHOICE")
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("AI processing")
                        .font(WCFont.serif(30))
                        .foregroundStyle(Theme.textStrong)
                        .accessibilityAddTraits(.isHeader)
                    Text(
                        "Unprompted uses Anthropic for guide processing, question generation, "
                            + "and optional coaching. Answers you submit may be scored by "
                            + "Anthropic or OpenAI. During a limited provider evaluation, the "
                            + "same answer may be sent to both while only Anthropic decides "
                            + "the result you see."
                    )
                        .font(WCFont.sans(15))
                        .foregroundStyle(Theme.textSecondary)
                        .lineSpacing(4)

                    disclosure(
                        "WHEN PROCESSING A GUIDE",
                        "The guide text, its title, and the plan length, weekly capacity, "
                            + "mode, deadline, and subject hints you chose are sent to "
                            + "Anthropic to propose topics and plan structure."
                    )
                    disclosure(
                        "WHEN SCORING AN ANSWER",
                        "The topic, question, your transcript, any follow-up, the card's "
                            + "mastery summary, trusted answer basis, source excerpt, and "
                            + "rubric are sent to Anthropic or OpenAI for scoring. Optional "
                            + "coaching is processed by Anthropic. During the limited provider "
                            + "evaluation, that scoring context may be sent to both providers "
                            + "at the same time. OpenAI's output cannot affect the result shown "
                            + "or saved; only privacy-safe comparison and usage metadata are retained."
                    )
                    disclosure(
                        "WHAT ISN'T SENT",
                        "Your Apple sign-in credential and voice recording are not sent to "
                            + "either AI provider. iOS provides a text transcript; Unprompted "
                            + "sends text. OpenAI also receives a stable pseudonymous safety "
                            + "identifier derived with a private app secret, not your Apple "
                            + "credential, name, or email."
                    )

                    InlineNotice {
                        Text(
                            "You can decline and still use saved lessons, Study Plans, "
                                + "history, and settings. Guide processing, question generation, "
                                + "scoring, and AI coaching stay unavailable until you allow them."
                        )
                            .font(WCFont.sans(13))
                            .foregroundStyle(Theme.textSecondary)
                            .lineSpacing(3)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Link("Read Unprompted's privacy policy ↗", destination: PrivacyLinks.policy)
                        Link(
                            "Read Anthropic's API data policy ↗",
                            destination: PrivacyLinks.anthropicRetention
                        )
                        Link(
                            "Read OpenAI's API data controls ↗",
                            destination: PrivacyLinks.openAIDataControls
                        )
                    }
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.meta)

                    if let error = auth.errorMessage {
                        Text(error).font(WCFont.sans(13)).foregroundStyle(Theme.scoreLow)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.top, 30)
                .padding(.bottom, 18)
            }
            VStack(spacing: 8) {
                PrimaryButton(title: working ? "Saving…" : "Allow AI processing") {
                    save("grant")
                }
                .disabled(working)
                Button("Continue without AI") { save("decline") }
                    .buttonStyle(.plain)
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.meta)
                    .frame(maxWidth: .infinity, minHeight: Metrics.minTapTarget)
                    .disabled(working)
            }
            .padding(.horizontal, Metrics.screenPadding)
            .padding(.top, 8)
            .padding(.bottom, Metrics.bottomSafeArea)
            .background(Theme.bg)
        }
        .background(Theme.bg)
    }

    private func disclosure(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            MetaText(
                text: label, font: WCFont.mono(10), tracking: 0.9,
                color: Theme.metaFaint
            )
            Text(text)
                .font(WCFont.sans(13.5))
                .foregroundStyle(Theme.textMuted)
                .lineSpacing(3)
        }
    }

    private func save(_ action: String) {
        guard !working else { return }
        working = true
        Task {
            _ = await auth.updateAIConsent(action)
            working = false
        }
    }
}
