# Phase 5 natural-language Planner Agent implementation status

Status date: 2026-08-03. Offline/replay implementation; no cloud or robot call
was performed.

## Implemented

- `GoalSpec` preserves the local original instruction and binds a stable logical
  object to exactly one candidate's provider object ID, tracker session, and
  grounding observation. A model cannot rewrite the original instruction or
  cite an object absent from the evidence bundle.
- A separate planner-facing semantic registry exposes observation, complete
  object listing, symbolic movement, ID selection, pick/place, verification,
  stop/resume, anomaly inspection, and re-observation. It contains no raw ROS,
  joint, velocity, service-management, safety-bypass, or operator capabilities.
- `PlannerAgent` captures an initial observation, obtains a structured grounded
  goal, proves the `LocationRef` is executable locally, asks the model for a
  complete arbitrary semantic `PlanGraph`, validates every action/schema and
  terminal success evidence, and submits it only through `ExecutionSupervisor`.
- `build_gemini_autonomy_runtime` is an explicit composition root for an
  approved semantic backend. It does not replace the current real ROS path as a
  side effect and opens no network/robot connection when constructed.
- Natural-language task/status endpoints are available only when an
  `AutonomyService` is explicitly injected into the Flask process.

## Offline evidence and remaining gates

Tests execute “把左边的红色箱子放到东北格” through replay decisions, ground
FoundationPose object ID `12`, preserve `grid/ne`, and run five semantic nodes
without a stage click. A model-selected ID absent from the observation is
rejected before a mission or physical invocation is created.

The existing real adapter does not yet claim the new semantic object-ID and
fresh-selection-epoch capability. A real Robotics-ER 2 cloud call, real
observation source, production semantic backend, Web authentication, simulation
run, and HIL remain required. This is not a Phase 5 exit declaration.
