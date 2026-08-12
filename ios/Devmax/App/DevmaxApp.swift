import AuthenticationServices
import SwiftUI
import UIKit

@main
struct DevmaxApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var state = AppState()
    @StateObject private var plan = StudyPlanState()
    @StateObject private var auth = AuthState()
    @StateObject private var publicFlow = PublicOnboardingState()
    @StateObject private var flags = DebugFlags.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            AppEntryView()
                .environmentObject(state)
                .environmentObject(plan)
                .environmentObject(auth)
                .environmentObject(publicFlow)
                .environmentObject(flags)
                .preferredColorScheme(.dark)  // light mode is not designed or supported
                .task {
                    delegate.state = state
                    await auth.bootstrap()
                    await auth.checkAppleCredentialState()
                }
                .onChange(of: scenePhase) { _, phase in
                    // Backgrounding mid-answer must not lose the transcript, and the
                    // debounce updateDraft applies is exactly what can't be waited
                    // out here — so flush rather than schedule.
                    if phase != .active {
                        state.flushDraft()
                        publicFlow.persist()
                        plan.flushPracticeDebriefDraft()
                    } else {
                        Task { await auth.checkAppleCredentialState() }
                    }
                }
                .onReceive(
                    NotificationCenter.default.publisher(
                        for: ASAuthorizationAppleIDProvider.credentialRevokedNotification
                    )
                ) { _ in
                    Task { await auth.checkAppleCredentialState() }
                }
                .onReceive(NotificationCenter.default.publisher(for: .aiConsentRequired)) { _ in
                    auth.markAIConsentRequired()
                }
        }
    }
}

struct AppEntryView: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState
    @EnvironmentObject private var auth: AuthState
    @EnvironmentObject private var publicFlow: PublicOnboardingState
    @EnvironmentObject private var flags: DebugFlags

    var body: some View {
        Group {
            if flags.route == "settings" {
                NavigationStack { FullSettingsScreen() }
            } else if flags.route == "privacy" {
                NavigationStack { DataPrivacyScreen() }
            } else if flags.route == "delete-account" {
                NavigationStack { DeleteAccountScreen() }
            } else if flags.route == "ai-consent" {
                AIConsentScreen()
            } else if PublicOnboardingState.handlesDebugRoute(flags.route) {
                PublicOnboardingView()
            } else if auth.isAuthenticated, auth.profile != nil,
                      auth.needsAIConsentPresentation
            {
                AIConsentScreen()
            } else if auth.isAuthenticated, auth.profile?.onboardingCompleted == true {
                RootView()
                    .task {
                        await state.loadToday()
                        await state.applyDebugRoute(plan: plan)
                    }
            } else if auth.status == .checking {
                Theme.bg.ignoresSafeArea()
            } else if auth.isAuthenticated && auth.profile == nil {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Couldn't load this account.")
                        .font(WCFont.serif(28)).foregroundStyle(Theme.textStrong)
                    Text("Your session is still saved. Retry when the service is reachable.")
                        .font(WCFont.sans(14)).foregroundStyle(Theme.textMuted)
                    PrimaryButton(title: "Try again") { Task { await auth.refreshProfile() } }
                }
                .padding(.horizontal, Metrics.screenPadding)
            } else {
                PublicOnboardingView()
                    .task {
                        if auth.isAuthenticated { await publicFlow.restoreImportIfNeeded() }
                    }
            }
        }
        .background(Theme.bg)
    }
}

struct RootView: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        NavigationStack(path: $state.path) {
            TodayScreen()
                .navigationDestination(for: AppState.Screen.self) { screen in
                    switch screen {
                    case .today:
                        TodayScreen()
                    case .conversation:
                        ConversationScreen()
                    case .history(let id):
                        CardHistoryScreen(cardID: id)
                    case .learning(let id):
                        LearnCardScreen(cardID: id)
                    case .sprintSetup:
                        SprintSetupScreen()
                    case .coverage:
                        CoverageScreen()
                    case .recap:
                        SessionRecapScreen()
                    case .planBuild:
                        PlanBuildScreen()
                    case .planPreview:
                        PlanPreviewScreen()
                    case .planOverview(let id):
                        PlanOverviewScreen(planID: id)
                    case .planWeek(let id, let index):
                        PlanWeekScreen(planID: id, index: index)
                    case .planItem(let id, let itemID):
                        PlanItemScreen(planID: id, itemID: itemID)
                    case .practiceDebrief(let id, let itemID, let showOffer):
                        PracticeDebriefScreen(
                            planID: id, itemID: itemID, showCompletionOffer: showOffer
                        )
                    case .planProposal(let id, let kind):
                        PlanProposalScreen(planID: id, kind: kind)
                    // The item id is part of the route's identity — it is what
                    // makes two reopens of different items distinct entries in
                    // the path — but the screen reads the loaded item from state.
                    case .planReopen(let id, _):
                        PlanProposalScreen(planID: id, kind: .reopen)
                    case .planCards(let id, let itemID):
                        PlanCardsScreen(planID: id, itemID: itemID)
                    case .planUpdates(let id):
                        PlanUpdatesScreen(planID: id)
                    case .planLifecycle(let id, let action, let origin):
                        PlanLifecycleConfirmationScreen(
                            planID: id, action: action, origin: origin
                        )
                    case .planRecap(let id):
                        PlanRecapScreen(planID: id)
                    case .planAudit(let destination):
                        PlanAuditScreen(destination: destination)
                    case .materialSetup:
                        PublicOnboardingView()
                    case .fullSettings:
                        FullSettingsScreen()
                    case .privacy:
                        DataPrivacyScreen()
                    case .deleteAccount:
                        DeleteAccountScreen()
                    }
                }
        }
        .tint(Theme.accent)
    }
}

/// APNs registration and deep-linking. The push payload carries the card id so a
/// tap lands directly in that card's Conversation, from cold start or background.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    weak var state: AppState?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        // The mock API has no server to register a token with, and the system
        // prompt would sit on top of every screenshot being compared.
        guard !DebugFlags.shared.useMockAPI else { return true }
        // Never trigger the system permission sheet on launch. Public onboarding
        // asks only after a real review and a chosen window. Existing founder
        // installs that already granted permission still register normally.
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized ||
                    settings.authorizationStatus == .provisional
            else { return }
            DispatchQueue.main.async { application.registerForRemoteNotifications() }
        }
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor [state] in
            do {
                try await state?.api.registerDeviceToken(token)
            } catch {
                // Without this the push loop just never starts, with no clue why.
                // The server side of the same symptom is trigger-review reporting
                // reason=no_devices.
                NSLog("devmax: uploading the APNs token failed: \(error)")
            }
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        // Usually a provisioning problem: no paid Apple Developer membership, or an
        // App ID without the Push Notifications capability.
        NSLog("devmax: APNs registration failed: \(error.localizedDescription)")
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let info = response.notification.request.content.userInfo
        guard let raw = info["card_id"] as? String, let id = UUID(uuidString: raw) else { return }
        await MainActor.run { [state] in
            guard let state else { return }
            if let card = state.queue.first(where: { $0.id == id }) {
                state.beginSession(cards: [card])
            } else {
                // The queue may not have loaded yet on a cold start.
                Task {
                    await state.loadToday()
                    if let card = state.queue.first(where: { $0.id == id }) {
                        state.beginSession(cards: [card])
                    }
                }
            }
        }
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }
}
