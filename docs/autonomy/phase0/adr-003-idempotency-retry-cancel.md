# ADR-003: Retry, idempotency, timeout, and cancellation

Status: accepted for Phase 1.

## Decision

- One robot has one writer lock shared by all mutating sources. Phase 1 does not
  queue; a conflicting new call receives `RESOURCE_BUSY` before backend dispatch.
- Only explicitly `concurrency_safe` read-only skills bypass that writer lock.
- A bounded in-process table maps an idempotency key to one invocation. Same
  key and same effective arguments attach to it; different arguments receive
  `IDEMPOTENCY_CONFLICT`.
- Physical skills have no implicit retry. Legacy RobotToolClient retry settings
  are retained only as inert compatibility fields.
- The idempotency key supplied to a pick invocation is the SubmitCarryTask
  request ID. It never receives a timestamp suffix.
- Mission keys include a persisted execution epoch. They remain stable for one
  logical run; an explicit terminal-state reset increments the epoch and clears
  prior PlanStep invocation/result fields before a new physical run.
- StepDebug keys remain stable within one loaded plan. A UI retry attaches to
  the existing invocation; loading a new plan or explicitly rewinding after
  reconciliation starts a new plan execution epoch.
- A timeout after dispatch returns `unknown`; the worker and writer lock remain
  draining until the backend returns. The dispatcher never uses an async thread
  timeout as permission to redispatch.
- A pre-cancelled request is terminal before a backend call. Runtime cancellation
  is a request only. An uninterruptible pick/place prevents successors while it
  drains. Navigation is `cancelled` only after a future backend can positively
  confirm stop; Phase 1 otherwise reports requested/unknown.

## Consequences

An operator may need reconciliation before proceeding after a response loss.
This is deliberate: avoiding duplicate physical motion takes precedence over
availability. Persistent idempotency and cross-restart recovery are deferred.
