import XCTest
@testable import Devmax

private enum AuthTransportTestError: Error {
    case unexpectedRequest
    case persistenceFailed
}

private final class AuthURLProtocolStub: URLProtocol, @unchecked Sendable {
    static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?

    override static func canInit(with request: URLRequest) -> Bool { true }
    override static func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw AuthTransportTestError.unexpectedRequest }
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

private final class LockedRequestCount: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0

    func next() -> Int {
        lock.lock()
        defer { lock.unlock() }
        let current = value
        value += 1
        return current
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

final class AuthCredentialTests: XCTestCase {
    private struct ExpectedFailure: Error {}

    private var credentials: AuthCredentials {
        AuthCredentials(
            accessToken: "access-token",
            refreshToken: "refresh-token",
            accessExpiresAt: Date().addingTimeInterval(900),
            refreshExpiresAt: Date().addingTimeInterval(86_400)
        )
    }

    func testFailedDurableWriteNeverCreatesAnInMemorySession() async {
        let persistence = AuthCredentialPersistence(
            load: { nil },
            save: { _ in throw ExpectedFailure() },
            clear: {}
        )
        let store = AuthTokenStore(persistence: persistence)

        do {
            try await store.replace(with: credentials)
            XCTFail("a failed durable write must fail authentication")
        } catch is ExpectedFailure {
            // Expected.
        } catch {
            XCTFail("unexpected error: \(error)")
        }

        let accessToken = await store.accessToken()
        let usable = await store.hasUsableSession()
        XCTAssertNil(accessToken)
        XCTAssertFalse(usable)
    }

    func testReadBackMismatchNeverCreatesAnInMemorySession() async {
        let persistence = AuthCredentialPersistence(
            load: { Data("not the saved credential".utf8) },
            save: { _ in },
            clear: {}
        )
        let store = AuthTokenStore(persistence: persistence)

        do {
            try await store.replace(with: credentials)
            XCTFail("unreadable persisted credentials must fail authentication")
        } catch {
            // Exact storage errors are intentionally private; the contract is
            // that authentication does not advance.
        }

        let accessToken = await store.accessToken()
        XCTAssertNil(accessToken)
    }

    func testVerifiedPersistenceCanRecoverOnAColdStore() async throws {
        final class Box: @unchecked Sendable {
            var data: Data?
        }
        let box = Box()
        let persistence = AuthCredentialPersistence(
            load: { box.data },
            save: { box.data = $0 },
            clear: { box.data = nil }
        )
        let first = AuthTokenStore(persistence: persistence)
        try await first.replace(with: credentials)

        let relaunched = AuthTokenStore(persistence: persistence)
        let recovered = await relaunched.accessToken()
        let usable = await relaunched.hasUsableSession()
        XCTAssertEqual(recovered, credentials.accessToken)
        XCTAssertTrue(usable)
    }
}

final class FounderClaimTransportTests: XCTestCase {
    override func tearDown() {
        AuthURLProtocolStub.handler = nil
        super.tearDown()
    }

    private func authClient(
        store: AuthTokenStore = AuthTokenStore(persistence: nil)
    ) -> AuthClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AuthURLProtocolStub.self]
        return AuthClient(
            baseURL: URL(string: "https://devmax-production.up.railway.app")!,
            session: URLSession(configuration: configuration),
            store: store
        )
    }

    private func response(
        for request: URLRequest,
        status: Int = 200,
        json: String
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
            throw AuthTransportTestError.unexpectedRequest
        }
        stream.open()
        defer { stream.close() }

        var result = Data()
        var buffer = [UInt8](repeating: 0, count: 4_096)
        while true {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count < 0 { throw stream.streamError ?? AuthTransportTestError.unexpectedRequest }
            if count == 0 { return result }
            result.append(contentsOf: buffer.prefix(count))
        }
    }

    private var tokenJSON: String {
        """
        {
          "access_token": "founder-access",
          "refresh_token": "founder-refresh",
          "access_expires_at": "2026-08-12T00:00:00Z",
          "refresh_expires_at": "2026-09-10T00:00:00Z",
          "token_type": "bearer"
        }
        """
    }

    private func profileJSON(isFounder: Bool) -> String {
        """
        {
          "id": "00000000-0000-0000-0000-000000000001",
          "onboarding_completed": true,
          "is_founder": \(isFounder),
          "display_name": "Founder",
          "email": "founder@example.com"
        }
        """
    }

    func testFounderClaimUsesOneScopedHeaderThenBearerOnlyVerification() async throws {
        let calls = LockedRequestCount()
        AuthURLProtocolStub.handler = { [self] request in
            switch calls.next() {
            case 0:
                XCTAssertEqual(request.httpMethod, "POST")
                XCTAssertEqual(request.url?.path, "/auth/founder/apple-claim")
                XCTAssertEqual(
                    request.value(forHTTPHeaderField: "X-Founder-Claim-Token"),
                    "one-time-claim-token"
                )
                XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
                XCTAssertNil(request.value(forHTTPHeaderField: "X-API-Key"))
                let body = try bodyData(for: request)
                let object = try XCTUnwrap(
                    JSONSerialization.jsonObject(with: body) as? [String: Any]
                )
                XCTAssertEqual(object["identity_token"] as? String, "apple-identity")
                XCTAssertEqual(object["authorization_code"] as? String, "apple-code")
                XCTAssertEqual(object["nonce"] as? String, "raw-nonce")
                XCTAssertEqual(object["display_name"] as? String, "Founder")
                return response(for: request, json: tokenJSON)

            case 1:
                XCTAssertEqual(request.httpMethod, "GET")
                XCTAssertEqual(request.url?.path, "/auth/me")
                XCTAssertEqual(
                    request.value(forHTTPHeaderField: "Authorization"),
                    "Bearer founder-access"
                )
                XCTAssertNil(request.value(forHTTPHeaderField: "X-Founder-Claim-Token"))
                XCTAssertNil(request.value(forHTTPHeaderField: "X-API-Key"))
                XCTAssertNil(request.httpBody)
                return response(for: request, json: profileJSON(isFounder: true))

            default:
                throw AuthTransportTestError.unexpectedRequest
            }
        }

        let client = authClient()
        let profile = try await client.claimFounder(
            identityToken: "apple-identity",
            authorizationCode: "apple-code",
            nonce: "raw-nonce",
            displayName: "Founder",
            claimToken: "one-time-claim-token"
        )

        XCTAssertTrue(profile.isFounder)
        XCTAssertEqual(calls.count, 2)
        let storedAccess = await client.store.accessToken()
        XCTAssertEqual(storedAccess, "founder-access")
    }

    func testOrdinaryAppleSignInNeverCarriesTheFounderClaimHeader() async throws {
        let calls = LockedRequestCount()
        AuthURLProtocolStub.handler = { [self] request in
            _ = calls.next()
            XCTAssertEqual(request.url?.path, "/auth/apple")
            XCTAssertNil(request.value(forHTTPHeaderField: "X-Founder-Claim-Token"))
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            return response(for: request, json: tokenJSON)
        }

        let client = authClient()
        try await client.signIn(
            identityToken: "public-identity",
            authorizationCode: "public-code",
            nonce: "public-nonce",
            displayName: nil
        )

        XCTAssertEqual(calls.count, 1)
    }

    func testFounderClaimClearsCredentialsWhenBearerIsNotFounder() async {
        let calls = LockedRequestCount()
        AuthURLProtocolStub.handler = { [self] request in
            if calls.next() == 0 {
                return response(for: request, json: tokenJSON)
            }
            return response(for: request, json: profileJSON(isFounder: false))
        }

        let client = authClient()
        do {
            _ = try await client.claimFounder(
                identityToken: "apple-identity",
                authorizationCode: "apple-code",
                nonce: "raw-nonce",
                displayName: nil,
                claimToken: "one-time-claim-token"
            )
            XCTFail("a non-founder bearer must fail the migration")
        } catch {
            // Expected: no account detail escapes this boundary.
        }

        XCTAssertEqual(calls.count, 2)
        let storedAccess = await client.store.accessToken()
        XCTAssertNil(storedAccess)
    }

    func testFounderClaimStopsBeforeBearerVerificationWhenPersistenceFails() async {
        let calls = LockedRequestCount()
        AuthURLProtocolStub.handler = { [self] request in
            _ = calls.next()
            return response(for: request, json: tokenJSON)
        }
        let store = AuthTokenStore(
            persistence: AuthCredentialPersistence(
                load: { nil },
                save: { _ in throw AuthTransportTestError.persistenceFailed },
                clear: {}
            )
        )
        let client = authClient(store: store)

        do {
            _ = try await client.claimFounder(
                identityToken: "apple-identity",
                authorizationCode: "apple-code",
                nonce: "raw-nonce",
                displayName: nil,
                claimToken: "one-time-claim-token"
            )
            XCTFail("failed durable persistence must fail the claim")
        } catch {
            // Expected.
        }

        XCTAssertEqual(calls.count, 1)
        let storedAccess = await store.accessToken()
        XCTAssertNil(storedAccess)
    }

    func testBlankFounderClaimTokenMakesNoNetworkRequest() async {
        let calls = LockedRequestCount()
        AuthURLProtocolStub.handler = { _ in
            _ = calls.next()
            throw AuthTransportTestError.unexpectedRequest
        }
        let client = authClient()

        do {
            _ = try await client.claimFounder(
                identityToken: "apple-identity",
                authorizationCode: "apple-code",
                nonce: "raw-nonce",
                displayName: nil,
                claimToken: "   "
            )
            XCTFail("a blank claim token must fail locally")
        } catch {
            // Expected.
        }

        XCTAssertEqual(calls.count, 0)
    }
}

@MainActor
final class FounderClaimRoutingTests: XCTestCase {
    func testClaimTokenRoutesAnUnauthenticatedBuildToExistingOwner() {
        XCTAssertEqual(
            PublicOnboardingState.initialStep(
                route: "", founderClaimAvailable: true
            ).rawValue,
            PublicOnboardingState.Step.returning.rawValue
        )
    }

    func testKeylessProductionBuildStartsAtWelcome() {
        XCTAssertEqual(
            PublicOnboardingState.initialStep(
                route: "", founderClaimAvailable: false
            ).rawValue,
            PublicOnboardingState.Step.welcome.rawValue
        )
    }

    func testReturningOwnerDebugRouteRemainsAvailableWithoutAClaimToken() {
        XCTAssertEqual(
            PublicOnboardingState.initialStep(
                route: "returning", founderClaimAvailable: false
            ).rawValue,
            PublicOnboardingState.Step.returning.rawValue
        )
    }
}
