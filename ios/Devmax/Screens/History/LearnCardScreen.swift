import SwiftUI

/// Source-backed first exposure. This screen never receives or renders the
/// canonical question, and it has no path into Conversation: recall returns
/// later, after the server-owned quiet delay.
struct LearnCardScreen: View {
    let cardID: UUID
    @EnvironmentObject private var state: AppState
    @State private var load: AppState.LoadState = .loading
    @State private var learning: LearningCard?
    @State private var staleAvailability = false

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Button { state.path.removeLast() } label: {
                        Text("← Card")
                            .font(TypeRole.secondaryAction)
                            .foregroundStyle(Theme.meta)
                    }
                    .buttonStyle(.plain)
                    .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

                    switch load {
                    case .loading:
                        LoadingList(
                            label: "OPENING LEARNING CARD", inset: 0,
                            showsScoreColumn: false, separator: .overlay
                        )
                        .padding(.top, 8)
                    case .error:
                        if staleAvailability {
                            staleAvailabilityView
                                .padding(.top, 18)
                        } else {
                            LoadFailure { Task { await loadLearning() } }
                                .padding(.top, 18)
                        }
                    case .ready:
                        if let learning { content(learning) }
                    }
                }
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, Metrics.bottomSafeArea)
            }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task { await loadLearning() }
    }

    private func content(_ card: LearningCard) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            lessonHeading(card)
            sourceBlock(card)

            learningBlock("CORE EXPLANATION", card.coreExplanation, serif: true)
            learningBlock("ESSENTIAL IDEA", card.essentialAccount)
            learningBlock("VALID ALTERNATIVE", card.acceptableAlternative)
            learningBlock("GO DEEPER", card.depthExtension)
            learningBlock("BOUNDARY OR FAILURE", card.boundaryExtension)
            learningBlock("COMMON TRAP", card.misconception)
            doneWhenBlock
            lessonFooter(card)
        }
        .wcFade()
    }

    private func lessonHeading(_ card: LearningCard) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(card.topic)
                .font(TypeRole.historyTitle)
                .tracking(-0.4)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            MetaText(
                text: card.category, font: WCFont.mono(10), tracking: 1.0,
                color: Theme.metaDim, uppercased: true
            )
            Text("Build the mechanism with the source open. Recall returns later, closed-book.")
                .font(WCFont.serif(17))
                .foregroundStyle(Theme.textSerif)
                .lineSpacing(17 * 1.45 - 17 * 1.2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 5)
        }
        .padding(.top, 6)
    }

    private func sourceBlock(_ card: LearningCard) -> some View {
        Block(label: "START HERE") {
            VStack(alignment: .leading, spacing: 7) {
                Text(card.sourceLabel)
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                if !card.sourceSection.isEmpty {
                    MetaText(
                        text: card.sourceSection, font: WCFont.mono(10),
                        tracking: 0.6, color: Theme.metaDim
                    )
                }
                if let url = safeSourceURL(card.sourceUrl) {
                    Link(destination: url) {
                        Text("Open source ↗")
                            .font(TypeRole.secondaryAction)
                            .foregroundStyle(Theme.meta)
                            .underline()
                            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                    }
                    .accessibilityHint("Opens the source in your browser")
                }
                if !card.sourceExcerpt.isEmpty {
                    Text("“\(card.sourceExcerpt)”")
                        .font(WCFont.sans(13.5))
                        .foregroundStyle(Theme.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var doneWhenBlock: some View {
        Block(label: "DONE WHEN") {
            Text(
                "Close the source. Explain the core mechanism plus one meaningful "
                    + "trade-off or boundary in your own words. Then stop and wait "
                    + "for the scored review."
            )
            .font(WCFont.sans(14))
            .foregroundStyle(Theme.textSecondary)
            .lineSpacing(3)
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func lessonFooter(_ card: LearningCard) -> some View {
        if let label = RecallGate.label(card.recallAvailableAt) {
            MetaText(
                text: label.replacingOccurrences(
                    of: "recall available", with: "SCORED REVIEW AVAILABLE"
                ) + " · YOUR SCHEDULE WASN'T CHANGED",
                font: TypeRole.metaBody,
                tracking: 1.0, color: Theme.metaFaint, uppercased: true
            )
            .padding(.top, 24)
        } else {
            MetaText(
                text: "YOUR SCHEDULE WASN'T CHANGED", font: TypeRole.metaBody,
                tracking: 1.0, color: Theme.metaFaint
            )
            .padding(.top, 24)
        }
        PrimaryButton(title: "Done for now") { state.path.removeLast() }
            .padding(.top, 16)
    }

    @ViewBuilder
    private func learningBlock(_ label: String, _ text: String, serif: Bool = false) -> some View {
        if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            Block(label: label) {
                Text(text)
                    .font(serif ? WCFont.serif(17) : WCFont.sans(14))
                    .foregroundStyle(serif ? Theme.textSerif : Theme.textSecondary)
                    .lineSpacing(serif ? 17 * 1.45 - 17 * 1.2 : 3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func safeSourceURL(_ value: String) -> URL? {
        guard let url = URL(string: value),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              url.host != nil
        else { return nil }
        return url
    }

    private var staleAvailabilityView: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 7) {
                Text("This card changed before learning opened.")
                    .font(TypeRole.bodyLarge)
                    .foregroundStyle(Theme.textSecondary)
                MetaText(
                    text: "RETURN TO THE CARD FOR THE CURRENT ACTION",
                    font: WCFont.mono(11), tracking: 0.44, color: Theme.metaDim
                )
            }
            SecondaryButton(title: "Back to card", fillsWidth: false) {
                state.path.removeLast()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.rowInset)
        .padding(.top, 28)
        .overlay(alignment: .top) { Hairline() }
        .wcFade()
    }

    private func loadLearning() async {
        load = .loading
        staleAvailability = false
        do {
            let value = try await state.api.learnCard(cardID)
            learning = value
            state.markLearningExposure(value)
            load = .ready
        } catch APIError.status(409) {
            staleAvailability = true
            load = .error
        } catch {
            load = .error
        }
    }
}
