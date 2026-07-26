import SwiftUI
import UIKit

@main
struct WarmCacheApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var state = AppState()
    @StateObject private var flags = DebugFlags.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .environmentObject(flags)
                .preferredColorScheme(.dark)  // light mode is out of scope
                .task {
                    delegate.state = state
                    await state.loadToday()
                    await state.applyDebugRoute()
                }
                .onChange(of: scenePhase) { _, phase in
                    // Backgrounding mid-answer must not lose the transcript. Flush
                    // rather than persist: the debounce in persistDraft is exactly
                    // what we can't afford to wait out here.
                    if phase != .active { state.flushDraft() }
                }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var state: AppState

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
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
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
                NSLog("warmcache: uploading the APNs token failed: \(error)")
            }
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        // Usually a provisioning problem: no paid Apple Developer membership, or an
        // App ID without the Push Notifications capability.
        NSLog("warmcache: APNs registration failed: \(error.localizedDescription)")
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
