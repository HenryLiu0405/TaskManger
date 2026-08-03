# Phase 0 compatibility boundary

## Must remain compatible in Phase 1

- Existing HTTP URLs and request bodies.
- Home RUN loads a plan and performs zero robot actions.
- StepDebug state names and the existing `ok`/`message` response convention.
- Legacy action results projected as `status=ok|error`, `error_code`,
  `message`, `state`, and `metrics`.
- Existing SSE endpoints and event envelope.
- Legacy PlanStep JSON with `action/current`, flat place coordinates, and
  correlation IDs embedded in `args` remains readable through the compatibility
  converter.
- Debug target locking remains available as an operator preview.

## Defects that must be fixed, not preserved

- Direct production `adapter.execute()` bypasses.
- Multiple schemas for the same skill.
- Unknown action fields reaching an adapter.
- Concurrent mutating actions on one robot.
- Automatic retry of a physical action.
- Treating a transport timeout as a confirmed action failure.
- Treating a cancel request as confirmed robot stop.
- Releasing the execution lock while a timed-out call is still running.
- ROS query placeholders that return “skipped” as success.
- Wall-clock suffixes in SubmitCarryTask request IDs.
- Stale or wrong-object FoundationPose samples and frame relabeling.
- Legacy acceptance reports that exit zero on failed scenarios.

## Explicitly outside Phase 0/1

Natural-language planning, VLM gating, autonomous drop recovery, automatic
replanning, persistent queues, cross-process recovery, full physical cancel,
and direct execution from Home RUN remain disabled or unchanged for a later
Execution Supervisor phase.

