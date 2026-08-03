"""Compatibility conversion between legacy PlanStep args and skill v1 args."""

from __future__ import annotations

from typing import Any, Mapping


_CONTEXT_KEYS = {"request_id", "goal_id", "step_id", "mission_id"}
_ANNOTATION_KEYS = {
    "slot_nav2_x", "slot_nav2_y", "grid_nav2_x", "grid_nav2_y",
    "stock_nav2_x", "stock_nav2_y", "destination", "task_index",
}


def normalize_legacy_call(
    tool: str,
    args: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return strict skill args and non-forwarded orchestration annotations."""

    raw = dict(args)
    annotations: dict[str, Any] = {}
    for key in list(raw):
        if key in _ANNOTATION_KEYS:
            annotations[key] = raw.pop(key)
        elif key in _CONTEXT_KEYS:
            raw.pop(key)

    if tool == "move_to":
        target = raw.get("target")
        if target is None and any(axis in raw for axis in ("x", "y", "z", "theta")):
            target = _pose_from_flat(raw)
        canonical = {
            "target": _normalize_pose(target or {}),
            "timeout_s": _timeout(raw),
        }
        for key in ("action", "current", "speed"):
            if key in raw:
                annotations[f"legacy_{key}"] = raw[key]
        if "action" in raw and raw["action"] != "start":
            # Preserve invalid legacy values so strict validation rejects the
            # request instead of silently rewriting its meaning.
            canonical["action"] = raw["action"]
        canonical.update(_unknown(raw, {
            "target", "x", "y", "z", "theta", "timeout_s",
            "action", "current", "speed",
        }))
        return canonical, annotations

    if tool == "pick":
        canonical = {
            "object_id": raw.get("object_id", ""),
            "timeout_s": _timeout(raw),
        }
        canonical.update(_unknown(raw, {"object_id", "timeout_s"}))
        return canonical, annotations

    if tool == "place":
        target = raw.get("target") or _pose_from_flat(raw)
        canonical = {
            "target": _normalize_pose(target),
            "timeout_s": _timeout(raw),
        }
        if "object_id" in raw:
            annotations["object_id"] = raw["object_id"]
        canonical.update(_unknown(raw, {
            "target", "x", "y", "z", "theta", "timeout_s", "object_id",
        }))
        return canonical, annotations

    if tool in {"get_pose", "get_gripper_state", "pause_navigation", "resume_navigation",
                "stand", "step_back", "drop_detector_enable", "drop_detector_disable",
                "adapter_reset", "get_robot_state", "clear_target"}:
        return raw, annotations

    # Internal primitives use their already-structured args.  Context metadata
    # was stripped above so it can never leak to the backend.
    return raw, annotations


def legacy_backend_args(tool: str, canonical_args: Mapping[str, Any], adapter: Any) -> dict[str, Any]:
    """Project canonical args onto the existing RobotAdapter protocol."""

    args = dict(canonical_args)
    if tool == "move_to":
        target = dict(args["target"])
        projected = {
            "target": target,
            "action": "start",
            "timeout_s": args["timeout_s"],
        }
        should_inject = True
        route_real = getattr(adapter, "should_route_real_move_to", None)
        if callable(route_real):
            try:
                should_inject = not bool(route_real(tool, projected))
            except Exception:
                should_inject = True
        if should_inject:
            projected["current"] = _current_pose(adapter)
        return projected
    if tool == "place":
        target = dict(args["target"])
        return {
            "x": target["x"],
            "y": target["y"],
            "z": target["z"],
            "theta": target.get("theta", 0.0),
            "timeout_s": args["timeout_s"],
        }
    return args


def _current_pose(adapter: Any) -> dict[str, float]:
    try:
        snapshot = adapter.snapshot()
    except Exception:
        snapshot = {}
    pose = snapshot.get("robot_pose") or snapshot.get("pose") or {}
    return _normalize_pose(pose)


def _timeout(raw: Mapping[str, Any]) -> float:
    value = raw.get("timeout_s", 30.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return value  # Strict schema validation will reject it.


def _pose_from_flat(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "x": raw.get("x"),
        "y": raw.get("y"),
        "z": raw.get("z", 0.0),
        "theta": raw.get("theta", 0.0),
    }


def _normalize_pose(value: Mapping[str, Any]) -> dict[str, Any]:
    pose = dict(value)
    normalized = {
        "x": pose.get("x"),
        "y": pose.get("y"),
        "z": pose.get("z", 0.0),
        "theta": pose.get("theta", 0.0),
    }
    normalized.update(_unknown(pose, {"x", "y", "z", "theta"}))
    return normalized


def _unknown(raw: Mapping[str, Any], consumed: set[str]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in consumed}
