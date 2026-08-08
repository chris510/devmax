import Foundation

@MainActor
final class PublicOnboardingState: ObservableObject {
    private enum PendingAfterSignIn { case guide, manual, collection }
    enum Step: String {
        case welcome, material, guide, fileError, planPath, planIntent, planSetup
        case handoff, importing, importFailed, importReady, topics, manual
        case collections, collectionDetail, planPreview, review, scoring, pace
        case reminders, remindersDenied, empty, learnBranch
        case returning
        case studyMaterial
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

    let api: DevmaxAPI
    private var pollTask: Task<Void, Never>?
    private var persistTask: Task<Void, Never>?
    private var pendingAfterSignIn: PendingAfterSignIn = .guide

    init(api: DevmaxAPI = APIConfig.client) {
        self.api = api
        draft = PublicSetupStore.read() ?? PublicSetupDraft()
        step = Self.debugStep(DebugFlags.shared.route) ?? .welcome
    }

    deinit {
        pollTask?.cancel()
        persistTask?.cancel()
    }

    var guideIsValid: Bool {
        draft.guideText.trimmingCharacters(in: .whitespacesAndNewlines).count >= 200
    }

    var preparedTitle: String {
        draft.title.isEmpty ? (draft.originalFilename.isEmpty ? "Your study guide" : draft.originalFilename) : draft.title
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
        case .guide: .guide
        }
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
        guard guideIsValid else {
            error = "Add at least 200 readable characters. Your draft is still saved."
            step = .fileError
            return
        }
        busy = true
        error = ""
        step = .importing
        do {
            let value = try await api.startMaterialImport(
                MaterialImportRequest(
                    title: preparedTitle, sourceText: draft.guideText,
                    originalFilename: draft.originalFilename, mimeType: draft.mimeType,
                    importPath: draft.importPath, intent: draft.intent,
                    requestedWeeks: draft.requestedWeeks,
                    weeklyCapacityMinutes: draft.weeklyCapacityHours * 60,
                    mode: "flexible", deadline: nil,
                    previousVersionId: draft.previousVersionID
                )
            )
            job = value
            draft.sourceID = value.id
            persist()
            beginPolling(value.id)
        } catch {
            self.error = "The guide is safe, but processing couldn't start."
            step = .importFailed
        }
        busy = false
    }

    func retryImport() async {
        guard let id = job?.id ?? draft.sourceID else { return await startImport() }
        step = .importing
        do {
            job = try await api.retryMaterialImport(id)
            beginPolling(id)
        } catch {
            self.error = "The guide is still saved. Try again when the service is reachable."
            step = .importFailed
        }
    }

    func restoreImportIfNeeded() async {
        guard draft.sourceID == nil else {
            if let id = draft.sourceID {
                job = try? await api.materialImport(id)
                routeImportResult()
            }
            return
        }
        guard let latest = try? await api.materialImports().first else { return }
        job = latest
        draft.sourceID = latest.id
        persist()
        routeImportResult()
    }

    func routeImportResult() {
        guard let job else { return }
        switch job.status {
        case "ready", "needs_attention":
            selectedTopics = job.cleanTopicIDs
            step = .importReady
        case "failed": step = .importFailed
        case "confirmed", "superseded": step = .empty
        default: step = .importing; beginPolling(job.id)
        }
    }

    private func beginPolling(_ id: UUID) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            let intervals = [2.0, 4.0, 8.0, 15.0]
            var intervalIndex = 0
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(intervals[intervalIndex])) }
                catch { return }
                guard let self else { return }
                do {
                    self.job = try await self.api.materialImport(id)
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
            plan.preview = try? await api.savedPlanPreview(draftID)
            plan.previewLoad = plan.preview == nil ? .error : .ready
            step = .planPreview
        } else {
            step = .topics
        }
    }

    func confirmTopics(app: AppState) async {
        guard let job else { return }
        busy = true
        defer { busy = false }
        do {
            let result = try await api.confirmMaterial(job.id, topics: Array(selectedTopics))
            await beginFirstReview(cardIDs: result.createdCardIds, app: app)
        } catch {
            self.error = "Resolve the highlighted topics before creating review cards."
        }
    }

    func updateTopic(
        _ topic: MaterialTopic, name: String, answerAnchor: String,
        action: String = "keep", mergeInto: UUID? = nil
    ) async {
        do {
            let updated = try await api.editMaterialTopic(
                topic.id, topic: name, answerAnchor: answerAnchor,
                action: action, mergeInto: mergeInto
            )
            if var current = job,
               let index = current.topics.firstIndex(where: { $0.id == topic.id })
            {
                current.topics[index] = updated
                job = current
                if updated.isClean { selectedTopics.insert(updated.id) }
                else { selectedTopics.remove(updated.id) }
            }
            editingTopic = nil
        } catch { self.error = "That topic edit couldn't be saved." }
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

    func beginGuideUpdate(_ source: MaterialImport) {
        var update = PublicSetupDraft()
        update.title = source.title
        update.importPath = source.importPath
        update.intent = source.intent
        update.requestedWeeks = draft.requestedWeeks
        update.weeklyCapacityHours = draft.weeklyCapacityHours
        update.previousVersionID = source.id
        draft = update
        persist()
        step = .guide
    }

    func deleteMaterial(_ id: UUID) async {
        do {
            try await api.deleteMaterialImport(id)
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

    private func beginFirstReview(cardIDs: [UUID], app: AppState) async {
        let library = (try? await api.cards(sort: "next_review", mode: "conversational")) ?? []
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

    static func handlesDebugRoute(_ route: String) -> Bool { debugStep(route) != nil }

    private static func debugStep(_ route: String) -> Step? {
        switch route {
        case "welcome": .welcome
        case "material": .material
        case "guide", "file-import": .guide
        case "file-error": .fileError
        case "signin", "signin-error": .handoff
        case "plan-path": .planPath
        case "plan-intent": .planIntent
        case "plan-setup", "capacity-conflict": .planSetup
        case "extracting", "import-background": .importing
        case "extract-error": .importFailed
        case "import-ready": .importReady
        case "topics", "topics-grouped", "needs-attention", "topic-edit": .topics
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
        case "returning": .returning
        case "guide-update": .importReady
        default: nil
        }
    }
}
