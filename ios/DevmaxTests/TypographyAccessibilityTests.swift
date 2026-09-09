import SwiftUI
import XCTest
@testable import Devmax

@MainActor
final class TypographyAccessibilityTests: XCTestCase {
    func testEveryBundledFamilyScalesWithDynamicType() {
        for font in [WCFont.serif(25), WCFont.serif(19, italic: true), WCFont.sans(16), WCFont.mono(11)] {
            let normal = height(font, size: .large)
            let accessible = height(font, size: .accessibility3)
            XCTAssertGreaterThan(accessible, normal * 1.4)
        }
    }

    private func height(_ font: Font, size: DynamicTypeSize) -> CGFloat {
        let controller = UIHostingController(rootView:
            Text("Explain the mechanism from memory.")
                .font(font)
                .fixedSize(horizontal: false, vertical: true)
                .environment(\.dynamicTypeSize, size)
        )
        return controller.sizeThatFits(in: CGSize(width: 346, height: 3000)).height
    }
}
