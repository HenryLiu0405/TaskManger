# ADR-001: Skill layering and authority

Status: accepted for Phase 1.

## Decision

Use a two-layer catalog backed by one `SkillRegistry`.

- Planner-visible composite skills: `move_to`, `pick`, `place`.
- Reliable read-only queries may be registered but are not planner-visible by
  default: `get_pose`, `get_gripper_state`.
- ROS, FoundationPose, Gateway, wait, recovery, and adapter operations are
  internal primitives with `planner_visible=false`.
- Robot switching, service management, safety bypass, and input-source control
  remain operator-only capabilities outside planner authority.

Every definition records version, JSON Schema 2020-12 input/output shape,
resources, side-effect/risk metadata, timeout, retry/cancellation mode,
preconditions, and verifier name. RobotToolSuite projects its schemas and
handlers from this registry instead of maintaining a second contract.

## Consequences

The model cannot call ROS primitives or bypass safety. A backend that lacks an
internal primitive must return `UNSUPPORTED_CAPABILITY`; it cannot silently
skip it. Phase 1 wraps the existing ROS composite sequences and does not split
them into start/wait calls until the ROS bridge exposes a genuine start-only
contract.

