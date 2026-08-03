# Phase 1 skill execution implementation

The production API server owns one SkillRegistry/SkillDispatcher pair and
injects it into StepDebugController, MissionRunner, and DevConsoleController.
RobotToolSuite is generated from the same registry.

SONIC input-source control and the process-local safety bypass are registered
as `operator_only` capabilities. They are not advertised to planners, and
their mutations compete for the same per-robot writer lock as physical skills.
Robot switching and service lifecycle management remain outside planner/skill
authority.

## Canonical composite inputs

- `move_to`: `target{x,y,z,theta}`, `timeout_s`
- `pick`: `object_id`, `timeout_s`
- `place`: `target{x,y,z,theta}`, `timeout_s`
- queries: empty object

Schemas reject unknown properties. The compatibility converter removes legacy
`action`, `current`, correlation IDs, and stock/grid hints before validation.
Hints are stored in PlanStep `annotations` and are not forwarded as action args.

## Rollout switch

`PHI_ACTION_EXECUTOR=skills|legacy-direct` selects one path. Fake/stub defaults
to skills. ROS defaults to legacy-direct until code review, interface-owner
confirmation, and the separate HIL checklist are approved. Never run both paths
for comparison against a physical robot.

## HIL order after separate approval

1. Read-only capability/service/state/input-source checks.
2. One safe-point `move_to`.
3. One pre-positioned-object `pick`.
4. One approved-bin `place`.
5. One fixed four-step plan.

Stop immediately for any unexpected state, pose, frame, idempotency ID, or
verification result. The workspace must be clear and an emergency-stop operator
must be assigned before step 2.
