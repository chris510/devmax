import XCTest
@testable import Devmax

@MainActor
final class CardHistoryLoadingTests: XCTestCase {
    override func tearDown() {
        AuditURLProtocol.onStart = nil
        super.tearDown()
    }

    private func fixture() throws -> String {
        let url = try XCTUnwrap(
            Bundle(for: Self.self).url(forResource: "card_detail", withExtension: "json")
        )
        return try String(contentsOf: url, encoding: .utf8)
    }

    func testFailedHistoryCanRetryAndShowStoredSessions() async throws {
        let history = CardHistoryState()
        let api = AuditURLProtocol.api()
        let json = try fixture()
        let card = try LiveAPI.decoder.decode(CardDetail.self, from: Data(json.utf8))
        AuditURLProtocol.onStart = { $0.respond("{}", status: 500) }

        await history.load(cardID: card.id, api: api)

        guard case .failed = history.phase else {
            return XCTFail("A transport failure must have a retryable presentation")
        }
        AuditURLProtocol.onStart = { $0.respond(json) }
        await history.load(cardID: card.id, api: api)

        guard case .ready(let loaded) = history.phase else {
            return XCTFail("Retry must leave the failure state")
        }
        XCTAssertEqual(loaded, card)
        XCTAssertEqual(loaded.sessions.count, 3)
    }

    func testMalformedHistoryDoesNotLeaveAnEmptyScreen() async {
        AuditURLProtocol.onStart = { $0.respond(#"{"id":"invalid"}"#) }
        let history = CardHistoryState()

        await history.load(cardID: UUID(), api: AuditURLProtocol.api())

        guard case .failed = history.phase else {
            return XCTFail("A decoding failure needs the same recovery as a network failure")
        }
    }

    func testLateFailureCannotReplaceNewlyLoadedHistory() async throws {
        let history = CardHistoryState()
        let api = AuditURLProtocol.api()
        let json = try fixture()
        let started = expectation(description: "first history request started")
        var held: AuditURLProtocol?
        AuditURLProtocol.onStart = { request in
            Task { @MainActor in
                held = request
                started.fulfill()
            }
        }
        let older = Task { await history.load(cardID: UUID(), api: api) }
        await fulfillment(of: [started], timeout: 2)
        AuditURLProtocol.onStart = { $0.respond(json) }
        await history.load(cardID: UUID(), api: api)
        held?.respond("{}", status: 500)
        await older.value

        guard case .ready = history.phase else {
            return XCTFail("Only the newest load can publish history or a failure")
        }
    }
}
