# App Store privacy checklist

Last updated: August 13, 2026

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
| Other Diagnostic Data | Yes | No | App Functionality |

Other User Content includes imported guides and source excerpts, topics, notes,
answer transcripts, answer bases, rubrics, and Study Plan content. Product
Interaction includes completion state, scores, coaching feedback, review
history, reminder preferences, spaced-repetition schedules, model-call
operation/time, and derived shadow comparisons. Other Diagnostic Data includes
the account-linked provider/model/response identifiers, route/outcome/failure,
latency, token/cache/cost counts, and qualification fingerprint retained for
reliability and billing verification. Neither category contains a new copy of
raw study content. Hosting/security request logs may be unlinked, but the App
Store answer is **linked** because the model-call diagnostic rows reference the
account and session.

Do not declare Audio Data for Unprompted's own collection: the app does not
upload or store voice recordings. It uses Apple's speech-recognition framework
and receives the resulting text. The policy still names Apple as that service.

## Third-party AI review note

Unprompted names Anthropic and OpenAI before first use and asks the user to Allow
AI processing or Continue without AI. It records the disclosure version, action,
and timestamp on the server. Anthropic receives only the text and study context
listed in the disclosure for guide processing, question generation, optional
coaching, and any scoring it performs. Submitted-answer scoring may instead send
that listed scoring context to OpenAI. During the owner-only provider evaluation,
the same scoring context is sent to both providers at once; Anthropic alone
determines the result shown and saved. OpenAI's result is used only for the
privacy-safe comparison and usage metadata described above. OpenAI also receives
a stable pseudonymous safety identifier derived from the account UUID with a
private app secret—not the Apple credential, name, or email.

OpenAI scoring uses the Responses API with `store: false`, so response
application state is not retained for the request. OpenAI states that standard
API data is not used for training by default unless the customer opts in. Default
abuse-monitoring logs may contain prompts and responses and are retained for up
to 30 days, subject to OpenAI's stated legal and safety exceptions. Setting
`store: false` does not disable that abuse-monitoring retention. Decline or
withdrawal blocks a provider call that has not yet been authorized without
deleting saved study data. A long guide import rechecks permission immediately
before every transmission. A request already authorized for transmission may
finish because it cannot be recalled; account deletion discards its late result.
Removing an imported source deletes that source and its source-owned transient
draft/raw import response. If the user explicitly created a Study Plan from the
guide, that plan retains its own guide provenance until account deletion; the
public and in-app retention copy must state this distinction.

## Required release gates

- [ ] Migration 0014 applied successfully.
- [ ] `/health` reports the intended required consent policy, latest supported
      policy, and cataloged minimum iOS build; a code deploy did not implicitly
      advance the required policy.
- [ ] The consent-capable build names both providers before any AI request and
  discloses the dual-provider shadow and pseudonymous safety identifier, then
  sends the exact policy version rendered with grant or decline.
- [ ] Missing or stale policy versions reject grant and decline without writing
  an event; withdrawal still succeeds from an older client.
- [ ] Decline leaves saved lessons, plans, history, and settings readable.
- [ ] Withdrawal blocks a provider transmission that has not yet crossed its
  authorization boundary and presents the choice again; an explicit guide parse
  retry rechecks permission.
- [ ] Consent grant is visible in `/auth/me`; then enable
  `AI_CONSENT_ENFORCEMENT_ENABLED=true` and restart the API.
- [ ] Privacy manifest is present in the signed archive and its data categories
  match the App Store Connect answers above.
- [ ] Privacy and support URLs are publicly reachable in a signed-out browser.
- [ ] The public policy links to both providers' current API data policies and
  distinguishes OpenAI response storage from default abuse-monitoring retention.
- [ ] Apple server-to-server endpoint is registered on the primary App ID.
- [ ] Revoked Apple credential signs the app out locally; a verified server
  event revokes API sessions without deleting study data.
- [ ] Account export contains consent history and linked model-call audit metadata;
      Delete account remains reachable
  inside the app.
