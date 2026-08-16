import SwiftUI
import XCTest
@testable import Devmax

final class SettingsValidationTests: XCTestCase {
    func testDefaultSettingsAreValid() {
        XCTAssertNil(SettingsValidation.message(for: .placeholder))
    }

    func testWindowMustBeAtLeastThirtyMinutes() {
        let window = NotificationWindow(
            label: "Morning", on: true, from: "08:00", to: "08:20"
        )

        XCTAssertEqual(
            SettingsValidation.windowMessage(window),
            "End must be at least 30 minutes after start."
        )
    }

    func testDisabledWindowStillUsesServerValidation() {
        var settings = AppSettings.placeholder
        settings.windows[0].on = false
        settings.windows[0].from = "09:00"
        settings.windows[0].to = "08:00"

        XCTAssertEqual(
            SettingsValidation.message(for: settings),
            "Morning: End must be at least 30 minutes after start."
        )
    }

    func testNativeTimeBindingWritesWireFormat() {
        var value = "08:00"
        let binding = Binding(get: { value }, set: { value = $0 })
        let date = Calendar.current.date(
            bySettingHour: 19, minute: 15, second: 0, of: Date()
        )!

        SettingsValidation.dateBinding(for: binding).wrappedValue = date

        XCTAssertEqual(value, "19:15")
    }

    func testReminderCapMatchesEnabledWindows() {
        var settings = AppSettings.placeholder
        settings.reviewsPerDay = 6

        XCTAssertEqual(
            SettingsValidation.normalizedReminderSettings(settings).reviewsPerDay,
            2
        )
        XCTAssertEqual(SettingsValidation.weeklyReminderValue(for: settings), "Up to 14/week")

        settings.windows[0].on = false
        settings.windows[1].on = false
        XCTAssertEqual(
            SettingsValidation.normalizedReminderSettings(settings).reviewsPerDay,
            1
        )
        XCTAssertEqual(SettingsValidation.weeklyReminderValue(for: settings), "Off")
    }

    func testReviewReminderDismissOnlyPopsItsOwnRoute() {
        var path: [AppState.Screen] = []

        XCTAssertFalse(SettingsNavigation.popReviewRemindersIfPresented(from: &path))
        XCTAssertTrue(path.isEmpty)

        path = [.fullSettings, .reviewReminders]
        XCTAssertTrue(SettingsNavigation.popReviewRemindersIfPresented(from: &path))
        XCTAssertEqual(path, [.fullSettings])

        XCTAssertFalse(SettingsNavigation.popReviewRemindersIfPresented(from: &path))
        XCTAssertEqual(path, [.fullSettings])
    }

    func testReviewReminderBackLabelMatchesItsOrigin() {
        XCTAssertEqual(
            SettingsNavigation.reviewRemindersBackLabel(for: [.reviewReminders]),
            "← Today"
        )
        XCTAssertEqual(
            SettingsNavigation.reviewRemindersBackLabel(
                for: [.fullSettings, .reviewReminders]
            ),
            "← Settings"
        )
    }
}
