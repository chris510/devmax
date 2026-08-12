import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy — Unprompted",
  description: "How Unprompted collects, uses, shares, retains, exports, and deletes study data.",
};

export default function Privacy() {
  return (
    <main className="policy-page">
      <header className="policy-header">
        <Link className="brand" href="/" aria-label="Unprompted home">
          <Image src="/unprompted-icon.png" alt="" width={36} height={36} priority />
          <span>Unprompted</span>
        </Link>
        <Link className="policy-back" href="/">← Product</Link>
      </header>

      <article className="policy-shell">
        <p className="eyebrow">PRIVACY POLICY · EFFECTIVE 12 AUGUST 2026</p>
        <h1>Study privately.<br /><em>Know where the text goes.</em></h1>
        <p className="policy-intro">
          Unprompted is a voice-first study and spaced-retrieval app operated by
          One Percent Studios LLC. This policy explains the data used to run the
          app, including when text is sent to Anthropic for AI processing.
        </p>

        <PolicySection title="What Unprompted collects">
          <ul>
            <li>Apple&apos;s app-specific account identifier, and the name or email Apple shares when available.</li>
            <li>Study guides, source excerpts, topics, answer bases, rubrics, Study Plans, notes, and completion state.</li>
            <li>Text answer transcripts, questions, scores, coaching feedback, review history, and spaced-repetition schedules.</li>
            <li>Review preferences, notification windows, device push tokens, authentication sessions, consent choices, and model-usage counts.</li>
            <li>Basic request metadata such as IP address, route, and time may appear in hosting and security logs.</li>
          </ul>
          <p>Unprompted does not upload or store voice recordings. iOS converts speech into a text transcript; the app sends text.</p>
        </PolicySection>

        <PolicySection title="Why the data is used">
          <p>Account and device data keeps the correct library private, supports Sign in with Apple, and delivers review notifications. Study data creates the plan and review experience, preserves drafts, scores answers, provides coaching, and schedules later recall. Limited model-usage records enforce cost and abuse safeguards.</p>
        </PolicySection>

        <PolicySection title="Anthropic AI processing">
          <p>AI processing is optional and requires an explicit in-app choice. Unprompted uses the Anthropic API for four purposes:</p>
          <ol>
            <li>proposing source-grounded topics and Study Plan structure from a guide;</li>
            <li>generating a stable review question when a card needs one;</li>
            <li>scoring a submitted answer transcript against the relevant answer basis and rubric; and</li>
            <li>providing an optional coaching response.</li>
          </ol>
          <p>For guide processing, Unprompted sends the guide text, title, plan length, weekly capacity, mode, deadline, and subject hints. For review, it sends the topic, question, transcript, any follow-up, the card&apos;s mastery summary, trusted answer basis, source excerpt, and rubric. It does not send the Apple sign-in credential or an audio recording to Anthropic.</p>
          <p>Anthropic states that standard commercial API inputs and outputs are not used to train its models by default and are normally deleted from its systems within 30 days, subject to its published exceptions for safety, legal obligations, explicit opt-in, or a different agreement.</p>
          <p><a href="https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data">Anthropic API retention ↗</a> · <a href="https://privacy.anthropic.com/en/articles/7996868-is-my-data-used-for-model-training">Anthropic model-training policy ↗</a></p>
        </PolicySection>

        <PolicySection title="Your choice and control">
          <p>You may decline AI processing and continue using saved lessons, Study Plans, history, and settings. Guide processing, question generation, scoring, and AI coaching remain unavailable. You may later allow or withdraw permission under Data &amp; privacy in Settings. Withdrawal prevents future submissions; it does not delete existing study data or change review schedules.</p>
          <p>You can export account data, remove individual imported sources, sign out locally, or permanently delete the account in the app. Account deletion revokes Sign in with Apple authorization before removing active account data.</p>
          <p>If Apple reports that the app&apos;s authorization was revoked or the Apple Account was deleted, Unprompted revokes active app sessions and requires sign-in again. It preserves study data until you explicitly use Delete account.</p>
        </PolicySection>

        <PolicySection title="Service providers and disclosure">
          <p>Unprompted uses Apple for authentication, speech recognition, and push delivery; Railway for application and database hosting; Anthropic for the optional text processing described above; and OpenAI Sites/Cloudflare to host this public website. Data is not sold and is not used for cross-app tracking or advertising.</p>
          <p>Data may also be disclosed when legally required, to protect the service or its users, or as part of a business transfer subject to appropriate safeguards.</p>
        </PolicySection>

        <PolicySection title="Retention and deletion">
          <p>Account and study data is retained while the account exists, unless you remove a source sooner. Authentication nonces expire quickly; access and refresh sessions expire or are revoked. Model-usage records retain operation and time, not the submitted guide or answer text.</p>
          <p>Deleting the account removes its active database records, including study material, cards, transcripts, scores, schedules, plans, settings, device tokens, and consent history. Provider-managed backups and operational logs, if present, age out under the configured hosting retention cycle and may be retained longer when required for security or law. Unprompted does not promise a shorter backup deadline than its deployed systems enforce.</p>
        </PolicySection>

        <PolicySection title="Security, children, and changes">
          <p>Unprompted uses TLS in transit, encrypted Apple refresh credentials at rest, short-lived access credentials, rotating refresh credentials, and per-account data isolation. No service can guarantee absolute security.</p>
          <p>The app is not directed to children under 13. If this policy or the data sent to an AI provider materially changes, Unprompted will present a new versioned disclosure and ask again before further AI processing.</p>
        </PolicySection>

        <PolicySection title="Questions or privacy requests">
          <p>Use the support contact shown on Unprompted&apos;s App Store listing. Account export and deletion are also available directly in the app under Settings → Data &amp; privacy.</p>
        </PolicySection>
      </article>
    </main>
  );
}

function PolicySection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="policy-section">
      <h2>{title}</h2>
      <div>{children}</div>
    </section>
  );
}
