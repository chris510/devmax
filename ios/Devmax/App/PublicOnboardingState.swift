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
    }
    enum LessonArtifactState: Equatable {
        case idle, preparing, ready, failed
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

    let api: DevmaxAPI
    let founderClaimAvailable: Bool
    private var pollTask: Task<Void, Never>?
    private var persistTask: Task<Void, Never>?
    private var pendingAfterSignIn: PendingAfterSignIn = .guide

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
        if step == .lesson {
            draft.importPath = "lesson"
            draft.intent = "already_studied"
            if draft.sourceType == "guide" { draft.sourceType = "article" }
        }
    }

    deinit {
        pollTask?.cancel()
        persistTask?.cancel()
    }

    var guideIsValid: Bool {
        var readableCharacters = 0
        for character in draft.guideText where !character.isWhitespace {
            readableCharacters += 1
            if readableCharacters == 200 { return true }
        }
        return false
    }

    var isLessonDraft: Bool { draft.importPath == "lesson" }

    var lessonSourceURLIsValid: Bool {
        let value = draft.sourceURL.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty || SafeExternalURL.parse(value) != nil
    }

    var lessonIsValid: Bool { guideIsValid && lessonSourceURLIsValid }

    var preparedTitle: String {
        draft.title.isEmpty
            ? (draft.originalFilename.isEmpty ? "Your study guide" : draft.originalFilename)
            : draft.title
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
        pollTask?.cancel()
        if !isLessonDraft || draft.sourceID != nil {
            draft = PublicSetupDraft()
            draft.importPath = "lesson"
            draft.intent = "already_studied"
            draft.sourceType = "article"
        }
        job = nil
        selectedTopics = []
        lessonProgress = nil
        lessonArtifactState = .idle
        lessonExportURL = nil
        error = ""
        persist()
        step = .lesson
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
        guard guideIsValid, !isLessonDraft || lessonSourceURLIsValid else {
            error = lessonSourceURLIsValid
                ? "Add at least 200 readable characters. Your draft is still saved."
                : "Use a full http or https source URL, or leave it blank."
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
                    kind: draft.sourceType,
                    sourceUrl: draft.sourceURL.trimmingCharacters(in: .whitespacesAndNewlines),
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
            self.error = isLessonDraft
                ? "Your lesson is still saved, but processing couldn't start."
                : "The guide is safe, but processing couldn't start."
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
            self.error = isLessonDraft
                ? "The lesson is still saved. Try again when the service is reachable."
                : "The guide is still saved. Try again when the service is reachable."
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
            let selected = job.topics
                .filter { selectedTopics.contains($0.id) }
                .sorted { $0.position < $1.position }
            let result = try await api.confirmMaterial(job.id, topics: selected.map(\.id))
            if isLessonDraft {
                await beginLessonStudy(
                    cardIDs: result.createdCardIds, topics: selected, app: app
                )
            } else {
                await beginFirstReview(cardIDs: result.createdCardIds, app: app)
            }
        } catch {
            self.error = "Resolve the highlighted topics before creating review cards."
        }
    }

    func loadLessonProgress() async {
        guard isLessonDraft, let id = job?.id ?? draft.sourceID else { return }
        if let value = try? await api.lessonProgress(id) { lessonProgress = value }
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
        lessonArtifactState = .preparing
        error = ""
        do {
            let progress: LessonProgress
            if let current = lessonProgress, current.complete {
                progress = current
            } else {
                progress = try await api.lessonProgress(id)
                lessonProgress = progress
            }
            guard progress.complete else {
                error = "Finish the selected concepts before preparing learning notes."
                lessonArtifactState = .failed
                return
            }
            let artifacts: MaterialArtifacts
            if job?.artifactsReady == true {
                artifacts = try await api.materialArtifacts(id)
            } else {
                artifacts = try await api.distillLesson(id)
            }
            lessonExportURL = try Self.writeLessonExport(artifacts)
            lessonArtifactState = .ready
        } catch {
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
        do {
            let updated = try await api.editMaterialTopic(
                topic.id, topic: name, answerAnchor: answerAnchor,
                action: action, mergeInto: mergeInto
            )
            if var current = job,
               let index = current.topics.firstIndex(where: { $0.id == topic.id }) {
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
        update.sourceType = source.kind
        update.sourceURL = source.sourceUrl ?? ""
        update.requestedWeeks = draft.requestedWeeks
        update.weeklyCapacityHours = draft.weeklyCapacityHours
        update.previousVersionID = source.id
        draft = update
        persist()
        step = source.importPath == "lesson" ? .lesson : .guide
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

    private func beginLessonStudy(
        cardIDs: [UUID], topics: [MaterialTopic], app: AppState
    ) async {
        let library = (try? await api.cards(sort: "next_review", mode: "conversational")) ?? []
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
        // The debug route remains renderable in a keyless build so visual QA can
        // confirm the disabled state. Automatic production routing still requires
        // the claim token in `initialStep` above.
        case "returning": .returning
        case "guide-update": .importReady
        default: nil
        }
    }
}
