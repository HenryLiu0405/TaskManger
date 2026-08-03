# Phase 4 cloud VLM gateway implementation status

Status date: 2026-08-03. No cloud request or camera upload was performed.

## Implemented

- `ModelGateway` accepts injected provider adapters, sends observation media as
  bytes, advertises the complete planner-visible semantic skill catalog, and
  includes an explicit boundary treating visible scene text as untrusted data.
- Structured decisions support goal, plan, tool call, re-observation,
  verification, replanning, recovery, finish, and abort. Draft 2020-12 schema
  validation, observation/world-version checks, and skill argument validation
  happen before a decision can reach an execution controller.
- Operator-only or non-planner skills are rejected even when the model knows
  their names.
- `ModelCallLedger` records provider/model, prompt version and hash,
  observation/world/plan linkage, exact frame hashes, structured response,
  usage, cost, latency timestamps, and errors. It stores neither raw image bytes
  nor prompt text.
- Concurrency limiting and a per-provider circuit breaker are present.
  Provider errors are not retried by the gateway. `ReplayProvider` supports
  deterministic offline decisions.
- `GeminiRoboticsER2Provider` now supports both the current Interactions API and
  the `generateContent` request shape used by the supplied quick start. It sends
  inline image bytes, requests structured JSON, converts native function calls
  into the local decision envelope, and keeps the API key only in the transport
  header. Google's documented standard endpoint,
  `gemini-robotics-er-2-preview`, is the default; the general
  `gemini-flash-latest` alias is not a Robotics-ER 2 identity. Interactions is
  the default API mode, while `generateContent` remains a compatibility mode.
- The HTTP transport is injected for tests. Importing or composing the provider
  performs no network call, and provider exceptions omit headers and bodies.

## Offline evidence and remaining gates

Ten tests cover two independently configured providers, byte media delivery,
complete agent-level tool vocabulary, trace/privacy fields, stale and malformed
output rejection, operator-only denial, timeout-style provider failures,
circuit opening, scene prompt-injection metadata, replay, both Gemini API modes,
structured schema projection, function-call normalization, credential redaction,
and pre-transport media-size rejection.

No real API request was made. Account-level Robotics-ER 2 availability,
credential rotation, retention policy, real provider timeout/cost accounting,
and a real multi-channel `ObservationBundle` upload remain deployment gates.
The roadmap also asks for two real configurable providers; only Gemini and the
offline replay/injected-provider boundary are implemented. Passing offline
transport tests does not satisfy that real-provider exit gate.
