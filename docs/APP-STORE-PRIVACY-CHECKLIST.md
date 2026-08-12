# App Store privacy checklist

Last updated: August 12, 2026

Use this sheet for App Store Connect and review notes. It describes the shipped
client and API; keep it synchronized with `ios/Devmax/PrivacyInfo.xcprivacy` and
the public privacy policy.

## URLs

- Privacy Policy: `https://devmax-recall.christrinh5.chatgpt.site/privacy`
- Privacy Choices: `https://devmax-recall.christrinh5.chatgpt.site/privacy`
- Support URL: `https://devmax-recall.christrinh5.chatgpt.site/`
- Sign in with Apple server notification endpoint:
  `https://devmax-production.up.railway.app/auth/apple/notifications`

The privacy policy and support URL must be publicly reachable without an
Unprompted, Apple, ChatGPT, or workspace login before external TestFlight or App
Review.

## App privacy answers

Unprompted collects the following data, links it to the user's account, uses it
only for App Functionality and the personalization noted below, and does not use
it for tracking or advertising:

| Data type | Linked | Tracking | Purpose |
|---|---:|---:|---|
| Name | Yes | No | App Functionality |
| Email Address | Yes | No | App Functionality |
| User ID | Yes | No | App Functionality |
| Device ID | Yes | No | App Functionality (APNs delivery token) |
| Other User Content | Yes | No | App Functionality; Product Personalization |
| Product Interaction | Yes | No | App Functionality; Product Personalization |
| Other Diagnostic Data | No | No | App Functionality (hosting/security request logs) |

Other User Content includes imported guides and source excerpts, topics, notes,
answer transcripts, answer bases, rubrics, and Study Plan content. Product
Interaction includes completion state, scores, coaching feedback, review
history, reminder preferences, and spaced-repetition schedules.

Do not declare Audio Data for Unprompted's own collection: the app does not
upload or store voice recordings. It uses Apple's speech-recognition framework
and receives the resulting text. The policy still names Apple as that service.

## Third-party AI review note

Unprompted names Anthropic before first use and asks the user to Allow AI
processing or Continue without AI. It records the disclosure version, action,
and timestamp on the server. Anthropic receives only the text and study context
listed in the disclosure for guide processing, question generation, scoring,
and optional coaching. Decline or withdrawal blocks those provider calls at the
server boundary without deleting saved study data.

## Required release gates

- [ ] Migration 0014 applied successfully.
- [ ] Build 6 or later shows the consent choice before any Anthropic request.
- [ ] Decline leaves saved lessons, plans, history, and settings readable.
- [ ] Withdrawal blocks a live AI operation and presents the choice again.
- [ ] Consent grant is visible in `/auth/me`; then enable
  `AI_CONSENT_ENFORCEMENT_ENABLED=true` and restart the API.
- [ ] Privacy manifest is present in the signed archive and its data categories
  match the App Store Connect answers above.
- [ ] Privacy and support URLs are publicly reachable in a signed-out browser.
- [ ] Apple server-to-server endpoint is registered on the primary App ID.
- [ ] Revoked Apple credential signs the app out locally; a verified server
  event revokes API sessions without deleting study data.
- [ ] Account export contains consent history; Delete account remains reachable
  inside the app.
