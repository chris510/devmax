# Devmax Android

This directory contains the native Kotlin/Jetpack Compose Android client. It is
an additive client beside the existing SwiftUI app; the FastAPI backend remains
the product core.

## Current milestone: M0 feasibility spike

M0 is deliberately fixture-backed. It proves the risky Android lifecycle seam
before authentication, push, or full review parity are built:

- compatible decoding of shared `/cards/due` and session-start fixtures, with
  their exact shape and resume semantics validated against FastAPI models;
- one deterministic Raft review session;
- fake incremental speech callbacks behind a capture-generation boundary;
- exact per-session atomic draft checkpoints on every partial and keystroke;
- voice/text switching without duplication;
- Activity recreation with the microphone restored stopped;
- book-fold and unfold transitions on a Pixel 10 Pro Fold emulator;
- a hinge-aware, centered single-column layout; and
- cold-process restoration after the app is killed during fake recognition.

M0 does **not** connect to production, authenticate, register for FCM, submit an
answer, show follow-ups/scores/history, or use Android `SpeechRecognizer`. The
screen says `M0 · FIXTURE` so it cannot be mistaken for a connected client.

## Toolchain

- Android Studio Quail 3 / 2026.1.3
- Android Gradle Plugin 9.3.0
- Gradle 9.5.0, distribution checksum pinned in the wrapper
- Kotlin 2.3.21 with the Compose compiler plugin
- JDK 17 compilation toolchain
- compile/target SDK 36, min SDK 23
- Pixel 10 Pro Fold, Android 16.1 / API 36.1 Google Play ARM64 image

`androidx.core` stays on 1.18.0 because 1.19.0 requires compile SDK 37. The
client can target API 36 without opting into the Android 17 preview toolchain.

## Build and verify

From `android/` on macOS:

```sh
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="$HOME/Library/Android/sdk"

./gradlew testDebugUnitTest lintDebug assembleDebug
```

For emulator tests, start an unfolded Pixel 10 Pro Fold AVD and keep it awake:

```sh
adb emu unfold
adb shell input keyevent KEYCODE_WAKEUP
adb shell wm dismiss-keyguard
adb shell svc power stayon true

./gradlew connectedDebugAndroidTest
```

The connected suite performs both an Activity recreation and a real emulator
book-fold/unfold. The fold test also asserts that the question column moves
wholly into one pane instead of straddling the separating fold.

The debug APK is written to:

```text
app/build/outputs/apk/debug/app-debug.apk
```

## Shared contract fixtures

The app packages fixtures directly from:

```text
../contracts/mobile/review/v1/
```

Do not copy them into the Android module. Both local decoder tests and the app
must consume the same files so wire drift cannot hide behind duplicate mocks.

## Promotion gates

Before M0 becomes a connected founder alpha:

1. Amend and implement provider-linked Google/Apple identity semantics.
2. Make SwiftUI decode the same shared review fixtures and add Android checks to
   CI so wire changes cannot drift between clients.
3. Add installation-scoped FCM registration and provider-aware push dispatch.
4. Connect Today → session → draft → up to two probes → score/history using the
   real backend while preserving the existing server-owned invariants.
5. Replace fixture speech with lifecycle-managed native recognition and verify
   60–120 second answers on a physical Samsung Fold-class device.
6. Keep text as a complete fallback and disclose when the installed recognition
   service may process audio off-device.

Emulator success is enough to continue implementation. It is not evidence that
Samsung speech, microphones/Bluetooth, closed-cover handoff, thermals, or battery
behavior are release-ready.
