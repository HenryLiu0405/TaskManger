# TaskManger Agentic Robot Roadmap

## 1. Purpose and authority

This file is the repository-level operating guide for Codex and other coding
agents working in this repository. It applies to the entire repository unless a
more deeply nested `AGENTS.md` provides narrower instructions for a subdirectory.

The product goal is to evolve the current manually stepped robot workflow into
an autonomous, visually grounded robot agent. A user should be able to provide
one natural-language instruction; a cloud VLM planner should observe the scene,
choose the target object, construct and revise a plan, invoke semantic robot
skills, verify results, and recover from a box drop during transport without the
operator clicking through every action stage in the Web App.

User instructions in the current task take precedence over this roadmap. This
roadmap describes intended direction; it is not evidence that a phase has been
completed. Before making changes, inspect the current code, documentation,
tests, Git state, and any more recent user decisions.

## 2. Current repository status

Status recorded on 2026-08-03:

- Phase 0 and Phase 1 have substantial user-authored staged changes in the
  working tree. Treat all existing changes as user-owned. Do not reset, unstage,
  discard, overwrite, amend, or commit them unless the user explicitly asks.
- Phase 0 has an offline architecture/interface baseline and review materials.
  Hardware/interface freeze and full HIL acceptance are still separate gates.
- Phase 1 has a versioned skill catalog, dispatcher, adapters, compatibility
  paths, and expanded offline tests. In the project's Python 3.10 environment,
  the offline suite was observed passing 82/82 tests.
- The real ROS path must remain on its currently approved compatibility mode
  until the relevant HIL gate is explicitly approved and passed. Do not switch
  a real robot to a new execution path merely because offline tests pass.
- Phase 2 through Phase 8 are roadmap work. Inspect the repository before
  assuming that none of their components have since been implemented.

The preferred project runtime is Python 3.10 or newer as declared by the project
environment files. On the original development machine, the `AgenticRobot`
Conda environment satisfies this requirement. Do not treat failures caused only
by an older system Python as project regressions.

## 3. Decisions already made by the user

The following are product decisions, not open design questions:

1. Execute the complete Phase 0 through Phase 8 roadmap.
2. Real robot camera observations are allowed and expected to be sent to a
   configured cloud VLM service.
3. The VLM is a full planner/decision agent from the initial VLM integration. It
   must not be reduced to a visual pass/fail gate, fixed action template, or
   purely advisory component.
4. The VLM may inspect the scene, choose an object by perception `object_id`,
   construct and revise plans, invoke all agent-level semantic skills, request
   new observations, and initiate recovery behavior without per-step approval.
5. FoundationPose or the active perception provider must support selecting the
   intended target by `object_id` rather than relying only on an approximate
   stock coordinate.
6. Executable destinations are temporarily restricted to the nine-grid scene,
   but all new task, planner, mission, and skill interfaces must use a general
   `LocationRef`. Do not permanently encode the nine-grid into the architecture.
7. The primary recovery scenario is a box dropping while the robot is carrying
   it during navigation. Treat this as a first-class mission event.
8. The Web App should become a monitoring, debugging, intervention, and replay
   surface. It must not remain the mechanism that advances every normal stage.

“VLM unrestricted” means unrestricted semantic observation, planning, tool
selection, replanning, and recovery at the agent layer. It does not mean that a
cloud model publishes arbitrary ROS topics, sends raw joint/velocity commands,
disables the emergency stop, invokes a safety bypass, or impersonates operator
authority. Semantic actions still pass through the local execution runtime and
the robot's non-negotiable physical safety invariants. This is an architectural
boundary, not a restriction to a fixed task sequence.

## 4. Target architecture

The target control flow is:

```text
Natural-language instruction
  -> Cloud VLM Planner Agent
  -> GoalSpec
  -> versioned PlanGraph / PlanRevision
  -> local Execution Supervisor
  -> semantic Skill Registry and Dispatcher
  -> ROS / Gateway / FoundationPose adapters
  -> robot
  -> action results, observations, and events
  -> World State + Observation Hub
  -> Cloud VLM Planner Agent
```

The architecture has five clear responsibility boundaries:

### 4.1 Cloud VLM Planner Agent

Responsible for:

- Natural-language goal understanding.
- Visual scene understanding and object grounding.
- Selecting an `ObjectRef` and `LocationRef`.
- Generating a complete plan rather than advancing a hard-coded sequence.
- Choosing the next semantic skill.
- Requesting additional images or viewpoints.
- Comparing action results with visual evidence.
- Revising the unexecuted plan tail.
- Diagnosing anomalies and proposing/performing recovery.
- Determining when a goal is complete, impossible, or requires intervention.

### 4.2 Execution Supervisor

Responsible for:

- Being the sole normal entry point for physical agent actions.
- Running plans continuously without Web stage clicks.
- Owning the authoritative active mission and invocation state.
- Enforcing per-robot single-writer execution.
- Maintaining a durable mission, plan revision, action, and event ledger.
- Applying state preconditions and validating structured outputs.
- Managing operation deadlines, cancellation, interruption, and reconciliation.
- Freezing normal execution when a safety-relevant event occurs.
- Providing a high-priority interrupt lane that is not blocked by a long normal
  action.
- Preventing an unknown physical action from being blindly retried.

### 4.3 Semantic Skill layer

Planner-facing skills express task intent, for example:

- `observe_scene`
- `list_objects`
- `move_to_location`
- `select_object`
- `pick_object`
- `place_at_location`
- `verify_holding`
- `verify_placement`
- `stop_motion`
- `resume_motion`
- `inspect_anomaly`
- `request_reobservation`

Internal primitives may wrap navigation start/status/cancel, target selection,
fresh-pose waiting, carry submission, lay-down, posture control, odometry, and
camera access. Internal primitives are implementation details and must not leak
into high-level plan semantics.

### 4.4 Robot and perception adapters

Responsible for translating stable skill contracts into the concrete ROS,
Gateway, FoundationPose, camera, simulation, or future provider interfaces.
Adapters execute; they do not decide mission strategy.

### 4.5 World State and Observation Hub

Responsible for combining timestamped robot state, perception objects, images,
active actions, plan state, and events into versioned observations. Unknown data
must be represented as unknown, not as fabricated zero/default values.

## 5. Core data contracts

These contracts should be versioned, serializable, persisted where necessary,
and shared across Planner, Mission, Supervisor, Skill, Web, and replay tooling.

### 5.1 `LocationRef`

The planner and mission store symbolic references, not trusted raw coordinates.

Current nine-grid example:

```json
{
  "schema_version": "1.0",
  "kind": "named",
  "namespace": "grid",
  "location_id": "ne",
  "scene_id": "scene-001",
  "scene_version": "v1"
}
```

Initial executable grid IDs are:

```text
grid/nw  grid/n  grid/ne
grid/w   grid/c  grid/e
grid/sw  grid/s  grid/se
```

The schema should be extensible to `named`, `pose_ref`, `region`, and
`object_relative`, but the initial resolver may reject non-nine-grid destination
kinds. A separate `ResolvedLocation` contains the numeric pose, frame, map and
resolver versions, provenance, resolution ID, and validity information. Do not
place resolved coordinates directly into the durable semantic goal as if they
were the user's location identity.

### 5.2 `ObjectRef`

Do not overload one `object_id` with both business identity and tracker identity.

```json
{
  "logical_object_id": "task-box-00",
  "category": "box",
  "attributes": {"color": "red"},
  "perception_ref": {
    "provider": "foundationpose",
    "object_id": 12,
    "tracker_session_id": "fp-session-23",
    "observation_id": "obs-456"
  }
}
```

- `logical_object_id` remains stable for the mission.
- Perception `object_id` identifies the current provider candidate.
- `tracker_session_id` prevents ID reuse across provider restarts.
- `observation_id` binds the choice to the evidence shown to the VLM.
- A new perception ID after a drop may be linked to the same logical object by
  an explicit lineage/re-identification record.

Target selection should return a selection token/epoch. A pick must prove that
the pose and submitted target belong to the same selected object and fresh
selection epoch.

### 5.3 `ObservationBundle`

An atomic observation can include:

```json
{
  "observation_id": "obs-123",
  "world_version": 42,
  "captured_at": "...",
  "valid_until": "...",
  "robot_id": "robot-01",
  "frames": [],
  "objects": [],
  "robot_state": {},
  "active_action": {},
  "recent_events": []
}
```

Frame entries should contain channel, capture timestamp, sequence, content type,
hash, and synchronization metadata. Candidate channels include raw RGB,
FoundationPose overlay, depth colormap, mask, and drop-detector visualization.
VLM actions must be traceable to an observation and world version. If the world
has materially changed, re-observe or replan before execution.

### 5.4 `GoalSpec`, `PlanGraph`, and `PlanRevision`

A `GoalSpec` preserves the original instruction, grounded object selector,
destination `LocationRef`, constraints, and success criteria. A `PlanGraph`
contains semantic action nodes, preconditions, success evidence, and failure or
recovery branches. Every change creates a `plan_revision_id`; already completed
physical nodes are immutable and must not be silently regenerated.

## 6. Cross-phase invariants

All phases must preserve these properties:

1. One authoritative runtime and one normal physical writer per robot.
2. Web, fixed missions, and the VLM Agent use the same execution boundary.
3. Caller-supplied strings such as `source` are audit metadata, not authority.
4. Operator-only, service-management, control-source, and safety-administration
   capabilities require a trusted local principal and are never exposed merely
   because the model knows a tool name.
5. No implicit retry of a dispatched physical action.
6. A timeout after dispatch means the physical result may be `UNKNOWN`; reconcile
   before issuing a replacement action.
7. Dispatch/ack timeout, operation timeout, and caller wait timeout are distinct
   concepts.
8. Cancellation is not considered complete until the physical subsystem
   acknowledges it or the system proves a safe terminal state.
9. High-priority stop/interrupt requests must not wait behind the normal writer
   lock.
10. High-risk semantic skills require explicit success evidence. Missing or
    unavailable verification is not silently equivalent to success.
11. World State fields have source, timestamp, freshness, and unknown semantics.
12. Logical object identity, perception identity, and selection epoch remain
    distinct.
13. VLM decisions, prompts, model/provider versions, observations, plans, skill
    results, and recovery actions are traceable and replayable.
14. Cloud failure must never prevent a local immediate stop.
15. The Web UI is not the mission clock. Refreshing or closing it must not stop
    ordinary background execution.

## 7. Phase 0 — baseline, interface freeze, and acceptance definition

### Goal

Capture the current manually stepped system precisely enough that every later
change can be compared against a known baseline.

### Required work

- Map Web actions through HTTP, mission/control code, ROS services/topics,
  Gateway, FoundationPose, cameras, and robot results.
- Record at least one successful fixed-task golden trace with versions, inputs,
  action sequence, state changes, visual evidence, and final outcome.
- Freeze the authoritative semantics of navigation, pick/carry, place, target
  selection, drop detection, and camera interfaces.
- Maintain a machine-readable interface catalog and UI/HTTP compatibility map.
- Document the actual state machine and success/failure evidence.
- Record external repositories, deployed commits, service units, frame names,
  timeouts, and ownership boundaries.
- Define offline, replay, simulation, and hardware acceptance gates.

### Deliverables

- Architecture baseline.
- Compatibility boundary.
- Interface and UI/HTTP catalogs.
- Risk register.
- Golden task trace.
- HIL checklist and phase exit report.

### Exit gate

The team can explain what every current stage click does, identify the one
authoritative implementation of each external contract, replay the baseline,
and compare future behavior against it. Offline review readiness is not the same
as hardware acceptance.

## 8. Phase 1 — modular semantic skills and one dispatch contract

### Goal

Extract robot behavior into versioned, independently testable, composable skills
without forcing the planner to understand ROS implementation details.

### Required work

- Maintain a canonical skill catalog with strict input/output schemas.
- Separate planner-facing semantic composites from internal primitives.
- Route MissionRunner, Web developer tools, and compatibility paths through one
  dispatcher instance owned by the application runtime.
- Define stable result, error, verification, cancellation, idempotency, and
  timeout semantics.
- Preserve the existing fixed mission through a compatibility adapter.
- Provide fake/simulation adapters for deterministic offline testing.
- Do not advertise an adapter capability whose physical semantics are not
  actually implemented.

### Deliverables

- Skill definitions and schemas.
- Registry and dispatcher.
- ROS, fake, simulation, and compatibility adapters as applicable.
- Skill contract, dispatcher, integration, and HTTP compatibility tests.
- Phase 1 implementation and migration documentation.

### Exit gate

The existing fixed mission can execute entirely through semantic skills; all
normal control paths share one physical writer; duplicate requests attach rather
than re-execute; unknown outcomes fail-stop; and the approved offline suite
passes. Real ROS switching remains a separate HIL decision.

### Phase 1 carryover into Phase 2

Before a cloud Agent drives real hardware, close any remaining gaps in trusted
capability enforcement, authoritative robot state, required verification,
operation timeout semantics, physical cancellation/interrupt behavior, and
true object-ID binding. Address these as runtime foundations, not as ad hoc
prompt rules.

## 9. Phase 2 — Execution Supervisor and unattended execution

### Goal

Remove per-stage Web clicking. Even without a VLM, an existing fixed plan should
run continuously until completion, an explicit pause, or a real exception.

### Required work

- Introduce one `RobotRuntime` per physical robot.
- Introduce an `ExecutionSupervisor` as the sole normal action entry point.
- Execute a `PlanGraph` continuously and persist node-level evidence.
- Store missions, plan revisions, invocations, events, and reconciliation state.
- Add a high-priority interrupt lane for stop/cancel actions.
- Upgrade long navigation to start/status/cancel or a suitable action-style
  lifecycle with stop acknowledgement.
- Replace run-time local-cache reset assumptions with physical/persisted-state
  reconciliation.
- Model orthogonal state dimensions, at minimum:
  - Mission: idle/running/recovering/completed/failed.
  - Motion: stopped/moving/arrived/unknown.
  - Payload: empty/holding/drop_suspected/dropped/unknown.
  - Perception: fresh/stale/unavailable.
  - Control: autonomous/operator/estop.
  - Execution: idle/running/cancel_requested/reconciliation_required.
- Convert the Web App into a monitor and intervention surface.

### Deliverables

- Robot runtime/composition root.
- Supervisor and plan executor.
- Durable action/event ledger.
- World-state store and state-transition contracts.
- Interrupt/cancel/reconciliation interfaces.
- Updated Web mission timeline.

### Exit gate

- One submission runs the existing fixed mission without stage clicks.
- Closing or refreshing the Web UI does not interrupt the mission.
- A process crash during an action never causes automatic physical re-dispatch.
- An interrupt can request and confirm stop while a normal action is active.
- A drop event can at least stop/freeze transport safely, even though autonomous
  re-pick is deferred to Phase 7.

## 10. Phase 3 — semantic world model, `LocationRef`, `ObjectRef`, and ID selection

### Goal

Give Planner, Mission, Supervisor, perception, and skills one versioned semantic
model that supports the current nine-grid without binding the future architecture
to it.

### Required work

- Implement versioned `LocationRef` and `ResolvedLocation` contracts.
- Implement a `LocationResolver` for the current nine-grid and trusted stock
  locations.
- Add a translator for legacy grid strings where compatibility is required.
- Implement `ObjectRef`, tracker-session identity, observation identity, and
  object lineage.
- Extend FoundationPose/perception with a versioned select-by-object-ID contract.
- Return a selection token/epoch and enforce it through fresh-pose and pick.
- Store the complete candidate object set, not only an arbitrary first match.
- Build a timestamped, synchronized visual ring buffer.
- Wrap drop status in a structured event with time, sequence, detector health,
  active object/mission context, and evidence references.

### Deliverables

- Shared world-model package/schemas.
- Nine-grid location resolver.
- FoundationPose/perception ID-selection adapter.
- Full object candidate store.
- Observation ring buffer and structured drop event.
- Compatibility translators and schema tests.

### Exit gate

- Every new mission stores destinations as `LocationRef`.
- Only the nine-grid namespace is executable initially, while the schema remains
  extensible.
- The ID selected by the VLM is the ID whose fresh pose is submitted for pick.
- Wrong IDs, stale frames, old selection epochs, and reused tracker IDs fail
  deterministically.
- A stopped camera cannot continue supplying an indefinitely “valid” old frame.

## 11. Phase 4 — cloud VLM gateway and visual observation pipeline

### Goal

Send real, versioned robot observations to configurable cloud VLM providers and
receive structured, replayable decisions.

### Required work

- Implement a provider-neutral `CloudVLMClient`/`ModelGateway`.
- Support configured cloud multimodal providers without coupling mission code to
  one vendor SDK.
- Send image bytes or provider-supported media objects, not inaccessible local
  Web URLs.
- Support raw RGB, overlay, mask, depth visualization, and drop visualization.
- Support single frames, synchronized multi-channel bundles, action before/after
  frames, and short event windows where supported.
- Let the VLM request a fresh observation or additional view.
- Define strict schemas for scene understanding, object grounding, goal, plan,
  visual verification, and recovery outputs.
- Persist model/provider version, prompt version, observation ID, response ID,
  latency, usage, and plan revision linkage.
- Keep credentials out of source, prompts, audit payloads, and Git.
- Configure TLS, retention, concurrency, timeout, retry-at-transport-before-
  dispatch only, circuit breaking, and cost reporting.
- Treat text visible in camera images as untrusted scene content and test visual
  prompt-injection behavior.

### Deliverables

- Cloud model gateway and provider adapters.
- Observation serialization/upload pipeline.
- Structured response validation.
- Model call ledger and replay provider.
- Cloud failure, malformed output, stale response, and prompt-injection tests.

### Exit gate

- A real `ObservationBundle` can be sent to two configurable providers and
  replayed offline.
- Every VLM decision is traceable to exact images and a world version.
- Invalid JSON, provider timeout, network loss, or stale output cannot produce an
  undefined physical action.
- The VLM can emit the complete agent-level action vocabulary, not only approve
  or reject a prewritten action.

## 12. Phase 5 — natural-language Planner Agent and end-to-end autonomy

### Goal

Turn one natural-language instruction into a grounded goal and a continuously
executed robot task without per-step approval.

### Required flow

```text
instruction
  -> initial observation
  -> GoalSpec
  -> object grounding and ObjectRef
  -> destination LocationRef
  -> PlanGraph / PlanRevision
  -> Supervisor execution
  -> action evidence returned to the Agent
```

### Required Agent behavior

- Resolve references such as color, left/right relation, and object category.
- Choose a perception candidate by `object_id`.
- Ground destinations into the current nine-grid `LocationRef` namespace.
- Generate complete semantic plans without a fixed pick/move/place template.
- Invoke any registered agent-level observation, motion, manipulation,
  verification, or recovery skill.
- Ask for new visual evidence when the scene is ambiguous.
- Continue, re-observe, replan, recover, finish, or abort based on results.
- Avoid asking for per-stage human confirmation during normal operation.

### Deliverables

- Planner Agent loop.
- Goal and plan schemas/prompts.
- Agent tool bridge to the Supervisor, not directly to ROS.
- Conversation/task API and Web instruction input.
- Grounding, planning, and end-to-end replay/simulation tests.

### Exit gate

A command such as “把左边的红色箱子放到东北格” produces a grounded
`ObjectRef`, `grid/ne` `LocationRef`, complete plan, autonomous execution, and
final verification without stage clicks.

## 13. Phase 6 — closed-loop observation, verification, and online replanning

### Goal

Move from one-shot planning to a continuous observe-decide-act-verify loop.

### Required work

- Provide observations before and after semantic skills when useful.
- Let the VLM validate grasp, placement, obstruction, and scene changes using
  both deterministic state and visual evidence.
- Trigger re-observation/replanning on object disappearance, ID change, stale
  pose, grasp failure, placement failure, occupied destination, navigation
  blockage, world-version change, or model-requested new evidence.
- Make plan-tail replacement an explicit, persisted `PlanRevision` operation.
- Preserve completed action nodes and their idempotency identity across replans.
- Record conflicts between model judgement and deterministic sensors; do not
  silently overwrite either source.
- Keep local stopping and safe holding behavior available during cloud outages.

### Deliverables

- Closed-loop Agent controller.
- Visual verification tools.
- Re-observation and plan-tail revision APIs.
- Conflict/evidence records.
- Scenario and fault-injection suite.

### Exit gate

- The Agent can autonomously choose continue, re-observe, replan, recover, or
  abort.
- Scene changes invalidate stale plan assumptions.
- Replanning never repeats an already successful physical action.
- First-attempt pick/place failures can produce a new evidence-based strategy.

## 14. Phase 7 — autonomous recovery from a box dropped during transport

### Goal

When a held box drops while the robot is navigating, stop safely, diagnose the
event, reacquire the logical object, re-pick it, and continue to the original
destination.

### Required state flow

```text
CARRYING
  -> DROP_SUSPECTED
  -> INTERRUPT_REQUESTED
  -> STOP_CONFIRMED
  -> DIAGNOSING
  -> DROP_CONFIRMED | FALSE_ALARM | UNCERTAIN
  -> OBJECT_REACQUIRED
  -> RECOVERY_PLAN_READY
  -> REPICKING
  -> HOLDING_VERIFIED
  -> NEW_NAVIGATION
  -> RESUMED
```

### Required behavior

1. Arm drop interpretation only in an appropriate holding/transport window.
2. On a drop edge, locally freeze the normal plan, retain the original
   destination `LocationRef` and logical `ObjectRef`, capture odometry and event
   frames, and request stop without waiting for cloud inference.
3. Confirm navigation cancellation/stop and safe velocity before recovery
   motion. If stop is unconfirmed, enter a terminal/intervention state.
4. Send the event window, RGB, overlay, mask, depth, object list, and robot/plan
   state to the VLM.
5. Let the VLM classify confirmed drop, temporary occlusion/false alarm, camera
   failure, or uncertain evidence, and request more observation if useful.
6. For a confirmed drop, re-observe, identify candidate objects, explicitly bind
   a new perception ID to the original logical object, choose a recovery approach,
   assess reachability, re-pick, and verify holding.
7. Create a new navigation invocation to the saved destination. Do not blindly
   resume an old invocation with an uncertain physical history.
8. Persist recovery as a separate `RecoveryPlanRevision` with its own idempotency
   and evidence.
9. Do not make unconditional step-back or fixed sleeps the default recovery
   policy. Such actions, if selected, require normal semantic planning and
   feasibility checks.
10. Allow intervention when the object is unsafe/unreachable, stop cannot be
    confirmed, perception is unavailable, or the recovery budget is exhausted.

### Required scenarios

- Real drop.
- Brief occlusion/false alarm.
- Camera stream failure.
- Multiple boxes visible.
- Perception ID changes after reset.
- Dropped object is unreachable or unsafe to approach.
- Stop/cancel acknowledgement is missing.
- Cloud VLM timeout.
- First re-pick fails.
- Process restarts during recovery.
- Recovery succeeds and the original grid destination is completed.

### Exit gate

- No normal follow-up action is issued between drop suspicion and confirmed
  stop.
- The old navigation invocation is not blindly retried or resumed.
- Re-pick uses a fresh selection epoch and evidence for the reacquired object.
- One drop event has at most one active recovery plan.
- Successful recovery resumes and completes the original mission destination.

## 15. Phase 8 — console, replay, evaluation, HIL, and deployment

### Goal

Turn the autonomy stack into an observable, testable, deployable system with
clear real-robot evidence.

### Web console

Display at minimum:

- Original instruction and `GoalSpec`.
- Grounded `ObjectRef` and `LocationRef`.
- VLM input keyframes and structured decisions.
- Plan graph and revision diffs.
- Active invocation and authoritative World State.
- Skill inputs, outputs, verification, and errors.
- Drop/interrupt/recovery timeline.
- Cloud model latency, usage, and estimated cost.
- Explicit pause, emergency stop, and operator takeover controls.

### Evaluation metrics

- GoalSpec accuracy.
- Object-grounding accuracy and ID-switch count.
- Nine-grid grounding accuracy.
- Stale-observation rejection rate.
- Invalid-model-output physical action count (target: zero).
- End-to-end mission success rate and duration.
- Replan count and recovery success rate.
- Drop detection and confirmed-stop latency.
- Drop reacquisition/re-pick/resume success rate.
- False-recovery rate.
- VLM calls, P50/P95 latency, and cost per mission.
- Duplicate physical actions after crash/restart (target: zero).

### Deployment ladder

1. Deterministic offline tests.
2. Recorded-observation replay.
3. Full VLM behavior in simulation.
4. Static hardware interface checks.
5. Individually approved motion/manipulation HIL.
6. Fixed mission end-to-end HIL.
7. Natural-language cloud VLM end-to-end HIL.
8. Controlled injected-drop recovery HIL.
9. Repeated and long-duration operation.

The Agent should have the same complete semantic capability in replay,
simulation, and approved real-robot trials. The ladder gates the environment and
hardware evidence; it does not reduce the VLM to a passive observer.

### Exit gate

The end-to-end acceptance scenario succeeds:

1. The user provides one natural-language instruction.
2. The system captures and uploads the relevant real observations.
3. The VLM chooses the intended object by perception ID and creates `ObjectRef`.
4. The destination is represented as a nine-grid `LocationRef`.
5. The VLM creates and may revise a complete semantic plan.
6. The Supervisor executes navigation, pick, transport, place, and verification
   without stage clicks.
7. If the box drops during transport, the system stops locally, diagnoses,
   reacquires, re-picks, and continues to the original destination.
8. The console can replay every model decision, physical action, event, and
   evidence item.

## 16. Required working method for Codex

For every task in this repository:

1. Read this file and any nested `AGENTS.md` that applies.
2. Inspect Git status and preserve unrelated/user-authored changes.
3. Read the relevant Phase 0/1 documentation and current implementation before
   proposing or changing architecture.
4. Determine which phase and exit criterion the task advances. State this in the
   working plan for non-trivial changes.
5. Do not restart completed work merely because the roadmap describes it. Audit
   current behavior and implement the smallest coherent missing slice.
6. Keep data contracts and execution invariants explicit. Add schema/contract
   tests whenever changing a boundary.
7. Prefer deterministic offline tests, replay, and fakes before hardware tests.
8. Never run ROS, hardware, camera-upload, external cloud calls, service restarts,
   or HIL scripts without the user's explicit authorization for that action.
9. Do not enable or switch a real execution path solely as a side effect of
   implementing code.
10. When a phase slice is completed, report which deliverables and exit criteria
    were verified and which still require HIL or external dependencies.
11. Update roadmap/status documentation when implementation materially changes
    the truth of a phase; do not declare a phase complete based only on code
    presence.

Relevant starting references include:

- `docs/autonomy/phase0/architecture-baseline.md`
- `docs/autonomy/phase0/compatibility-boundary.md`
- `docs/autonomy/phase0/interface-catalog.json`
- `docs/autonomy/phase0/http-ui-matrix.json`
- `docs/autonomy/phase0/risk-register.md`
- `docs/autonomy/phase0/exit-report.md`
- `docs/autonomy/phase0/adr-001-skill-layering.md`
- `docs/autonomy/phase0/adr-002-results-and-errors.md`
- `docs/autonomy/phase0/adr-003-idempotency-retry-cancel.md`
- `docs/autonomy/phase1/implementation.md`
- `environment.yml`
- `tests/offline/`
- `tests/hardware/README.md`

## 17. Prohibited shortcuts

Do not:

- Recreate the old Web click sequence inside an Agent loop and call it autonomy.
- Hard-code nine-grid coordinates into VLM prompts or durable goals.
- Treat a logical task object ID as interchangeable with a perception tracker ID.
- Let the VLM call raw ROS primitives or safety-administration endpoints directly.
- Rely on prompt wording as the only enforcement of a physical invariant.
- Represent unknown pose/holding state with plausible zero/default values.
- Mark a high-risk physical action successful without its required evidence.
- Retry a timed-out physical action before reconciliation.
- Allow a normal writer lock to prevent an emergency/drop stop request.
- Wait for cloud VLM inference before issuing the first local stop after a drop.
- Blindly resume the old navigation invocation after drop recovery.
- Implement recovery as a chain of fixed sleeps and unconditional motions.
- Send API keys, secrets, raw image payloads, or sensitive prompts into Git or
  ordinary audit logs.
- Claim HIL or phase completion when only offline tests have run.

## 18. Recommended critical path

```text
Close Phase 0/1 review and HIL-independent contracts
  -> Phase 2 remove stage clicks and establish the Supervisor
  -> Phase 3 establish World State, LocationRef, ObjectRef, and ID selection
  -> Phase 4 connect real observations to the cloud VLM
  -> Phase 5 natural-language end-to-end Planner Agent
  -> Phase 6 closed-loop verification and replanning
  -> Phase 7 transport-drop autonomous recovery
  -> Phase 8 replay, evaluation, HIL, and deployment
```

Parallel work is acceptable only when shared schemas and ownership boundaries are
already frozen. In particular, do not build the Planner, cloud observation path,
and drop recovery around incompatible temporary object/location/state models.
