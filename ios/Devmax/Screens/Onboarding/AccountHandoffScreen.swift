import AuthenticationServices
import SwiftUI

/// The account handoff appears only when a prepared setup is worth saving.
/// Authentication is not a new tab and it does not request notification,
/// microphone, or speech permissions.
struct AccountHandoffScreen: View {
    @EnvironmentObject private var auth: AuthState

    var preparedTitle: String?
    var preparedMeta: String = "GUIDE SAVED ON THIS DEVICE"
    var backAction: (() -> Void)?
    var forceFailure = false

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: "ACCOUNT")

            VStack(alignment: .leading, spacing: 24) {
                Text(isFailure ? "Couldn't sign in." : "Keep this setup.")
                    .font(WCFont.serif(isFailure ? 29 : 30))
                    .foregroundStyle(Theme.textStrong)
                    .accessibilityAddTraits(.isHeader)

                Text(accountCopy)
                .font(WCFont.serif(18))
                .foregroundStyle(Theme.textSerif)
                .lineSpacing(5)

                if isFailure {
                    signInNotice
                }

                if let preparedTitle {
                    VStack(alignment: .leading, spacing: 8) {
                        MetaText(
                            text: isFailure ? "STILL READY" : "READY TO PROCESS",
                            font: WCFont.mono(10),
                            tracking: 1.1, color: Theme.meta
                        )
                        Text(preparedTitle)
                            .font(WCFont.sans(16, weight: 500))
                            .foregroundStyle(Theme.text)
                        MetaText(
                            text: preparedMeta, font: WCFont.mono(9.5),
                            tracking: 0.6, color: Theme.metaFaint
                        )
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(15)
                    .background(
                        Theme.surface,
                        in: RoundedRectangle(cornerRadius: Metrics.inlineRadius)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: Metrics.inlineRadius)
                            .strokeBorder(Theme.border, lineWidth: 1)
                    )
                }

                if !isFailure {
                    signInNotice
                }

                MetaText(
                    text: isFailure
                        ? "NO ACCOUNT CREATED · NO GUIDE PROCESSED"
                        : "YOUR EMAIL CAN REMAIN HIDDEN · NO PASSWORD TO CREATE",
                    font: WCFont.mono(10), tracking: 0.55, color: Theme.metaFaint
                )
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .padding(.horizontal, Metrics.screenPadding)
            .padding(.bottom, 20)

            VStack(spacing: 6) {
                AppleContinueButton(
                    purpose: .signIn,
                    accessibilityHint: "Signs in without losing the setup saved on this device."
                )
                if let backAction {
                    Button("Back to my guide", action: backAction)
                        .buttonStyle(.plain)
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.metaAlt)
                        .frame(maxWidth: .infinity, minHeight: Metrics.minTapTarget)
                }
            }
            .padding(.horizontal, Metrics.screenPadding)
            .padding(.bottom, Metrics.bottomSafeArea)
        }
        .background(Theme.bg)
    }

    private var accountCopy: String {
        if isFailure {
            return "Your guide is still saved on this device. Nothing was sent for processing."
        }
        if preparedTitle != nil {
            return "Sign in before Devmax processes your guide. Your topics, "
                + "review history, and schedule will stay with your account."
        }
        return "Sign in to keep your study material, review history, and schedule "
            + "together on this account."
    }

    private var isFailure: Bool { forceFailure || auth.errorMessage != nil }

    private var signInNotice: some View {
        InlineNotice {
            Text(noticeCopy)
                .font(WCFont.sans(13.5))
                .foregroundStyle(Theme.textSecondary)
                .lineSpacing(3)
        }
    }

    private var noticeCopy: String {
        if isFailure {
            return "Apple sign-in didn't complete. Try again, or return to the guide "
                + "without losing your text."
        }
        return "Continue with Apple shares your Apple identity with Devmax. "
            + "Your guide is sent for topic extraction only after sign-in succeeds."
    }

}

/// AuthenticationServices owns the label, Apple mark, colors, and pressed state.
/// Progress and errors stay outside the control so it never turns into a branded
/// approximation or changes its required `Continue with Apple` label mid-request.
struct AppleContinueButton: View {
    @EnvironmentObject private var auth: AuthState

    let purpose: AppleAuthorizationPurpose
    var enabled = true
    var accessibilityHint: String

    private var canStart: Bool {
        enabled && !auth.isWorking && auth.isAppleAuthorizationReady(for: purpose)
    }

    var body: some View {
        VStack(spacing: 8) {
            SignInWithAppleButton(.continue) { request in
                auth.configureAppleRequest(request, for: purpose)
            } onCompletion: { result in
                auth.completeAppleAuthorization(result, for: purpose)
            }
            .signInWithAppleButtonStyle(.white)
            .frame(maxWidth: .infinity)
            .frame(height: 54)
            .clipShape(RoundedRectangle(cornerRadius: Metrics.primaryRadius))
            .disabled(!canStart)
            .opacity(canStart ? 1 : 0.55)
            .accessibilityHint(accessibilityHint)

            if auth.isWorking {
                HStack(spacing: 7) {
                    ProgressView()
                        .controlSize(.small)
                        .tint(Theme.metaAlt)
                    Text("Connecting…")
                        .font(WCFont.mono(10))
                        .tracking(0.4)
                        .foregroundStyle(Theme.metaAlt)
                }
                .frame(minHeight: 18)
                .accessibilityElement(children: .combine)
            }
        }
        .task(id: "\(purpose.rawValue)-\(enabled)") {
            guard enabled else { return }
            await auth.prepareAppleAuthorization(for: purpose)
        }
    }
}
