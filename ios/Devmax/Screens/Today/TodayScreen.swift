import SwiftUI

/// Answers "what's due and how am I doing" in under two seconds.
struct TodayScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState
    @EnvironmentObject private var flow: PublicOnboardingState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header
            planLine
            captureLine

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch state.load {
                    case .loading: LoadingList()
                    case .error: LoadFailure { Task { await state.loadToday() } }
                    case .ready:
                        if state.queue.isEmpty, state.library.isEmpty {
                            NoMaterialTodayContent()
                        } else if state.queue.isEmpty {
                            EmptyQueue()
                        } else { rowList }
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
            case .plans: PlansSheet()
            case .planCapacity:
                if let id = plan.overview?.id ?? state.planSummary?.planId {
                    PlanCapacitySheet(planID: id)
                }
            }
        }
        .fullScreenCover(item: $state.captureRoute) { route in
            CaptureFlowScreen(route: route)
                .environmentObject(state)
        }
    }

    // MARK: - Study Plan
    //
    // One compact line, and one secondary scheduling fact. No description, no
    // capacity, no progress paragraph — Today asks "what should I do now", and
    // the plan's answer to that is where you are and when the next block is.
    //
    // The accent appears on the caret only.

    private var planLine: some View {
        Button {
            if let id = state.planSummary?.planId {
                state.path.append(.planOverview(id))
            } else {
                state.path.append(.planBuild)
            }
        } label: {
            HStack(spacing: 8) {
                MetaText(
                    text: planText,
                    font: WCFont.mono(10.5), tracking: 0.9,
                    // Slightly stronger than the row metadata below it, so the
                    // line reads as a destination rather than a caption.
                    color: state.planSummaryFailed ? Theme.metaFaint : Theme.meta
                )
                Spacer(minLength: 0)
                Text("→")
                    .font(WCFont.mono(11))
                    .foregroundStyle(Theme.accent)
            }
            .contentShape(Rectangle())
            .padding(.horizontal, 15)
            .padding(.vertical, 12)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: Metrics.inlineRadius))
            .overlay(
                RoundedRectangle(cornerRadius: Metrics.inlineRadius)
                    .strokeBorder(Theme.border, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .frame(minHeight: Metrics.minTapTarget)
        .accessibilityLabel(
            state.planSummaryFailed
                ? "Study plan unavailable. Opens the plan."
                : (state.planSummary?.accessibleLine ?? "Add a study guide.")
        )
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.bottom, 8)
    }

    private var planText: String {
        // A Study Plan outage says so and stays tappable. It never blocks or
        // delays the due cards above it.
        if state.planSummaryFailed { return "PLAN · UNAVAILABLE" }
        return (state.planSummary ?? .none).todayLine
    }

    @ViewBuilder
    private var captureLine: some View {
        if !state.captures.isEmpty {
            Button { state.captureRoute = .inbox } label: {
                HStack(spacing: 8) {
                    MetaText(
                        text: "\(state.captures.count) captured gap\(state.captures.count == 1 ? "" : "s")",
                        font: WCFont.mono(10.5), tracking: 0.65, color: Theme.meta
                    )
                    Spacer(minLength: 0)
                    Text("→")
                        .font(WCFont.mono(11))
                        .foregroundStyle(Theme.accent)
                }
                .padding(.horizontal, Metrics.screenPadding)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget)
            .padding(.bottom, 4)
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
        .padding(.bottom, 16)
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

    /// Tapping a band filters the list; tapping it again clears.
    private var masteryBands: some View {
        CountSegments(
            segments: state.bands.map { entry in
                CountSegments.Segment(
                    id: entry.band.rawValue,
                    text: "\(entry.count) \(entry.band.rawValue)",
                    color: entry.band.color,
                    isActive: state.filter == entry.band
                )
            }
        ) { segment in
            withAnimation(Motion.fadeFast) {
                state.filter = segment.isActive ? nil : ScoreStyle.Band(rawValue: segment.id)
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

    @ViewBuilder
    private var bottomBlock: some View {
        if state.load == .ready, state.queue.isEmpty, state.library.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 10) {
                    // Creation remains available without making a raised plus the
                    // app's primary navigation. The menu keeps both add paths in
                    // one quiet, 44pt target beside the optional sprint.
                    Menu {
                        Button("Add study material") {
                            flow.step = .material
                            state.path.append(.materialSetup)
                        }
                        Button("Capture a gap") {
                            state.savedCapture = nil
                            state.addError = false
                            state.sheet = .add
                        }
                    } label: {
                        Text("+  Add")
                            .font(WCFont.sans(14))
                            .foregroundStyle(Theme.textMuted)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 11)
                            .background(
                                RoundedRectangle(cornerRadius: Metrics.secondaryRadius)
                                    .strokeBorder(Theme.border, lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: .infinity, minHeight: Metrics.minTapTarget)
                    .accessibilityLabel("Add")
                    .accessibilityHint("Choose study material or capture a gap.")

                    // A sprint draws from the whole library, even when nothing is
                    // due. It stays secondary to the scheduled review action.
                    SecondaryButton(title: "Review sprint") {
                        state.enterSprintSetup()
                    }
                }

                // Start appears only when more than one card is due.
                if state.visibleQueue.count > 1 {
                    PrimaryButton(title: "Start review · \(state.visibleQueue.count) cards") {
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
}

struct NoMaterialTodayContent: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var flow: PublicOnboardingState

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Add something you want to understand.")
                .font(TypeRole.emptyQueue).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            PrimaryButton(title: "Add study material") {
                flow.step = .material
                state.path.append(.materialSetup)
            }
            SecondaryButton(title: "Add a few topics") { open(.manual) }
            Button("Browse Devmax collections") { open(.collections) }
                .buttonStyle(.plain).font(TypeRole.secondaryAction).foregroundStyle(Theme.meta)
                .frame(minHeight: Metrics.minTapTarget)
        }
        .padding(.horizontal, Metrics.rowInset).padding(.top, 28)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func open(_ step: PublicOnboardingState.Step) {
        flow.step = step
        state.path.append(.materialSetup)
        if step == .collections { Task { await flow.loadCollections() } }
    }
}

struct TodayRow: View {
    @EnvironmentObject private var state: AppState
    let card: DueCard
    let onOpenHistory: () -> Void
    let onStart: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: Metrics.scoreColumnGap) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    // The topic name is its own tap target — everything else on the
                    // row starts a Conversation.
                    // The underline is drawn by the text, not stacked under it. A
                    // separate rule had to be width-locked with `.fixedSize()` to
                    // match the topic, which stopped the topic wrapping and pushed
                    // long ones off both edges of the row. The prototype's
                    // `border-bottom: 1px dotted` follows each wrapped line, and
                    // unlike the other underlined spans it carries no `nowrap`.
                    Button(action: onOpenHistory) {
                        Text(card.topic)
                            .font(TypeRole.rowTopic)
                            .tracking(-0.165)
                            .foregroundStyle(Theme.text)
                            .underline(true, pattern: .dot, color: Theme.dottedUnderline)
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

            ScoreColumn(score: state.displayScore(card))
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
///
/// Shared by Today, Review Sprint Setup and Coverage: the prototype drives all
/// three from one `skeleton` list, and they differ only in the label, the
/// horizontal inset, and whether the row carries a score column. Keeping one
/// copy is what stops row geometry drifting between screens, since the drift
/// would only ever show up in a screenshot comparison.
struct LoadingList: View {
    var label: String = "LOADING QUEUE"
    var inset: CGFloat = Metrics.rowInset
    var showsScoreColumn = true
    /// Today separates rows with a leading `Hairline`; the Sprint screens use a
    /// top overlay so the rule sits flush with their section rules.
    var separator: Separator = .leading

    enum Separator { case leading, overlay }

    private let widths: [[CGFloat]] = [[0.58, 0.84, 0.34], [0.46, 0.72, 0.34], [0.63, 0.79, 0.34]]
    private let heights: [CGFloat] = [12, 10, 8]
    private let fills = [Theme.skeleton1, Theme.skeleton2, Theme.skeleton3]

    var body: some View {
        VStack(spacing: 0) {
            ForEach(0..<3, id: \.self) { row in
                if separator == .leading { Hairline() }
                HStack(alignment: .top, spacing: Metrics.scoreColumnGap) {
                    GeometryReader { geo in
                        VStack(alignment: .leading, spacing: 9) {
                            ForEach(0..<(showsScoreColumn ? 3 : 2), id: \.self) { i in
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(fills[i])
                                    .frame(width: geo.size.width * widths[row][i], height: heights[i])
                            }
                        }
                    }
                    .frame(height: showsScoreColumn ? 48 : 31)

                    if showsScoreColumn {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Theme.skeleton1)
                            .frame(width: 12, height: 12)
                            .frame(width: Metrics.scoreColumnWidth, alignment: .center)
                            .padding(.top, 2)
                    }
                }
                .padding(.top, Metrics.rowTopPadding)
                .padding(.bottom, Metrics.rowBottomPadding)
                .padding(.horizontal, inset)
                .overlay(alignment: .top) { if separator == .overlay { Hairline() } }
            }

            MetaText(text: label, font: WCFont.mono(10), tracking: 1.2, color: Theme.metaFaint)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, inset)
                .padding(.top, 16)
        }
    }
}

/// The app's offline vocabulary — line, mono note, secondary Retry. No red, no
/// icon, no banner.
///
/// Split from `LoadFailure` so Conversation can show the same treatment inside its
/// already-padded thread without inheriting a list screen's inset and hairline.
/// Content is the only parameter: a caller wanting different *styling* wants a
/// different component, not a knob on this one.
///
/// Deliberately does not apply `wcFade` — it slides 6px as well as fading, so it
/// has to wrap whatever chrome the caller adds. Applied here, `LoadFailure`'s
/// hairline would sit still while the block moved under it.
struct LoadFailureBody: View {
    var title = "Couldn't reach the server."
    var note: String?
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 7) {
                Text(title)
                    .font(TypeRole.bodyLarge)
                    .foregroundStyle(Theme.textSecondary)

                if let note {
                    MetaText(text: note, font: WCFont.mono(11), tracking: 0.44, color: Theme.metaDim)
                }
            }

            SecondaryButton(title: "Retry", fillsWidth: false, action: retry)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The list-screen offline state: the body above, plus the row inset and hairline
/// every screen that owns a list draws it with. Nine call sites pass only a retry.
struct LoadFailure: View {
    let retry: () -> Void

    var body: some View {
        LoadFailureBody(note: DueCache.note, retry: retry)
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
            VStack(alignment: .leading, spacing: 6) {
                Text("No reviews due")
                    .font(TypeRole.emptyQueue)
                    .foregroundStyle(Theme.textSecondary)
                    .lineSpacing(22 * 1.4 - 22 * 1.2)
                    .fixedSize(horizontal: false, vertical: true)
                Text(nextDueLine)
                    .font(WCFont.sans(13.5))
                    .foregroundStyle(Theme.textMuted)
            }

            if !state.library.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    MetaText(text: "COMING UP", font: WCFont.mono(10), tracking: 1.2, color: Theme.metaDimAlt)
                    ForEach(upcomingCards.prefix(3)) { card in
                        HStack(spacing: 12) {
                            Text(card.topic)
                                .font(WCFont.sans(14))
                                .foregroundStyle(Theme.metaAlt)
                            Spacer(minLength: 0)
                            MetaText(text: upcomingDay(for: card), font: WCFont.mono(12),
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

    private var nextDueLine: String {
        guard let next = state.library.compactMap({ card -> (CardSummary, Date)? in
            guard let availableAt = ReviewAvailability.effectiveDate(
                for: card, timeZone: accountTimeZone
            ) else { return nil }
            return (card, availableAt)
        }).min(by: { $0.1 < $1.1 })?.0 else {
            return "More reviews are scheduled."
        }
        return "Next due \(upcomingDay(for: next).capitalized)"
    }

    private var upcomingCards: [CardSummary] {
        state.library.sorted {
            let left = ReviewAvailability.effectiveDate(for: $0, timeZone: accountTimeZone)
                ?? .distantFuture
            let right = ReviewAvailability.effectiveDate(for: $1, timeZone: accountTimeZone)
                ?? .distantFuture
            return left < right
        }
    }

    private var accountTimeZone: TimeZone {
        TimeZone(identifier: state.settings.timezone) ?? .current
    }

    /// Cards are scheduled to a date, and learning can move honest recall later.
    /// Show the effective day without borrowing a notification-window time.
    private func upcomingDay(for card: CardSummary) -> String {
        ReviewAvailability.weekday(for: card, timeZone: accountTimeZone) ?? "LATER"
    }
}

/// The review date remains unchanged after learning, while `recallNotBeforeAt`
/// temporarily closes retrieval. Empty Today must name the later boundary.
enum ReviewAvailability {
    static func effectiveDate(for card: CardSummary, timeZone: TimeZone) -> Date? {
        guard let scheduled = scheduledDate(card.nextReviewAt, timeZone: timeZone) else {
            return nil
        }
        guard let boundary = card.recallNotBeforeAt, !boundary.isEmpty else {
            return scheduled
        }
        guard let recallNotBefore = WireDate.parse(boundary) else { return nil }
        return max(scheduled, recallNotBefore)
    }

    static func weekday(for card: CardSummary, timeZone: TimeZone) -> String? {
        guard let date = effectiveDate(for: card, timeZone: timeZone) else { return nil }
        let formatter = DateFormatter()
        formatter.locale = .current
        formatter.timeZone = timeZone
        formatter.dateFormat = "EEEE"
        return formatter.string(from: date).uppercased()
    }

    private static func scheduledDate(_ value: String, timeZone: TimeZone) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: value)
    }
}
