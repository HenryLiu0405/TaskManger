# Phase 2 execution-supervisor implementation status

Status date: 2026-08-03. This document records offline implementation evidence,
not HIL approval or completion of the entire phase.

## Implemented

- `phi_robot.autonomy.RobotRuntime` is the composition root for one robot and
  owns one dispatcher, `ExecutionSupervisor`, SQLite ledger, World State, and
  independent interrupt lane.
- Versioned `PlanGraph`, `PlanRevision`, `PlanNode`, and separate dispatch,
  operation, and caller-wait timeout contracts are available.
- The SQLite ledger persists missions, revisions, invocations, structured
  results, events, interrupts, and World State. An invocation is durably marked
  dispatched before calling the backend.
- Restart recovery converts prepared/dispatched/unknown actions to
  `reconciliation_required`; it never re-dispatches them. A resolution requires
  a trusted local principal and explicit physical evidence.
- The supervisor continuously executes a fixed plan in a background thread.
  The REST mission path and `MissionApiService` use this boundary; Web refresh
  or disconnect is not the execution clock.
- The deployed composition root gives legacy developer actions and autonomous
  semantic actions the same dispatcher and robot ID, so they share one normal
  physical-writer lock instead of creating parallel execution paths.
- The interrupt lane freezes normal execution before requesting stop and does
  not acquire the dispatcher writer lock. Unconfirmed stop enters
  `intervention_required`. A result arriving after the freeze is recorded but
  cannot overwrite the authoritative post-stop World State.
- World State has independent mission, motion, payload, perception, control,
  and execution dimensions. Every fact includes source/time/evidence, and
  unknown values serialize as `null` rather than a plausible default.
- `GET /api/missions/:id/timeline` exposes the durable mission, revision,
  invocation, interrupt, event, and World State snapshot. Existing mission HTTP
  status is maintained through a compatibility projection.

## Offline evidence

`tests/offline/test_execution_supervisor.py` covers unattended execution,
durable evidence, restart fail-closed behavior, trusted reconciliation, one
runtime per robot, required verification, immutable dispatched nodes, a blocked
normal writer plus independent confirmed stop, unconfirmed stop, and the
post-interrupt late-result race.

The complete offline suite passed 127/127 and the frontend state suite passed
6/6 in the project Python 3.10 environment on 2026-08-03.

## Remaining Phase 2 gates

- The ROS adapter now maps the existing `/nav_pause` and `/odom` interfaces to
  timestamped stop evidence and deliberately reports an unconfirmed stop when
  that evidence is unavailable. Its thresholds and physical behavior still
  require approval in HIL before the autonomous path is enabled.
- The Web timeline API exists, but the production UI still needs the full
  timeline/intervention presentation.
- Cross-process single-writer deployment ownership and service restart behavior
  require target-host validation.
- No real ROS default path was changed, and no ROS, hardware, service, camera,
  or network action was run.
