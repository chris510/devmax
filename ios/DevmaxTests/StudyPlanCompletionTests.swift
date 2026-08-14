import XCTest
@testable import Devmax

private enum PlanCompletionTransportError: Error {
    case unexpectedRequest
}

private final class PlanCompletionURLProtocolStub: URLProtocol, @unchecked Sendable {
    static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?

    override static func canInit(with request: URLRequest) -> Bool { true }
    override static func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            guard let handler = Self.handler else {
                throw PlanCompletionTransportError.unexpectedRequest
            }
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private final class PlanCompletionRequestLog: @unchecked Sendable {
    struct Call {
        let method: String
        let path: String
        let query: [String: String]
    }

    private let lock = NSLock()
    private var stored: [Call] = []

    @discardableResult
    func record(_ request: URLRequest) -> Int {
        let query = URLComponents(
            url: request.url!, resolvingAgainstBaseURL: false
        )?.queryItems?.reduce(into: [String: String]()) { values, item in
            values[item.name] = item.value
        } ?? [:]
        let call = Call(
            method: request.httpMethod ?? "", path: request.url?.path ?? "", query: query
        )
        lock.lock()
        defer { lock.unlock() }
        stored.append(call)
        return stored.filter { $0.method == call.method && $0.path == call.path }.count
    }

    var calls: [Call] {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }
}

final class StudyPlanCompletionTests: XCTestCase {
    private let planID = UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
    private let itemID = UUID(uuidString: "00000000-0000-0000-0000-000000000010")!

    override func tearDown() {
        PlanCompletionURLProtocolStub.handler = nil
        super.tearDown()
    }

    private func api() -> LiveAPI {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PlanCompletionURLProtocolStub.self]
        return LiveAPI(
            baseURL: URL(string: "https://example.test")!, apiKey: "test-key",
            session: URLSession(configuration: configuration),
            tokenStore: AuthTokenStore(persistence: nil)
        )
    }

    private func itemJSON(
        revision: Int?, title: String, status: String = "pending"
    ) -> String {
        let revisionField = revision.map { "\"plan_revision\":\($0)," } ?? ""
        let completedAt = status == "complete" ? "\"2026-08-13T20:00:00Z\"" : "null"
        return """
        {"id":"\(itemID.uuidString)","plan_id":"\(planID.uuidString)",
         \(revisionField)
         "full_title":"\(title)","phase_title":"Foundations","week_index":1,
         "type":"learn","priority":"core","status":"\(status)",
         "why_it_matters":"Understand the request path.",
         "done_when":"Explain it closed-book.","estimate_minutes":60,
         "estimate_source":"imported","estimate_confidence":"high",
         "source_excerpt":"","source_label":"","recall_supported":true,"notes":"",
         "study_block_label":"","study_block_weekday":null,
         "study_block_minute_of_day":null,"study_block_reminder_on":false,
         "completed_at":\(completedAt),"reopened_at":null,"linked_card_ids":[],
         "card_proposals_available":false,"practice_debrief_eligible":false,
         "practice_debrief":null,"blocked_by":[]}
        """
    }

    private func overviewJSON(revision: Int) -> String {
        """
        {"id":"\(planID.uuidString)","title":"Study plan",
         "subject":"Senior backend interview","mode":"flexible","status":"active",
         "week_index":1,"week_total":12,
         "forecast_label":"Est. completion · week of 19 Oct",
         "forecast_end_plan_week":12,"revision":\(revision),
         "supports_recall_cards":true,"phases":[],"latest_change":null}
        """
    }

    private func response(
        for request: URLRequest, status: Int = 200, json: String
    ) -> (HTTPURLResponse, Data) {
        (
            HTTPURLResponse(
                url: request.url!, statusCode: status, httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!,
            Data(json.utf8)
        )
    }

    func testCompletionSendsTheRevisionLoadedWithTheItem() async throws {
        let log = PlanCompletionRequestLog()
        PlanCompletionURLProtocolStub.handler = { [self] request in
            log.record(request)
            return response(
                for: request,
                json: itemJSON(revision: 7, title: "Request lifecycle", status: "complete")
            )
        }

        let item = try await api().completePlanItem(planID, itemID: itemID, revision: 7)

        let call = try XCTUnwrap(log.calls.first)
        XCTAssertEqual(call.method, "POST")
        XCTAssertEqual(call.path, "/study-plans/\(planID)/items/\(itemID)/complete")
        XCTAssertEqual(call.query, ["base_plan_revision": "7"])
        XCTAssertTrue(item.isComplete)
        XCTAssertEqual(item.planRevision, 7)
    }

    func testCompletionOmitsTheGuardForAnOlderServerResponse() async throws {
        let log = PlanCompletionRequestLog()
        PlanCompletionURLProtocolStub.handler = { [self] request in
            log.record(request)
            return response(
                for: request,
                json: itemJSON(revision: nil, title: "Legacy item", status: "complete")
            )
        }

        let item = try await api().completePlanItem(planID, itemID: itemID, revision: nil)

        XCTAssertTrue(try XCTUnwrap(log.calls.first).query.isEmpty)
        XCTAssertNil(item.planRevision)
    }

    @MainActor
    func testStaleCompletionReloadsCurrentContentWithoutResubmitting() async throws {
        let log = PlanCompletionRequestLog()
        let itemPath = "/study-plans/\(planID)/items/\(itemID)"
        let overviewPath = "/study-plans/\(planID)"
        PlanCompletionURLProtocolStub.handler = { [self] request in
            let occurrence = log.record(request)
            let method = request.httpMethod ?? ""
            let path = request.url?.path ?? ""
            if method == "GET", path == itemPath {
                return response(
                    for: request,
                    json: occurrence == 1
                        ? itemJSON(revision: 7, title: "Original curriculum")
                        : itemJSON(revision: 8, title: "Upgraded curriculum")
                )
            }
            if method == "POST", path == "\(itemPath)/complete" {
                return response(
                    for: request, status: 409,
                    json: #"{"detail":"plan changed"}"#
                )
            }
            if method == "GET", path == overviewPath {
                return response(for: request, json: overviewJSON(revision: 8))
            }
            throw PlanCompletionTransportError.unexpectedRequest
        }
        let state = StudyPlanState(api: api())
        await state.loadItem(planID, itemID: itemID)
        XCTAssertEqual(state.item?.planRevision, 7)

        let completed = await state.completeItem()

        XCTAssertFalse(completed)
        XCTAssertFalse(state.itemBusy)
        XCTAssertEqual(state.itemLoad, .ready)
        XCTAssertEqual(state.item?.fullTitle, "Upgraded curriculum")
        XCTAssertEqual(state.item?.planRevision, 8)
        XCTAssertEqual(state.item?.status, "pending")
        XCTAssertEqual(state.overview?.revision, 8)
        XCTAssertEqual(state.itemError, "Couldn't mark this complete. Nothing changed.")

        let completionCalls = log.calls.filter {
            $0.method == "POST" && $0.path == "\(itemPath)/complete"
        }
        XCTAssertEqual(completionCalls.count, 1, "a stale completion must never auto-resubmit")
        XCTAssertEqual(completionCalls.first?.query, ["base_plan_revision": "7"])
    }
}
