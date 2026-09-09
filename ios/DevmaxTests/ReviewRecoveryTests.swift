import XCTest
@testable import Devmax

@MainActor
final class ReviewRecoveryTests: XCTestCase {
    override func tearDown() {
        AuditURLProtocol.onStart = nil
        super.tearDown()
    }

    func testEndingUploadsTheOwnedDraftBeforeAbandoningAndRetainsTheLocalCopy() async throws {
        let state = AppState(api: AuditURLProtocol.api())
        let cardID = UUID()
        let session = ActiveCardSession(id: UUID(), practice: true, turnIndex: 2)
        DraftStore.save("Unfinished spoken answer", for: cardID, sessionID: session.id, turnIndex: 2)
        defer { DraftStore.clear(for: cardID) }
        var requests: [String] = []
        AuditURLProtocol.onStart = { request in
            Task { @MainActor in
                requests.append(request.request.httpMethod! + " " + request.request.url!.path)
                request.respond("", status: 204)
            }
        }

        try await state.endReview(cardID: cardID, session: session)

        XCTAssertEqual(requests, [
            "PATCH /sessions/\(session.id)/draft", "POST /sessions/\(session.id)/abandon"
        ])
        XCTAssertEqual(DraftStore.read(for: cardID, sessionID: session.id, turnIndex: 2),
                       "Unfinished spoken answer")
        XCTAssertFalse(state.endingReview)
        XCTAssertTrue(state.path.isEmpty, "Only a confirmed History load may offer Learn")
    }

    func testFailedDraftUploadLeavesAttemptOpenAndCanRetry() async throws {
        let state = AppState(api: AuditURLProtocol.api())
        let cardID = UUID()
        let session = ActiveCardSession(id: UUID(), practice: false, turnIndex: 0)
        DraftStore.save("My answer", for: cardID, sessionID: session.id, turnIndex: 0)
        defer { DraftStore.clear(for: cardID) }
        var paths: [String] = []
        AuditURLProtocol.onStart = { request in
            Task { @MainActor in
                paths.append(request.request.url!.path)
                request.respond("{}", status: 503)
            }
        }
        do {
            try await state.endReview(cardID: cardID, session: session)
            XCTFail("A failed upload must stop abandonment")
        } catch {}
        XCTAssertEqual(paths, ["/sessions/\(session.id)/draft"])
        XCTAssertFalse(state.endingReview)
        AuditURLProtocol.onStart = { $0.respond("", status: 204) }
        try await state.endReview(cardID: cardID, session: session)
    }

    func testEndingDoesNotUploadAPreviousTurnIntoTheCurrentProbe() async throws {
        let state = AppState(api: AuditURLProtocol.api())
        let cardID = UUID()
        let session = ActiveCardSession(id: UUID(), practice: false, turnIndex: 1)
        DraftStore.save("Old opening", for: cardID, sessionID: session.id, turnIndex: 0)
        defer { DraftStore.clear(for: cardID) }
        AuditURLProtocol.onStart = { request in
            XCTAssertEqual(request.request.httpMethod, "POST")
            XCTAssertTrue(request.request.url!.path.hasSuffix("/abandon"))
            request.respond("", status: 204)
        }
        try await state.endReview(cardID: cardID, session: session)
    }

    func testEndCannotRaceAnAnswerableConversation() async {
        let state = AppState(api: AuditURLProtocol.api())
        state.sessionID = UUID()
        AuditURLProtocol.onStart = { _ in XCTFail("Must not start a request") }
        do {
            try await state.endReview(
                cardID: UUID(), session: ActiveCardSession(id: state.sessionID!, practice: false, turnIndex: 0)
            )
            XCTFail("Conversation must first preserve its draft and release ownership")
        } catch {}
    }

    func testArchivedDiscoveryUsesExplicitQueryWithoutReplacingActiveLibrary() async {
        let state = AppState(api: AuditURLProtocol.api())
        state.libraryLoad = .ready
        AuditURLProtocol.onStart = { request in
            XCTAssertEqual(URLComponents(url: request.request.url!, resolvingAgainstBaseURL: false)?
                .queryItems?.first(where: { $0.name == "lifecycle" })?.value, "archived")
            request.respond("[]")
        }
        await state.loadArchivedCards()
        XCTAssertEqual(state.archiveLoad, .ready)
        XCTAssertEqual(state.libraryLoad, .ready)
    }

    func testUnknownAndFailedDiscoveryNeverClaimsAnEmptyLibraryOrCreatesAPlan() {
        let state = AppState(api: AuditURLProtocol.api())
        XCTAssertFalse(state.hasConfirmedEmptyLibrary)
        state.libraryLoad = .error
        XCTAssertFalse(state.hasConfirmedEmptyLibrary)
        state.openStudyPlan()
        XCTAssertEqual(state.sheet, .plans)
        XCTAssertTrue(state.path.isEmpty)
        state.libraryLoad = .ready
        XCTAssertTrue(state.hasConfirmedEmptyLibrary)
        state.planSummary = StudyPlanSummary.none
        state.sheet = nil
        state.openStudyPlan()
        XCTAssertEqual(state.path, [.planBuild])
    }
}
