# AI systems foundations for the senior-backend plan

This is the AI application-systems layer of the twelve-week curriculum. It is
not a second parallel syllabus. Distributed systems remains the spine; the AI
work applies the same ideas—state, authority, queues, retries, observability,
capacity, failure isolation, and evaluation—to a probabilistic hosted
dependency.

The target is a senior backend or backend-leaning platform/product engineer at
an AI company. Model research, distributed training, GPU kernels, quantization
implementation, and inference-runtime engineering remain outside the core plan.

## What “the architecture already being built” means

Devmax is already an application-side reliability layer around hosted
inference:

- durable user, card, session, draft, and Study Plan state lives in Postgres;
- prompts assemble trusted answer authority and current request context;
- model output is parsed and validated before application code commits effects;
- a complete answer is one transaction, while retries and idempotency protect
  interrupted work;
- consent, ownership, scheduling, and authorization remain code-owned;
- scoring calibration uses reviewed cases rather than assuming fluent output is
  correct;
- long imports and provider calls have explicit timeout, recovery, and
  observability concerns.

That makes Devmax a good laboratory for inference, evals, state boundaries,
durable model workflows, gateways, and retrieval. It does **not** make Devmax an
inference platform: it does not schedule GPUs, implement continuous batching,
manage model weights, or optimize kernels.

## Fixed budget

Version 5 changes content, not capacity. The plan remains 116 scheduled items,
12 weeks, and 1,200 minutes per week.

| Study Plan item | Week | Reallocated AI systems time |
|---|---:|---:|
| `V4-W1-L3` · hosted inference lifecycle and retry semantics | 1 | 90m |
| `V4-W2-L1` · model, context, application state, and authority | 2 | 90m |
| `V4-W4-L3` · bounded workers and a measured eval experiment | 4 | 60m |
| `V4-W5-L2` · recoverable workflows and bounded tool authority | 5 | 120m |
| `V4-W6-L3` · durable long-running AI jobs | 6 | 60m |
| `V4-W7-L5` · trusted model routing and semantic fallback gates | 7 | 60m |
| `V4-W8-L2` · permission-filtered ingestion and hybrid retrieval | 8 | 90m |
| `V4-W9-L5` · vector tuning and end-to-end RAG evaluation | 9 | 90m |
| **Newly reallocated** |  | **660m / 11h** |

The existing Week 9 ChatGPT walkthrough (120m) and Week 11 ChatGPT mock
(180m) add five hours, for **16 explicit AI hours inside the same 240-hour
plan**. Coding, behavioral work, conventional system designs, and the unseen
readiness mocks are unchanged.

## Learning sequence and external evidence

The source links and exact completion conditions live on each Study Plan item.
The artifact is completed outside the recall loop.

1. **Hosted inference boundary.** Draw a request from application context through
   network submission, provider tokenization and queueing, prefill,
   autoregressive decode, streaming, timeout, and retry. Treat provider-internal
   queue placement as implementation-dependent. Explain why inference does not
   update model parameters.
2. **State and authority.** Produce a table separating model weights, transient
   request context, durable application memory, and code-owned policy. Apply it
   to a refund or scoring side effect.
3. **Measured reliability.** Inspect the twelve-case draft pack, run four
   representative labelled cases, repeat one failure-sensitive case three
   times, and report schema validity separately from semantic success and
   failure classes. The pack is `api/evals/ai-foundations-v1.json`.
4. **Workflow versus agent.** Keep durable progress, authorization, budgets,
   stopping rules, and irreversible effects in code. Give a model only bounded,
   least-privilege decisions or tools, then inject provider and semantic
   failures.
5. **Durable AI jobs.** Design accepted/running/failed/canceled/completed state,
   idempotent submission, polling, cancellation, result retention, and outbox or
   webhook publication.
6. **Model gateway.** Route by trusted identity, consent, quota, prompt version,
   and policy. A provider fallback must satisfy both a versioned response
   contract and the product eval gate; compatible JSON alone is insufficient.
7. **Retrieval ingestion.** Use the ten-chunk, nine-document versioned corpus in
   `api/evals/ai-retrieval-lab-v1.json`, preserve its provenance and ACL
   metadata, compare lexical with semantic or hybrid retrieval on three
   labelled queries, and show where authorization filtering occurs.
8. **End-to-end RAG.** Tune one HNSW parameter, measure recall@k on the eight
   positive-qrel lab queries, report forbidden-chunk leakage and expected-empty
   correctness on the two authorization-denial queries, and report exploratory
   median and maximum latency plus index cost. Then classify answer failures as
   retrieval, generation, citation, or access-control failures.

## What belongs in Devmax

The four compact reconstruction prompts live in
`api/modules/ai-foundations.json`:

1. training versus hosted inference;
2. the inference request lifecycle and latency boundaries;
3. context windows versus explicit durable conversation state;
4. evaluation-driven reliability.

They intentionally remain `draft_review`. A model or coding agent cannot label
its own proposed authority as human-approved. After review, activate only the
cohort whose mapped Study Plan item is complete. Opening the card-owned Learn
endpoint—not completing the mapped Study Plan item—records the exposure
boundary; scored recall waits until the later of eight hours and the next local
day. Add later cards only from a concrete observed gap with trusted answer
authority.

Structured output is taught throughout, but it is not a fifth foundation card.
It constrains shape; it does not prove truth, permission, task success, or safe
side effects.

## Primary self-study resources

- [Google ML Crash Course: fine-tuning, distillation, and prompt engineering](https://developers.google.com/machine-learning/crash-course/llm/tuning)
  distinguishes parameter updates from request-time prompting.
- [Google Cloud: inference optimization](https://cloud.google.com/discover/inference-optimization)
  introduces prefill and decode; [NVIDIA NIM metrics](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html)
  separates time to first token, end-to-end latency, and inter-token latency.
- [NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
  covers confabulation, prompt injection, oversight, and decision criteria;
  [OpenAI conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
  covers explicit context and application- or provider-managed conversation
  state.
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
  shows why schema handling still needs evals and edge-case handling.
- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
  covers realistic tasks, grader choice, human calibration, repeated trials,
  regression suites, and production monitoring.
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
  distinguishes simple workflows from bounded agentic loops.
- [OpenAI retrieval guide](https://developers.openai.com/api/docs/guides/retrieval)
  covers semantic search, vector stores, chunking, indexing, and metadata
  filtering.
- `api/evals/ai-retrieval-lab-v1.json` supplies the synthetic versioned corpus,
  ACL fixtures, and qrels used by the Week 8 and Week 9 experiments.

Provider documentation supplies examples, not product authority. Keep the
concepts vendor-neutral and record exact provider/model/prompt/harness versions
whenever measuring behavior.

## Readiness evidence

Recall scores diagnose gaps; they do not certify AI-systems readiness. Claim the
skill only when all four artifacts exist:

- a complete application-side AI system design with state and authority
  boundaries;
- a measured eval report with repeated trials and explicit regression gates;
- an architecture review that defends workflow-versus-agent and provider
  fallback decisions;
- an unseen failure drill covering timeout ambiguity, invalid shape, semantic
  error, unauthorized retrieval or tool use, and duplicate delivery.

## Operator approval and activation

Before changing any foundation entry from `draft_review` to `approved`:

1. open every cited section and check each answer-basis and rubric sentence;
2. speak every strong eval answer and confirm it fits in under two minutes;
3. reconcile all twelve qualitative eval labels;
4. confirm the manifest topic maps to the intended Study Plan item; if that row
   was completed before this content version, require a fresh replacement
   activity rather than crediting old work;
5. run the seed and preservation tests;
6. activate one target-week cohort only after its mapped lesson is complete.

The older `api/modules/ai-application.json` topics are advanced, ungrounded
application ideas. They are neither substitutes for these foundations nor safe
to seed.
