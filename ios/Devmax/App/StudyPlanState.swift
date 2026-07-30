import Foundation

/// Study Plan's domain state.
///
/// A second `ObservableObject` alongside `AppState` rather than more properties
/// on it: `AppState` already owns the navigation path, the review queue, and the
/// whole Conversation machine, and Study Plan is a parallel product surface with
/// its own loading, its own failure modes, and no shared state beyond the plan
/// summary Today shows. Navigation still goes through `AppState.path` — this
/// object never pushes a screen, it only holds what a screen renders.
@MainActor
final class StudyPlanState: ObservableObject {
    enum Load: Equatable { case idle, loading, ready, error }

    private let api: DevmaxAPI

    // The map
    @Published var overviewLoad: Load = .idle
    @Published var overview: PlanOverview?
    /// One phase open at a time, enforced here rather than per-row.
    @Published var openPhase: Int?
    /// Whether the user (or a screenshot route) has chosen which phase is open.
    /// Without this, the screen's own `.task` reload would reset the choice back
    /// to the current phase every time the overview refreshed — including right
    /// after a route set it deliberately.
    private var phaseChosen = false
    /// Which plan `phaseChosen` refers to. Without it, expanding a phase on one
    /// plan suppressed the auto-open-the-current-phase default on the next one.
    private var phaseChosenFor: UUID?

    // Drill-down
    @Published var weekLoad: Load = .idle
    @Published var week: WeekDetail?
    @Published var itemLoad: Load = .idle
    @Published var item: PlanItemDetail?
    @Published var itemBusy = false
    @Published var itemError: String?

    // Decisions
    @Published var proposalLoad: Load = .idle
    @Published var proposal: PlanProposal?
    @Published var proposalKind: ProposalKind = .replan
    @Published var applying = false
    @Published var applyError: String?

    // Capacity
    @Published var capacityHours = 12
    @Published var capacityWeek = 4

    // Creation
    @Published var guideText = ""
    @Published var requestedWeeks = 12
    @Published var weeklyCapacityHours = 12
    @Published var fixedDeadline = false
    @Published var deadline = Date().addingTimeInterval(60 * 60 * 24 * 84)
    @Published var subjectHint = ""
    @Published var previewLoad: Load = .idle
    @Published var preview: PlanPreview?
    @Published var creating = false

    // Lifecycle
    @Published var plansLoad: Load = .idle
    @Published var plans: PlanList?
    @Published var revisions: [PlanRevisionEntry] = []
    @Published var recap: PlanRecap?
    @Published var lifecycleBusy = false

    // Cards
    @Published var cardLoad: Load = .idle
    @Published var cards: CardProposalList?
    @Published var selectedProposals: Set<UUID> = []
    @Published var addedProposals: Set<UUID> = []
    @Published var addingCards = false
    @Published var cardError: String?
    @Published var expandedGate: UUID?
    /// Held across a retry so the same key means the same intent — that is what
    /// makes a lost response safe to re-send.
    private var acceptKey = UUID().uuidString

    init(api: DevmaxAPI = APIConfig.client) {
        self.api = api
        if let draft = GuideDraftStore.read() {
            guideText = draft.guideText
            requestedWeeks = draft.requestedWeeks
            weeklyCapacityHours = draft.weeklyCapacityHours
            fixedDeadline = draft.fixedDeadline
            subjectHint = draft.subjectHint
        }
    }

    // MARK: - the map

    func loadOverview(_ id: UUID) async {
        overviewLoad = overview == nil ? .loading : overviewLoad
        do {
            overview = try await api.planOverview(id)
            if phaseChosenFor != id { phaseChosen = false }
            if !phaseChosen {
                openPhase = overview?.phases.first { $0.status == "Current" }?.index
            }
            overviewLoad = .ready
        } catch {
            overviewLoad = .error
        }
    }

    /// One at a time. Tapping the open phase closes it.
    func togglePhase(_ index: Int) {
        phaseChosen = true
        phaseChosenFor = overview?.id
        openPhase = openPhase == index ? nil : index
    }

    /// Used by the screenshot routes to pin an expansion state that survives the
    /// screen's own load.
    func chooseOpenPhase(_ index: Int?) {
        phaseChosen = true
        phaseChosenFor = overview?.id
        openPhase = index
    }

    func loadWeek(_ planID: UUID, index: Int) async {
        weekLoad = .loading
        do {
            week = try await api.planWeek(planID, index: index)
            capacityWeek = index
            capacityHours = max(1, (week?.capacityMinutes ?? 720) / 60)
            weekLoad = .ready
        } catch {
            weekLoad = .error
        }
    }

    func loadItem(_ planID: UUID, itemID: UUID) async {
        itemLoad = .loading
        itemError = nil
        do {
            item = try await api.planItem(planID, itemID: itemID)
            itemLoad = .ready
        } catch {
            itemLoad = .error
        }
    }

    // MARK: - item progress

    @discardableResult
    func completeItem() async -> Bool {
        guard let item else { return false }
        itemBusy = true
        itemError = nil
        defer { itemBusy = false }
        do {
            self.item = try await api.completePlanItem(item.planId, itemID: item.id)
            await refreshAfterProgress(item.planId)
            return true
        } catch {
            itemError = "Couldn't mark this complete. Nothing changed."
            return false
        }
    }

    func previewReopen() async {
        guard let item else { return }
        proposalKind = .reopen
        proposalLoad = .loading
        do {
            proposal = try await api.previewReopen(item.planId, itemID: item.id)
            proposalLoad = .ready
        } catch {
            proposalLoad = .error
        }
    }

    func applyReopen() async -> Bool {
        guard let item, let revision = overview?.revision ?? proposal?.basePlanRevision
        else { return false }
        applying = true
        applyError = nil
        defer { applying = false }
        do {
            self.item = try await api.reopenPlanItem(
                item.planId, itemID: item.id, revision: revision
            )
            await refreshAfterProgress(item.planId)
            return true
        } catch {
            applyError = "Couldn't reopen this. Nothing changed."
            return false
        }
    }

    func setReminder(on: Bool) async {
        guard let item else { return }
        do {
            let updated = try await api.editPlanItem(
                item.planId, itemID: item.id,
                edit: PlanItemEdit(studyBlockReminderOn: on)
            )
            self.item = updated
            await StudyReminderService.shared.sync(item: updated)
        } catch {
            itemError = "Couldn't change the reminder."
        }
    }

    func editItem(_ edit: PlanItemEdit) async {
        guard let item else { return }
        itemBusy = true
        itemError = nil
        do {
            self.item = try await api.editPlanItem(item.planId, itemID: item.id, edit: edit)
            await refreshAfterProgress(item.planId)
        } catch {
            itemError = "Couldn't save that edit."
        }
        itemBusy = false
    }

    private func refreshAfterProgress(_ planID: UUID) async {
        await loadOverview(planID)
        if let index = week?.index { await loadWeek(planID, index: index) }
    }

    // MARK: - decisions

    func previewCapacity(_ planID: UUID, hours: Int) async {
        guard let revision = overview?.revision else { return }
        proposalKind = .capacity
        proposalLoad = .loading
        do {
            proposal = try await api.updateWeekCapacity(
                planID, index: capacityWeek, minutes: hours * 60, revision: revision
            )
            proposalLoad = .ready
        } catch {
            proposalLoad = .error
        }
    }

    /// What resuming this plan would cost. A short pause usually costs nothing;
    /// a fixed plan past its deadline comes back invalid with the reason.
    func previewResume(_ planID: UUID) async {
        proposalKind = .resume
        proposalLoad = .loading
        do {
            proposal = try await api.previewResume(planID)
            proposalLoad = .ready
        } catch {
            proposalLoad = .error
        }
    }

    func previewReplan(_ planID: UUID) async {
        guard let revision = overview?.revision else { return }
        proposalKind = .replan
        proposalLoad = .loading
        do {
            proposal = try await api.previewReplan(
                planID, request: ReplanRequest(basePlanRevision: revision)
            )
            proposalLoad = .ready
        } catch {
            proposalLoad = .error
        }
    }

    /// Apply is only ever reachable from a proposal that validated, and the
    /// server recomputes it anyway — a stale revision comes back as a conflict
    /// rather than overwriting someone else's change.
    func applyProposal(_ planID: UUID) async -> Bool {
        guard let proposal, proposal.canApply else { return false }
        applying = true
        applyError = nil
        defer { applying = false }
        do {
            _ = try await api.applyReplan(
                planID,
                request: ReplanRequest(
                    basePlanRevision: proposal.basePlanRevision,
                    capacityOverrides: proposalKind == .capacity
                        ? [String(capacityWeek): capacityHours * 60] : [:]
                )
            )
            await loadOverview(planID)
            await loadWeek(planID, index: capacityWeek)
            return true
        } catch APIError.status(409) {
            applyError = "The plan changed while you were deciding. Review the new "
                + "proposal before applying it."
            await previewReplan(planID)
            return false
        } catch {
            applyError = "Couldn't apply that. Nothing changed."
            return false
        }
    }

    // MARK: - creation

    var capacityMinutes: Int { weeklyCapacityHours * 60 }

    /// Disk is written on every edit and is the source of truth on relaunch —
    /// the same rule the answer draft follows, for the same reason.
    func persistGuide() {
        GuideDraftStore.save(
            GuideDraft(
                guideText: guideText, requestedWeeks: requestedWeeks,
                weeklyCapacityHours: weeklyCapacityHours, fixedDeadline: fixedDeadline,
                subjectHint: subjectHint
            )
        )
    }

    var canPreview: Bool { guideText.trimmingCharacters(in: .whitespacesAndNewlines).count >= 200 }

    func runPreview() async {
        persistGuide()
        previewLoad = .loading
        do {
            preview = try await api.previewGuide(
                GuidePreviewRequest(
                    guideText: guideText,
                    requestedWeeks: requestedWeeks,
                    weeklyCapacityMinutes: capacityMinutes,
                    mode: fixedDeadline ? "fixed" : "flexible",
                    deadline: fixedDeadline ? Self.isoDay.string(from: deadline) : nil,
                    subjectHint: subjectHint
                )
            )
            previewLoad = .ready
        } catch {
            previewLoad = .error
        }
    }

    func retryPreview() async {
        guard let draftID = preview?.draftId else { return await runPreview() }
        previewLoad = .loading
        do {
            preview = try await api.retryPreview(draftID: draftID)
            previewLoad = .ready
        } catch {
            previewLoad = .error
        }
    }

    /// Resolving a check is an edit to the draft, re-validated server-side. The
    /// client never decides that a check is satisfied.
    func resolveCheck(_ key: String) async {
        guard let draftID = preview?.draftId else { return }
        var edit = PreviewEdit()
        switch key {
        case "estimates":
            edit.estimatesReviewed = []
        case "coverage": edit.omissionsAcknowledged = true
        case "retrieval": edit.retrievalApproved = []
        case "dependencies": edit.dependenciesConfirmed = []
        default: break
        }
        do { preview = try await api.editPreview(draftID: draftID, edit: edit) } catch {}
    }

    func createPlan() async -> UUID? {
        guard let draftID = preview?.draftId, preview?.canCreate == true else { return nil }
        creating = true
        defer { creating = false }
        do {
            let created = try await api.createPlan(draftID: draftID, activate: true)
            overview = created
            overviewLoad = .ready
            // The guide has become a plan; the local copy has done its job.
            GuideDraftStore.clear()
            guideText = ""
            return created.id
        } catch {
            previewLoad = .error
            return nil
        }
    }

    private static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = .current
        return f
    }()

    // MARK: - lifecycle

    func loadPlans() async {
        plansLoad = .loading
        do {
            plans = try await api.plans()
            plansLoad = .ready
        } catch {
            plansLoad = .error
        }
    }

    func loadRevisions(_ planID: UUID) async {
        revisions = (try? await api.planRevisions(planID)) ?? []
    }

    func loadRecap(_ planID: UUID) async {
        recap = try? await api.planRecap(planID)
    }

    @discardableResult
    func lifecycle(_ action: PlanLifecycleAction, planID: UUID) async -> Bool {
        guard !lifecycleBusy else { return false }
        lifecycleBusy = true
        applyError = nil
        defer { lifecycleBusy = false }
        do {
            switch action {
            case .pause:
                overview = try await api.pausePlan(planID)
                await StudyReminderService.shared.cancelAll(planID: planID)
            case .complete: overview = try await api.completePlan(planID)
            case .archive: overview = try await api.archivePlan(planID)
            case .duplicate: overview = try await api.duplicatePlan(planID)
            case .activate:
                let target = try await api.planOverview(planID)
                overview = try await api.activatePlan(
                    planID, revision: target.revision
                )
            }
            await loadPlans()
            return true
        } catch {
            applyError = "Couldn't do that. Nothing changed."
            return false
        }
    }

    // MARK: - cards

    /// Proposal state belongs to one completed item. Reset it before opening a
    /// different item's gate so the destination generates that item's candidates
    /// instead of briefly reusing the previous item's response.
    func prepareCardProposals() {
        cardLoad = .idle
        cards = nil
        selectedProposals = []
        addedProposals = []
        cardError = nil
        expandedGate = nil
        acceptKey = UUID().uuidString
    }

    func loadCardProposals(generate: Bool) async {
        guard let item else { return }
        cardLoad = .loading
        cardError = nil
        do {
            cards = generate
                ? try await api.createCardProposals(item.planId, itemID: item.id)
                : try await api.cardProposals(item.planId, itemID: item.id)
            selectedProposals = Set(cards?.proposals.filter(\.isSuggested).map(\.id) ?? [])
            cardLoad = .ready
        } catch {
            cardLoad = .error
        }
    }

    func toggleProposal(_ id: UUID) {
        if selectedProposals.contains(id) { selectedProposals.remove(id) }
        else { selectedProposals.insert(id) }
    }

    var addLabel: String {
        selectedProposals.count == 1
            ? "Add 1 card" : "Add \(selectedProposals.count) cards"
    }

    /// The retry reuses the key and the selection deliberately: same key plus
    /// same request means the server recognises a replay and returns the original
    /// response instead of creating a second card.
    func addSelectedCards() async {
        guard let cards, !selectedProposals.isEmpty else { return }
        addingCards = true
        cardError = nil
        defer { addingCards = false }
        do {
            let result = try await api.acceptCardProposals(
                cards.planId, selected: Array(selectedProposals),
                idempotencyKey: acceptKey,
                revision: cards.proposals.first?.revision ?? 1,
                edits: [:]
            )
            // ADDED only after a committed response, never optimistically.
            addedProposals.formUnion(selectedProposals)
            selectedProposals = []
            _ = result
        } catch APIError.status(409) {
            cardError = "One of these already exists as a card. Nothing was created."
            await loadCardProposals(generate: false)
        } catch {
            cardError = "Couldn't add the card. Your selection and edits are still here."
        }
    }

    func resolveOverlap(_ proposalID: UUID, action: String) async {
        guard let cards else { return }
        try? await api.resolveDuplicate(cards.planId, proposalID: proposalID, action: action)
        await loadCardProposals(generate: false)
    }
}
