import SwiftUI
import UIKit
import UserNotifications

struct FullSettingsScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var flow: PublicOnboardingState
    @EnvironmentObject private var auth: AuthState
    @State private var notificationStatus = "CHECKING"
    @State private var showSignOut = false

    var body: some View {
        PublicSettingsPage(title: "Settings", back: { state.path.removeLast() }) {
            settingsSection("STUDY MATERIAL") {
                destination("Imported guides, topics, and plans") {
                    flow.step = .studyMaterial
                    state.path.append(.materialSetup)
                    Task { await flow.loadStudyMaterial() }
                }
            }
            settingsSection("REVIEWS") {
                settingValue("Reviews per day", value: "\(state.settings.reviewsPerDay)")
                settingValue("Default answer", value: "Voice or text")
                settingValue("Read questions aloud", value: "This device")
            }
            settingsSection("NOTIFICATIONS") {
                destination("iOS permission · \(notificationStatus)") {
                    UIApplication.shared.open(URL(string: UIApplication.openSettingsURLString)!)
                }
            }
            settingsSection("ACCOUNT") {
                settingValue(
                    auth.profile?.displayName.isEmpty == false ? auth.profile!.displayName : "Apple account",
                    value: auth.profile?.email ?? ""
                )
                destination("Sign out") { showSignOut = true }
                destination("Delete account") { state.path.append(.deleteAccount) }
            }
            settingsSection("DATA & PRIVACY") {
                destination("Export, processing, and deletion") { state.path.append(.privacy) }
            }
            settingsSection("ABOUT") {
                settingValue("Devmax", value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0")
                settingValue("Collections", value: "Versioned and reviewed")
            }
        }
        .task {
            let settings = await UNUserNotificationCenter.current().notificationSettings()
            notificationStatus = switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral: "ON"
            case .denied: "OFF"
            case .notDetermined: "NOT ASKED"
            @unknown default: "UNKNOWN"
            }
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
        VStack(alignment: .leading, spacing: 4) {
            MetaText(text: label, font: WCFont.mono(10), tracking: 1.1, color: Theme.metaFaint)
                .padding(.bottom, 4)
            content()
        }
    }

    private func destination(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(title).font(WCFont.sans(14.5)).foregroundStyle(Theme.text)
                Spacer()
                Text("→").font(WCFont.mono(11)).foregroundStyle(Theme.accent)
            }
            .frame(minHeight: Metrics.minTapTarget)
        }
        .buttonStyle(.plain)
    }

    private func settingValue(_ title: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(WCFont.sans(14.5)).foregroundStyle(Theme.text)
            Spacer(minLength: 10)
            Text(value).font(WCFont.sans(12.5)).foregroundStyle(Theme.metaAlt).lineLimit(1)
        }
        .frame(minHeight: Metrics.minTapTarget)
    }
}

struct DataPrivacyScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var flow: PublicOnboardingState
    @State private var exporting = false
    @State private var exportURL: URL?
    @State private var error = ""

    var body: some View {
        PublicSettingsPage(title: "Data & privacy", back: { state.path.removeLast() }) {
            Text("How AI processing works")
                .font(WCFont.serif(22)).foregroundStyle(Theme.textStrong)
            privacyNote("Guide processing", "Devmax sends guide text needed to propose topics and plan structure to its AI provider. The source remains attached to your account until you delete it or the account.")
            privacyNote("Answer scoring", "Devmax sends your transcript and relevant study material for scoring. It receives a transcript, not an audio recording; iOS handles speech recognition.")
            privacyNote("AI-provider retention", "Anthropic states that standard API inputs and outputs are deleted from its systems within 30 days and are not used for model training by default. Exceptions can apply for abuse prevention, legal obligations, explicit opt-in, or a different agreement.")
            privacyNote("Your control", "You can export account data, remove individual study material, or delete the entire account. Deletion is separate from signing out.")

            Link("Read Anthropic's API data policy", destination: URL(string: "https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data")!)
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
