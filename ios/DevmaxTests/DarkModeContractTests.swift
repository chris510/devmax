import XCTest
@testable import Devmax

final class DarkModeContractTests: XCTestCase {
    func testApplicationBundleForcesDarkSystemAppearance() {
        XCTAssertEqual(
            Bundle.main.object(forInfoDictionaryKey: "UIUserInterfaceStyle") as? String,
            "Dark"
        )
    }
}
