import AuthenticationServices
import CryptoKit
import Foundation
import Security
import SwiftUI
import UIKit

struct AuthCredentials: Codable, Equatable, Sendable {
    let accessToken: String
    let refreshToken: String
    let accessExpiresAt: Date
    let refreshExpiresAt: Date
}

private enum CredentialKeychain {
    static let service = "com.christrinh.devmax.auth"
    static let account = "primary"

    static func load() -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else {
            return nil
        }
        return result as? Data
    }

    static func save(_ data: Data) {
        clear()
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: data,
        ]
        let status = SecItemAdd(query as CFDictionary, nil)
        if status != errSecSuccess {
            NSLog("devmax: saving auth credentials failed (%d)", status)
        }
    }

    static func clear() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

actor AuthTokenStore {
    static let shared = AuthTokenStore()

    private var credentials: AuthCredentials?
    private var refreshTask: Task<AuthCredentials, Error>?

    private init() {
        guard let data = CredentialKeychain.load() else { return }
        credentials = try? JSONDecoder().decode(AuthCredentials.self, from: data)
    }

    func accessToken() -> String? { credentials?.accessToken }

    func hasUsableSession() -> Bool {
        guard let credentials else { return false }
        return credentials.refreshExpiresAt > Date()
    }

    func replace(with credentials: AuthCredentials) {
        self.credentials = credentials
        if let data = try? JSONEncoder().encode(credentials) {
            CredentialKeychain.save(data)
        }
    }

    func refresh(
        using operation: @escaping @Sendable (String) async throws -> AuthCredentials
    ) async throws -> AuthCredentials {
        if let refreshTask {
            let refreshed = try await refreshTask.value
            replace(with: refreshed)
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
            replace(with: refreshed)
            return refreshed
        } catch {
            clear()
            throw error
        }
    }

    func clear() {
        credentials = nil
        CredentialKeychain.clear()
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

    private func post<Response: Decodable, Body: Encodable>(
        _ path: String, body: Body
    ) async throws -> Response {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.httpBody = try LiveAPI.encoder.encode(body)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
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
        await store.replace(with: credentials)
    }

    func refresh(_ refreshToken: String) async throws -> AuthCredentials {
        try await post(
            "auth/refresh", body: RefreshBody(refreshToken: refreshToken)
        )
    }
}

@MainActor
final class AuthState: NSObject, ObservableObject {
    enum Status: Equatable { case checking, signedOut, signingIn, authenticated }

    @Published private(set) var status: Status = .checking
    @Published private(set) var errorMessage: String?
    @Published private(set) var profile: AccountProfile?

    private let client: AuthClient
    private let api: DevmaxAPI
    private var nonce: String?
    private var controller: ASAuthorizationController?

    init(client: AuthClient = APIConfig.authClient, api: DevmaxAPI = APIConfig.client) {
        self.client = client
        self.api = api
        super.init()
    }

    var isAuthenticated: Bool { status == .authenticated }
    var isWorking: Bool { status == .checking || status == .signingIn }

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

    func startAppleSignIn() {
        guard !isWorking else { return }
        status = .signingIn
        errorMessage = nil
        Task {
            do {
                let nonce = try await client.nonce()
                self.nonce = nonce
                let request = ASAuthorizationAppleIDProvider().createRequest()
                request.requestedScopes = [.fullName, .email]
                request.nonce = SHA256.hash(data: Data(nonce.utf8))
                    .map { String(format: "%02x", $0) }
                    .joined()
                let controller = ASAuthorizationController(authorizationRequests: [request])
                controller.delegate = self
                controller.presentationContextProvider = self
                self.controller = controller
                controller.performRequests()
            } catch {
                status = .signedOut
                errorMessage = "Couldn't start sign-in. Your setup is still here."
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
        status = .signedOut
    }
}

extension AuthState: ASAuthorizationControllerDelegate {
    func authorizationController(
        controller: ASAuthorizationController,
        didCompleteWithAuthorization authorization: ASAuthorization
    ) {
        guard
            let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
            let identityData = credential.identityToken,
            let codeData = credential.authorizationCode,
            let identityToken = String(data: identityData, encoding: .utf8),
            let authorizationCode = String(data: codeData, encoding: .utf8),
            let nonce
        else {
            status = .signedOut
            errorMessage = "Apple didn't return usable credentials. Try again."
            return
        }

        let displayName = credential.fullName.map {
            PersonNameComponentsFormatter().string(from: $0)
        }
        Task {
            do {
                try await client.signIn(
                    identityToken: identityToken,
                    authorizationCode: authorizationCode,
                    nonce: nonce,
                    displayName: displayName
                )
                self.nonce = nil
                self.controller = nil
                await loadAuthenticatedProfile()
            } catch {
                status = .signedOut
                errorMessage = "Sign-in didn't finish. Your setup is still here."
            }
        }
    }

    func authorizationController(
        controller: ASAuthorizationController,
        didCompleteWithError error: Error
    ) {
        self.controller = nil
        nonce = nil
        status = .signedOut
        if (error as? ASAuthorizationError)?.code != .canceled {
            errorMessage = "Sign-in didn't finish. Your setup is still here."
        }
    }
}

extension AuthState: ASAuthorizationControllerPresentationContextProviding {
    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow) ?? ASPresentationAnchor()
    }
}
