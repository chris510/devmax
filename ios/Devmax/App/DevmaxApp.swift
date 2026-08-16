import AuthenticationServices
import Combine
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
                    delegate.attach(state)
                    await auth.bootstrap()
                    await auth.checkAppleCredentialState()
                    delegate.setRoutingAuthenticated(auth.isAuthenticated)
                }
                .onChange(of: auth.isAuthenticated) { _, authenticated in
                    // Bootstrap may finish signed out, then Apple sign-in can
                    // succeed without recreating the app delegate. Retained push
                    // work drains only after that authenticated transition.
                    delegate.setRoutingAuthenticated(authenticated)
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
                        Task {
                            await auth.checkAppleCredentialState()
                            // Also provides a bounded retry for a retained token
                            // or push after a transient network failure.
                            delegate.setRoutingAuthenticated(auth.isAuthenticated)
                        }
                    }
                }
                .onReceive(
                    NotificationCenter.default.publisher(
                        for: ASAuthorizationAppleIDProvider.credentialRevokedNotification
                    )
                ) { _ in
                    Task {
                        await auth.checkAppleCredentialState()
                        delegate.setRoutingAuthenticated(auth.isAuthenticated)
                    }
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
            if flags.route == "library" {
                NavigationStack { LibraryScreen() }
            } else if flags.route == "review-reminders" {
                NavigationStack { ReviewRemindersScreen() }
            } else if flags.route == "settings" {
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
                    case .library:
                        LibraryScreen()
                    case .libraryCards:
                        LibraryCardsScreen()
                    case .libraryCaptures:
                        CaptureFlowScreen(
                            route: .inbox,
                            inboxBackTitle: "← Library",
                            close: { state.path.removeLast() }
                        )
                    case .reviewReminders:
                        ReviewRemindersScreen()
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
    /// A notification response may arrive before SwiftUI's root task attaches
    /// AppState on a cold launch. Retain the card identity until both halves of
    /// that race exist instead of dropping the user's tap.
    private var pendingCardID: UUID?
    private var routingPendingCard = false
    private var pendingDeviceToken: String?
    private var uploadingDeviceToken = false
    private var queueReadiness: AnyCancellable?
    private var navigationReadiness: AnyCancellable?
    /// AppState attaches before authentication bootstrap completes. Push work
    /// waits for this gate so neither the due-card load nor token registration
    /// runs while signed out or races the credential restore it depends on.
    private var routingActive = false
    /// Separates otherwise-identical authenticated states across a sign-out.
    /// An APNs registration started for the old account must not consume the
    /// token that the next account still needs to register.
    private var routingGeneration = 0
    /// AppState's queue has no account provenance of its own. Once auth changes,
    /// a pending route may not trust any card already in memory until a Today
    /// load succeeds under one stable, active generation.
    private var pendingRouteRequiresReload = false

    @MainActor
    func attach(_ state: AppState) {
        self.state = state
        queueReadiness = state.$load.removeDuplicates().sink { [weak self] load in
            guard load == .ready else { return }
            Task { @MainActor [weak self] in self?.routePendingCardIfPossible() }
        }
        navigationReadiness = state.$path.removeDuplicates().sink { [weak self, weak state] _ in
            Task { @MainActor [weak self, weak state] in
                guard let state, !state.hasConversationInPath else { return }
                self?.routePendingCardIfPossible()
            }
        }
        drainPendingPushWork()
    }

    @MainActor
    func setRoutingAuthenticated(_ authenticated: Bool) {
        if routingActive != authenticated {
            routingGeneration += 1
            pendingRouteRequiresReload = true
        }
        routingActive = authenticated
        guard authenticated else { return }
        drainPendingPushWork()
    }

    @MainActor
    private func drainPendingPushWork() {
        routePendingCardIfPossible()
        uploadPendingDeviceTokenIfPossible()
    }

    @MainActor
    func receiveNotificationCard(_ id: UUID) {
        pendingCardID = id
        routePendingCardIfPossible()
    }

    @MainActor
    private func routePendingCardIfPossible() {
        guard routingActive, !routingPendingCard, let state,
              !state.hasConversationInPath, state.canBeginSession,
              let id = pendingCardID
        else { return }
        let routeGeneration = routingGeneration
        routingPendingCard = true
        Task { @MainActor [weak self, weak state] in
            guard let self, let state else { return }
            var observedGeneration = routeGeneration
            while self.routingActive {
                if self.routingGeneration != observedGeneration {
                    self.pendingRouteRequiresReload = true
                    observedGeneration = self.routingGeneration
                }
                let cardIsMissing = state.queue.first(where: { $0.id == id }) == nil
                guard self.pendingRouteRequiresReload || cardIsMissing else { break }

                // Mark first, then load. If auth changes or the request fails,
                // the flag survives this task and forces the next activation to
                // replace the possibly stale queue before inspecting it.
                self.pendingRouteRequiresReload = true
                let loadGeneration = self.routingGeneration
                await state.loadToday()
                guard self.routingActive else { break }
                guard self.routingGeneration == loadGeneration else {
                    observedGeneration = self.routingGeneration
                    continue
                }
                guard state.load == .ready else { break }
                self.pendingRouteRequiresReload = false
                observedGeneration = loadGeneration
                break
            }
            if !self.routingActive {
                // Authentication can be revoked while Today is loading. Keep
                // the tap for a later successful sign-in instead of routing or
                // declaring it stale with credentials no longer available.
            } else if self.pendingRouteRequiresReload {
                // The current account still has no successfully loaded queue.
                // Retain the tap; a later activation or successful load retries.
            } else if state.hasConversationInPath {
                // The route may have changed while Today was loading. Leave the
                // tap pending; `$path` drains it after the owning Conversation
                // and any child History screen have both left the stack.
            } else if self.pendingCardID == id,
               let card = state.queue.first(where: { $0.id == id }) {
                // An answer may still be committing after Conversation closed.
                // Keep the tap pending until AppState can own a new session.
                if state.beginSession(cards: [card]) {
                    self.pendingCardID = nil
                }
            } else if self.pendingCardID == id, state.load == .ready {
                // A current queue that does not contain the card means the push
                // is stale or the review is no longer available.
                self.pendingCardID = nil
            }
            self.routingPendingCard = false
            // If another tap arrived while this load was in flight, latest wins
            // and is routed in a fresh pass.
            if let pending = self.pendingCardID, pending != id {
                self.routePendingCardIfPossible()
            }
        }
    }

    @MainActor
    func receiveDeviceToken(_ token: String) {
        pendingDeviceToken = token
        uploadPendingDeviceTokenIfPossible()
    }

    @MainActor
    private func uploadPendingDeviceTokenIfPossible() {
        guard routingActive, !uploadingDeviceToken, let state,
              let token = pendingDeviceToken
        else { return }
        let generation = routingGeneration
        uploadingDeviceToken = true
        Task { @MainActor [weak self, weak state] in
            guard let self, let state else { return }
            var retryForCurrentAccount = false
            do {
                try await state.api.registerDeviceToken(token)
                if self.routingActive, self.routingGeneration == generation {
                    if self.pendingDeviceToken == token { self.pendingDeviceToken = nil }
                } else {
                    // The request belonged to an account that signed out while
                    // it was in flight. Keep the token for the next account.
                    retryForCurrentAccount = self.routingActive
                }
            } catch {
                // Retain for the next activation/registration callback rather
                // than silently dropping a token delivered during cold start.
                NSLog("devmax: uploading the APNs token failed: \(error)")
            }
            self.uploadingDeviceToken = false
            if self.pendingDeviceToken != token || retryForCurrentAccount {
                self.uploadPendingDeviceTokenIfPossible()
            }
        }
    }

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
        Task { @MainActor [weak self] in self?.receiveDeviceToken(token) }
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
        receiveNotificationCard(id)
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }
}
