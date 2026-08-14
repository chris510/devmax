# Devmax privacy and data handling

Last updated: August 13, 2026

This document records the behavior implemented by Devmax. It is product and
engineering policy, not a substitute for legal review before a public App Store
release.

## Data Devmax stores

Devmax stores the Apple account identifier needed to recognize an account;
imported study material and its source versions; proposed and confirmed review
topics; Study Plans; answer transcripts; scores and coaching feedback; spaced-
repetition schedules; reminder settings; and registered device tokens.

Devmax does not upload or store voice recordings. iOS speech recognition turns
speech into text, and Devmax receives the transcript.

Account data is retained while the account exists. Removing an individual
imported source also removes its source-owned transient guide draft and raw
import response; a Study Plan the user explicitly created keeps its own guide
provenance. A user can also delete the complete account in the app. Account
deletion revokes the Apple authorization before deleting study material, cards,
history, schedules, plans, settings, tokens, and account records. If revocation
cannot complete, server deletion stops rather than partially deleting the
account. A user can also export the account data as JSON.

Server backups and operational logs follow the hosting provider's configured
retention policy. The public policy deliberately does not promise a shorter
backup deletion deadline than the deployed systems enforce. The App Store
support listing remains the public contact for privacy questions.

## AI processing

Devmax uses the Anthropic API to propose source-grounded topics and plan structure,
generate review questions, and provide optional coaching. A submitted answer may
be scored by Anthropic or OpenAI. Devmax sends only the guide, answer transcript,
and related study context needed for the specific operation.

During an owner-only provider evaluation, the same scoring context may be sent
to Anthropic and OpenAI at the same time. Anthropic remains authoritative: only
its result is shown or written, while the OpenAI result is retained only as
privacy-safe comparison and usage metadata. OpenAI also receives a stable
pseudonymous safety identifier derived from the account UUID with a private app
secret. It is not the Apple credential, name, email, question, or answer.

The linked operational record includes account/session/scoring-event
correlation, provider and model, provider response ID, route and typed outcome,
latency, token counts, qualification fingerprint, and derived shadow Recall and
behavior comparisons. It does not add a copy of the raw question, answer,
grounding, feedback, or mastery text.

Anthropic's published commercial API policy says inputs and outputs are not used
to train its models by default. Its standard retention policy says API inputs and
outputs are deleted from its backend within 30 days, except where a different
agreement applies or longer retention is needed for usage-policy enforcement or
legal compliance. Devmax does not submit provider feedback or opt users into model
training.

When OpenAI handles scoring, Devmax uses the Responses API with `store: false`,
so response application state is not retained for that request. OpenAI states
that standard API data is not used to train its models by default unless the
customer explicitly opts in. Its default abuse-monitoring logs may contain
prompts and responses and are retained for up to 30 days, unless longer
retention is required by law or reasonably necessary to prevent harm.
`store: false` does not disable that default abuse-monitoring retention.

Current provider references:

- <https://privacy.anthropic.com/en/articles/7996868-is-my-data-used-for-model-training>
- <https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data>
- <https://developers.openai.com/api/docs/guides/your-data#default-usage-policies-by-endpoint>

Before any of those operations, the current versioned Anthropic and OpenAI
disclosure must be granted explicitly. Grant and decline are accepted only when
the client identifies the exact disclosure version it rendered; withdrawal stays
available from an older client. Decline and withdrawal are recorded server-side
and block future guide processing, question generation, answer scoring, and
coaching while leaving saved lessons, Study Plans, history, settings, and
schedules readable. A completed withdrawal prevents any provider call that has
not yet been authorized. Long guide imports recheck permission immediately
before every transmission, including an explicit retry. A provider request
already authorized for transmission may finish because withdrawal or account
deletion cannot recall it. If the account is deleted while that request runs,
Devmax discards the result instead of restoring deleted account data.

## Sign in with Apple account changes

The app checks Apple's credential state at launch/foreground and observes Apple's
credential-revoked notification. A revoked or missing relationship clears local
credentials and returns to sign-in. The API accepts only cryptographically
verified Apple server-to-server notifications; consent-revoked and
account-deleted events clear the stored Apple refresh authorization and revoke
every live Devmax session without silently deleting study data.

## Sensitive study material

Devmax is a study tool, not a medical or legal authority. Users should import only
material they are permitted to process and should avoid unnecessary personal,
patient, client, or confidential information. Devmax treats the user's source as
the answer basis and does not claim that an extracted topic is independently
verified professional guidance.
