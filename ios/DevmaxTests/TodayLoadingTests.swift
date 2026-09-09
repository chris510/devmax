import XCTest
@testable import Devmax

/// Keeps real LiveAPI requests open without blocking URLSession's loading thread.
/// Tests can deliver responses in a different order from the requests.
final class AuditURLProtocol: URLProtocol, @unchecked Sendable {
    static var onStart: ((AuditURLProtocol) -> Void)?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { Self.onStart?(self) }
    override func stopLoading() {}

    func respond(_ json: String, status: Int = 200) {
        let response = HTTPURLResponse(
            url: request.url!, statusCode: status, httpVersion: nil,
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    static func api() -> LiveAPI {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AuditURLProtocol.self]
        return LiveAPI(
            baseURL: URL(string: "https://example.test")!, apiKey: "test-key",
            session: URLSession(configuration: configuration),
            tokenStore: AuthTokenStore(persistence: nil)
        )
    }
}

@MainActor
final class TodayLoadingTests: XCTestCase {
    private static let cardID = UUID()
    private static var dueJSON: String {
        """
        [{"id":"\(cardID)","topic":"Consistent hashing","category":"Core Concept",
          "mastery_summary":"","last_score":2,"due_label":"due today",
          "resumable":false,"missed_count":0}]
        """
    }

    override func tearDown() {
        AuditURLProtocol.onStart = nil
        super.tearDown()
    }

    private func settle(until ready: () -> Bool) async {
        for _ in 0..<100 {
            if ready() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
    }

    func testDueCardsBecomeUsableBeforeOptionalRequestsFinish() async {
        let state = AppState(api: AuditURLProtocol.api())
        var held: [AuditURLProtocol] = []
        AuditURLProtocol.onStart = { request in
            Task { @MainActor in
                if request.request.url?.path == "/cards/due" {
                    request.respond(Self.dueJSON)
                } else {
                    held.append(request)
                }
            }
        }
        let load = Task { await state.loadToday() }
        await settle { !state.queue.isEmpty }

        XCTAssertEqual(state.queue.first?.id, Self.cardID)
        XCTAssertEqual(state.load, .ready, "A slow plan/captures request must not hold the queue skeleton")

        // Finish every request even if the assertion failed, so no task leaks.
        AuditURLProtocol.onStart = { $0.respond("{}", status: 503) }
        held.forEach { $0.respond("{}", status: 503) }
        await load.value
        XCTAssertEqual(state.load, .ready)
        XCTAssertTrue(state.planSummaryFailed)
    }

    func testPlanSummaryArrivesEvenWhenDueQueueFails() async {
        let state = AppState(api: AuditURLProtocol.api())
        AuditURLProtocol.onStart = { request in
            if request.request.url?.path == "/study-plans/active/summary" {
                request.respond(#"{"active":false,"title":"","subject":"","phase_title":""}"#)
            } else {
                request.respond("{}", status: 503)
            }
        }

        await state.loadToday()

        XCTAssertEqual(state.load, .error)
        XCTAssertNotNil(state.planSummary, "An available plan must survive an unrelated queue outage")
        XCTAssertFalse(state.planSummaryFailed)
    }

    func testOlderRefreshCannotReinsertCardsRemovedByNewerRefresh() async {
        let state = AppState(api: AuditURLProtocol.api())
        var oldestDue: AuditURLProtocol?
        AuditURLProtocol.onStart = { request in
            Task { @MainActor in
                if request.request.url?.path == "/cards/due", oldestDue == nil {
                    oldestDue = request
                } else if request.request.url?.path == "/cards/due" {
                    request.respond("[]")
                } else {
                    request.respond("{}", status: 503)
                }
            }
        }
        let older = Task { await state.loadToday() }
        await settle { oldestDue != nil }
        XCTAssertNotNil(oldestDue)
        await state.loadToday()
        XCTAssertTrue(state.queue.isEmpty)

        oldestDue?.respond(Self.dueJSON)
        await older.value

        XCTAssertTrue(state.queue.isEmpty, "Late pre-review data must not resurrect an answered card")
        XCTAssertEqual(state.load, .ready)
    }
}
