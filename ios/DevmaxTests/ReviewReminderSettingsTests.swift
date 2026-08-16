import XCTest
@testable import Devmax

final class ReviewReminderSettingsTests: XCTestCase {
    func testLegacyWindowWithoutDaysRetainsEveryDayBehavior() throws {
        let json = Data(#"""
        {"reviews_per_day":2,"timezone":"America/Los_Angeles",
         "windows":[{"label":"Morning","on":true,"from":"07:10","to":"08:30"}]}
        """#.utf8)

        let settings = try LiveAPI.decoder.decode(AppSettings.self, from: json)

        XCTAssertEqual(settings.windows.first?.days, Array(1...7))
        XCTAssertEqual(settings.weeklyReminderMaximum, 7)
    }

    func testExplicitNullDaysDoesNotBecomeAnEverydaySchedule() {
        let json = Data(#"""
        {"reviews_per_day":2,"timezone":"America/Los_Angeles",
         "windows":[{"label":"Morning","on":true,"from":"07:10","to":"08:30",
                     "days":null}]}
        """#.utf8)

        XCTAssertThrowsError(try LiveAPI.decoder.decode(AppSettings.self, from: json))
    }

    func testSelectedISODaysRoundTripOnTheWire() throws {
        let settings = AppSettings(
            reviewsPerDay: 1,
            timezone: "America/Los_Angeles",
            windows: [
                NotificationWindow(
                    label: "Morning", on: true, from: "07:10", to: "08:30",
                    days: [1, 3, 5]
                )
            ]
        )

        let encoded = try LiveAPI.encoder.encode(settings)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        )
        let windows = try XCTUnwrap(object["windows"] as? [[String: Any]])

        XCTAssertEqual(windows.first?["days"] as? [Int], [1, 3, 5])
        XCTAssertEqual(
            try LiveAPI.decoder.decode(AppSettings.self, from: encoded).windows.first?.days,
            [1, 3, 5]
        )
    }

    func testWeeklyMaximumCapsOverlappingWindowsPerDay() {
        let settings = AppSettings(
            reviewsPerDay: 2,
            timezone: "America/Los_Angeles",
            windows: [
                NotificationWindow(
                    label: "A", on: true, from: "07:10", to: "08:30",
                    days: [1, 2, 3]
                ),
                NotificationWindow(
                    label: "B", on: true, from: "12:15", to: "18:40",
                    days: [2, 3, 4]
                ),
                NotificationWindow(
                    label: "C", on: true, from: "21:00", to: "22:30",
                    days: [3, 4, 5]
                ),
            ]
        )

        // Daily active-window counts are 1, 2, 3, 2, 1, 0, 0. The daily cap
        // turns that into 1 + 2 + 2 + 2 + 1 = 8.
        XCTAssertEqual(settings.weeklyReminderMaximum, 8)
        XCTAssertEqual(settings.weeklyReminderMaximumLabel, "Up to 8 reminders per week")
    }

    func testOneWindowMovesFromTwoToThreeRemindersBySelectingOneMoreDay() {
        var settings = AppSettings(
            reviewsPerDay: 2,
            timezone: "America/Los_Angeles",
            windows: [
                NotificationWindow(
                    label: "Morning", on: true, from: "07:10", to: "08:30",
                    days: [1, 3]
                )
            ]
        )

        XCTAssertEqual(settings.weeklyReminderMaximum, 2)
        settings.windows[0].days.append(5)
        XCTAssertEqual(settings.weeklyReminderMaximum, 3)
        XCTAssertEqual(settings.weeklyReminderMaximumLabel, "Up to 3 reminders per week")
    }

    func testReminderScheduleValidationRejectsBadSpansAndCollidingStarts() {
        var settings = AppSettings(
            reviewsPerDay: 2,
            timezone: "America/Los_Angeles",
            windows: [
                NotificationWindow(
                    label: "Morning", on: true, from: "07:10", to: "08:30", days: [1]
                ),
                NotificationWindow(
                    label: "Evening", on: true, from: "21:00", to: "22:30", days: [1]
                ),
            ]
        )

        settings.windows[1].to = "06:30"
        XCTAssertEqual(
            settings.reminderScheduleValidationMessage,
            "Each reminder window must end at least 30 minutes after it starts."
        )

        settings.windows[1].from = "07:10"
        settings.windows[1].to = "12:15"
        XCTAssertEqual(
            settings.reminderScheduleValidationMessage,
            "Windows on the same day need different start times."
        )

        settings.windows[1].days = [2]
        XCTAssertNil(settings.reminderScheduleValidationMessage)
    }

    @MainActor
    func testFailedSettingsWriteRestoresTheLastSavedSettings() async {
        let state = AppState(api: MockAPI(settingsUpdateFails: true))
        let saved = state.settings
        var draft = saved
        draft.windows[0].days = [1, 3, 5]

        let succeeded = await state.persistSettings(draft)

        XCTAssertFalse(succeeded)
        XCTAssertEqual(state.settings, saved)
    }

}
