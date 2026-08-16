import XCTest
@testable import Devmax

private enum PilotTransportError: Error {
    case unexpectedRequest
}

private final class PilotURLProtocolStub: URLProtocol, @unchecked Sendable {
    static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?

    override static func canInit(with request: URLRequest) -> Bool { true }
    override static func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw PilotTransportError.unexpectedRequest }
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

final class PilotAPIClientTests: XCTestCase {
    override func tearDown() {
        PilotURLProtocolStub.handler = nil
        super.tearDown()
    }

    private func api(clientBuild: Int = 10) -> LiveAPI {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PilotURLProtocolStub.self]
        return LiveAPI(
            baseURL: URL(string: "https://example.test")!, apiKey: "pilot-key",
            session: URLSession(configuration: configuration),
            tokenStore: AuthTokenStore(persistence: nil), clientBuild: clientBuild
        )
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

    private func bodyData(for request: URLRequest) throws -> Data {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else {
            throw PilotTransportError.unexpectedRequest
        }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4_096)
        while true {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count < 0 { throw stream.streamError ?? PilotTransportError.unexpectedRequest }
            if count == 0 { return data }
            data.append(contentsOf: buffer.prefix(count))
        }
    }

    func testCentralBuildHeaderAndSafePreviewPath() async throws {
        PilotURLProtocolStub.handler = { [self] request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(
                request.url?.path,
                "/materials/imports/00000000-0000-0000-0000-000000000901/preview"
            )
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-Devmax-Client-Build"), "10")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-API-Key"), "pilot-key")
            return response(
                for: request,
                json: #"{"id":"00000000-0000-0000-0000-000000000901","title":"Networking 101","kind":"article","source_url":"","content_provenance":"learner_notes","status":"ready","import_path":"lesson","intent":"already_studied","clean_count":0,"attention_count":0,"error":"","lesson_grounding_required":false,"proposals_ready_at":null,"review_opened_at":null,"confirmed_at":null,"topics":[]}"#
            )
        }

        let sourceID = UUID(uuidString: "00000000-0000-0000-0000-000000000901")!
        let preview = try await api().lessonPilotPreview(sourceID)

        XCTAssertEqual(preview.id, sourceID)
        XCTAssertTrue(preview.topics.isEmpty)
    }

    func testPilotUpgradeEnvelopeBecomesTypedClientError() async {
        PilotURLProtocolStub.handler = { [self] request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-Devmax-Client-Build"), "9")
            return response(
                for: request, status: 426,
                json: #"{"detail":{"code":"pilot_upgrade_required","minimum_client_build":10}}"#
            )
        }

        do {
            _ = try await api(clientBuild: 9).materialImports()
            XCTFail("an enrolled stale build must not decode an authority-bearing payload")
        } catch APIError.pilotUpgradeRequired(let minimumBuild) {
            XCTAssertEqual(minimumBuild, 10)
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testUnassignedPilotSourceEnvelopeBecomesTypedClientError() async {
        PilotURLProtocolStub.handler = { [self] request in
            XCTAssertEqual(request.httpMethod, "POST")
            return response(
                for: request, status: 404,
                json: #"{"detail":{"code":"pilot_source_not_assigned"}}"#
            )
        }

        do {
            _ = try await api().markLessonReviewOpened(UUID())
            XCTFail("the additive rollout fallback must retain its structured reason")
        } catch APIError.pilotSourceNotAssigned {
            // Expected.
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }

    func testPilotExclusionUsesThePreviewResponseShape() async throws {
        PilotURLProtocolStub.handler = { [self] request in
            XCTAssertEqual(request.httpMethod, "PATCH")
            XCTAssertEqual(
                request.url?.path,
                "/materials/topics/00000000-0000-0000-0000-000000000902"
            )
            let body = try XCTUnwrap(
                JSONSerialization.jsonObject(with: bodyData(for: request))
                    as? [String: String]
            )
            XCTAssertEqual(body, ["action": "exclude"])
            return response(
                for: request,
                json: #"{"id":"00000000-0000-0000-0000-000000000902","position":1,"section_title":"Network layer","topic":"Network layer best-effort delivery","formation_question":null,"status":"excluded","issue":"","formation_state":"unavailable","transfer_state":"unavailable"}"#
            )
        }

        let proposalID = UUID(uuidString: "00000000-0000-0000-0000-000000000902")!
        let proposal = try await api().excludePilotLessonProposal(proposalID)

        XCTAssertEqual(proposal.status, "excluded")
        XCTAssertFalse(proposal.isAvailable)
    }
}
