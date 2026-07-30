import Foundation
import UserNotifications

/// Study-block reminders. Local notifications, and deliberately nothing more.
///
/// These are **not** the review push. They never touch APNs, never register a
/// device token, and never reach the server — the backend has no idea they
/// exist, which is the point: a study block is a nudge, not a commitment. So:
///
///   * missing one changes nothing — not the plan, not a card, not mastery
///   * changing one changes nothing — not capacity, not the forecast
///   * pausing a plan silences its reminders and moves no dates
///   * resuming restores only the ones that were already on
///
/// Scheduling is by `DateComponents` in the device's own calendar, so a repeating
/// weekly block stays at the same wall-clock time across a timezone change and
/// across daylight saving, rather than drifting by an hour.
actor StudyReminderService {
    static let shared = StudyReminderService()

    private let center = UNUserNotificationCenter.current()

    /// `wc.plan.<planID>.<itemID>` — one request per item, so a single item's
    /// reminder can be replaced or removed without touching the others.
    private nonisolated func identifier(planID: UUID, itemID: UUID) -> String {
        "wc.plan.\(planID.uuidString).\(itemID.uuidString)"
    }

    /// Asked for **only** when the user first switches a reminder on.
    ///
    /// `AppDelegate` skips authorization entirely under `WC_MOCK`, because a
    /// fixtures build has no server to register a push token with. That guard is
    /// right for APNs and wrong here — a local reminder has nothing to do with a
    /// server — so this path requests its own permission and is testable in the
    /// simulator.
    func requestAuthorizationIfNeeded() async -> Bool {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            return true
        case .denied:
            return false
        default:
            return (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        }
    }

    /// Bring one item's reminder into line with its current settings.
    func sync(item: PlanItemDetail) async {
        let id = identifier(planID: item.planId, itemID: item.id)
        center.removePendingNotificationRequests(withIdentifiers: [id])

        guard item.studyBlockReminderOn,
              !item.isComplete,
              let weekday = item.studyBlockWeekday,
              let minute = item.studyBlockMinuteOfDay,
              await requestAuthorizationIfNeeded()
        else { return }

        let content = UNMutableNotificationContent()
        content.title = "Study block"
        content.body = item.fullTitle
        content.sound = .default
        // Enough to deep-link to the item; nothing the plan reads back.
        content.userInfo = [
            "plan_id": item.planId.uuidString, "plan_item_id": item.id.uuidString,
        ]

        // Calendar.current, so the block stays at the same local wall-clock time
        // through a timezone change rather than moving with UTC.
        var components = DateComponents()
        // DateComponents.weekday is 1 = Sunday; the plan stores 1 = Monday.
        components.weekday = weekday == 7 ? 1 : weekday + 1
        components.hour = minute / 60
        components.minute = minute % 60
        components.calendar = Calendar.current
        components.timeZone = TimeZone.current

        try? await center.add(
            UNNotificationRequest(
                identifier: id,
                content: content,
                trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: true)
            )
        )
    }

    /// Pausing a plan stops its reminders. It does not change the plan.
    func cancelAll(planID: UUID) async {
        let prefix = "wc.plan.\(planID.uuidString)."
        let pending = await center.pendingNotificationRequests()
        let ids = pending.map(\.identifier).filter { $0.hasPrefix(prefix) }
        guard !ids.isEmpty else { return }
        center.removePendingNotificationRequests(withIdentifiers: ids)
    }

    /// Resuming restores only what was already on, which is why this takes the
    /// items rather than a plan id — `studyBlockReminderOn` is the record of what
    /// the user had chosen, and it survives the pause untouched.
    func restore(items: [PlanItemDetail]) async {
        for item in items where item.studyBlockReminderOn {
            await sync(item: item)
        }
    }

    func pendingCount(planID: UUID) async -> Int {
        let prefix = "wc.plan.\(planID.uuidString)."
        return await center.pendingNotificationRequests()
            .filter { $0.identifier.hasPrefix(prefix) }.count
    }
}
