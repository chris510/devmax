import SwiftUI
import UIKit
import UserNotifications

struct FullSettingsScreen: View {
    @Environment(\.scenePhase) private var scenePhase
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var flow: PublicOnboardingState
    @EnvironmentObject private var auth: AuthState
    @AppStorage(Preferences.readAloudKey) private var readAloud = true
    @State private var notificationStatus = "CHECKING"
    @State private var studyReminderCount: Int?
    @State private var showSignOut = false

    var body: some View {
        PublicSettingsPage(title: "Settings", back: { state.path.removeLast() }) {
            settingsSection("STUDY") {
                destination("Material", value: materialValue) {
                    state.path.append(.library)
                }
                panelDivider
                destination("Study plan", value: planValue) {
                    if let id = state.planSummary?.planId {
                        state.path.append(.planOverview(id))
                    } else {
                        state.path.append(.planBuild)
                    }
                }
            }
            settingsSection("REVIEWS") {
                settingValue("Answering", value: "Voice + text")
                panelDivider
                toggleValue("Read aloud", isOn: $readAloud)
                panelDivider
                destination(
                    "Review reminders",
                    value: SettingsValidation.reminderValue(for: state.settings)
                ) {
                    state.path.append(.reviewReminders)
                }
            }
            settingsSection("NOTIFICATIONS") {
                destination("Permission", value: notificationStatus.capitalized) {
                    handleNotificationPermission()
                }
                panelDivider
                if let planID = state.planSummary?.planId {
                    destination("Study reminders", value: studyReminderValue) {
                        state.path.append(.planOverview(planID))
                    }
                } else {
                    settingValue("Study reminders", value: "Not set")
                }
            }
            settingsSection("PRIVACY") {
                destination("AI processing", value: aiProcessingValue) {
                    state.path.append(.privacy)
                }
                panelDivider
                destination("Data & privacy") { state.path.append(.privacy) }
            }
            settingsSection("ACCOUNT") {
                settingValue("Signed in with Apple", value: accountValue)
                panelDivider
                action("Sign out") { showSignOut = true }
                panelDivider
                destination("Delete account") { state.path.append(.deleteAccount) }
            }
            settingsSection("ABOUT") {
                settingValue(
                    "Devmax",
                    value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
                )
            }
        }
        .task {
            async let deviceSettings = UNUserNotificationCenter.current().notificationSettings()
            async let cards: Void = state.loadLibrary()
            async let collections = try? flow.api.materialCollections()
            async let summary = try? state.api.activePlan()

            let (settings, _, loadedCollections, loadedSummary) = await (
                deviceSettings, cards, collections, summary
            )
            notificationStatus = switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral: "ON"
            case .denied: "OFF"
            case .notDetermined: "NOT ASKED"
            @unknown default: "UNKNOWN"
            }
            if let loadedCollections { flow.collections = loadedCollections }
            if let loadedSummary { state.planSummary = loadedSummary }
            if let id = state.planSummary?.planId {
                studyReminderCount = await StudyReminderService.shared.pendingCount(planID: id)
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await refreshNotificationStatus() } }
        }
        .alert("Sign out on this device?", isPresented: $showSignOut) {
            Button("Cancel", role: .cancel) {}
            Button("Sign out", role: .destructive) { Task { await auth.signOutLocally() } }
        } message: {
            Text("Local credentials and unfinished local drafts will be removed. Account data stays on the server.")
        }
    }

    private func settingsSection<Content: View>(
        _ label: String, @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            MetaText(text: label, font: WCFont.mono(10), tracking: 1.1, color: Theme.metaFaint)
            QuietPanel { content() }
        }
    }

    private var panelDivider: some View { Hairline().padding(.horizontal, 1) }

    private func destination(
        _ title: String, value: String = "", action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(title).font(WCFont.sans(15, weight: 500)).foregroundStyle(Theme.text)
                Spacer(minLength: 8)
                if !value.isEmpty {
                    Text(value)
                        .font(WCFont.mono(10.5))
                        .foregroundStyle(Theme.metaAlt)
                        .lineLimit(1)
                }
                Text("›").font(WCFont.sans(17)).foregroundStyle(Theme.metaFaint)
            }
            .frame(minHeight: 56)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func settingValue(_ title: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(WCFont.sans(15, weight: 500)).foregroundStyle(Theme.text)
            Spacer(minLength: 10)
            Text(value).font(WCFont.mono(10.5)).foregroundStyle(Theme.metaAlt).lineLimit(1)
        }
        .frame(minHeight: 56)
    }

    private func toggleValue(_ title: String, isOn: Binding<Bool>) -> some View {
        HStack(spacing: 12) {
            Text(title).font(WCFont.sans(15, weight: 500)).foregroundStyle(Theme.text)
            Spacer()
            Text(isOn.wrappedValue ? "On" : "Off")
                .font(WCFont.mono(10.5)).foregroundStyle(Theme.metaAlt)
            Toggle34(isOn: isOn, accessibilityLabel: title)
        }
        .frame(minHeight: 56)
    }

    private func action(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(WCFont.sans(15, weight: 500))
                .foregroundStyle(Theme.text)
                .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var materialValue: String {
        switch state.libraryLoad {
        case .loading where state.library.isEmpty: "Checking"
        case .error where state.library.isEmpty: "Unavailable"
        default: "\(state.library.count) cards"
        }
    }

    private var planValue: String {
        guard let summary = state.planSummary, summary.active else { return "Not set" }
        return summary.weekIndex.map { "Week \($0)" } ?? "Active"
    }

    private var studyReminderValue: String {
        guard let studyReminderCount else { return "Checking" }
        return studyReminderCount == 0 ? "Off" : "\(studyReminderCount) on"
    }

    private var aiProcessingValue: String {
        auth.profile?.aiProcessingAllowed == true ? "Allowed" : "Not allowed"
    }

    private var accountValue: String {
        if let email = auth.profile?.email, !email.isEmpty { return email }
        if let name = auth.profile?.displayName, !name.isEmpty { return name }
        return "Connected"
    }

    private func handleNotificationPermission() {
        Task {
            let center = UNUserNotificationCenter.current()
            let current = await center.notificationSettings()
            if current.authorizationStatus == .notDetermined {
                let granted = (try? await center.requestAuthorization(options: [.alert, .sound]))
                    ?? false
                if granted, !DebugFlags.shared.useMockAPI {
                    UIApplication.shared.registerForRemoteNotifications()
                }
                await refreshNotificationStatus()
            } else if let url = URL(string: UIApplication.openSettingsURLString) {
                await UIApplication.shared.open(url)
            }
        }
    }

    @MainActor
    private func refreshNotificationStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        notificationStatus = switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral: "ON"
        case .denied: "OFF"
        case .notDetermined: "NOT ASKED"
        @unknown default: "UNKNOWN"
        }
    }
}

struct ReviewRemindersScreen: View {
    @EnvironmentObject private var state: AppState
    @State private var draft: AppSettings = .placeholder
    @State private var saving = false
    @State private var errorText = ""

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: "SETTINGS")
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("One reminder per enabled window when a review is due.")
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.textMuted)
                        .lineSpacing(3)

                    QuietPanel {
                        ForEach(Array(draft.windows.indices), id: \.self) { index in
                            ReminderWindowEditor(
                                window: $draft.windows[index],
                                errorText: SettingsValidation.windowMessage(draft.windows[index])
                            )
                            if index < draft.windows.count - 1 { Hairline() }
                        }
                        if !draft.windows.isEmpty { Hairline() }
                        HStack(alignment: .firstTextBaseline) {
                            Text("Time zone")
                                .font(WCFont.sans(15, weight: 500))
                                .foregroundStyle(Theme.text)
                            Spacer()
                            Text(timeZoneLabel)
                                .font(WCFont.mono(10.5))
                                .foregroundStyle(Theme.metaAlt)
                        }
                        .frame(minHeight: 56)
                    }
                    .disabled(saving)

                    MetaText(
                        text: reminderSummary,
                        font: WCFont.mono(10), tracking: 0.5, color: Theme.metaFaint
                    )

                    if !errorText.isEmpty {
                        InlineNotice {
                            Text(errorText)
                                .font(WCFont.sans(13))
                                .foregroundStyle(Theme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .wcFade(Motion.fadeFast)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 18)
            }

            footer
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .onAppear { draft = state.settings }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button { dismissIfPresented() } label: {
                Text(SettingsNavigation.reviewRemindersBackLabel(for: state.path))
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .disabled(saving)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            Text("Review reminders")
                .font(TypeRole.screenTitle)
                .tracking(-0.6)
                .foregroundStyle(Theme.text)
                .accessibilityAddTraits(.isHeader)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.bottom, 12)
    }

    private var footer: some View {
        HStack(spacing: 14) {
            Button("Cancel") { dismissIfPresented() }
                .buttonStyle(.plain)
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textMuted)
                .frame(minWidth: 82, minHeight: Metrics.minTapTarget)
                .disabled(saving)

            PrimaryButton(
                title: saving ? "Saving…" : "Save changes",
                enabled: normalizedDraft != state.settings && !saving
                    && SettingsValidation.message(for: normalizedDraft) == nil
            ) {
                startSaving()
            }
        }
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.top, 10)
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }

    private var timeZoneLabel: String {
        let part = draft.timezone.split(separator: "/").last.map(String.init) ?? draft.timezone
        return part.replacingOccurrences(of: "_", with: " ")
    }

    private var enabledWindowCount: Int { draft.windows.filter(\.on).count }

    private var reminderSummary: String {
        let value = SettingsValidation.reminderValue(for: draft)
        return value == "Off" ? value : "\(value) daily"
    }

    /// The server field stays for wire compatibility. The UI offers one
    /// reminder per enabled window, so the daily cap must match that promise.
    private var normalizedDraft: AppSettings {
        SettingsValidation.normalizedReminderSettings(draft)
    }

    private func startSaving() {
        guard !saving else { return }
        let value = normalizedDraft
        if let validation = SettingsValidation.message(for: value) {
            errorText = validation
            return
        }

        saving = true
        errorText = ""
        Task { await persist(value) }
    }

    @MainActor
    private func persist(_ value: AppSettings) async {
        do {
            state.settings = try await state.api.updateSettings(value)
            saving = false
            dismissIfPresented()
        } catch {
            saving = false
            errorText = "Couldn't save changes. Your edits are still here."
        }
    }

    @MainActor
    private func dismissIfPresented() {
        guard !saving else { return }
        SettingsNavigation.popReviewRemindersIfPresented(from: &state.path)
    }
}

private struct ReminderWindowEditor: View {
    @Binding var window: NotificationWindow
    let errorText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Text(window.label)
                    .font(WCFont.sans(15, weight: 500))
                    .foregroundStyle(Theme.text)
                Spacer()
                Toggle34(
                    isOn: $window.on,
                    accessibilityLabel: "\(window.label) reminder"
                )
            }

            HStack(spacing: 10) {
                timeField("START", value: $window.from)
                timeField("END", value: $window.to)
            }

            if let errorText {
                Text(errorText)
                    .font(WCFont.sans(12.5))
                    .foregroundStyle(Theme.scoreLow)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 14)
    }

    private func timeField(_ label: String, value: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            MetaText(
                text: label, font: WCFont.mono(9.5), tracking: 0.7,
                color: Theme.metaFaint
            )
            DatePicker(
                label,
                selection: SettingsValidation.dateBinding(for: value),
                displayedComponents: .hourAndMinute
            )
            .labelsHidden()
            .datePickerStyle(.compact)
            .tint(Theme.textMuted)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
        .overlay(
            RoundedRectangle(cornerRadius: Metrics.inputRadius)
                .strokeBorder(Theme.border, lineWidth: 1)
        )
    }
}

struct DataPrivacyScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var flow: PublicOnboardingState
    @EnvironmentObject private var auth: AuthState
    @State private var exporting = false
    @State private var exportURL: URL?
    @State private var error = ""
    @State private var savingConsent = false
    @State private var confirmWithdrawal = false

    var body: some View {
        PublicSettingsPage(title: "Data & privacy", back: { state.path.removeLast() }) {
            Text("How AI processing works")
                .font(WCFont.serif(22)).foregroundStyle(Theme.textStrong)
            privacyNote(
                "Provider",
                "Unprompted uses Anthropic for guide processing, question generation, "
                    + "and optional coaching. Answer scoring may use Anthropic or OpenAI. "
                    + "During a limited evaluation, the same answer may go to both while "
                    + "only Anthropic decides the result."
            )
            privacyNote(
                "Guide processing",
                "Unprompted sends guide text, its title, and the plan length, weekly "
                    + "capacity, mode, deadline, and subject hints you chose to Anthropic "
                    + "to propose topics and plan structure. Removing an imported source "
                    + "deletes that source and its transient import copy. A Study Plan you "
                    + "explicitly create keeps its guide provenance until you delete the account."
            )
            privacyNote(
                "Answer scoring",
                "Unprompted sends the topic, question, your transcript, any follow-up, "
                    + "the card's mastery summary, trusted answer basis, source excerpt, "
                    + "and rubric to Anthropic or OpenAI for scoring. Optional coaching "
                    + "stays with Anthropic. During the limited provider evaluation, the "
                    + "same scoring context may go to both at once. OpenAI's output cannot "
                    + "affect the result shown or saved; only privacy-safe comparison and "
                    + "usage metadata are retained. Both receive text, not an audio recording; iOS handles speech "
                    + "recognition. OpenAI also receives a stable pseudonymous safety "
                    + "identifier, not your Apple credential, name, or email."
            )
            privacyNote(
                "Anthropic data handling",
                "Anthropic states that standard API inputs and outputs are deleted from "
                    + "its systems within 30 days and are not used for model training by "
                    + "default. Exceptions can apply for abuse prevention, legal obligations, "
                    + "explicit opt-in, or a different agreement."
            )
            privacyNote(
                "OpenAI data handling",
                "OpenAI scoring uses the Responses API with store: false, so response "
                    + "application state is not retained for that request. OpenAI states "
                    + "standard API data is not used for training by default. Its default "
                    + "abuse-monitoring logs may include prompts and responses and are "
                    + "retained for up to 30 days, with limited legal and safety exceptions."
            )
            privacyNote(
                "Your control",
                "You can export account data, remove individual study material, or delete "
                    + "the entire account. Deletion is separate from signing out."
            )

            privacyNote(
                "AI processing choice",
                auth.profile?.aiProcessingAllowed == true
                    ? "Allowed · recorded for the current Anthropic and OpenAI disclosure."
                    : "Not allowed · AI features are unavailable, while saved lessons, "
                        + "plans, history, and settings remain usable."
            )
            if auth.profile?.aiProcessingAllowed == true {
                SecondaryButton(title: "Withdraw AI processing permission") {
                    confirmWithdrawal = true
                }
                .disabled(savingConsent)
            } else {
                PrimaryButton(title: savingConsent ? "Saving…" : "Allow AI processing") {
                    updateConsent("grant")
                }
                .disabled(savingConsent)
            }

            Link("Read Unprompted's privacy policy", destination: PrivacyLinks.policy)
                .font(WCFont.sans(14)).foregroundStyle(Theme.meta)
                .frame(minHeight: Metrics.minTapTarget)
            Link("Read Anthropic's API data policy", destination: PrivacyLinks.anthropicRetention)
                .font(WCFont.sans(14)).foregroundStyle(Theme.meta)
                .frame(minHeight: Metrics.minTapTarget)
            Link("Read OpenAI's API data controls", destination: PrivacyLinks.openAIDataControls)
                .font(WCFont.sans(14)).foregroundStyle(Theme.meta)
                .frame(minHeight: Metrics.minTapTarget)

            if let exportURL {
                ShareLink(item: exportURL) {
                    HStack { Text("Share account export"); Spacer(); Text("→").foregroundStyle(Theme.accent) }
                        .font(WCFont.sans(14.5)).foregroundStyle(Theme.text)
                        .frame(minHeight: Metrics.minTapTarget)
                }
            } else {
                Button(exporting ? "Preparing export…" : "Export my data") {
                    Task { await exportData() }
                }
                .buttonStyle(.plain).font(WCFont.sans(14.5)).foregroundStyle(Theme.text)
                .disabled(exporting).frame(minHeight: Metrics.minTapTarget)
            }
            if !error.isEmpty { Text(error).font(WCFont.sans(13)).foregroundStyle(Theme.scoreLow) }
            Hairline()
            Button("Delete account") { state.path.append(.deleteAccount) }
                .buttonStyle(.plain).font(WCFont.sans(14.5)).foregroundStyle(Theme.scoreLow)
                .frame(minHeight: Metrics.minTapTarget)
        }
        .alert("Withdraw AI processing permission?", isPresented: $confirmWithdrawal) {
            Button("Cancel", role: .cancel) {}
            Button("Withdraw", role: .destructive) { updateConsent("withdraw") }
        } message: {
            Text(
                "New guide processing, scoring, question generation, and AI coaching "
                    + "will stop when withdrawal completes. A guide request already "
                    + "authorized for transmission may finish. Your saved data and review "
                    + "schedule will not be deleted."
            )
        }
    }

    private func privacyNote(_ title: String, _ body: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(WCFont.sans(15, weight: 500)).foregroundStyle(Theme.text)
            Text(body).font(WCFont.sans(13.5)).foregroundStyle(Theme.textMuted).lineSpacing(3)
        }
    }

    private func exportData() async {
        exporting = true
        defer { exporting = false }
        do {
            let data = try await flow.api.exportAccount()
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("devmax-export.json")
            try data.write(to: url, options: .atomic)
            exportURL = url
        } catch { self.error = "The export couldn't be prepared. Try again." }
    }

    private func updateConsent(_ action: String) {
        guard !savingConsent else { return }
        savingConsent = true
        Task {
            let saved = await auth.updateAIConsent(action)
            if !saved { error = auth.errorMessage ?? "That privacy choice couldn't be saved." }
            savingConsent = false
        }
    }
}

struct DeleteAccountScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var auth: AuthState
    @State private var confirm = false
    @State private var deleting = false
    @State private var error = ""

    var body: some View {
        PublicSettingsPage(title: "Delete account", back: { state.path.removeLast() }) {
            Text("This removes the account from Devmax.")
                .font(WCFont.serif(23)).foregroundStyle(Theme.textStrong)
            Text("Study material, topics, cards, review history, schedules, plans, settings, and device tokens will be deleted. Signing out does not do this.")
                .font(WCFont.sans(14)).foregroundStyle(Theme.textMuted).lineSpacing(4)
            InlineNotice {
                Text("Apple authorization is revoked before server data is removed. If revocation is unavailable, deletion stops safely and can be retried.")
                    .font(WCFont.sans(13)).foregroundStyle(Theme.textSecondary).lineSpacing(3)
            }
            PrimaryButton(title: "Keep my account") { state.path.removeLast() }
            SecondaryButton(title: deleting ? "Deleting…" : "Delete my account") {
                confirm = true
            }
            .disabled(deleting)
            .opacity(deleting ? 0.55 : 1)
            if !error.isEmpty { Text(error).font(WCFont.sans(13)).foregroundStyle(Theme.scoreLow) }
        }
        .alert("Delete this account permanently?", isPresented: $confirm) {
            Button("Cancel", role: .cancel) {}
            Button("Delete account", role: .destructive) { Task { await delete() } }
        } message: { Text("This cannot be undone.") }
    }

    private func delete() async {
        deleting = true
        defer { deleting = false }
        do { try await auth.deleteAccount() }
        catch { self.error = "Deletion couldn't finish safely. Nothing was partially deleted." }
    }
}

private struct PublicSettingsPage<Content: View>: View {
    let title: String
    let back: () -> Void
    @ViewBuilder let content: Content

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: "SETTINGS")
            HStack {
                Button("← Back", action: back).buttonStyle(.plain)
                    .font(TypeRole.secondaryAction).foregroundStyle(Theme.metaAlt)
                Spacer()
            }
            .frame(minHeight: Metrics.minTapTarget).padding(.horizontal, Metrics.screenPadding)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text(title).font(TypeRole.screenTitle).foregroundStyle(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    content
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding).padding(.bottom, 30)
            }
        }
        .background(Theme.bg).navigationBarHidden(true)
    }
}
