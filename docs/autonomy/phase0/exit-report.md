# Phase 0 / Phase 1 offline exit report

Report date: 2026-07-31. This report covers code and offline evidence only. No
ROS node, network endpoint, subprocess, or robot adapter was invoked by the
offline suite, and no HIL step was attempted.

## Review status

Implementation is ready for code review. It is **not approved for HIL**. The
target-host Ubuntu 22.04/Humble gate, external FoundationPose snapshot, and
Gateway/body-frame owner confirmations remain release checks.
`RosAcceptanceAdapter` therefore continues to default to `legacy-direct`.

## Local evidence

The available development host is macOS, so it does not provide the frozen
Ubuntu/Humble runtime. The repository's `AgenticRobot` Conda environment was
created with Python 3.10.20 and all declared development dependencies. On this
host:

- The authoritative offline suite discovered and passed all 82 tests with zero
  skips and exit status 0.
- The five Flask HTTP/SSE and operator-control compatibility tests also passed
  as an explicit targeted run.
- Python compilation completed for `phi_robot`, `tests`, `bridge`, and the API
  launcher.
- All 6 existing frontend state tests passed.
- The legacy Phase 1 runner produced `0/3`, printed an explicit failure
  conclusion, and exited with status 1.
- Test execution produced no Git-visible working-tree delta.

The authoritative gate is:

```bash
source /opt/ros/humble/setup.bash
TASKMANAGER_PYTHON=python3.10 ./scripts/check_offline.sh
```

It deliberately fails before collection unless the host is Ubuntu 22.04, the
active ROS distribution is Humble, and Python is exactly 3.10. Install
`requirements.txt` and `requirements-dev.txt` in the target environment before
running it; skipped tests are not acceptable in the target exit report.
For an isolated setup, `environment.yml` defines the reviewable Conda
environment `AgenticRobot`; creating it is an explicit developer action and is
not performed by the offline suite.

## Phase 0 gate

| Gate | Status | Evidence |
|---|---|---|
| Default suite causes zero ROS/robot actions | pass locally | Offline import/static boundary and Scripted/Fake adapters only |
| Ubuntu 22.04 / Humble / Python 3.10 reproducible | partial: Python pass, target OS/ROS pending | `AgenticRobot` reconstructed Python 3.10.20 and passed 82/82; strict Ubuntu/Humble preflight remains |
| Every UI action maps to one backend entry | pass | `http-ui-matrix.json` |
| ROS/FP/Gateway conflicts resolved or explicitly block rollout | pass with HIL blockers | `/start_navigation`, Humble, `/odom`, stable carry ID, `pelvis`, and service response frozen; external owner hashes remain blockers |
| Fake happy/failure/blocking scenarios repeat | pass locally | Offline contract, integration, concurrency, timeout, and cancellation tests |
| Deliverables are trackable and tests do not dirty Git state | pass locally | `.gitignore` exceptions and before/after status comparison |

## Phase 1 gate

| Gate | Status | Evidence |
|---|---|---|
| One schema source for five compatibility skills | pass locally | `SkillRegistry` generates RobotToolSuite definitions |
| Planner steps pass strict validation | pass locally | Canonical planner contract test |
| Invalid/unknown inputs stop before adapter | pass locally | JSON Schema 2020-12 and backend-call assertions |
| StepDebug, MissionRunner, DevConsole share dispatcher | pass locally | API composition root, static bypass check, source-competition tests |
| Operator-only input/safety mutations share dispatcher | pass locally | Planner-invisible capabilities and HTTP writer-lock test passed in `AgenticRobot` |
| Maximum one mutating call per robot | pass locally | Blocking concurrency tests; different robots retain independent locks |
| Same idempotency key produces one side effect | pass locally | Terminal and concurrent duplicate tests |
| Timeout/cancel never starts a successor | pass locally | `unknown`/draining and unconfirmed-cancel tests |
| Fake full carry and fault paths pass | pass locally | Golden trace and fail-stop tests |
| HTTP/SSE compatibility | pass locally | Five Flask HTTP/SSE compatibility tests passed under Python 3.10.20 |
| Default suite does not import ROS | pass locally | Runtime and AST boundary assertions |

## HIL blockers

Before changing ROS to `skills` or running any hardware script, record all of:

1. FoundationPose service/message repository commit and object-ID field types.
2. Confirmation that FP publishes transformed `pelvis` poses with valid ROS
   timestamps and Gateway uses the same `WAIC_CARRY_TARGET_FRAME`.
3. Confirmation that the deployed `/start_navigation` bridge was rebuilt with
   the updated `ExecuteTrajectory` response.
4. A clean **target Ubuntu/Humble host** offline-gate run with zero skips and
   no working-tree delta. The equivalent macOS/Conda run is already 82/82.
5. Separate operator approval for the staged HIL checklist.
