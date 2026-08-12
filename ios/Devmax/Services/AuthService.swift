import AuthenticationServices
import CryptoKit
import Foundation
import Security
import SwiftUI

struct AuthCredentials: Codable, Equatable, Sendable {
    let accessToken: String
    let refreshToken: String
    let accessExpiresAt: Date
    let refreshExpiresAt: Date
}

private enum CredentialKeychain {
    static let service = "com.christrinh.devmax.auth"
    static let account = "primary"

    struct Failure: Error, Equatable {
        let status: OSStatus
    }

    private static var identityQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    static func load() -> Data? {
        var query = identityQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else {
            return nil
        }
        return result as? Data
    }

    static func save(_ data: Data) throws {
        let attributes: [String: Any] = [
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: data,
        ]
        var status = SecItemUpdate(
            identityQuery as CFDictionary, attributes as CFDictionary
        )
        if status == errSecItemNotFound {
            var add = identityQuery
            for (key, value) in attributes { add[key] = value }
            status = SecItemAdd(add as CFDictionary, nil)
            // A concurrent writer can win between the update and add. Re-run the
            // atomic update instead of deleting either writer's usable credential.
            if status == errSecDuplicateItem {
                status = SecItemUpdate(
                    identityQuery as CFDictionary, attributes as CFDictionary
                )
            }
        }
        guard status == errSecSuccess else { throw Failure(status: status) }
        guard load() == data else { throw Failure(status: errSecDecode) }
    }

    static func clear() {
        SecItemDelete(identityQuery as CFDictionary)
    }
}

/// Injectable so failure-path tests can prove an unsuccessful durable write never
/// becomes an authenticated in-memory session.
struct AuthCredentialPersistence: @unchecked Sendable {
    let load: () -> Data?
    let save: (Data) throws -> Void
    let clear: () -> Void

    static let keychain = AuthCredentialPersistence(
        load: CredentialKeychain.load,
        save: CredentialKeychain.save,
        clear: CredentialKeychain.clear
    )
}

actor AuthTokenStore {
    static let shared = AuthTokenStore()

    private var credentials: AuthCredentials?
    private var refreshTask: Task<AuthCredentials, Error>?
    private let persistence: AuthCredentialPersistence?

    init(
        credentials: AuthCredentials? = nil,
        persistence: AuthCredentialPersistence? = .keychain
    ) {
        self.persistence = persistence
        if let credentials {
            self.credentials = credentials
        } else if let data = persistence?.load() {
            self.credentials = try? JSONDecoder().decode(AuthCredentials.self, from: data)
        }
    }

    func accessToken() -> String? { credentials?.accessToken }

    func hasUsableSession() -> Bool {
        guard let credentials else { return false }
        return credentials.refreshExpiresAt > Date()
    }

    func replace(with credentials: AuthCredentials) throws {
        if let persistence {
            let data = try JSONEncoder().encode(credentials)
            try persistence.save(data)
            guard
                let recovered = persistence.load(),
                try JSONDecoder().decode(AuthCredentials.self, from: recovered) == credentials
            else { throw CredentialKeychain.Failure(status: errSecDecode) }
        }
        self.credentials = credentials
    }

    func refresh(
        using operation: @escaping @Sendable (String) async throws -> AuthCredentials
    ) async throws -> AuthCredentials {
        if let refreshTask {
            let refreshed = try await refreshTask.value
            try replace(with: refreshed)
            return refreshed
        }
        guard let refreshToken = credentials?.refreshToken else {
            throw APIError.unauthorized
        }
        let task = Task { try await operation(refreshToken) }
        refreshTask = task
        defer { refreshTask = nil }
        do {
            let refreshed = try await task.value
            try replace(with: refreshed)
            return refreshed
        } catch {
            clear()
            throw error
        }
    }

    func clear() {
        credentials = nil
        persistence?.clear()
    }
}

private struct AuthNonceResponse: Decodable { let nonce: String }

private struct AppleSignInBody: Encodable {
    let identityToken: String
    let authorizationCode: String
    let nonce: String
    let displayName: String?
}

private struct RefreshBody: Encodable { let refreshToken: String }

struct AuthClient {
    let baseURL: URL
    var session: URLSession = .shared
    var store: AuthTokenStore = .shared

    private func request<Response: Decodable>(
        _ method: String,
        _ path: String,
        body: Data? = nil,
        headers: [String: String] = [:]
    ) async throws -> Response {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        for (name, value) in headers {
            request.setValue(value, forHTTPHeaderField: name)
        }
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.transport(error)
        }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            if code == 401 { throw APIError.unauthorized }
            throw APIError.status(code)
        }
        return try LiveAPI.decoder.decode(Response.self, from: data)
    }

    private func post<Response: Decodable, Body: Encodable>(
        _ path: String,
        body: Body,
        headers: [String: String] = [:]
    ) async throws -> Response {
        try await request(
            "POST", path, body: try LiveAPI.encoder.encode(body), headers: headers
        )
    }

    private func get<Response: Decodable>(
        _ path: String,
        bearerToken: String
    ) async throws -> Response {
        return try await request(
            "GET",
            path,
            headers: ["Authorization": "Bearer \(bearerToken)"]
        )
    }

    func nonce() async throws -> String {
        struct Empty: Encodable {}
        let response: AuthNonceResponse = try await post("auth/nonce", body: Empty())
        return response.nonce
    }

    func signIn(
        identityToken: String,
        authorizationCode: String,
        nonce: String,
        displayName: String?
    ) async throws {
        let credentials: AuthCredentials = try await post(
            "auth/apple",
            body: AppleSignInBody(
                identityToken: identityToken,
                authorizationCode: authorizationCode,
                nonce: nonce,
                displayName: displayName
            )
        )
        try await store.replace(with: credentials)
    }

    /// The dedicated migration secret is deliberately accepted by one method
    /// and attached to one request. It can never leak into normal sign-in,
    /// refresh, `/auth/me`, or `LiveAPI` traffic.
    func claimFounder(
        identityToken: String,
        authorizationCode: String,
        nonce: String,
        displayName: String?,
        claimToken: String
    ) async throws -> AccountProfile {
        guard !claimToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw APIError.unauthorized
        }
        let credentials: AuthCredentials = try await post(
            "auth/founder/apple-claim",
            body: AppleSignInBody(
                identityToken: identityToken,
                authorizationCode: authorizationCode,
                nonce: nonce,
                displayName: displayName
            ),
            headers: ["X-Founder-Claim-Token": claimToken]
        )

        do {
            // `replace` persists and reads the exact bytes back before making the
            // session visible in memory. Only then prove the returned bearer is
            // the fixed founder without reusing either migration credential.
            try await store.replace(with: credentials)
            let profile: AccountProfile = try await get(
                "auth/me", bearerToken: credentials.accessToken
            )
            guard profile.isFounder else { throw APIError.unauthorized }
            return profile
        } catch {
            await store.clear()
            throw error
        }
    }

    func refresh(_ refreshToken: String) async throws -> AuthCredentials {
        try await post(
            "auth/refresh", body: RefreshBody(refreshToken: refreshToken)
        )
    }
}

enum AppleAuthorizationPurpose: String, Hashable, Sendable {
    case signIn
    case founderClaim
}

@MainActor
final class AuthState: ObservableObject {
    enum Status: Equatable { case checking, signedOut, signingIn, authenticated }

    @Published private(set) var status: Status = .checking
    @Published private(set) var errorMessage: String?
    @Published private(set) var profile: AccountProfile?
    @Published private(set) var isPreparingAppleAuthorization = false
    @Published private(set) var consentInterventionRequested = false

    private let client: AuthClient
    private let api: DevmaxAPI
    private var appleNonce: String?
    private var preparedApplePurpose: AppleAuthorizationPurpose?

    init(client: AuthClient = APIConfig.authClient, api: DevmaxAPI = APIConfig.client) {
        self.client = client
        self.api = api
    }

    var isAuthenticated: Bool { status == .authenticated }
    var needsAIConsentPresentation: Bool {
        profile?.aiConsentPromptRequired == true || consentInterventionRequested
    }
    var isWorking: Bool {
        status == .checking || status == .signingIn || isPreparingAppleAuthorization
    }

    func bootstrap() async {
        if DebugFlags.shared.useMockAPI || APIConfig.hasLegacyKey {
            await loadAuthenticatedProfile()
            return
        }
        guard await client.store.hasUsableSession() else {
            status = .signedOut
            return
        }
        await loadAuthenticatedProfile()
    }

    func isAppleAuthorizationReady(for purpose: AppleAuthorizationPurpose) -> Bool {
        appleNonce != nil && preparedApplePurpose == purpose
    }

    /// Apple's SwiftUI button configures its request synchronously, while Devmax's
    /// replay-resistant nonce comes from the API. Prepare that nonce when the
    /// screen appears, then enable the native button only after it is available.
    func prepareAppleAuthorization(
        for purpose: AppleAuthorizationPurpose,
        preservingError: Bool = false
    ) async {
        guard !isAppleAuthorizationReady(for: purpose),
              !isPreparingAppleAuthorization
        else { return }

        isPreparingAppleAuthorization = true
        if !preservingError { errorMessage = nil }
        defer { isPreparingAppleAuthorization = false }

        do {
            // Debug routes render entirely from fixtures. Giving their native
            // Apple button a local nonce keeps screenshot QA independent of a
            // server and can never authenticate because no proof is submitted.
            let nonce = if DebugFlags.shared.useMockAPI {
                "fixture-\(UUID().uuidString)"
            } else {
                try await client.nonce()
            }
            guard !Task.isCancelled else { return }
            appleNonce = nonce
            preparedApplePurpose = purpose
        } catch {
            appleNonce = nil
            preparedApplePurpose = nil
            if status != .authenticated { status = .signedOut }
            errorMessage = "Couldn't prepare Apple sign-in. Your setup is still here."
        }
    }

    func configureAppleRequest(
        _ request: ASAuthorizationAppleIDRequest,
        for purpose: AppleAuthorizationPurpose
    ) {
        guard let appleNonce, preparedApplePurpose == purpose else {
            errorMessage = "Couldn't prepare Apple sign-in. Try again."
            return
        }
        errorMessage = nil
        status = .signingIn
        request.requestedScopes = [.fullName, .email]
        request.nonce = SHA256.hash(data: Data(appleNonce.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }

    func completeAppleAuthorization(
        _ result: Result<ASAuthorization, Error>,
        for purpose: AppleAuthorizationPurpose
    ) {
        switch result {
        case .failure(let error):
            clearPreparedAppleAuthorization()
            if status != .authenticated { status = .signedOut }
            if (error as? ASAuthorizationError)?.code != .canceled {
                errorMessage = "Sign-in didn't finish. Your setup is still here."
            }
            prepareFreshAppleAuthorization(for: purpose)

        case .success(let authorization):
            guard
                preparedApplePurpose == purpose,
                let nonce = appleNonce,
                let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
                let identityData = credential.identityToken,
                let codeData = credential.authorizationCode,
                let identityToken = String(data: identityData, encoding: .utf8),
                let authorizationCode = String(data: codeData, encoding: .utf8)
            else {
                clearPreparedAppleAuthorization()
                if status != .authenticated { status = .signedOut }
                errorMessage = "Apple didn't return usable credentials. Try again."
                prepareFreshAppleAuthorization(for: purpose)
                return
            }

            let displayName = credential.fullName.map {
                PersonNameComponentsFormatter().string(from: $0)
            }
            clearPreparedAppleAuthorization()
            status = .signingIn
            Task {
                switch purpose {
                case .signIn:
                    do {
                        try await client.signIn(
                            identityToken: identityToken,
                            authorizationCode: authorizationCode,
                            nonce: nonce,
                            displayName: displayName
                        )
                        await loadAuthenticatedProfile()
                    } catch {
                        status = .signedOut
                        errorMessage = "Sign-in didn't finish. Your setup is still here."
                        prepareFreshAppleAuthorization(for: purpose)
                    }

                case .founderClaim:
                    do {
                        guard let claimToken = APIConfig.founderClaimToken else {
                            throw APIError.unauthorized
                        }
                        profile = try await client.claimFounder(
                            identityToken: identityToken,
                            authorizationCode: authorizationCode,
                            nonce: nonce,
                            displayName: displayName,
                            claimToken: claimToken
                        )
                        status = .authenticated
                        errorMessage = nil
                    } catch {
                        profile = nil
                        status = .signedOut
                        errorMessage = "The owner claim didn't finish. Nothing changed. Try again."
                        prepareFreshAppleAuthorization(for: purpose)
                    }
                }
            }
        }
    }

    func signOutLocally() async {
        try? await api.logout()
        await clearLocalAccountState()
    }

    func markOnboardingComplete() async {
        profile = try? await api.completeOnboarding()
    }

    func refreshProfile() async {
        await loadAuthenticatedProfile()
    }

    func markAIConsentRequired() {
        consentInterventionRequested = true
    }

    func updateAIConsent(_ action: String) async -> Bool {
        errorMessage = nil
        do {
            _ = try await api.updateAIConsent(action: action)
            await loadAuthenticatedProfile()
            consentInterventionRequested = false
            return true
        } catch {
            errorMessage = "That privacy choice couldn't be saved. Nothing was sent."
            return false
        }
    }

    func checkAppleCredentialState() async {
        guard isAuthenticated,
              !DebugFlags.shared.useMockAPI,
              let identifier = profile?.appleUserIdentifier,
              !identifier.isEmpty
        else { return }

        let credentialState = await withCheckedContinuation { continuation in
            ASAuthorizationAppleIDProvider().getCredentialState(forUserID: identifier) { state, _ in
                continuation.resume(returning: state)
            }
        }
        if Self.appleCredentialRequiresSignOut(credentialState) {
            await clearLocalAccountState()
            errorMessage = "Apple authorization changed. Sign in again to continue."
        }
    }

    static func appleCredentialRequiresSignOut(
        _ state: ASAuthorizationAppleIDProvider.CredentialState
    ) -> Bool {
        switch state {
        case .revoked, .notFound: true
        case .authorized, .transferred: false
        @unknown default: false
        }
    }

    func deleteAccount() async throws {
        try await api.deleteAccount()
        await clearLocalAccountState()
    }

    private func loadAuthenticatedProfile() async {
        errorMessage = nil
        profile = try? await api.accountProfile()
        status = .authenticated
        if profile == nil { errorMessage = "Couldn't load this account." }
    }

    private func clearLocalAccountState() async {
        await client.store.clear()
        GuideDraftStore.clear()
        PublicSetupStore.clear()
        profile = nil
        consentInterventionRequested = false
        status = .signedOut
    }

    private func clearPreparedAppleAuthorization() {
        appleNonce = nil
        preparedApplePurpose = nil
    }

    private func prepareFreshAppleAuthorization(for purpose: AppleAuthorizationPurpose) {
        Task { await prepareAppleAuthorization(for: purpose, preservingError: true) }
    }
}
