import SwiftUI

/// A single home for study inputs and review outputs. It is reached from
/// Settings and does not introduce a tab bar or a global add control.
struct LibraryScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var flow: PublicOnboardingState
    @State private var sourcesLoaded = false
    @State private var sourcesFailed = false
    @State private var capturesLoaded = false
    @State private var capturesFailed = false
    @State private var collectionsLoaded = false
    @State private var collectionsFailed = false

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: "LIBRARY")
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    section("STUDY") {
                        LibraryDestinationRow(
                            title: "Study material", value: sourcesValue,
                            action: openStudyMaterial
                        )
                        panelDivider
                        LibraryDestinationRow(
                            title: "Captured gaps", value: capturesValue,
                            action: { state.path.append(.libraryCaptures) }
                        )
                    }

                    section("REVIEW") {
                        LibraryDestinationRow(
                            title: "Review cards", value: cardsValue,
                            action: { state.path.append(.libraryCards) }
                        )
                        panelDivider
                        LibraryDestinationRow(
                            title: "Collections", value: collectionsValue,
                            action: openCollections
                        )
                    }

                    section("PLAN") {
                        LibraryDestinationRow(
                            title: "Study plan", value: planValue,
                            action: openPlan
                        )
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, Metrics.bottomSafeArea)
            }
            .refreshable { await load() }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task { await load() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Button { state.path.removeLast() } label: {
                Text("← Settings")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            HStack(alignment: .firstTextBaseline) {
                Text("Library")
                    .font(TypeRole.screenTitle)
                    .tracking(-0.6)
                    .foregroundStyle(Theme.text)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button("Add", action: openAdd)
                    .buttonStyle(.plain)
                    .font(WCFont.sans(15))
                    .foregroundStyle(Theme.textMuted)
                    .frame(minWidth: Metrics.minTapTarget, minHeight: Metrics.minTapTarget)
            }
            .padding(.bottom, 16)
        }
        .padding(.horizontal, Metrics.screenPadding)
    }

    private func section<Content: View>(
        _ label: String, @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            MetaText(
                text: label, font: WCFont.mono(10), tracking: 1.1,
                color: Theme.metaFaint
            )
            QuietPanel { content() }
        }
    }

    private var panelDivider: some View {
        Hairline().padding(.horizontal, 1)
    }

    private var sourcesValue: String {
        if sourcesFailed, flow.imports.isEmpty { return "Unavailable" }
        if !sourcesLoaded, flow.imports.isEmpty { return "Checking" }
        let count = flow.imports.count
        let issues = flow.imports.filter { source in
            ["failed", "needs_attention"].contains(source.status)
                || source.requiresLessonGroundingRecovery
        }.count
        let base = "\(count) source\(count == 1 ? "" : "s")"
        return issues == 0 ? base : "\(base) · \(issues) issue\(issues == 1 ? "" : "s")"
    }

    private var capturesValue: String {
        if capturesFailed, state.captures.isEmpty { return "Unavailable" }
        if !capturesLoaded, state.captures.isEmpty { return "Checking" }
        let count = state.captures.count
        return "\(count) open"
    }

    private var cardsValue: String {
        switch state.libraryLoad {
        case .loading where state.library.isEmpty: return "Checking"
        case .error where state.library.isEmpty: return "Unavailable"
        default: return "\(state.library.count) active"
        }
    }

    private var collectionsValue: String {
        if collectionsFailed, flow.collections.isEmpty { return "Unavailable" }
        if !collectionsLoaded, flow.collections.isEmpty { return "Checking" }
        return "\(flow.collections.filter(\.available).count) available"
    }

    private var planValue: String {
        guard let summary = state.planSummary, summary.active else { return "Not set" }
        guard let week = summary.weekIndex else { return "Active" }
        return "Week \(week)"
    }

    private func openStudyMaterial() {
        flow.step = .studyMaterial
        state.path.append(.materialSetup)
    }

    private func openCollections() {
        flow.step = .collections
        state.path.append(.materialSetup)
        Task { await flow.loadCollections() }
    }

    private func openPlan() {
        if let id = state.planSummary?.planId {
            state.path.append(.planOverview(id))
        } else {
            state.path.append(.planBuild)
        }
    }

    private func openAdd() {
        flow.step = .material
        state.path.append(.materialSetup)
    }

    @MainActor
    private func load() async {
        async let sources = try? flow.api.materialImports()
        async let captures = try? state.api.captures()
        async let collections = try? flow.api.materialCollections()
        async let cards: Void = state.loadLibrary()
        async let summary = try? state.api.activePlan()

        let (newSources, newCaptures, newCollections, _, newSummary) = await (
            sources, captures, collections, cards, summary
        )

        sourcesLoaded = newSources != nil
        sourcesFailed = newSources == nil
        if let newSources { flow.imports = newSources }

        capturesLoaded = newCaptures != nil
        capturesFailed = newCaptures == nil
        if let newCaptures { state.captures = newCaptures }

        collectionsLoaded = newCollections != nil
        collectionsFailed = newCollections == nil
        if let newCollections { flow.collections = newCollections }

        if let newSummary { state.planSummary = newSummary }
    }
}

struct LibraryCardsScreen: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: "LIBRARY")
            header

            ScrollView {
                LazyVStack(spacing: 0) {
                    switch state.libraryLoad {
                    case .loading:
                        LoadingList(label: "LOADING CARDS", inset: 0)
                    case .error:
                        LoadFailure { Task { await state.loadLibrary() } }
                    case .ready:
                        if state.library.isEmpty {
                            Text("No review cards yet.")
                                .font(TypeRole.emptyQueue)
                                .foregroundStyle(Theme.textSecondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.top, 24)
                        } else {
                            ForEach(state.library) { card in
                                Hairline()
                                Button { state.path.append(.history(card.id)) } label: {
                                    HStack(alignment: .top, spacing: Metrics.scoreColumnGap) {
                                        VStack(alignment: .leading, spacing: 6) {
                                            Text(card.topic)
                                                .font(TypeRole.rowTopic)
                                                .foregroundStyle(Theme.text)
                                                .frame(maxWidth: .infinity, alignment: .leading)
                                            HStack(spacing: 8) {
                                                MetaText(
                                                    text: card.category,
                                                    font: TypeRole.metaTag,
                                                    tracking: 0.9,
                                                    color: Theme.metaDim,
                                                    uppercased: true
                                                )
                                                MetaText(
                                                    text: card.dueLabel,
                                                    font: TypeRole.metaRow,
                                                    tracking: 0.4,
                                                    color: Theme.metaFaint
                                                )
                                            }
                                        }
                                        ScoreColumn(score: state.displayScore(card))
                                    }
                                    .padding(.vertical, 15)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                            }
                            Hairline()
                        }
                    }
                }
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, Metrics.bottomSafeArea)
            }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task {
            if state.libraryLoad != .ready { await state.loadLibrary() }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button { state.path.removeLast() } label: {
                Text("← Library")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            Text("Review cards")
                .font(TypeRole.screenTitle)
                .tracking(-0.6)
                .foregroundStyle(Theme.text)
                .accessibilityAddTraits(.isHeader)
            MetaText(
                text: state.libraryLoad.status {
                    "\(state.library.count) ACTIVE CARD\(state.library.count == 1 ? "" : "S")"
                },
                font: WCFont.mono(11), tracking: 0.5, color: Theme.metaAlt
            )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.bottom, 14)
    }
}

private struct LibraryDestinationRow: View {
    let title: String
    let value: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(title)
                    .font(WCFont.sans(15, weight: 500))
                    .foregroundStyle(Theme.text)
                Spacer(minLength: 8)
                Text(value)
                    .font(WCFont.mono(11))
                    .tracking(0.3)
                    .foregroundStyle(Theme.metaAlt)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                Text("›")
                    .font(WCFont.sans(17))
                    .foregroundStyle(Theme.metaFaint)
            }
            .frame(minHeight: 56)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens \(title.lowercased()).")
    }
}
