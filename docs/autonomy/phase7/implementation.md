# Phase 7 transport-drop recovery implementation status

Status date: 2026-08-03. Offline deterministic implementation; no cloud, ROS,
camera, or hardware action was performed.

## Implemented

- `DropRecoveryCoordinator` arms recovery only while payload is known holding
  and an active transport node is moving.
- A drop edge is durably recorded and immediately invokes the Supervisor's
  independent local stop lane before any observation or model call. Unconfirmed
  stop enters intervention and never calls the cloud or dispatches recovery.
- After confirmed stop, the old action must drain to a known terminal state.
  Its invocation is retained and never retried/resumed. Recovery cannot start
  while a previous physical result is unresolved.
- Gemini/replay diagnosis classifies confirmed drop, false alarm, camera
  failure, or uncertainty and can spend a bounded re-observation budget.
- Confirmed-drop plans must reidentify the same logical object in fresh
  candidates, record explicit lineage when perception ID/session changes,
  select it, re-pick, verify holding, and create a new navigation node to the
  original `LocationRef`. Fixed sleeps and unconditional step-back are not
  built into the coordinator.
- Recovery is a separate `RevisionKind.RECOVERY` graph with its own event/state
  timeline and bounded evidence-based re-pick attempts. One mission has at most
  one active recovery event.

## Offline evidence and remaining gates

The deterministic scenario blocks the old transport action, injects a drop,
confirms local zero-velocity stop, changes FoundationPose ID/session, creates
lineage, selects/re-picks/verifies, invokes a distinct new navigation node, and
completes the original `grid/ne` destination. A missing stop acknowledgement
produces intervention with zero model/recovery calls.

False alarm, camera failure, cloud timeout, multiple boxes, unreachable object,
process restart, repeated first-repick failure, simulation, and every real-drop
HIL scenario still need broader recorded/live evidence. This is not a Phase 7
exit declaration.
