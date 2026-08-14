import PDFKit
import SwiftUI
import UniformTypeIdentifiers
import UserNotifications

private enum LessonSourceKind: String, CaseIterable, Identifiable {
    case article, documentation, book, course, notes, other

    var id: String { rawValue }
    var label: String {
        switch self {
        case .article: "Article"
        case .documentation: "Documentation"
        case .book: "Book or paper"
        case .course: "Course lesson"
        case .notes: "Notes"
        case .other: "Other"
        }
    }
}

struct PublicOnboardingView: View {
    @EnvironmentObject private var flow: PublicOnboardingState
    @EnvironmentObject private var app: AppState
    @EnvironmentObject private var plan: StudyPlanState
    @EnvironmentObject private var auth: AuthState

    var body: some View {
        Group {
            switch flow.step {
            case .welcome: welcome
            case .material: chooseMaterial
            case .guide, .lesson, .fileError: guide
            case .planPath: planPath
            case .planIntent: planIntent
            case .planSetup: planSetup
            case .handoff:
                AccountHandoffScreen(
                    preparedTitle: flow.handoffTitle,
                    preparedMeta: "\(flow.draft.guideText.count) CHARACTERS · SAVED ON THIS DEVICE",
                    backAction: { flow.step = flow.handoffBackStep },
                    forceFailure: DebugFlags.shared.route == "signin-error"
                )
            case .importing: importing
            case .importFailed: importFailed
            case .importReady: importReady
            case .topics: topics
            case .manual: manual
            case .collections: collections
            case .collectionDetail: collectionDetail
            case .planPreview: planPreview
            case .review: RootView()
            case .scoring: scoring
            case .pace: pace
            case .reminders, .remindersDenied: reminders
            case .empty: NoMaterialScreen()
            case .learnBranch: learnBranch
            case .returning: existingOwner
            case .studyMaterial: studyMaterial
            }
        }
        .background(Theme.bg)
        .onChange(of: auth.isAuthenticated) { _, signedIn in
            // Mock bootstrap is authenticated by design. Keep the dedicated
            // sign-in visual routes parked on their requested state; only a real
            // onboarding flow should advance after authentication changes.
            if signedIn, !["signin", "signin-error"].contains(DebugFlags.shared.route) {
                flow.resumeAfterSignIn(app: app)
            }
        }
        .sheet(item: $flow.editingTopic) { topic in
            TopicEditSheet(
                topic: topic,
                mergeTarget: flow.job?.topics.first {
                    $0.id != topic.id && $0.isClean
                },
                save: { name, anchor in
                    Task { await flow.updateTopic(topic, name: name, answerAnchor: anchor) }
                },
                exclude: {
                    Task {
                        await flow.updateTopic(
                            topic, name: topic.topic, answerAnchor: topic.answerAnchor,
                            action: "exclude"
                        )
                    }
                },
                merge: { target in
                    Task {
                        await flow.updateTopic(
                            topic, name: topic.topic, answerAnchor: topic.answerAnchor,
                            action: "merge", mergeInto: target.id
                        )
                    }
                }
            )
            .presentationDetents([.large])
            .presentationBackground(Theme.surface)
        }
        .task(id: flow.step.rawValue) {
            if [.importReady, .topics].contains(flow.step), flow.job == nil,
               let item = try? await flow.api.materialImports().first
            {
                flow.job = item
                flow.selectedTopics = item.cleanTopicIDs
                if DebugFlags.shared.route == "topic-edit" {
                    flow.editingTopic = item.topics.first
                }
            }
            if flow.step == .collectionDetail, flow.collection == nil {
                await flow.openCollection("system-design-foundations")
            }
            if flow.step == .planPreview, plan.preview == nil {
                let id = UUID(uuidString: "00000000-0000-0000-0000-000000000904")!
                plan.preview = try? await flow.api.savedPlanPreview(id)
                plan.previewLoad = plan.preview == nil ? .error : .ready
            }
        }
    }

    private var welcome: some View {
        PublicPage(kicker: "DEVMAX", title: "Understand it.\nThen keep it.") {
            Text("Bring the material you already trust. Devmax turns it into short, spoken retrieval practice and schedules what to revisit.")
                .publicBody()
            PublicNote("No streaks. No invented curriculum. Your source remains the source.")
        } footer: {
            PrimaryButton(title: "Start with my material") { flow.step = .material }
            Button("I already have an account") {
                flow.step = auth.isAuthenticated ? .empty : .handoff
            }
            .publicSecondary()
        }
    }

    private var chooseMaterial: some View {
        PublicPage(back: { flow.step = .welcome }, kicker: "SETUP · 1 OF 3", title: "What do you want to study?") {
            PublicChoice(
                title: "Bring a guide", detail: "Paste text or choose a text-based PDF, TXT, or Markdown file.",
                badge: "RECOMMENDED"
            ) { flow.step = .guide }
            PublicChoice(title: "Add a few topics", detail: "Fast setup. Each topic needs a trusted answer anchor.") {
                flow.step = .manual
            }
            PublicChoice(title: "Browse Devmax collections", detail: "Reviewed starter material where a collection is available.") {
                flow.step = .collections
                Task { await flow.loadCollections() }
            }
        }
    }

    private var guide: some View {
        PublicPage(
            back: { backFromSourceEntry() },
            kicker: flow.isLessonDraft ? "NEW LESSON" : "YOUR SOURCE",
            title: flow.isLessonDraft ? "Add a lesson" : "Add your study guide"
        ) {
            TextField(
                flow.isLessonDraft ? "Lesson title" : "Guide title",
                text: $flow.draft.title
            )
                .publicField()
                .onChange(of: flow.draft.title) { _, _ in flow.schedulePersist() }
            if flow.isLessonDraft { lessonSourceFields }
            TextEditor(text: $flow.draft.guideText)
                .font(WCFont.sans(14.5))
                .foregroundStyle(Theme.text)
                .scrollContentBackground(.hidden)
                .frame(minHeight: 230)
                .padding(11)
                .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
                .overlay(RoundedRectangle(cornerRadius: Metrics.inputRadius).stroke(Theme.border))
                .onChange(of: flow.draft.guideText) { _, _ in flow.schedulePersist() }
            HStack {
                MetaText(
                    text: "\(flow.draft.guideText.count) CHARACTERS",
                    font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                )
                Spacer()
                Button("Choose a file") { flow.filePickerShown = true }
                    .font(TypeRole.secondaryAction).foregroundStyle(Theme.meta)
            }
            if flow.step == .fileError || !flow.error.isEmpty { PublicError(flow.error) }
            if flow.isLessonDraft {
                PublicNote(
                    "The URL is attribution only and is never fetched. "
                        + "Pasted text is stored with this lesson until you delete it. "
                        + "Only distilled notes and recall prompts are exported."
                )
            } else {
                PublicNote("Devmax sends the guide text needed to propose review topics to its AI provider. The source stays attached to your account so you can review or delete it later.")
            }
        } footer: {
            if flow.isLessonDraft {
                PrimaryButton(title: "Extract concepts", enabled: flow.lessonIsValid) {
                    flow.persist()
                    flow.prepareGuideImport(authenticated: auth.isAuthenticated)
                }
            } else {
                PrimaryButton(title: "Continue", enabled: flow.guideIsValid) {
                    flow.step = .planPath
                }
            }
        }
        .fileImporter(
            isPresented: $flow.filePickerShown,
            allowedContentTypes: [.plainText, .pdf, UTType(filenameExtension: "md") ?? .text]
        ) { result in importFile(result) }
    }

    private var planPath: some View {
        PublicPage(back: { flow.step = .guide }, kicker: "HOW TO USE IT", title: "Turn this guide into…") {
            PublicChoice(title: "A Study Plan", detail: "Organize the whole guide across weeks, with retrieval built in.", badge: "RECOMMENDED") {
                flow.draft.importPath = "plan"; flow.persist(); flow.step = .planIntent
            }
            PublicChoice(title: "Review topics only", detail: "Extract source-grounded topics and start practicing without a weekly plan.") {
                flow.draft.importPath = "topics"; flow.draft.intent = "already_studied"; flow.persist()
                flow.prepareGuideImport(authenticated: auth.isAuthenticated)
            }
        }
    }

    private var planIntent: some View {
        PublicPage(back: { flow.step = .planPath }, kicker: "PLAN INTENT", title: "Where are you starting?") {
            PublicChoice(title: "I've already studied this", detail: "Begin retrieval after you review the extracted topics.") {
                flow.draft.intent = "already_studied"; flow.persist(); flow.step = .planSetup
            }
            PublicChoice(title: "I'm learning it over time", detail: "Unlock each review only after its study item is complete.") {
                flow.draft.intent = "learn"; flow.persist(); flow.step = .planSetup
            }
        }
    }

    private var planSetup: some View {
        PublicPage(back: { flow.step = .planIntent }, kicker: "PLAN CONSTRAINTS", title: "Set an honest pace") {
            publicStepper(label: "DURATION", value: "\(flow.draft.requestedWeeks) weeks", min: 2, max: 52, binding: $flow.draft.requestedWeeks)
            publicStepper(label: "WEEKLY CAPACITY", value: "\(flow.draft.weeklyCapacityHours) hours", min: 1, max: 40, binding: $flow.draft.weeklyCapacityHours)
            PublicNote("If the material does not fit, Devmax will ask you to add time, extend the plan, or reduce scope. It will not silently drop material.")
        } footer: {
            PrimaryButton(title: "Save and process guide") {
                flow.persist(); flow.prepareGuideImport(authenticated: auth.isAuthenticated)
            }
        }
    }

    private var importing: some View {
        PublicPage(kicker: "SAVED · PROCESSING", title: "Your guide is safe.") {
            Text("Devmax is reading the source structure and preparing proposals. A long guide may take a while.").publicBody()
            PublicMaterialCard(title: flow.preparedTitle, meta: "\(flow.draft.guideText.count) CHARACTERS · SAVED TO YOUR ACCOUNT")
            PublicNote("You can leave this screen or close the app. Processing continues, and the result will remain in Study material.")
        } footer: {
            SecondaryButton(title: "Go to Today") { leaveToToday() }
        }
    }

    private var importFailed: some View {
        PublicPage(kicker: "IMPORT PAUSED", title: "The guide is still here.") {
            Text(flow.error.isEmpty ? "Processing didn't finish. No topics or cards were created." : flow.error).publicBody()
            PublicMaterialCard(title: flow.preparedTitle, meta: "FULL SOURCE RETAINED · NOTHING CREATED")
        } footer: {
            PrimaryButton(title: "Try processing again") { Task { await flow.retryImport() } }
            Button(flow.isLessonDraft ? "Back to my lesson" : "Back to my guide") {
                flow.step = flow.isLessonDraft ? .lesson : .guide
            }.publicSecondary()
        }
    }

    private var importReady: some View {
        PublicPage(kicker: "IMPORT READY", title: "Review before anything is created.") {
            PublicMaterialCard(
                title: flow.job?.title ?? flow.preparedTitle,
                meta: "\(flow.job?.cleanCount ?? 3) READY · \(flow.job?.attentionCount ?? 0) NEED ATTENTION"
            )
            if let comparison = flow.job?.comparison, !comparison.isEmpty {
                PublicMaterialCard(
                    title: "Source version changes",
                    meta: "\(comparison["added", default: 0]) ADDED · \(comparison["changed", default: 0]) CHANGED · \(comparison["removed", default: 0]) REMOVED"
                )
                PublicNote("This is a proposal only. Existing cards, scores, review history, and the saved plan stay unchanged until you confirm the new version.")
            }
            Text("Clean proposals can be approved by section. Exceptions stay separate so a 16-week guide does not become a 100-row chore.").publicBody()
        } footer: {
            PrimaryButton(title: flow.isLessonDraft ? "Review concepts" : "Review proposals") {
                Task { await flow.openImportedResult(plan: plan) }
            }
            Button("Later — keep it in Study material") { leaveToToday() }.publicSecondary()
        }
    }

    private var topics: some View {
        PublicPage(
            back: { flow.step = .importReady },
            kicker: flow.isLessonDraft ? "REVIEW CONCEPTS" : "REVIEW TOPICS",
            title: "Confirm what you'll practice"
        ) {
            if let job = flow.job {
                ForEach(job.topics) { topic in
                    Button {
                        if flow.selectedTopics.contains(topic.id) { flow.selectedTopics.remove(topic.id) }
                        else if topic.isClean { flow.selectedTopics.insert(topic.id) }
                    } label: {
                        HStack(alignment: .top, spacing: 12) {
                            Text(flow.selectedTopics.contains(topic.id) ? "✓" : "○")
                                .foregroundStyle(topic.isClean ? Theme.accent : Theme.metaFaint)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(topic.topic).font(WCFont.sans(15, weight: 500)).foregroundStyle(Theme.text)
                                Text(
                                    topicSubtitle(topic)
                                )
                                    .font(WCFont.sans(12.5)).foregroundStyle(Theme.textMuted).lineLimit(3)
                                if flow.isLessonDraft,
                                   let prompts = topic.recallQuestions, !prompts.isEmpty
                                {
                                    MetaText(
                                        text: prompts.map(\.levelLabel).joined(separator: " · "),
                                        font: WCFont.mono(9), tracking: 0.3,
                                        color: Theme.metaFaint, uppercased: true
                                    )
                                }
                                if !topic.sectionTitle.isEmpty {
                                    MetaText(text: topic.sectionTitle, font: WCFont.mono(9.5), tracking: 0.5, color: Theme.metaFaint)
                                }
                            }
                            Spacer()
                        }
                        .padding(.vertical, 10)
                    }
                    .buttonStyle(.plain)
                    HStack(spacing: 18) {
                        Button("Edit source anchor") { flow.editingTopic = topic }
                        Button("Remove") {
                            Task {
                                await flow.updateTopic(
                                    topic, name: topic.topic, answerAnchor: topic.answerAnchor,
                                    action: "exclude"
                                )
                            }
                        }
                    }
                    .font(WCFont.sans(12.5)).foregroundStyle(Theme.meta)
                    .buttonStyle(.plain).frame(minHeight: Metrics.minTapTarget)
                    Hairline()
                }
            } else {
                PublicMaterialCard(title: "Offer", meta: "FORMATION · SOURCE ANCHOR READY")
                PublicMaterialCard(title: "Acceptance", meta: "FORMATION · SOURCE ANCHOR READY")
            }
            if !flow.error.isEmpty { PublicError(flow.error) }
        } footer: {
            PrimaryButton(
                title: flow.busy
                    ? "Creating…"
                    : (flow.isLessonDraft
                        ? (flow.selectedTopics.count == 1
                            ? "Study 1 concept"
                            : "Study \(flow.selectedTopics.count) concepts")
                        : "Create selected topics"),
                enabled: !flow.selectedTopics.isEmpty && !flow.busy
            ) {
                Task { await flow.confirmTopics(app: app) }
            }
        }
    }

    private var lessonSourceFields: some View {
        VStack(alignment: .leading, spacing: 10) {
            MetaText(
                text: "SOURCE TYPE", font: WCFont.mono(9.5),
                tracking: 0.8, color: Theme.meta
            )
            Menu {
                ForEach(LessonSourceKind.allCases) { kind in
                    Button(kind.label) {
                        flow.draft.sourceType = kind.rawValue
                        flow.schedulePersist()
                    }
                }
            } label: {
                HStack {
                    Text(LessonSourceKind(rawValue: flow.draft.sourceType)?.label ?? "Other")
                        .font(WCFont.sans(14.5))
                        .foregroundStyle(Theme.text)
                    Spacer()
                    Text("Change")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.meta)
                }
                .padding(.horizontal, 13)
                .frame(minHeight: Metrics.minTapTarget)
                .background(
                    Theme.inputFill,
                    in: RoundedRectangle(cornerRadius: Metrics.inputRadius)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: Metrics.inputRadius)
                        .strokeBorder(Theme.border, lineWidth: 1)
                )
            }

            TextField("Source URL (optional)", text: $flow.draft.sourceURL)
                .textInputAutocapitalization(.never)
                .keyboardType(.URL)
                .autocorrectionDisabled()
                .publicField()
                .onChange(of: flow.draft.sourceURL) { _, _ in flow.schedulePersist() }
            if !flow.lessonSourceURLIsValid {
                MetaText(
                    text: "USE A FULL HTTP OR HTTPS URL",
                    font: WCFont.mono(9.5), tracking: 0.7, color: Theme.scoreLow
                )
            }
        }
    }

    private var manual: some View {
        PublicPage(back: { flow.step = .material }, kicker: "MANUAL TOPICS", title: "Add what you want to recall") {
            ForEach(flow.manualTopics.indices, id: \.self) { index in
                VStack(alignment: .leading, spacing: 7) {
                    TextField("Topic", text: $flow.manualTopics[index].topic).publicField()
                    MetaText(text: "A GOOD ANSWER SHOULD INCLUDE…", font: WCFont.mono(9.5), tracking: 0.8, color: Theme.meta)
                    TextField("Trusted rule, mechanism, conditions, or limits", text: $flow.manualTopics[index].answerAnchor, axis: .vertical)
                        .publicField()
                }
            }
            Button("+ Add another topic") { flow.manualTopics.append(ManualTopic(topic: "", answerAnchor: "")) }
                .publicSecondary()
            if !flow.error.isEmpty { PublicError(flow.error) }
            PublicNote("An answer anchor is required before scored practice. Devmax will not invent one from a topic name.")
        } footer: {
            PrimaryButton(title: flow.busy ? "Saving…" : "Save and try one review", enabled: !flow.busy) {
                flow.saveManual(authenticated: auth.isAuthenticated, app: app)
            }
        }
    }

    private var collections: some View {
        PublicPage(back: { flow.step = .material }, kicker: "REVIEWED MATERIAL", title: "Devmax collections") {
            ForEach(flow.collections) { item in
                PublicChoice(title: item.title, detail: item.subtitle, badge: "V\(item.version) · \(item.topicCount) TOPICS") {
                    Task { await flow.openCollection(item.id) }
                }
            }
            if flow.collections.isEmpty {
                PublicChoice(title: "System design foundations", detail: "Reviewed core mechanisms for software-engineering interviews.", badge: "6 TOPICS") {
                    Task { await flow.openCollection("system-design-foundations") }
                }
            }
            PublicNote("No reviewed law, medicine, or anatomy collection is available yet. Bring your own trusted guide for those subjects.")
        }
    }

    private var collectionDetail: some View {
        PublicPage(back: { flow.step = .collections }, kicker: "REVIEWED COLLECTION", title: flow.collection?.title ?? "System design foundations") {
            Text(flow.collection?.subtitle ?? "Core mechanisms and design decisions for interviews.").publicBody()
            ForEach(flow.collection?.sections ?? ["Request and data foundations", "Concrete technologies", "Patterns and application"], id: \.self) {
                PublicMaterialCard(title: $0, meta: "REVIEWED SOURCE SECTION")
            }
            PublicNote(flow.collection?.sourceNote ?? "Versioned, reviewed starter material. You can inspect and remove it later.")
        } footer: {
            PrimaryButton(title: flow.busy ? "Adding…" : "Add collection", enabled: !flow.busy) {
                Task { await flow.addCollection(authenticated: auth.isAuthenticated, app: app) }
            }
        }
    }

    private var planPreview: some View {
        PublicPage(back: { flow.step = .importReady }, kicker: "PLAN PREVIEW", title: plan.preview?.title ?? "Review your plan") {
            if let preview = plan.preview {
                Text(preview.summaryLine).publicBody()
                Text(preview.forecastLabel).font(WCFont.sans(14)).foregroundStyle(Theme.textMuted)
                ForEach(preview.checks, id: \.key) { check in
                    Button {
                        guard !check.isResolved else { return }
                        Task { await plan.resolveCheck(check.key) }
                    } label: {
                        PublicMaterialCard(
                            title: check.label,
                            meta: check.isResolved ? "READY" : "NEEDS ATTENTION · TAP TO REVIEW"
                        )
                    }
                    .buttonStyle(.plain)
                }
                if !preview.canCreate { PublicError("Resolve each exception before the plan can be created. Nothing has changed yet.") }
            } else {
                PublicError("The saved plan preview could not be loaded. The guide is still attached to your account.")
            }
        } footer: {
            PrimaryButton(title: plan.creating ? "Creating…" : "Create plan", enabled: plan.preview?.canCreate == true && !plan.creating) {
                Task {
                    if await plan.createPlan() != nil {
                        if flow.draft.intent == "learn" { flow.step = .learnBranch }
                        else {
                            flow.selectedTopics = flow.job?.cleanTopicIDs ?? []
                            flow.step = .topics
                        }
                    }
                }
            }
        }
    }

    private var scoring: some View {
        PublicPage(
            kicker: "YOUR FIRST SCORE",
            title: app.usesRecallContract
                ? "One recall score. Coaching without grades."
                : "One number, three checks."
        ) {
            if app.usesRecallContract {
                Text("Recall measures whether the essential account was correct. It is the only signal that schedules the topic. When useful, you can practice going deeper or testing a boundary — without turning those answers into mastery scores.").publicBody()
                PublicMaterialCard(title: "Recall", meta: "DID YOU RETRIEVE THE ESSENTIAL IDEA?")
                PublicMaterialCard(title: "Go deeper", meta: "OPTIONAL QUALITATIVE PRACTICE AFTER A PASS")
            } else {
                Text("The 0–5 score summarizes this answer. Devmax always checks Accuracy, Depth, and Boundaries; only Accuracy changes the spaced-repetition interval.").publicBody()
                PublicMaterialCard(title: "Accuracy", meta: "WAS THE ESSENTIAL IDEA CORRECT?")
                PublicMaterialCard(title: "Depth", meta: "DID YOU EXPLAIN HOW OR WHY?")
                PublicMaterialCard(title: "Boundaries", meta: "DID YOU RECOGNIZE CONDITIONS OR LIMITS?")
            }
            PublicNote("Your answer and relevant study material are sent for scoring. Devmax receives the transcript, not an audio recording.")
        } footer: {
            PrimaryButton(title: "Set my review pace") { flow.step = .pace }
        }
    }

    private var pace: some View {
        PublicPage(kicker: "SETUP · 3 OF 3", title: "Choose a light daily pace") {
            publicStepper(label: "REVIEWS PER DAY", value: "\(app.settings.reviewsPerDay)", min: 1, max: 6, binding: Binding(
                get: { app.settings.reviewsPerDay },
                set: { app.settings.reviewsPerDay = $0 }
            ))
            PublicMaterialCard(title: "Morning", meta: "\(app.settings.windows.first?.from ?? "08:00")–\(app.settings.windows.first?.to ?? "09:00") · REVIEW WINDOW")
            PublicNote("This sets the scheduler's delivery window. iOS permission comes next, after you have chosen when reminders are useful.")
        } footer: {
            PrimaryButton(title: "Continue") { app.saveSettings(app.settings); flow.step = .reminders }
        }
    }

    private var reminders: some View {
        PublicPage(kicker: flow.step == .remindersDenied ? "REMINDERS OFF" : "OPTIONAL REMINDERS", title: flow.step == .remindersDenied ? "You're all set." : "Review when it fits.") {
            Text(flow.step == .remindersDenied ? "You declined notifications. Devmax still works normally whenever you open it." : "Allow one short prompt inside the review windows you just chose. No streak warnings or engagement nudges.").publicBody()
            PublicNote("You can change this later in Settings. Declining does not block setup.")
        } footer: {
            if flow.step == .remindersDenied {
                PrimaryButton(title: "Go to Today") { Task { await completeOnboarding() } }
            } else {
                PrimaryButton(title: "Enable reminders") { requestNotifications() }
                Button("Not now") { flow.step = .remindersDenied }.publicSecondary()
            }
        }
    }

    private var learnBranch: some View {
        PublicPage(kicker: "WEEK 1 READY", title: "Learn first. Retrieve second.") {
            Text("Your plan is ready. Complete the first study item before Devmax offers its review topic, so the first score measures recall rather than a cold guess.").publicBody()
        } footer: {
            PrimaryButton(title: "Open Week 1") { Task { await completeOnboarding() } }
        }
    }

    private var existingOwner: some View {
        PublicPage(kicker: "ACCOUNT UPGRADE", title: "Keep everything you've built.") {
            Text("Your existing cards, scores, review history, Study Plans, and notification windows are ready to attach to your Apple identity.").publicBody()
            PublicMaterialCard(title: "Existing Devmax library", meta: "NO ONBOARDING REPLAY · NO SCHEDULE CHANGES")
            PublicNote("This is a one-time owner claim. It does not create a second library or alter any spaced-repetition value.")
            if let error = auth.errorMessage { PublicError(error) }
        } footer: {
            AppleContinueButton(
                purpose: .founderClaim,
                enabled: flow.founderClaimAvailable,
                accessibilityHint: "Securely attaches the existing Devmax library to your Apple identity."
            )
        }
    }

    private var studyMaterial: some View {
        PublicPage(back: {
            if !app.path.isEmpty { app.path.removeLast() }
        }, kicker: "SETTINGS", title: "Study material") {
            if flow.imports.isEmpty {
                Text("No imported guides yet.").publicBody()
            } else {
                ForEach(flow.imports) { source in
                    VStack(alignment: .leading, spacing: 10) {
                        PublicMaterialCard(
                            title: source.title,
                            meta: "VERSION \(source.version) · \(source.status.uppercased()) · \(source.characterCount) CHARACTERS"
                        )
                        HStack(spacing: 18) {
                            Button("Import updated version") { flow.beginGuideUpdate(source) }
                            Button("Remove") { Task { await flow.deleteMaterial(source.id) } }
                                .foregroundStyle(Theme.scoreLow)
                        }
                        .buttonStyle(.plain).font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.meta).frame(minHeight: Metrics.minTapTarget)
                    }
                }
            }
            Hairline()
            PublicChoice(title: "Add another guide", detail: "Paste text or choose a supported text-based file.") { flow.step = .guide }
            PublicChoice(title: "Add manual topics", detail: "Create grounded review topics without a guide.") { flow.step = .manual }
            PublicChoice(title: "Browse collections", detail: "See reviewed, versioned starter material.") {
                flow.step = .collections; Task { await flow.loadCollections() }
            }
            if !flow.error.isEmpty { PublicError(flow.error) }
        }
        .task { await flow.loadStudyMaterial() }
    }

    private func publicStepper(
        label: String, value: String, min: Int, max: Int, binding: Binding<Int>
    ) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            MetaText(text: label, font: WCFont.mono(10), tracking: 1.0, color: Theme.meta)
            StepperControl(
                value: value,
                decrement: { binding.wrappedValue = Swift.max(min, binding.wrappedValue - 1) },
                increment: { binding.wrappedValue = Swift.min(max, binding.wrappedValue + 1) }
            )
        }
    }

    private func backFromSourceEntry() {
        if flow.isLessonDraft, !app.path.isEmpty {
            app.path.removeLast()
        } else {
            flow.step = .material
        }
    }

    private func topicSubtitle(_ topic: MaterialTopic) -> String {
        if flow.isLessonDraft {
            return topic.canonicalQuestion?.isEmpty == false
                ? (topic.canonicalQuestion ?? topic.answerAnchor)
                : topic.answerAnchor
        }
        return topic.answerAnchor.isEmpty ? topic.issue : topic.answerAnchor
    }

    private func leaveToToday() {
        if auth.profile?.onboardingCompleted == true, !app.path.isEmpty {
            app.path.removeLast()
        } else {
            flow.step = .empty
        }
    }

    private func importFile(_ result: Result<URL, Error>) {
        let url: URL
        do {
            url = try result.get()
        } catch {
            return showFileError()
        }
        Task { @MainActor in
            do {
                let imported = try await Task.detached(priority: .userInitiated) {
                    try extractStudyFile(at: url)
                }.value
                flow.draft.guideText = imported.text
                flow.draft.mimeType = imported.mimeType
                flow.draft.originalFilename = imported.filename
                if flow.draft.title.isEmpty { flow.draft.title = imported.title }
                flow.error = ""
                flow.step = flow.isLessonDraft ? .lesson : .guide
                flow.persist()
            } catch {
                showFileError()
            }
        }
    }

    private func showFileError() {
        flow.error = "That file does not contain enough readable text. Use a text-based PDF, TXT, or Markdown file. Your current draft was not replaced."
        flow.step = .fileError
    }

    private func requestNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
            DispatchQueue.main.async {
                if granted { UIApplication.shared.registerForRemoteNotifications() }
                flow.step = granted ? .reminders : .remindersDenied
                if granted { Task { await completeOnboarding() } }
            }
        }
    }

    private func completeOnboarding() async {
        flow.draft = PublicSetupDraft()
        PublicSetupStore.clear()
        await auth.markOnboardingComplete()
        await app.loadToday()
    }
}

private struct ImportedStudyFile: Sendable {
    let text: String
    let mimeType: String
    let filename: String
    let title: String
}

private enum FileImportError: Error { case unreadable, noText }

private func extractStudyFile(at url: URL) throws -> ImportedStudyFile {
    let accessed = url.startAccessingSecurityScopedResource()
    defer { if accessed { url.stopAccessingSecurityScopedResource() } }
    let data = try Data(contentsOf: url)
    let text: String
    let mimeType: String
    if url.pathExtension.lowercased() == "pdf" {
        guard let pdf = PDFDocument(data: data) else { throw FileImportError.unreadable }
        text = (0..<pdf.pageCount).compactMap { pdf.page(at: $0)?.string }
            .joined(separator: "\n\n")
        mimeType = "application/pdf"
    } else {
        guard let decoded = String(data: data, encoding: .utf8) else {
            throw FileImportError.unreadable
        }
        text = decoded
        mimeType = url.pathExtension.lowercased() == "md" ? "text/markdown" : "text/plain"
    }
    guard text.trimmingCharacters(in: .whitespacesAndNewlines).count >= 200 else {
        throw FileImportError.noText
    }
    return ImportedStudyFile(
        text: text,
        mimeType: mimeType,
        filename: url.lastPathComponent,
        title: url.deletingPathExtension().lastPathComponent
    )
}

private struct TopicEditSheet: View {
    let topic: MaterialTopic
    let mergeTarget: MaterialTopic?
    let save: (String, String) -> Void
    let exclude: () -> Void
    let merge: (MaterialTopic) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var anchor: String

    init(
        topic: MaterialTopic, mergeTarget: MaterialTopic?,
        save: @escaping (String, String) -> Void,
        exclude: @escaping () -> Void, merge: @escaping (MaterialTopic) -> Void
    ) {
        self.topic = topic; self.mergeTarget = mergeTarget
        self.save = save; self.exclude = exclude; self.merge = merge
        _name = State(initialValue: topic.topic)
        _anchor = State(initialValue: topic.answerAnchor)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("Edit topic").font(WCFont.serif(26)).foregroundStyle(Theme.textStrong)
                Spacer()
                Button("Close") { dismiss() }.buttonStyle(.plain)
                    .font(TypeRole.secondaryAction).foregroundStyle(Theme.meta)
            }
            TextField("Topic", text: $name).publicField()
            MetaText(text: "A GOOD ANSWER SHOULD INCLUDE…", font: WCFont.mono(10), tracking: 0.9, color: Theme.meta)
            TextField("Trusted basis", text: $anchor, axis: .vertical)
                .publicField().lineLimit(4...8)
            if !topic.sourceExcerpt.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    MetaText(text: "SOURCE EXCERPT", font: WCFont.mono(10), tracking: 0.9, color: Theme.metaFaint)
                    Text(topic.sourceExcerpt).font(WCFont.sans(13)).foregroundStyle(Theme.textMuted).lineSpacing(3)
                }
            }
            Spacer()
            PrimaryButton(
                title: "Save topic",
                enabled: !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    && !anchor.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ) { save(name, anchor); dismiss() }
            if let mergeTarget {
                SecondaryButton(title: "Merge into \(mergeTarget.topic)") {
                    merge(mergeTarget); dismiss()
                }
            }
            Button("Remove proposal") { exclude(); dismiss() }
                .buttonStyle(.plain).font(TypeRole.secondaryAction).foregroundStyle(Theme.scoreLow)
                .frame(maxWidth: .infinity, minHeight: Metrics.minTapTarget)
        }
        .padding(.horizontal, Metrics.screenPadding).padding(.top, 24)
        .padding(.bottom, Metrics.bottomSafeArea).background(Theme.surface)
    }
}

struct NoMaterialScreen: View {
    @EnvironmentObject private var flow: PublicOnboardingState

    var body: some View {
        PublicPage(kicker: "TODAY · NO MATERIAL", title: "Add something you want to understand.") {
            Text("A new account starts empty. Devmax will not create sample scores or a fake queue.").publicBody()
        } footer: {
            PrimaryButton(title: "Add study material") { flow.step = .material }
            SecondaryButton(title: "Add a few topics") { flow.step = .manual }
            Button("Browse Devmax collections") {
                flow.step = .collections
                Task { await flow.loadCollections() }
            }.publicSecondary()
        }
    }
}

private struct PublicPage<Content: View, Footer: View>: View {
    var back: (() -> Void)?
    let kicker: String
    let title: String
    @ViewBuilder let content: Content
    @ViewBuilder var footer: Footer

    init(
        back: (() -> Void)? = nil, kicker: String, title: String,
        @ViewBuilder content: () -> Content, @ViewBuilder footer: () -> Footer
    ) {
        self.back = back; self.kicker = kicker; self.title = title
        self.content = content(); self.footer = footer()
    }

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: kicker)
            if let back {
                Button("← Back", action: back).publicSecondary()
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, Metrics.screenPadding)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text(title)
                        .font(WCFont.serif(30)).foregroundStyle(Theme.textStrong)
                        .lineSpacing(4).accessibilityAddTraits(.isHeader)
                    content
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.top, back == nil ? 30 : 12)
                .padding(.bottom, 20)
            }
            VStack(spacing: 8) { footer }
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.top, 8)
                .padding(.bottom, Metrics.bottomSafeArea)
                .background(Theme.bg)
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
    }
}

private extension PublicPage where Footer == EmptyView {
    init(
        back: (() -> Void)? = nil, kicker: String, title: String,
        @ViewBuilder content: () -> Content
    ) {
        self.init(back: back, kicker: kicker, title: title, content: content) { EmptyView() }
    }
}

private struct PublicChoice: View {
    let title: String
    let detail: String
    var badge: String?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(title).font(WCFont.sans(16, weight: 500)).foregroundStyle(Theme.text)
                    Text(detail).font(WCFont.sans(13)).foregroundStyle(Theme.textMuted).lineSpacing(3)
                    if let badge { MetaText(text: badge, font: WCFont.mono(9.5), tracking: 0.7, color: Theme.metaFaint) }
                }
                Spacer(minLength: 8)
                Text("→").font(WCFont.mono(12)).foregroundStyle(Theme.accent)
            }
            .padding(15)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: Metrics.inlineRadius))
            .overlay(RoundedRectangle(cornerRadius: Metrics.inlineRadius).stroke(Theme.border))
        }
        .buttonStyle(.plain)
    }
}

private struct PublicMaterialCard: View {
    let title: String
    let meta: String
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(WCFont.sans(15, weight: 500)).foregroundStyle(Theme.text)
            MetaText(text: meta, font: WCFont.mono(9.5), tracking: 0.55, color: Theme.metaFaint)
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(14)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Metrics.inlineRadius))
        .overlay(RoundedRectangle(cornerRadius: Metrics.inlineRadius).stroke(Theme.border))
    }
}

private struct PublicNote: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        InlineNotice {
            Text(text).font(WCFont.sans(13)).foregroundStyle(Theme.textSecondary).lineSpacing(3)
        }
    }
}

private struct PublicError: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(WCFont.sans(13)).foregroundStyle(Theme.scoreLow).lineSpacing(3)
            .padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.scoreLow.opacity(0.08), in: RoundedRectangle(cornerRadius: Metrics.inlineRadius))
    }
}

private extension View {
    func publicBody() -> some View {
        font(WCFont.serif(17)).foregroundStyle(Theme.textSerif).lineSpacing(5)
            .fixedSize(horizontal: false, vertical: true)
    }

    func publicSecondary() -> some View {
        buttonStyle(.plain).font(TypeRole.secondaryAction).foregroundStyle(Theme.metaAlt)
            .frame(minHeight: Metrics.minTapTarget)
    }

    func publicField() -> some View {
        font(WCFont.sans(14.5)).foregroundStyle(Theme.text).tint(Theme.accent)
            .padding(12).background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
            .overlay(RoundedRectangle(cornerRadius: Metrics.inputRadius).stroke(Theme.border))
    }
}
