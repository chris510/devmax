import Foundation

@MainActor
final class PublicOnboardingState: ObservableObject {
    private enum PendingAfterSignIn { case guide, manual, collection }
    private enum LessonExportError: Error { case missingWritebackBundle }
    enum Step: String {
        case welcome, material, guide, lesson, fileError, planPath, planIntent, planSetup
        case handoff, importing, importFailed, importReady, topics, manual
        case collections, collectionDetail, planPreview, review, scoring, pace
        case reminders, remindersDenied, empty, learnBranch
        case returning
        case studyMaterial
        case lessonCheck
    }
    enum LessonArtifactState: Equatable {
        case idle, preparing, ready, failed
    }
    enum LessonCheckStage: Equatable {
        case preview, loading, loadFailed
        case attempt, resume, recording, text
        case submitting, submitFailed
        case restudying, authority
        case confirming, confirmationFailed, held, recallReady, completeNoCards
        case transfer, transferResume, transferRecording, transferText
        case transferSubmitting, transferFailed, transferSubmitted, transferDebrief
    }

    @Published var step: Step
    @Published var draft: PublicSetupDraft
    @Published var job: MaterialImport?
    @Published var selectedTopics: Set<UUID> = []
    @Published var manualTopics = [ManualTopic(topic: "", answerAnchor: "")]
    @Published var collections: [MaterialCollection] = []
    @Published var collection: MaterialCollectionDetail?
    @Published var busy = false
    @Published var error = ""
    @Published var filePickerShown = false
    @Published var editingTopic: MaterialTopic?
    @Published var imports: [MaterialImport] = []
    @Published var lessonProgress: LessonProgress?
    @Published var lessonArtifactState: LessonArtifactState = .idle
    @Published var lessonExportURL: URL?
    @Published var lessonPilotPreview: MaterialLessonPreview?
    @Published var lessonCheckStage: LessonCheckStage = .preview
    @Published var activeLessonCheck: LessonCheck?
    @Published var lessonAuthority: MaterialTopicAuthority?
    @Published var lessonCheckDraft = ""
    @Published var completedLessonProposalIDs: Set<UUID> = []
    @Published var lessonRecallNotBeforeAt: Date?
    @Published var confirmedLessonCardIDs: [UUID] = []
    @Published private(set) var importStartedAt: Date?
    @Published private(set) var lastImportCheckedAt: Date?

    let api: DevmaxAPI
    let founderClaimAvailable: Bool
    private var pollTask: Task<Void, Never>?
    private var persistTask: Task<Void, Never>?
    private var lessonDraftSyncTask: Task<Void, Never>?
    private var pendingAfterSignIn: PendingAfterSignIn = .guide
    private var importGeneration = 0
    private var debugLessonRoutePrepared = false

    init(
        api: DevmaxAPI = APIConfig.client,
        route: String = DebugFlags.shared.route,
        founderClaimAvailable: Bool = APIConfig.hasFounderClaimToken
    ) {
        self.api = api
        self.founderClaimAvailable = founderClaimAvailable
        draft = PublicSetupStore.read() ?? PublicSetupDraft()
        step = Self.initialStep(
            route: route, founderClaimAvailable: founderClaimAvailable
        )
        if step == .importing { importStartedAt = Date() }
        if step == .lesson {
            draft.importPath = "lesson"
            draft.intent = "already_studied"
            if draft.sourceType == "guide" { draft.sourceType = "article" }
        }
        if step == .lessonCheck {
            draft.importPath = "lesson"
            draft.intent = "already_studied"
            draft.sourceType = "article"
        }
    }

    deinit {
        pollTask?.cancel()
        persistTask?.cancel()
        lessonDraftSyncTask?.cancel()
    }

    var guideIsValid: Bool {
        var readableCharacters = 0
        for character in draft.guideText where !character.isWhitespace {
            readableCharacters += 1
            if readableCharacters == 200 { return true }
        }
        return false
    }

    var isLessonDraft: Bool {
        if let job,
           [.importing, .importFailed, .importReady, .topics].contains(step) {
            return job.importPath == "lesson"
        }
        return draft.importPath == "lesson"
    }

    var lessonSourceURLIsValid: Bool {
        let value = draft.sourceURL.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty || SafeExternalURL.parse(value) != nil
    }

    var lessonContentProvenanceIsSelected: Bool {
        LessonContentProvenance(rawValue: draft.contentProvenance) != nil
    }

    var lessonIsValid: Bool {
        guideIsValid && lessonSourceURLIsValid && lessonContentProvenanceIsSelected
    }

    var canConfirmSelectedTopics: Bool {
        guard !busy, !selectedTopics.isEmpty else { return false }
        guard isLessonDraft else { return true }
        guard job?.requiresLessonGroundingRecovery != true else { return false }
        guard lessonContentProvenanceIsSelected else { return false }
        guard let job else { return false }
        let selectedAreClean = job.topics
            .filter { selectedTopics.contains($0.id) }
            .allSatisfy(\.isClean)
        let everyProposalHasDecision = job.topics.allSatisfy {
            $0.status == "excluded" || selectedTopics.contains($0.id)
        }
        return selectedAreClean && everyProposalHasDecision
    }

    var preparedTitle: String {
        draft.title.isEmpty
            ? (draft.originalFilename.isEmpty ? "Your study guide" : draft.originalFilename)
            : draft.title
    }

    var lessonGroundingRecoveryRequired: Bool {
        job?.requiresLessonGroundingRecovery == true
    }

    var lessonGroundingRecheckFailed: Bool {
        lessonGroundingRecoveryRequired && job?.status == "failed"
    }

    var handoffTitle: String {
        switch pendingAfterSignIn {
        case .manual: "My review topics"
        case .collection: collection?.title ?? "Devmax collection"
        case .guide: preparedTitle
        }
    }

    var handoffBackStep: Step {
        switch pendingAfterSignIn {
        case .manual: .manual
        case .collection: .collectionDetail
        case .guide: isLessonDraft ? .lesson : .guide
        }
    }

    /// Opens the focused article/lesson path without creating a second ingestion
    /// state machine. An unsent lesson draft is resumed; a submitted or unrelated
    /// draft starts clean while the durable server import remains in Study material.
    func beginLesson() {
        if job != nil || !isLessonDraft || draft.sourceID != nil {
            draft = PublicSetupDraft()
            draft.importPath = "lesson"
            draft.intent = "already_studied"
            draft.sourceType = "article"
        }
        clearActiveImport()
        persist()
        step = .lesson
    }

    /// Opens the guide path while preserving only an unsent guide draft. A user
    /// who explicitly asks for another guide always gets a new import identity.
    func beginGuide(forceNew: Bool = false) {
        if forceNew || job != nil || isLessonDraft || draft.sourceID != nil {
            draft = PublicSetupDraft()
        }
        clearActiveImport()
        persist()
        step = .guide
    }

    private func clearActiveImport() {
        importGeneration &+= 1
        pollTask?.cancel()
        job = nil
        selectedTopics = []
        lessonProgress = nil
        lessonArtifactState = .idle
        lessonExportURL = nil
        lessonPilotPreview = nil
        lessonCheckStage = .preview
        activeLessonCheck = nil
        lessonAuthority = nil
        lessonCheckDraft = ""
        completedLessonProposalIDs = []
        lessonRecallNotBeforeAt = nil
        confirmedLessonCardIDs = []
        lessonDraftSyncTask?.cancel()
        debugLessonRoutePrepared = false
        importStartedAt = nil
        lastImportCheckedAt = nil
        error = ""
        busy = false
    }

    func persist() {
        persistTask?.cancel()
        PublicSetupStore.save(draft)
    }

    func schedulePersist() {
        persistTask?.cancel()
        persistTask = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(500)) }
            catch { return }
            guard let self else { return }
            PublicSetupStore.save(self.draft)
        }
    }

    func prepareGuideImport(authenticated: Bool) {
        persist()
        pendingAfterSignIn = .guide
        if authenticated { Task { await startImport() } } else { step = .handoff }
    }

    func resumeAfterSignIn(app: AppState) {
        guard step == .handoff else { return }
        switch pendingAfterSignIn {
        case .guide: Task { await startImport() }
        case .manual: saveManual(authenticated: true, app: app)
        case .collection: Task { await addCollection(authenticated: true, app: app) }
        }
    }

    func startImport() async {
        guard !busy else { return }
        guard guideIsValid, !isLessonDraft || lessonIsValid else {
            if !guideIsValid {
                error = "Add at least 200 readable characters. Your draft is still saved."
            } else if !lessonSourceURLIsValid {
                error = "Use a full http or https source URL, or leave it blank."
            } else {
                error = "Choose what the pasted lesson text represents."
            }
            step = .fileError
            return
        }
        // `startImport` always creates a new durable source. Clear any prior
        // identity before the POST so saving/retry UI can never route an old job.
        clearActiveImport()
        draft.sourceID = nil
        persist()
        let generation = importGeneration
        busy = true
        defer {
            if generation == importGeneration { busy = false }
        }
        error = ""
        importStartedAt = Date()
        lastImportCheckedAt = nil
        step = .importing
        do {
            let value = try await api.startMaterialImport(
                MaterialImportRequest(
                    title: preparedTitle, sourceText: draft.guideText,
                    originalFilename: draft.originalFilename, mimeType: draft.mimeType,
                    kind: draft.sourceType,
                    sourceUrl: draft.sourceURL.trimmingCharacters(in: .whitespacesAndNewlines),
                    contentProvenance: draft.contentProvenance,
                    importPath: draft.importPath, intent: draft.intent,
                    requestedWeeks: draft.requestedWeeks,
                    weeklyCapacityMinutes: draft.weeklyCapacityHours * 60,
                    mode: "flexible", deadline: nil,
                    previousVersionId: draft.previousVersionID
                )
            )
            guard generation == importGeneration else { return }
            job = value
            lastImportCheckedAt = Date()
            if ["pending", "processing"].contains(value.status) {
                importStartedAt = value.updatedAt
            }
            draft.sourceID = value.id
            persist()
            routeImportResult()
        } catch {
            guard generation == importGeneration else { return }
            self.error = isLessonDraft
                ? "Your lesson is still saved, but processing couldn't start."
                : "The guide is safe, but processing couldn't start."
            step = .importFailed
        }
    }

    func retryImport() async {
        guard !busy else { return }
        guard let id = job?.id ?? draft.sourceID else { return await startImport() }
        let generation = importGeneration
        busy = true
        defer {
            if generation == importGeneration { busy = false }
        }
        error = ""
        importStartedAt = Date()
        lastImportCheckedAt = nil
        step = .importing
        do {
            // The server may have finished after this screen last refreshed.
            // Reconcile first so a stale failure screen never retries a ready job.
            let latest = try? await api.materialImport(id)
            guard generation == importGeneration else { return }
            if let latest,
               latest.status != "failed",
               !latest.requiresLessonGroundingRecovery {
                job = latest
                lastImportCheckedAt = Date()
                if ["pending", "processing"].contains(latest.status) {
                    importStartedAt = latest.updatedAt
                }
                routeImportResult()
                return
            }
            let retried = try await api.retryMaterialImport(id)
            guard generation == importGeneration else { return }
            job = retried
            lastImportCheckedAt = Date()
            if let job, ["pending", "processing"].contains(job.status) {
                importStartedAt = job.updatedAt
            }
            routeImportResult()
        } catch {
            guard generation == importGeneration else { return }
            self.error = isLessonDraft
                ? "The lesson is still saved. Try again when the service is reachable."
                : "The guide is still saved. Try again when the service is reachable."
            step = .importFailed
        }
    }

    func restoreImportIfNeeded() async {
        let generation = importGeneration
        guard draft.sourceID == nil else {
            if let id = draft.sourceID {
                let restored = try? await api.materialImport(id)
                guard generation == importGeneration else { return }
                job = restored
                if let job {
                    lastImportCheckedAt = Date()
                    if ["pending", "processing"].contains(job.status) {
                        importStartedAt = job.updatedAt
                    }
                }
                routeImportResult()
            }
            return
        }
        guard let latest = try? await api.materialImports().first else { return }
        guard generation == importGeneration else { return }
        job = latest
        lastImportCheckedAt = Date()
        if ["pending", "processing"].contains(latest.status) {
            importStartedAt = latest.updatedAt
        }
        draft.sourceID = latest.id
        persist()
        routeImportResult()
    }

    func routeImportResult() {
        guard let job else { return }
        if job.importPath == "lesson" {
            // A saved lesson owns its classification. Never carry a valid choice
            // from a different draft into this source and overwrite its meaning.
            let classification = job.contentProvenance.flatMap {
                LessonContentProvenance(rawValue: $0)?.rawValue
            } ?? LessonContentProvenance.legacyUnspecified
            if draft.contentProvenance != classification {
                draft.contentProvenance = classification
                persist()
            }
        }
        switch job.status {
        case "ready", "needs_attention":
            error = ""
            if job.requiresLessonGroundingRecovery {
                selectedTopics = []
                step = .importFailed
                return
            }
            // A focused lesson is a small set of concepts the learner can review
            // individually. Do not turn a structural check into implicit approval.
            selectedTopics = job.importPath == "lesson" ? [] : job.cleanTopicIDs
            step = .importReady
        case "failed": step = .importFailed
        case "confirmed", "superseded": step = .empty
        default:
            if importStartedAt == nil { importStartedAt = job.updatedAt }
            step = .importing
            beginPolling(job.id)
        }
    }

    func refreshActiveImport() async {
        guard [.importing, .importFailed].contains(step),
              let id = job?.id ?? draft.sourceID
        else { return }
        let generation = importGeneration
        guard let latest = try? await api.materialImport(id) else { return }
        guard generation == importGeneration,
              (job?.id ?? draft.sourceID) == id
        else { return }
        let previousStatus = job?.status
        job = latest
        lastImportCheckedAt = Date()
        if latest.status != previousStatus,
           ["pending", "processing"].contains(latest.status) {
            importStartedAt = latest.updatedAt
        }
        routeImportResult()
    }

    private func beginPolling(_ id: UUID) {
        pollTask?.cancel()
        let generation = importGeneration
        pollTask = Task { [weak self] in
            let intervals = [2.0, 4.0, 8.0, 15.0]
            var intervalIndex = 0
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(intervals[intervalIndex])) }
                catch { return }
                guard let self else { return }
                do {
                    let previousStatus = self.job?.status
                    let latest = try await self.api.materialImport(id)
                    guard !Task.isCancelled,
                          generation == self.importGeneration,
                          (self.job?.id ?? self.draft.sourceID) == id
                    else { return }
                    self.job = latest
                    self.lastImportCheckedAt = Date()
                    if latest.status != previousStatus,
                       ["pending", "processing"].contains(latest.status) {
                        self.importStartedAt = latest.updatedAt
                    }
                    guard ["pending", "processing"].contains(self.job?.status ?? "") else {
                        self.routeImportResult()
                        return
                    }
                } catch {
                    // The job is durable; a transient poll failure is not an import failure.
                }
                intervalIndex = min(intervalIndex + 1, intervals.count - 1)
            }
        }
    }

    func openImportedResult(plan: StudyPlanState) async {
        guard let job else { return }
        if job.importPath == "plan", let draftID = job.planDraftId {
            let generation = importGeneration
            let sourceID = job.id
            let preview = try? await api.savedPlanPreview(draftID)
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            plan.preview = preview
            plan.previewLoad = plan.preview == nil ? .error : .ready
            step = .planPreview
        } else if job.importPath == "lesson" {
            await openLessonPilotPreview(sourceID: job.id)
        } else {
            step = .topics
        }
    }

    var currentLessonTopicPreview: MaterialTopicPreview? {
        if let proposalID = activeLessonCheck?.proposalId {
            return lessonPilotPreview?.topics.first { $0.id == proposalID }
        }
        return lessonPilotPreview?.topics.first {
            $0.isAvailable && !completedLessonProposalIDs.contains($0.id)
        }
    }

    var remainingLessonTopicPreviews: [MaterialTopicPreview] {
        (lessonPilotPreview?.topics ?? []).filter {
            $0.isAvailable && !completedLessonProposalIDs.contains($0.id)
        }
    }

    var displayedLessonTopicPreviews: [MaterialTopicPreview] {
        (lessonPilotPreview?.topics ?? []).filter {
            $0.hasTransferEntryPoint
                || ($0.isAvailable && !completedLessonProposalIDs.contains($0.id))
        }
    }

    /// Opens the pilot-only preview contract. A non-enrolled account may still
    /// receive 404 during the additive rollout and remains on the legacy lesson
    /// screen; an enrolled account never falls back after any other error.
    func openLessonPilotPreview(sourceID: UUID? = nil) async {
        guard !busy, let sourceID = sourceID ?? job?.id ?? draft.sourceID else { return }
        let generation = importGeneration
        lessonCheckStage = .loading
        error = ""
        busy = true
        defer {
            if importContextIsCurrent(generation: generation, sourceID: sourceID) {
                busy = false
            }
        }
        do {
            let preview = try await api.markLessonReviewOpened(sourceID)
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            lessonPilotPreview = preview
            lessonCheckStage = .preview
            step = .lessonCheck
        } catch APIError.pilotSourceNotAssigned {
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            lessonPilotPreview = nil
            if let status = job?.status, ["confirmed", "superseded"].contains(status) {
                step = .empty
            } else {
                step = .topics
            }
        } catch APIError.pilotUpgradeRequired(let minimumBuild) {
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            let suffix = minimumBuild.map { " Build \($0) or later is required." } ?? ""
            error = "This pilot lesson needs a newer Devmax build.\(suffix)"
            lessonCheckStage = .loadFailed
            step = .lessonCheck
        } catch {
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            self.error = "The lesson is safe, but its private concept preview couldn't open."
            lessonCheckStage = .loadFailed
            step = .lessonCheck
        }
    }

    func confirmTopics(app: AppState) async {
        guard !busy, canConfirmSelectedTopics, let job else { return }
        let generation = importGeneration
        let sourceID = job.id
        let lesson = job.importPath == "lesson"
        let selected = job.topics
            .filter { selectedTopics.contains($0.id) }
            .sorted { $0.position < $1.position }
        busy = true
        defer {
            if importContextIsCurrent(generation: generation, sourceID: sourceID) {
                busy = false
            }
        }
        do {
            let result = try await api.confirmMaterial(
                sourceID,
                topics: selected.map(\.id),
                contentProvenance: lesson ? draft.contentProvenance : nil
            )
            guard importContextIsCurrent(generation: generation, sourceID: sourceID) else {
                return
            }
            if lesson {
                await beginLessonStudy(
                    cardIDs: result.createdCardIds, topics: selected, app: app,
                    generation: generation, sourceID: sourceID
                )
            } else {
                await beginFirstReview(
                    cardIDs: result.createdCardIds, app: app,
                    expectedImport: (generation: generation, sourceID: sourceID)
                )
            }
        } catch {
            guard importContextIsCurrent(generation: generation, sourceID: sourceID) else {
                return
            }
            self.error = "Resolve the highlighted topics before creating review cards."
        }
    }

    func beginLessonActivity(_ preview: MaterialTopicPreview) async {
        guard !busy, preview.isAvailable else { return }
        let generation = importGeneration
        let sourceID = lessonPilotPreview?.id ?? job?.id ?? draft.sourceID
        guard let sourceID else { return }
        error = ""
        lessonAuthority = nil
        busy = true
        lessonCheckStage = preview.formationQuestion == nil ? .restudying : .loading
        defer {
            if importContextIsCurrent(generation: generation, sourceID: sourceID) {
                busy = false
            }
        }
        do {
            if preview.formationQuestion == nil {
                let authority = try await api.startLessonRestudy(proposalID: preview.id)
                guard importContextIsCurrent(
                    generation: generation, sourceID: sourceID
                ) else { return }
                receiveLessonAuthority(authority)
                return
            }

            let check = try await api.startFormationCheck(proposalID: preview.id)
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            activeLessonCheck = check
            if check.status == .exposed {
                let authority = try await api.reopenLessonAuthority(checkID: check.id)
                guard importContextIsCurrent(
                    generation: generation, sourceID: sourceID
                ) else { return }
                receiveLessonAuthority(authority)
                return
            }
            let local = LessonCheckDraftStore.read(for: check.id)
            lessonCheckDraft = local ?? check.draftText
            if local == nil, !check.draftText.isEmpty {
                LessonCheckDraftStore.save(check.draftText, for: check.id)
            }
            lessonCheckStage = lessonCheckDraft.isEmpty ? .attempt : .resume
        } catch {
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            self.error = preview.formationQuestion == nil
                ? "The source is safe, but restudy couldn't open."
                : "The source-closed check couldn't open."
            lessonCheckStage = .loadFailed
        }
    }

    func updateLessonCheckDraft(_ text: String) {
        lessonCheckDraft = text
        guard let check = activeLessonCheck else { return }
        LessonCheckDraftStore.save(text, for: check.id)
        lessonDraftSyncTask?.cancel()
        lessonDraftSyncTask = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(500)) }
            catch { return }
            guard let self, self.activeLessonCheck?.id == check.id else { return }
            do {
                let saved = try await self.api.saveLessonCheckDraft(
                    checkID: check.id, text: text
                )
                if self.activeLessonCheck?.id == saved.id {
                    self.activeLessonCheck = saved
                }
            } catch {
                // Disk is intentionally the immediate source of truth. A submit
                // retries the full answer even when this cheap backup is offline.
            }
        }
    }

    func flushLessonCheckDraft() {
        lessonDraftSyncTask?.cancel()
        guard let check = activeLessonCheck else { return }
        let text = lessonCheckDraft
        LessonCheckDraftStore.save(text, for: check.id)
        lessonDraftSyncTask = Task { [weak self] in
            guard let self else { return }
            _ = try? await self.api.saveLessonCheckDraft(checkID: check.id, text: text)
        }
    }

    func discardLessonCheckDraft() {
        lessonDraftSyncTask?.cancel()
        if let check = activeLessonCheck {
            LessonCheckDraftStore.clear(for: check.id)
            Task { [api] in
                _ = try? await api.saveLessonCheckDraft(checkID: check.id, text: "")
            }
        }
        lessonCheckDraft = ""
    }

    func submitLessonAttempt() async {
        guard !busy, let check = activeLessonCheck,
              check.kind == .formation,
              !lessonCheckDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return }
        let text = lessonCheckDraft
        LessonCheckDraftStore.save(text, for: check.id)
        lessonDraftSyncTask?.cancel()
        busy = true
        lessonCheckStage = .submitting
        error = ""
        defer { busy = false }
        do {
            let authority = try await api.submitFormationCheck(
                checkID: check.id, text: text
            )
            guard activeLessonCheck?.id == check.id else { return }
            LessonCheckDraftStore.clear(for: check.id)
            lessonCheckDraft = ""
            receiveLessonAuthority(authority)
        } catch {
            guard activeLessonCheck?.id == check.id else { return }
            self.error = "Couldn't check this explanation. Your words are safe on this phone."
            lessonCheckStage = .submitFailed
        }
    }

    func reopenCurrentLessonAuthority() async {
        guard !busy, let check = activeLessonCheck else { return }
        busy = true
        lessonCheckStage = .loading
        error = ""
        defer { busy = false }
        do {
            receiveLessonAuthority(
                try await api.reopenLessonAuthority(checkID: check.id)
            )
        } catch {
            self.error = "The answer boundary is safe, but the source couldn't reopen."
            lessonCheckStage = .loadFailed
        }
    }

    private func receiveLessonAuthority(_ authority: MaterialTopicAuthority) {
        lessonAuthority = authority
        activeLessonCheck = authority.check
        if let current = lessonRecallNotBeforeAt {
            lessonRecallNotBeforeAt = max(current, authority.recallNotBeforeAt)
        } else {
            lessonRecallNotBeforeAt = authority.recallNotBeforeAt
        }
        lessonCheckStage = authority.check.kind == .transfer
            ? .transferDebrief
            : .authority
    }

    func acceptLessonAuthority() async {
        guard !busy, let authority = lessonAuthority,
              authority.check.kind == .formation
        else { return }
        selectedTopics.insert(authority.proposalId)
        completedLessonProposalIDs.insert(authority.proposalId)
        lessonDraftSyncTask?.cancel()
        LessonCheckDraftStore.clear(for: authority.check.id)
        lessonCheckDraft = ""
        activeLessonCheck = nil
        if remainingLessonTopicPreviews.isEmpty {
            await confirmPilotLesson()
        } else {
            lessonAuthority = nil
            lessonCheckStage = .preview
        }
    }

    func excludeLessonProposal(_ proposalID: UUID) async {
        guard !busy else { return }
        busy = true
        error = ""
        defer { busy = false }
        do {
            _ = try await api.excludePilotLessonProposal(proposalID)
            selectedTopics.remove(proposalID)
            completedLessonProposalIDs.insert(proposalID)
            if activeLessonCheck?.proposalId == proposalID,
               let checkID = activeLessonCheck?.id {
                clearCompletedLessonCheck(checkID)
            }
            if remainingLessonTopicPreviews.isEmpty {
                await confirmPilotLesson()
            } else {
                lessonAuthority = nil
                lessonCheckStage = .preview
            }
        } catch {
            self.error = "That exclusion couldn't be saved. No card was created."
        }
    }

    private func clearCompletedLessonCheck(_ checkID: UUID) {
        lessonDraftSyncTask?.cancel()
        LessonCheckDraftStore.clear(for: checkID)
        lessonCheckDraft = ""
        activeLessonCheck = nil
        lessonAuthority = nil
    }

    private func confirmPilotLesson() async {
        guard let sourceID = lessonPilotPreview?.id ?? job?.id ?? draft.sourceID else {
            return
        }
        let wasBusy = busy
        busy = true
        defer { busy = wasBusy }
        lessonCheckStage = .confirming
        do {
            let result = try await api.confirmMaterial(
                sourceID, topics: Array(selectedTopics),
                contentProvenance: lessonPilotPreview?.contentProvenance
                    ?? draft.contentProvenance
            )
            confirmedLessonCardIDs = result.createdCardIds
            lessonAuthority = nil
            activeLessonCheck = nil
            if selectedTopics.isEmpty {
                lessonCheckStage = .completeNoCards
            } else {
                lessonCheckStage = lessonRecallNotBeforeAt.map { $0 <= Date() } == true
                    ? .recallReady
                    : .held
            }
        } catch {
            self.error = "The formation work is safe, but these concepts couldn't be confirmed."
            lessonCheckStage = lessonAuthority == nil ? .confirmationFailed : .authority
        }
    }

    func retryPilotLessonConfirmation() async {
        guard !busy else { return }
        await confirmPilotLesson()
    }

    func beginTransferCheck(_ preview: MaterialTopicPreview) async {
        guard !busy else { return }
        busy = true
        error = ""
        lessonCheckStage = .loading
        defer { busy = false }
        do {
            let check = try await api.startTransferCheck(proposalID: preview.id)
            activeLessonCheck = check
            let local = LessonCheckDraftStore.read(for: check.id)
            lessonCheckDraft = local ?? check.draftText
            if check.status == .submitted {
                LessonCheckDraftStore.clear(for: check.id)
                lessonCheckDraft = ""
                lessonCheckStage = .transferSubmitted
            } else {
                lessonCheckStage = lessonCheckDraft.isEmpty ? .transfer : .transferResume
            }
        } catch {
            self.error = "The research check isn't available yet."
            lessonCheckStage = .loadFailed
        }
    }

    func prepareLessonCheckDebugRoute(_ route: String) async {
        guard route.hasPrefix("lesson-pilot-"), !debugLessonRoutePrepared else { return }
        if job == nil, let source = try? await api.materialImports().first {
            job = source
            draft.sourceID = source.id
        }
        if lessonPilotPreview == nil {
            await openLessonPilotPreview()
        }
        guard let preview = lessonPilotPreview?.topics.first else { return }
        debugLessonRoutePrepared = true

        switch route {
        case "lesson-pilot-preview":
            lessonCheckStage = .preview
        case "lesson-pilot-attempt":
            await beginLessonActivity(preview)
            lessonCheckStage = .attempt
        case "lesson-pilot-attempt-text":
            await beginLessonActivity(preview)
            updateLessonCheckDraft(
                "IP can lose and reorder packets, so transport adds the guarantees the application needs."
            )
            lessonCheckStage = .text
        case "lesson-pilot-resume":
            await beginLessonActivity(preview)
            updateLessonCheckDraft(
                "IP finds a route between networks, but the delivery contract is only best effort, so"
            )
            lessonCheckStage = .resume
        case "lesson-pilot-provider-failure":
            await beginLessonActivity(preview)
            updateLessonCheckDraft("IP is unreliable, so TCP makes it reliable.")
            await submitLessonAttempt()
        case "lesson-pilot-correction", "lesson-pilot-authority":
            await beginLessonActivity(preview)
            updateLessonCheckDraft("IP is unreliable, so TCP makes it reliable.")
            await submitLessonAttempt()
        case "lesson-pilot-restudy":
            await beginLessonActivity(preview)
        case "lesson-pilot-held", "lesson-pilot-recall-ready",
             "lesson-pilot-confirm-failure":
            await beginLessonActivity(preview)
            if activeLessonCheck?.condition == .attemptFirst {
                updateLessonCheckDraft(
                    "IP has no delivery or ordering guarantee; transport adds the contract it needs."
                )
                await submitLessonAttempt()
            }
            await acceptLessonAuthority()
            if route == "lesson-pilot-recall-ready" { lessonCheckStage = .recallReady }
        case "lesson-pilot-no-cards":
            await beginLessonActivity(preview)
            if activeLessonCheck?.condition == .attemptFirst {
                updateLessonCheckDraft("IP is unreliable, so TCP makes it reliable.")
                await submitLessonAttempt()
            }
            await excludeLessonProposal(preview.id)
        case "lesson-pilot-transfer":
            await beginTransferCheck(preview)
            lessonCheckStage = .transfer
        case "lesson-pilot-transfer-text":
            await beginTransferCheck(preview)
            updateLessonCheckDraft(
                "Sequencing, acknowledgements, retransmission, and deduplication must live above IP."
            )
            lessonCheckStage = .transferText
        case "lesson-pilot-transfer-failure":
            await beginTransferCheck(preview)
            updateLessonCheckDraft("The transport layer supplies those guarantees.")
            await submitLessonTransfer()
        case "lesson-pilot-transfer-submitted", "lesson-pilot-transfer-debrief":
            await beginTransferCheck(preview)
            updateLessonCheckDraft(
                "Transport supplies sequencing, acknowledgement, retransmission, and deduplication."
            )
            await submitLessonTransfer()
            if route == "lesson-pilot-transfer-debrief" { await openTransferDebrief() }
        default:
            break
        }
    }

    func submitLessonTransfer() async {
        guard !busy, let check = activeLessonCheck,
              check.kind == .transfer,
              !lessonCheckDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return }
        let text = lessonCheckDraft
        LessonCheckDraftStore.save(text, for: check.id)
        lessonDraftSyncTask?.cancel()
        busy = true
        lessonCheckStage = .transferSubmitting
        error = ""
        defer { busy = false }
        do {
            let saved = try await api.submitTransferCheck(checkID: check.id, text: text)
            guard activeLessonCheck?.id == check.id else { return }
            activeLessonCheck = saved
            LessonCheckDraftStore.clear(for: check.id)
            lessonCheckDraft = ""
            lessonCheckStage = .transferSubmitted
        } catch {
            self.error = "Couldn't submit the research check. Your words are safe on this phone."
            lessonCheckStage = .transferFailed
        }
    }

    func openTransferDebrief() async {
        guard !busy, let check = activeLessonCheck, check.kind == .transfer else { return }
        busy = true
        lessonCheckStage = .loading
        error = ""
        defer { busy = false }
        do {
            receiveLessonAuthority(
                try await api.lessonTransferDebrief(checkID: check.id)
            )
        } catch {
            self.error = "The response is locked, but its debrief couldn't open."
            lessonCheckStage = .transferSubmitted
        }
    }

    private func importContextIsCurrent(generation: Int, sourceID: UUID) -> Bool {
        generation == importGeneration && (job?.id ?? draft.sourceID) == sourceID
    }

    func loadLessonProgress() async {
        guard isLessonDraft, let id = job?.id ?? draft.sourceID else { return }
        let generation = importGeneration
        guard let value = try? await api.lessonProgress(id) else { return }
        guard importContextIsCurrent(generation: generation, sourceID: id) else {
            return
        }
        lessonProgress = value
    }

    /// Distillation is explicit and operates on confirmed source-backed concepts,
    /// never on the conversation transcript. iOS cannot write into a Mac vault,
    /// so it shares the server's provider-neutral JSON writeback bundle for the
    /// vault importer to validate and apply.
    func prepareLessonArtifacts() async {
        guard isLessonDraft, let id = job?.id ?? draft.sourceID else {
            error = "This lesson no longer has a source to export."
            lessonArtifactState = .failed
            return
        }
        let generation = importGeneration
        let artifactsReady = job?.artifactsReady == true
        lessonArtifactState = .preparing
        error = ""
        do {
            let progress: LessonProgress
            if let current = lessonProgress, current.complete {
                progress = current
            } else {
                progress = try await api.lessonProgress(id)
                guard importContextIsCurrent(
                    generation: generation, sourceID: id
                ) else { return }
                lessonProgress = progress
            }
            guard progress.complete else {
                error = "Finish the selected concepts before preparing learning notes."
                lessonArtifactState = .failed
                return
            }
            let artifacts: MaterialArtifacts
            if artifactsReady {
                artifacts = try await api.materialArtifacts(id)
            } else {
                artifacts = try await api.distillLesson(id)
            }
            guard importContextIsCurrent(
                generation: generation, sourceID: id
            ) else { return }
            lessonExportURL = try Self.writeLessonExport(artifacts)
            lessonArtifactState = .ready
        } catch {
            guard importContextIsCurrent(
                generation: generation, sourceID: id
            ) else { return }
            self.error = "The lesson is safe, but its distilled export couldn't be prepared."
            lessonArtifactState = .failed
        }
    }

    private static func writeLessonExport(_ artifacts: MaterialArtifacts) throws -> URL {
        guard let bundle = artifacts.writebackBundle else {
            throw LessonExportError.missingWritebackBundle
        }
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let body = try encoder.encode(bundle)
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("devmax-learning-writeback-\(artifacts.sourceId).json")
        try body.write(to: url, options: .atomic)
        return url
    }

    func updateTopic(
        _ topic: MaterialTopic, name: String, answerAnchor: String,
        action: String = "keep", mergeInto: UUID? = nil
    ) async {
        guard !busy, let sourceID = job?.id else { return }
        let generation = importGeneration
        busy = true
        defer {
            if importContextIsCurrent(generation: generation, sourceID: sourceID) {
                busy = false
            }
        }
        do {
            let updated = try await api.editMaterialTopic(
                topic.id, topic: name, answerAnchor: answerAnchor,
                action: action, mergeInto: mergeInto
            )
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            if var current = job,
               let index = current.topics.firstIndex(where: { $0.id == topic.id }) {
                current.topics[index] = updated
                job = current
                if isLessonDraft {
                    // Editing changes the content that was reviewed. A learner
                    // must inspect and select it again, even if structure is clean.
                    selectedTopics.remove(updated.id)
                } else if updated.isClean {
                    selectedTopics.insert(updated.id)
                } else {
                    selectedTopics.remove(updated.id)
                }
            }
            editingTopic = nil
        } catch {
            guard importContextIsCurrent(
                generation: generation, sourceID: sourceID
            ) else { return }
            self.error = "That topic edit couldn't be saved."
        }
    }

    func saveManual(authenticated: Bool, app: AppState) {
        let valid = manualTopics.filter {
            !$0.topic.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && !$0.answerAnchor.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
        guard valid.count == manualTopics.count, !valid.isEmpty else {
            error = "Each topic needs a trusted basis for what a good answer includes."
            return
        }
        guard authenticated else {
            pendingAfterSignIn = .manual
            step = .handoff
            return
        }
        Task {
            busy = true
            defer { busy = false }
            do {
                let result = try await api.createManualMaterial(title: "My topics", topics: valid)
                await beginFirstReview(cardIDs: result.createdCardIds, app: app)
            } catch { self.error = "Those topics couldn't be saved yet." }
        }
    }

    func loadCollections() async {
        collections = (try? await api.materialCollections()) ?? []
    }

    func loadStudyMaterial() async {
        imports = (try? await api.materialImports()) ?? []
    }

    func openSavedImport(_ source: MaterialImport) {
        clearActiveImport()
        job = source
        error = ""
        lastImportCheckedAt = Date()
        if ["pending", "processing"].contains(source.status) {
            importStartedAt = source.updatedAt
        }
        if source.importPath == "lesson",
           ["confirmed", "superseded"].contains(source.status) {
            lessonCheckStage = .loading
            step = .lessonCheck
            Task { await openLessonPilotPreview(sourceID: source.id) }
            return
        }
        routeImportResult()
    }

    func beginGuideUpdate(_ source: MaterialImport) {
        var update = PublicSetupDraft()
        update.title = source.title
        update.importPath = source.importPath
        update.intent = source.intent
        update.sourceType = source.kind
        update.sourceURL = source.sourceUrl ?? ""
        // Classification belongs to the new pasted content, not its lineage.
        // Require an explicit choice for every updated lesson version.
        update.contentProvenance = LessonContentProvenance.legacyUnspecified
        update.requestedWeeks = draft.requestedWeeks
        update.weeklyCapacityHours = draft.weeklyCapacityHours
        update.previousVersionID = source.id
        clearActiveImport()
        draft = update
        persist()
        step = source.importPath == "lesson" ? .lesson : .guide
    }

    func deleteMaterial(_ id: UUID) async {
        do {
            try await api.deleteMaterialImport(id)
            LessonCheckDraftStore.clearAll()
            imports.removeAll { $0.id == id }
        } catch { self.error = "That study material couldn't be removed." }
    }

    func openCollection(_ id: String) async {
        collection = try? await api.materialCollection(id)
        step = .collectionDetail
    }

    func addCollection(authenticated: Bool, app: AppState) async {
        guard let collection else { return }
        guard authenticated else {
            pendingAfterSignIn = .collection
            step = .handoff
            return
        }
        busy = true
        defer { busy = false }
        do {
            let result = try await api.addMaterialCollection(collection.id)
            await beginFirstReview(cardIDs: result.createdCardIds, app: app)
        } catch { self.error = "That collection is already in this account or unavailable." }
    }

    private func beginFirstReview(
        cardIDs: [UUID], app: AppState,
        expectedImport: (generation: Int, sourceID: UUID)? = nil
    ) async {
        let library = (try? await api.cards(sort: "next_review", mode: "conversational")) ?? []
        if let expectedImport {
            guard importContextIsCurrent(
                generation: expectedImport.generation,
                sourceID: expectedImport.sourceID
            ) else { return }
        }
        let wanted = Set(cardIDs)
        guard let card = library.first(where: { wanted.contains($0.id) })?.asQueueCard() else {
            error = "The topic was saved, but its first review couldn't open."
            step = .empty
            return
        }
        app.firstReviewCompletion = { [weak self] in self?.step = .scoring }
        app.beginSession(cards: [card], replacingPath: true)
        step = .review
    }

    private func beginLessonStudy(
        cardIDs: [UUID], topics: [MaterialTopic], app: AppState,
        generation: Int, sourceID: UUID
    ) async {
        let library = (try? await api.cards(sort: "next_review", mode: "conversational")) ?? []
        guard importContextIsCurrent(generation: generation, sourceID: sourceID) else {
            return
        }
        let cards = Self.orderedLessonCards(
            cardIDs: cardIDs, topics: topics, library: library
        )
        guard !cards.isEmpty else {
            error = "The concepts were saved, but this study session couldn't open."
            step = .empty
            return
        }
        app.beginSession(
            cards: cards, replacingPath: true, origin: .lesson
        )
        step = .review
    }

    /// Confirmation returns card ids, while the learner approved proposals in
    /// source order. Rejoin those two server-owned facts without depending on the
    /// library endpoint's next-review sort. Any unmatched rolling-deploy card is
    /// retained in the confirmation order rather than silently dropped.
    static func orderedLessonCards(
        cardIDs: [UUID], topics: [MaterialTopic], library: [CardSummary]
    ) -> [DueCard] {
        let wanted = Set(cardIDs)
        let candidates = library.filter { wanted.contains($0.id) }
        var used = Set<UUID>()
        var ordered: [CardSummary] = []

        for topic in topics.sorted(by: { $0.position < $1.position }) {
            guard let card = candidates.first(where: {
                !used.contains($0.id) && $0.topic == topic.topic
            }) else { continue }
            ordered.append(card)
            used.insert(card.id)
        }

        let byID = Dictionary(uniqueKeysWithValues: candidates.map { ($0.id, $0) })
        ordered.append(contentsOf: cardIDs.compactMap { id in
            guard !used.contains(id), let card = byID[id] else { return nil }
            used.insert(id)
            return card
        })
        return ordered.map { $0.asQueueCard() }
    }

    static func handlesDebugRoute(_ route: String) -> Bool {
        debugStep(route) != nil
    }

    static func initialStep(route: String, founderClaimAvailable: Bool) -> Step {
        debugStep(route)
            ?? (founderClaimAvailable ? .returning : .welcome)
    }

    private static func debugStep(_ route: String) -> Step? {
        switch route {
        case "welcome": .welcome
        case "material": .material
        case "guide", "file-import": .guide
        case "lesson-add": .lesson
        case "file-error": .fileError
        case "signin", "signin-error": .handoff
        case "plan-path": .planPath
        case "plan-intent": .planIntent
        case "plan-setup", "capacity-conflict": .planSetup
        case "extracting", "import-background": .importing
        case "extract-error": .importFailed
        case "import-ready": .importReady
        case "topics", "topics-grouped", "needs-attention", "topic-edit",
             "lesson-concepts", "lesson-concept-expanded": .topics
        case "lesson-pilot-preview", "lesson-pilot-attempt",
             "lesson-pilot-attempt-text", "lesson-pilot-resume",
             "lesson-pilot-provider-failure", "lesson-pilot-correction",
             "lesson-pilot-authority", "lesson-pilot-restudy",
             "lesson-pilot-confirm-failure",
             "lesson-pilot-held", "lesson-pilot-recall-ready",
             "lesson-pilot-no-cards",
             "lesson-pilot-transfer", "lesson-pilot-transfer-text",
             "lesson-pilot-transfer-failure", "lesson-pilot-transfer-submitted",
             "lesson-pilot-transfer-debrief": .lessonCheck
        case "manual", "manual-anchor": .manual
        case "collections": .collections
        case "collection-detail": .collectionDetail
        case "plan-preview": .planPreview
        case "scoring": .scoring
        case "pace": .pace
        case "reminders": .reminders
        case "reminders-denied": .remindersDenied
        case "empty": .empty
        case "learn-branch": .learnBranch
        // The debug route remains renderable in a keyless build so visual QA can
        // confirm the disabled state. Automatic production routing still requires
        // the claim token in `initialStep` above.
        case "returning": .returning
        case "guide-update": .importReady
        default: nil
        }
    }
}
