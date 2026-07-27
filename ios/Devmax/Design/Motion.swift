import SwiftUI

/// The complete motion vocabulary — four animations, matching the handoff table.
///
/// Nothing else. No springs, no shimmer, no celebratory animation. If a new
/// transition seems needed, the design's answer is that it isn't.
enum Motion {
    /// New thread turn, sheets, inline errors, empty state, expanded transcript.
    static let fade = Animation.easeOut(duration: 0.30)
    static let fadeFast = Animation.easeOut(duration: 0.25)
    static let fadeSlow = Animation.easeOut(duration: 0.35)

    /// Score block landing. 450ms, opacity 0→1, translateY 10 → −2 → 0.
    static let settle = Animation.timingCurve(0.2, 0.7, 0.3, 1, duration: 0.45)

    static let pulseDuration: Double = 1.8
    static let dotCycle: Double = 1.0
    static let dotStagger: Double = 0.18
}

/// `wcFade` — opacity 0→1 with a 6px rise.
struct WCFade: ViewModifier {
    @State private var shown = false
    var animation: Animation = Motion.fade

    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 6)
            .onAppear { withAnimation(animation) { shown = true } }
    }
}

/// `wcSettle` — the score block landing: 10px down, past −2px, to rest.
struct WCSettle: ViewModifier {
    @State private var phase = 0

    func body(content: Content) -> some View {
        content
            .opacity(phase == 0 ? 0 : 1)
            .offset(y: phase == 0 ? 10 : (phase == 1 ? -2 : 0))
            .onAppear {
                withAnimation(Motion.settle) { phase = 1 }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.27) {
                    withAnimation(.easeOut(duration: 0.18)) { phase = 2 }
                }
            }
    }
}

extension View {
    func wcFade(_ animation: Animation = Motion.fade) -> some View {
        modifier(WCFade(animation: animation))
    }

    func wcSettle() -> some View { modifier(WCSettle()) }
}

/// `wcPulse` — the recording ring. A 1px accent border scaling 1 → 1.45 as it
/// fades out, forever, while recording.
struct PulsingRing: View {
    @State private var animating = false

    var body: some View {
        Circle()
            .strokeBorder(Theme.accent, lineWidth: 1)
            .scaleEffect(animating ? 1.45 : 1)
            .opacity(animating ? 0 : 0.55)
            .animation(
                .easeOut(duration: Motion.pulseDuration).repeatForever(autoreverses: false),
                value: animating
            )
            .onAppear { animating = true }
    }
}

/// The `SCORING` indicator: three 3px accent dots, 1s cycle, 180ms stagger.
/// Deliberately inline — never a full-screen spinner.
struct ScoringDots: View {
    @State private var animating = false

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(Theme.accent)
                    .frame(width: 3, height: 3)
                    .opacity(animating ? 1 : 0.25)
                    .animation(
                        .easeInOut(duration: Motion.dotCycle)
                            .repeatForever()
                            .delay(Double(index) * Motion.dotStagger),
                        value: animating
                    )
            }
        }
        .onAppear { animating = true }
    }
}
