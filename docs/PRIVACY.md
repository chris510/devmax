# Devmax privacy and data handling

Last updated: August 7, 2026

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

Account data is retained while the account exists. A user can remove an
individual imported source or delete the complete account in the app. Account
deletion revokes the Apple authorization before deleting study material, cards,
history, schedules, plans, settings, tokens, and account records. If revocation
cannot complete, server deletion stops rather than partially deleting the
account. A user can also export the account data as JSON.

Server backups and operational logs must follow the hosting provider's configured
retention policy. Before launch, that period and the support contact must be
filled into the public legal policy; the app does not currently promise a backup
deletion deadline it cannot enforce.

## AI processing

Devmax uses the Anthropic API to propose source-grounded topics and plan structure,
generate review questions, and score answer transcripts. It sends only the guide,
answer transcript, and related study context needed for that operation.

Anthropic's published commercial API policy says inputs and outputs are not used
to train its models by default. Its standard retention policy says API inputs and
outputs are deleted from its backend within 30 days, except where a different
agreement applies or longer retention is needed for usage-policy enforcement or
legal compliance. Devmax does not submit provider feedback or opt users into model
training.

Current provider references:

- <https://privacy.anthropic.com/en/articles/7996868-is-my-data-used-for-model-training>
- <https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data>

## Sensitive study material

Devmax is a study tool, not a medical or legal authority. Users should import only
material they are permitted to process and should avoid unnecessary personal,
patient, client, or confidential information. Devmax treats the user's source as
the answer basis and does not claim that an extracted topic is independently
verified professional guidance.
