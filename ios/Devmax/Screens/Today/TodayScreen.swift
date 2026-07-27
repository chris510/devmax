import SwiftUI

/// Answers "what's due and how am I doing" in under two seconds.
struct TodayScreen: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch state.load {
                    case .loading: LoadingList()
                    case .error: LoadFailure { Task { await state.loadToday() } }
                    case .ready:
                        if state.queue.isEmpty { EmptyQueue() } else { rowList }
                    }
                }
                .padding(.horizontal, Metrics.listContainerPadding)
                .padding(.bottom, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            bottomBlock
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .sheet(item: $state.sheet) { sheet in
            switch sheet {
            case .settings: SettingsSheet()
            case .add: QuickAddSheet()
            }
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Today")
                    .font(TypeRole.screenTitle)
                    .tracking(-0.6)
                    .foregroundStyle(Theme.text)

                MetaText(
                    text: "\(state.headerDate) · \(state.headerStatus)",
                    font: WCFont.mono(11.5), tracking: 0.35, color: Theme.metaAlt
                )

                if state.load == .ready, !state.bands.isEmpty { masteryBands }
            }
            Spacer(minLength: 0)
            settingsPill
        }
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.top, 14)
        .padding(.bottom, 18)
    }

    private var settingsPill: some View {
        Button { state.sheet = .settings } label: {
            MetaText(text: "SETTINGS", font: WCFont.mono(10.5), tracking: 1.26, color: Theme.meta)
                .padding(.horizontal, 11)
                .padding(.vertical, 7)
                .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }

    /// A single mono line of bands. Not a dashboard — no percentages, no bars,
    /// no second line. Tapping one filters the list; tapping it again clears.
    private var masteryBands: some View {
        FlowLayout(horizontalSpacing: 7, verticalSpacing: 3) {
            ForEach(Array(state.bands.enumerated()), id: \.element.band) { index, entry in
                let active = state.filter == entry.band
                let isLast = index == state.bands.count - 1
                Button {
                    withAnimation(Motion.fadeFast) {
                        state.filter = active ? nil : entry.band
                    }
                } label: {
                    VStack(spacing: 1) {
                        Text("\(entry.count) \(entry.band.rawValue)\(isLast ? "" : " ·")")
                            .font(WCFont.mono(11))
                            .tracking(0.33)
                            .foregroundStyle(active ? entry.band.color : Theme.metaAlt)
                        Rectangle()
                            .fill(active ? entry.band.color : .clear)
                            .frame(height: 1)
                    }
                    .fixedSize()
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: - Rows

    private var rowList: some View {
        LazyVStack(spacing: 0) {
            ForEach(state.visibleQueue) { card in
                Hairline()
                TodayRow(
                    card: card,
                    onOpenHistory: { state.path.append(.history(card.id)) },
                    onStart: { state.beginSession(cards: [card]) }
                )
            }

            Hairline().padding(.top, 4)
            MetaText(text: "TAP A TOPIC NAME FOR ITS HISTORY",
                     font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.rowInset)
                .padding(.top, 16)
        }
    }

    // MARK: - Bottom

    private var bottomBlock: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Quick-add stays visible in every state, including empty and error.
            Button { state.sheet = .add } label: {
                HStack(spacing: 8) {
                    Text("+").font(WCFont.sans(16))
                    Text("Ask about something else").font(TypeRole.rowSummary)
                }
                .foregroundStyle(Theme.meta)
                .padding(.vertical, 4)
            }
            .buttonStyle(.plain)

            // Always visible, including when the queue is empty or the fetch
            // failed — a sprint draws from the whole library, not from what's due.
            // Deliberately lower weight than Start, which stays the dominant
            // daily action.
            Button { state.enterSprintSetup() } label: {
                Text("Review sprint")
                    .font(WCFont.sans(15))
                    .foregroundStyle(Theme.textMuted)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .strokeBorder(Theme.border, lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)

            // Start appears only when more than one card is due.
            if state.visibleQueue.count > 1 {
                PrimaryButton(title: "Start — \(state.visibleQueue.count) cards") {
                    state.beginSession(cards: state.visibleQueue)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.top, Metrics.listContainerPadding)
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }
}

struct TodayRow: View {
    let card: DueCard
    let onOpenHistory: () -> Void
    let onStart: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: Metrics.scoreColumnGap) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    // The topic name is its own tap target — everything else on the
                    // row starts a Conversation.
                    Button(action: onOpenHistory) {
                        VStack(spacing: 2) {
                            Text(card.topic)
                                .font(TypeRole.rowTopic)
                                .tracking(-0.165)
                                .foregroundStyle(Theme.text)
                            DottedUnderline()
                        }
                        .fixedSize()
                    }
                    .buttonStyle(.plain)

                    MetaText(text: card.category, font: WCFont.mono(10), tracking: 1.0,
                             color: Theme.metaDim, uppercased: true)
                    Spacer(minLength: 0)
                }

                Text(card.masterySummary)
                    .font(TypeRole.rowSummary)
                    .foregroundStyle(Theme.textDim)
                    .lineSpacing(13.5 * 1.45 - 13.5 * 1.2)
                    .fixedSize(horizontal: false, vertical: true)

                WrappingChips(chips: chips)
            }

            ScoreColumn(score: card.lastScore)
        }
        .padding(.top, Metrics.rowTopPadding)
        .padding(.bottom, Metrics.rowBottomPadding)
        .padding(.horizontal, Metrics.rowInset)
        .contentShape(Rectangle())
        .onTapGesture(perform: onStart)
    }

    private var chips: [WrappingChips.Chip] {
        var result = [WrappingChips.Chip(text: card.dueLabel)]
        if card.resumable {
            result.append(.init(text: "· resumable", color: Theme.accent))
        }
        if card.missedCount > 0 {
            // The quiet missed indicator — never styled as a warning.
            result.append(.init(text: "· missed \(card.missedCount)×"))
        }
        return result
    }
}

/// Three static skeleton rows matching row geometry. No shimmer — static blocks
/// only, per the handoff.
struct LoadingList: View {
    private let widths: [[CGFloat]] = [[0.58, 0.84, 0.34], [0.46, 0.72, 0.34], [0.63, 0.79, 0.34]]
    private let heights: [CGFloat] = [12, 10, 8]
    private let fills = [Theme.skeleton1, Theme.skeleton2, Theme.skeleton3]

    var body: some View {
        VStack(spacing: 0) {
            ForEach(0..<3, id: \.self) { row in
                Hairline()
                HStack(alignment: .top, spacing: Metrics.scoreColumnGap) {
                    GeometryReader { geo in
                        VStack(alignment: .leading, spacing: 9) {
                            ForEach(0..<3, id: \.self) { i in
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(fills[i])
                                    .frame(width: geo.size.width * widths[row][i], height: heights[i])
                            }
                        }
                    }
                    .frame(height: 48)

                    RoundedRectangle(cornerRadius: 3)
                        .fill(Theme.skeleton1)
                        .frame(width: 12, height: 12)
                        .frame(width: Metrics.scoreColumnWidth, alignment: .center)
                        .padding(.top, 2)
                }
                .padding(.top, Metrics.rowTopPadding)
                .padding(.bottom, Metrics.rowBottomPadding)
                .padding(.horizontal, Metrics.rowInset)
            }

            MetaText(text: "LOADING QUEUE", font: WCFont.mono(10), tracking: 1.2, color: Theme.metaFaint)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.rowInset)
                .padding(.top, 16)
        }
    }
}

/// No red, no icon, no banner.
struct LoadFailure: View {
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 7) {
                Text("Couldn't reach the server.")
                    .font(TypeRole.bodyLarge)
                    .foregroundStyle(Theme.textSecondary)

                if let note = DueCache.note {
                    MetaText(text: note, font: WCFont.mono(11), tracking: 0.44, color: Theme.metaDim)
                }
            }

            SecondaryButton(title: "Retry", fillsWidth: false, action: retry)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.rowInset)
        .padding(.top, 28)
        .overlay(alignment: .top) { Hairline() }
        .wcFade()
    }
}

struct EmptyQueue: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 26) {
            Text("Nothing due until tomorrow, 7:10.")
                .font(TypeRole.emptyQueue)
                .foregroundStyle(Theme.textSecondary)
                .lineSpacing(22 * 1.4 - 22 * 1.2)
                .fixedSize(horizontal: false, vertical: true)

            if !state.upcoming.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    MetaText(text: "COMING UP", font: WCFont.mono(10), tracking: 1.2, color: Theme.metaDimAlt)
                    ForEach(state.upcoming.prefix(3)) { card in
                        HStack(spacing: 12) {
                            Text(card.topic)
                                .font(WCFont.sans(14))
                                .foregroundStyle(Theme.metaAlt)
                            Spacer(minLength: 0)
                            MetaText(text: upcomingTime(for: card), font: WCFont.mono(12),
                                     tracking: 0, color: Theme.metaDimAlt)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.rowInset)
        .padding(.top, 40)
        .wcFade()
    }

    /// The backend has no "upcoming" endpoint, so the day comes from the card's
    /// next review date and the time from the first enabled notification window.
    private func upcomingTime(for card: CardSummary) -> String {
        let parser = DateFormatter()
        parser.dateFormat = "yyyy-MM-dd"
        let day = DateFormatter()
        day.dateFormat = "EEE"
        let name = parser.date(from: card.nextReviewAt).map { day.string(from: $0) } ?? ""
        let time = state.settings.windows.first(where: { $0.on })?.from ?? "07:10"
        return "\(name.uppercased()) \(time)"
    }
}
