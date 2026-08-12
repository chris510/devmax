import AuthenticationServices
import XCTest
@testable import Devmax

private enum ConsentTransportError: Error {
    case unexpectedRequest
}

private final class ConsentURLProtocolStub: URLProtocol, @unchecked Sendable {
    static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?

    override static func canInit(with request: URLRequest) -> Bool { true }
    override static func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw ConsentTransportError.unexpectedRequest }
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

final class AIConsentTests: XCTestCase {
    override func tearDown() {
        ConsentURLProtocolStub.handler = nil
        super.tearDown()
    }

    private func api() -> LiveAPI {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ConsentURLProtocolStub.self]
        return LiveAPI(
            baseURL: URL(string: "https://example.test")!, apiKey: "test-key",
            session: URLSession(configuration: configuration),
            tokenStore: AuthTokenStore(persistence: nil)
        )
    }

    private func response(
        for request: URLRequest, status: Int, json: String
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
            throw ConsentTransportError.unexpectedRequest
        }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4_096)
        while true {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count < 0 { throw stream.streamError ?? ConsentTransportError.unexpectedRequest }
            if count == 0 { return data }
            data.append(contentsOf: buffer.prefix(count))
        }
    }

    func testAccountProfileDecodesVersionedAnthropicConsent() throws {
        let data = Data(#"""
        {
          "id":"00000000-0000-0000-0000-000000000001",
          "onboarding_completed":true,
          "is_founder":false,
          "display_name":"Casey",
          "email":"casey@privaterelay.appleid.com",
          "apple_user_identifier":"apple-subject",
          "ai_consent_status":"granted",
          "ai_consent_version":"anthropic-2026-08-12-v1",
          "ai_consent_updated_at":"2026-08-12T17:00:00Z",
          "ai_processing_allowed":true,
          "ai_consent_prompt_required":false
        }
        """#.utf8)

        let profile = try LiveAPI.decoder.decode(AccountProfile.self, from: data)
        XCTAssertEqual(profile.aiConsentStatus, "granted")
        XCTAssertEqual(profile.aiConsentVersion, "anthropic-2026-08-12-v1")
        XCTAssertTrue(profile.aiProcessingAllowed)
        XCTAssertFalse(profile.aiConsentPromptRequired)
        XCTAssertNotNil(profile.aiConsentUpdatedAt)
    }

    func testConsentChoiceUsesTheAuthenticatedConsentEndpoint() async throws {
        ConsentURLProtocolStub.handler = { [self] request in
            XCTAssertEqual(request.httpMethod, "PUT")
            XCTAssertEqual(request.url?.path, "/auth/ai-consent")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-API-Key"), "test-key")
            let object = try XCTUnwrap(
                JSONSerialization.jsonObject(with: bodyData(for: request)) as? [String: String]
            )
            XCTAssertEqual(object, ["action": "decline"])
            return response(
                for: request, status: 200,
                json: """
                {"provider":"Anthropic","policy_version":"anthropic-2026-08-12-v1",
                "status":"declined","updated_at":"2026-08-12T17:00:00Z",
                "processing_allowed":false,"prompt_required":false}
                """
            )
        }

        let receipt = try await api().updateAIConsent(action: "decline")
        XCTAssertEqual(receipt.provider, "Anthropic")
        XCTAssertFalse(receipt.processingAllowed)
        XCTAssertFalse(receipt.promptRequired)
    }

    func testStructuredConsentFailureRequestsTheConsentScreen() async {
        ConsentURLProtocolStub.handler = { [self] request in
            response(
                for: request, status: 403,
                json: """
                {"detail":{"code":"ai_consent_required","provider":"Anthropic",
                "policy_version":"anthropic-2026-08-12-v1"}}
                """
            )
        }
        let notification = expectation(forNotification: .aiConsentRequired, object: nil)

        do {
            _ = try await api().due()
            XCTFail("the blocked operation must fail")
        } catch APIError.status(let code) {
            XCTAssertEqual(code, 403)
        } catch {
            XCTFail("unexpected error: \(error)")
        }

        await fulfillment(of: [notification], timeout: 1)
    }

    func testUnrelatedForbiddenResponseDoesNotRequestConsent() async {
        ConsentURLProtocolStub.handler = { [self] request in
            response(for: request, status: 403, json: #"{"detail":"forbidden"}"#)
        }
        let notification = expectation(forNotification: .aiConsentRequired, object: nil)
        notification.isInverted = true

        _ = try? await api().due()

        await fulfillment(of: [notification], timeout: 0.1)
    }

    @MainActor
    func testAppleRevokedAndMissingCredentialsRequireLocalSignOut() {
        XCTAssertTrue(AuthState.appleCredentialRequiresSignOut(.revoked))
        XCTAssertTrue(AuthState.appleCredentialRequiresSignOut(.notFound))
        XCTAssertFalse(AuthState.appleCredentialRequiresSignOut(.authorized))
        XCTAssertFalse(AuthState.appleCredentialRequiresSignOut(.transferred))
    }
}
