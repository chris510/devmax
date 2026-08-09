import Image from "next/image";

const cards = [
  {
    topic: "Write-through caches",
    category: "CACHING",
    summary: "Eviction still applies when writes go straight through.",
    due: "due today · last seen 6d ago",
    score: "2",
    band: "shaky",
  },
  {
    topic: "Quorum reads",
    category: "CONSISTENCY",
    summary: "Why R + W > N is a choice, not a law.",
    due: "due today · last seen 11d ago",
    score: "1",
    band: "cold",
  },
  {
    topic: "Backpressure",
    category: "QUEUES",
    summary: "What a queue does when the consumer falls behind.",
    due: "due today · last seen 4d ago",
    score: "2",
    band: "shaky",
  },
];

function TodayProof({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`app-proof today-proof${compact ? " compact" : ""}`}>
      <div className="app-status"><span>6:42</span><span>● ●●</span></div>
      <div className="today-heading">
        <div>
          <h3>Today</h3>
          <p className="meta">MON 17 AUG · 3 CARDS DUE</p>
          <p className="distribution">2 shaky · 1 cold</p>
        </div>
        <span className="settings-pill">SETTINGS</span>
      </div>
      {!compact && <p className="plan-line">PLAN · WEEK 4 · TECHNOLOGIES · NEXT TUE 19:00 →</p>}
      <div className="card-list">
        {cards.slice(0, compact ? 2 : 3).map((card) => (
          <div className="due-card" key={card.topic}>
            <div>
              <div className="topic-line"><strong>{card.topic}</strong><span>{card.category}</span></div>
              {!compact && <p>{card.summary}</p>}
              <small>{card.due}</small>
            </div>
            <div className={`score ${card.band}`}><b>{card.score}</b><i /></div>
          </div>
        ))}
      </div>
      <button className="product-button" type="button">Start — 3 cards</button>
    </div>
  );
}

function QuestionProof({ dark = false }: { dark?: boolean }) {
  return (
    <div className={`app-proof question-proof${dark ? " dark-proof" : ""}`}>
      <div className="app-status"><span>6:42</span><span>×</span></div>
      <p className="meta">SYSTEM DESIGN · CACHING</p>
      <p className="question">Why does a write-through cache still need an eviction policy?</p>
      <div className="mic" aria-hidden="true"><span /></div>
      <p className="mic-label">TAP TO SPEAK</p>
      <p className="text-instead">or type instead</p>
    </div>
  );
}

function PlanProof() {
  return (
    <div className="app-proof plan-proof">
      <p className="meta">STUDY PLAN · WEEK 4 OF 12</p>
      {[
        ["1 · Foundations", "Complete"],
        ["2 · Technologies", "Current"],
        ["3 · Design practice", "Next"],
        ["4 · Integration", "Later"],
      ].map(([phase, status]) => (
        <div className={status === "Current" ? "phase current" : "phase"} key={phase}>
          <strong>{phase}</strong><span>{status}</span>
        </div>
      ))}
    </div>
  );
}

function ScoreProof() {
  return (
    <div className="score-proof">
      <div className="score-proof-top">
        <span>RECALL SIGNAL</span>
        <strong>2<i /></strong>
      </div>
      <p>You had the cache write path right. You missed why capacity pressure still forces eviction.</p>
      <div className="score-proof-axis"><span>MECHANISM</span><b>2</b></div>
      <div className="score-proof-axis"><span>TRADE-OFFS</span><b>3</b></div>
      <div className="score-proof-axis"><span>FAILURE MODES</span><b>1</b></div>
      <small>NEXT REVIEW · TOMORROW</small>
    </div>
  );
}

export default function Home() {
  return (
    <main>
      <div className="opening" id="top">
        <header className="site-header">
          <a className="brand" href="#top" aria-label="Unprompted home">
            <Image src="/unprompted-icon.png" alt="" width={36} height={36} priority />
            <span>Unprompted</span>
          </a>
          <nav aria-label="Primary navigation">
            <a href="#truth">Why recall</a>
            <a href="#plan">Study plan</a>
            <a href="#appearance">Appearance</a>
          </nav>
          <a className="nav-cta" href="#private-beta">Private beta <span>↘</span></a>
        </header>

        <section className="hero section-shell">
          <div className="hero-copy">
            <p className="eyebrow light">PRIVATE · VOICE-FIRST · RETRIEVAL PRACTICE</p>
            <h1><span>Remember</span><em>the hard parts.</em></h1>
            <p className="lede">Unprompted asks one sharp question, listens to the answer you actually give, then brings it back when memory needs the work.</p>
            <div className="hero-actions">
              <a className="button" href="#private-beta">Join the private beta</a>
              <a className="text-link" href="#truth">See the signal ↓</a>
            </div>
          </div>

          <div className="hero-stage" aria-label="Unprompted product preview">
            <div className="hero-question"><QuestionProof dark /></div>
            <div className="hero-today"><TodayProof compact /></div>
            <div className="time-stamp"><strong>1–3</strong><span>MINUTES<br />PER SESSION</span></div>
            <p className="stage-note">ONE QUESTION<br />NO WARM-UP</p>
          </div>
        </section>

        <div className="signal-rail" aria-label="Unprompted review loop">
          <span>ASK</span><i>→</i><span>LISTEN</span><i>→</i><span>CLARIFY ONCE</span><i>→</i><span>SCORE</span><i>→</i><span>RESCHEDULE</span>
        </div>
      </div>

      <section className="truth section-shell" id="truth">
        <div className="truth-statement">
          <p className="eyebrow">THE UNCOMFORTABLE BIT</p>
          <h2>Recognition feels fluent.<br /><em>Recall tells the truth.</em></h2>
        </div>
        <div className="truth-grid">
          <div className="truth-copy">
            <p>Re-reading a page can feel like knowing it. Real recall asks for the idea before the material is open.</p>
            <p>Unprompted measures that moment without turning it into a game. One score, one missing piece, one next review.</p>
            <div className="quiet-list">
              <span>NO STREAKS</span><span>NO XP</span><span>NO CELEBRATION THEATER</span>
            </div>
          </div>
          <ScoreProof />
        </div>
      </section>

      <section className="loop" id="how">
        <div className="section-shell">
          <div className="loop-heading">
            <p className="eyebrow light">THE WHOLE DAILY LOOP</p>
            <h2>Two honest minutes.<br />Then get on with your day.</h2>
          </div>
          <div className="loop-cards">
            <article className="loop-card voice-card"><span>01 / ANSWER</span><strong>Say it out loud.</strong><p>Voice is closer to the room. Text is always equal.</p><div className="voice-mark"><i /><i /><i /><i /><i /></div></article>
            <article className="loop-card probe-card"><span>02 / CLARIFY</span><strong>One probe. Max.</strong><p>If the explanation is shaky, Unprompted asks once—then stops.</p><div className="probe-line">WHAT BREAKS FIRST? <b>↗</b></div></article>
            <article className="loop-card schedule-card"><span>03 / SCHEDULE</span><strong>Bring it back later.</strong><p>Weak mechanism means sooner. Strong recall earns distance.</p><div className="schedule-track"><i /><i /><i /><i /></div></article>
          </div>
        </div>
      </section>

      <section className="plan-section section-shell" id="plan">
        <div className="plan-copy">
          <p className="eyebrow">STUDY PLAN</p>
          <h2>A map you can read<br /><em>half-awake.</em></h2>
          <p>Drop in a curriculum. Unprompted turns it into a weekly route: where you are, what is next, and what deserves retrieval later.</p>
          <div className="plan-key">
            <div><b>01</b><span>LEARN</span><p>Source material</p></div>
            <div><b>02</b><span>PRACTICE</span><p>Produce an answer</p></div>
            <div><b>03</b><span>RETRIEVE</span><p>Recall it later</p></div>
          </div>
        </div>
        <div className="plan-board">
          <div className="plan-board-head">
            <div><p>Senior backend interview</p><strong>Week 4 of 12 · Flexible</strong></div>
            <span>EST. COMPLETION<br />WEEK OF 19 OCT</span>
          </div>
          <div className="phase-map"><PlanProof /></div>
          <div className="week-slice">
            <p className="meta">THIS WEEK · 11H OF 12H PLANNED</p>
            <div><span>LEARN</span><strong>Relational and NoSQL systems</strong><small>240 min</small></div>
            <div><span>PRACTICE</span><strong>Redraw the gateway request flow</strong><small>90 min</small></div>
            <div><span>RETRIEVE</span><strong>Write-through caches</strong><small>2 of 4 done</small></div>
          </div>
        </div>
      </section>

      <section className="life section-shell">
        <div className="life-heading"><p className="eyebrow">BUILT FOR THE GAPS</p><h2>Small enough to start.<br />Serious enough to matter.</h2></div>
        <div className="life-grid">
          <article className="life-time"><strong>1–3</strong><span>MINUTES</span><p>In a queue. Before standup. Half-awake with coffee.</p></article>
          <article className="life-voice"><span>VOICE FIRST</span><div className="wave"><i /><i /><i /><i /><i /><i /><i /></div><p>Practice the form you will actually use.</p></article>
          <article className="life-safe"><span>YOUR WORDS STAY PUT</span><p>If the network drops, the answer is already saved.</p><b>LOCAL → DURABLE</b></article>
          <article className="life-private"><span>PRIVATE BY DESIGN</span><p>One user. No audience. No social pressure dressed up as motivation.</p></article>
        </div>
      </section>

      <section className="appearance" id="appearance">
        <div className="section-shell appearance-grid">
          <div className="appearance-copy">
            <p className="eyebrow light">SAME SIGNAL · YOUR LIGHT</p>
            <h2>Bright desk.<br />Dark room.<br /><em>Same honesty.</em></h2>
            <p>Choose light, dark, or follow the phone. The hierarchy stays put and color never carries the score alone.</p>
          </div>
          <div className="theme-stage">
            <div className="theme-light"><span>LIGHT</span><QuestionProof /></div>
            <div className="theme-dark"><span>DARK</span><QuestionProof dark /></div>
          </div>
        </div>
      </section>

      <section className="principles section-shell">
        <p className="eyebrow">WHAT UNPROMPTED OPTIMIZES FOR</p>
        <div className="principle-lines">
          <p><span>01</span>Recall, <em>not recognition.</em></p>
          <p><span>02</span>Signal, <em>not engagement.</em></p>
          <p><span>03</span>Progress, <em>without theater.</em></p>
        </div>
      </section>

      <section className="final-cta" id="private-beta">
        <div className="section-shell">
          <p className="eyebrow">PRIVATE BETA · INVITATION ONLY</p>
          <h2>Know what you know.<br /><em>Find what you don’t.</em></h2>
          <p>Unprompted is preparing for a small first release.</p>
          <a className="button dark-button" href="#top">Join the private beta <span>↗</span></a>
        </div>
      </section>

      <footer>
        <div className="brand"><Image src="/unprompted-icon.png" alt="" width={30} height={30} /><span>Unprompted</span></div>
        <p>Private voice-first recall coach</p>
        <div><a href="#private-beta">Privacy</a><a href="#private-beta">Contact</a></div>
      </footer>
    </main>
  );
}
