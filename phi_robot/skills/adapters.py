"""Adapter compatibility shim used by the v1 skill catalog."""

from __future__ import annotations

import math
import time
from typing import Any, Mapping

from .compat import legacy_backend_args
from .models import SkillRequest


class AdapterSkillHandler:
    """Translate canonical skills to the existing synchronous adapter surface."""

    CORE_SKILLS = {"move_to", "pick", "place", "get_pose", "get_gripper_state"}

    def __init__(self, adapter: Any, *, composite_pick: bool = False) -> None:
        self.adapter = adapter
        self.composite_pick = composite_pick

    def __call__(self, request: SkillRequest) -> Mapping[str, Any]:
        name = request.skill_name
        if name in self.CORE_SKILLS:
            return self._execute_core(request)
        return self._execute_primitive(request)

    def _execute_core(self, request: SkillRequest) -> Mapping[str, Any]:
        name = request.skill_name
        if name in {"get_pose", "get_gripper_state"} and _is_ros_acceptance(self.adapter):
            return self._error(
                request,
                "UNSUPPORTED_CAPABILITY",
                f"{name} is not backed by a reliable ROS query yet",
            )

        precondition_error = _check_core_precondition(request, self.adapter)
        if precondition_error is not None:
            return self._error(request, "PRECONDITION_FAILED", precondition_error)

        selected_here = False
        target_preselected = bool(request.annotations.get("target_preselected"))
        if name == "pick" and self.composite_pick and hasattr(self.adapter, "select_target_public"):
            if not target_preselected:
                pick_x, pick_y = _stock_xy(request.annotations)
                if pick_x is None:
                    return self._error(
                        request,
                        "PRECONDITION_FAILED",
                        "pick requires trusted stock coordinates in PlanStep annotations",
                    )
                reset_observation = getattr(self.adapter, "reset_pick_observation", None)
                if callable(reset_observation):
                    reset_observation()
                result = self.adapter.select_target_public(
                    select=True,
                    pick_x=pick_x,
                    pick_y=pick_y,
                    material_points_xy=[pick_x, pick_y],
                    step_id=request.context.step_id,
                )
                if result is None:
                    self._clear_target(request)
                    return self._unsupported(request)
                if not isinstance(result, Mapping):
                    self._clear_target(request)
                    return self._error(
                        request,
                        "BACKEND_ERROR",
                        "FoundationPose target selection returned no structured result",
                    )
                matched = result.get("matched_object_id", -1) if result else -1
                if not result or not result.get("success") or matched < 0:
                    # A select service may enter single-target mode before it
                    # reports a downstream matching failure.  Always request
                    # cleanup, even when selection did not become usable.
                    self._clear_target(request)
                    return self._error(
                        request,
                        "FP_LOCK_FAILED",
                        str(result.get("message", "FoundationPose target selection failed") if result else "FoundationPose service unavailable"),
                    )
                selected_here = True

            wait_pose = getattr(self.adapter, "wait_fp_pose_ready", None)
            if callable(wait_pose) and not wait_pose(min_wait_s=0.1, timeout_s=min(float(request.args["timeout_s"]), 8.0)):
                cleanup = self._clear_target(request)
                if cleanup.get("status") != "ok":
                    return self._error(
                        request,
                        "TARGET_CLEANUP_FAILED",
                        "fresh pose was unavailable and single-target cleanup was not confirmed",
                    )
                return self._error(request, "POSE_NOT_READY", "no fresh pose was available for the selected target")

        normalized: Mapping[str, Any]
        cleanup_result: Mapping[str, Any] | None = None
        try:
            projected = legacy_backend_args(name, request.args, self.adapter)
            raw = self.adapter.execute(
                name,
                projected,
                request_id=request.context.idempotency_key,
                goal_id=request.context.goal_id,
                step_id=request.context.step_id,
            )
            normalized = self._normalize_adapter_result(request, raw)
        finally:
            if (
                name == "pick"
                and self.composite_pick
                and (selected_here or target_preselected)
                and _target_mode_active(self.adapter)
            ):
                cleanup_result = self._clear_target(request)

        if cleanup_result is not None and cleanup_result.get("status") != "ok":
            if normalized.get("status") == "ok":
                return self._error(
                    request,
                    "TARGET_CLEANUP_FAILED",
                    "pick completed but exiting FoundationPose single-target mode was not confirmed",
                )
            normalized = {
                **dict(normalized),
                "cleanup_error": cleanup_result.get("message", "target cleanup failed"),
            }
        return normalized

    def _execute_primitive(self, request: SkillRequest) -> Mapping[str, Any]:
        name = request.skill_name
        args = dict(request.args)

        if name == "get_input_source":
            method = getattr(self.adapter, "get_sonic_input_source", None)
            if not callable(method):
                return self._unsupported(request)
            source = method()
            return {
                **self._ok(request, "input source read"),
                "active_source": str(source or "unknown"),
            }

        if name == "set_input_source":
            method = getattr(self.adapter, "set_sonic_input_source", None)
            if not callable(method):
                return self._unsupported(request)
            raw = method(bool(args["gamepad"]))
            return self._normalize_boolean_result(request, raw)

        if name == "set_safety_bypass":
            enabled = bool(args["enabled"])
            method = getattr(self.adapter, "set_safety_bypass", None)
            if callable(method):
                method(enabled)
            else:
                # The fake/remote adapters do not own the process-local safety
                # state.  Keep this operator capability available without
                # teaching those data-plane adapters about the HTTP process.
                from phi_robot.robot_state import safety_fsm

                safety_fsm.set_bypass(enabled)
            return {
                **self._ok(request, "safety bypass updated"),
                "bypass": enabled,
            }

        if name == "select_target":
            method = getattr(self.adapter, "select_target_public", None)
            if not callable(method):
                return self._unsupported(request)
            reset_observation = getattr(self.adapter, "reset_pick_observation", None)
            if callable(reset_observation):
                reset_observation()
            raw = method(
                select=True,
                pick_x=args["pick_x"],
                pick_y=args["pick_y"],
                material_points_xy=list(args.get("material_points_xy") or [args["pick_x"], args["pick_y"]]),
                step_id=request.context.step_id,
            )
            if raw is None:
                self._clear_target(request)
                return self._unsupported(request)
            matched = raw.get("matched_object_id", -1) if isinstance(raw, Mapping) else -1
            if not isinstance(raw, Mapping) or not raw.get("success") or matched < 0:
                self._clear_target(request)
                return self._error(
                    request,
                    "FP_LOCK_FAILED",
                    str(
                        raw.get("message", "FoundationPose target selection failed")
                        if isinstance(raw, Mapping)
                        else "FoundationPose service unavailable"
                    ),
                )
            return self._normalize_boolean_result(request, raw, success_key="success")

        if name == "clear_target":
            return self._clear_target(request)

        if name == "wait_fresh_pose":
            method = getattr(self.adapter, "wait_fp_pose_ready", None)
            if not callable(method):
                return self._unsupported(request)
            ready = method(min_wait_s=0.0, timeout_s=float(args["timeout_s"]))
            return (
                self._ok(request, "fresh matching pose observed")
                if ready
                else self._error(request, "POSE_NOT_READY", "fresh matching pose was not observed")
            )

        if name == "navigation_wait":
            method = getattr(self.adapter, "_wait_nav_reached", None)
            if not callable(method):
                return self._unsupported(request)
            reached = method(timeout=float(args["timeout_s"]))
            return (
                self._ok(request, "navigation reached")
                if reached
                else self._error(request, "NAV_TIMEOUT", "nav_reached was not observed before timeout")
            )

        if name == "wait_ready":
            method = getattr(self.adapter, "_wait_until_ready", None)
            if not callable(method):
                return self._unsupported(request)
            ready = method(str(args["operation"]))
            return (
                self._ok(request, "robot ready state observed")
                if ready
                else self._error(request, "VERIFICATION_FAILED", "robot did not reach ready state")
            )

        if name == "gateway_notify_goal_reached":
            method = getattr(self.adapter, "_call_notify_goal_reached", None)
            if not callable(method):
                return self._unsupported(request)
            raw = method(
                request.context.idempotency_key,
                request.context.goal_id,
                request.context.step_id,
            )
            return self._normalize_adapter_result(request, raw)

        if name == "submit_carry":
            method = getattr(self.adapter, "_call_submit_carry_task", None)
            if not callable(method):
                return self._unsupported(request)
            raw = method(
                request.context.idempotency_key,
                request.context.goal_id,
                request.context.step_id,
            )
            return self._normalize_adapter_result(request, raw)

        if name in {"lay_down", "replay"}:
            method_name = "_call_set_lay_down" if name == "lay_down" else "_call_request_replay"
            method = getattr(self.adapter, method_name, None)
            if not callable(method):
                return self._unsupported(request)
            raw = method(
                request.context.idempotency_key,
                request.context.goal_id,
                request.context.step_id,
            )
            return self._normalize_adapter_result(request, raw)

        if name == "reset_fp":
            method = getattr(self.adapter, "call_reset_fp", None)
            if not callable(method):
                return self._unsupported(request)
            raw = method()
            if raw is None:
                return self._unsupported(request)
            return self._normalize_boolean_result(request, raw, success_key="success")

        if name == "replan_pick":
            method = getattr(self.adapter, "replan_pick", None)
            if not callable(method):
                return self._unsupported(request)
            raw = method()
            return self._normalize_adapter_result(request, raw)

        # The current ROS adapter exposes navigation start+wait as one legacy
        # synchronous call.  Advertising a split start primitive as successful
        # would be unsafe, so it remains explicitly unsupported until the ROS
        # bridge supplies a true start-only contract.
        if name == "navigation_start":
            return self._unsupported(request)

        if name == "get_robot_state":
            method = getattr(self.adapter, "get_robot_state", None)
            if not callable(method):
                return self._unsupported(request)
            raw = method()
            if not isinstance(raw, Mapping):
                return self._error(
                    request,
                    "BACKEND_ERROR",
                    "get_robot_state returned no structured state",
                )
            robot_state = dict(raw)
            return {
                **self._ok(request, "robot state read"),
                "state": robot_state,
                "robot_state": robot_state,
            }

        if name == "get_runtime_snapshot":
            snapshot = _safe_snapshot(self.adapter)
            odom_method = getattr(self.adapter, "get_odom", None)
            odom: dict[str, Any] | None = None
            if callable(odom_method):
                raw_odom = odom_method()
                if isinstance(raw_odom, Mapping):
                    odom = dict(raw_odom)
            return {
                **self._ok(request, "cached runtime state read"),
                "snapshot": snapshot,
                "nav_reached": getattr(self.adapter, "nav_reached", None),
                "locked_object_id": getattr(
                    self.adapter,
                    "_fp_target_object_id",
                    getattr(self.adapter, "selected_object_id", None),
                ),
                "odom": odom,
                "target_pose": _target_pose_snapshot(self.adapter),
            }

        method_names = {
            "pause_navigation": "pause_navigation",
            "resume_navigation": "resume_navigation",
            "stand": "stand_robot",
            "step_back": "step_back_robot",
            "drop_detector_enable": "enable_drop_detector",
            "drop_detector_disable": "disable_drop_detector",
            "identify_dropped": "identify_dropped_box",
        }
        if name in method_names:
            method = getattr(self.adapter, method_names[name], None)
            if not callable(method):
                return self._unsupported(request)
            raw = method(**args) if args else method()
            if raw is None:
                raw = {"ok": True, "message": f"{name} requested"}
            return self._normalize_boolean_result(request, raw)

        if name == "adapter_reset":
            method = getattr(self.adapter, "reset", None)
            if not callable(method):
                return self._unsupported(request)
            method()
            return self._ok(request, "adapter reset complete")

        return self._unsupported(request)

    def _clear_target(self, request: SkillRequest) -> Mapping[str, Any]:
        method = getattr(self.adapter, "select_target_public", None)
        if not callable(method):
            return self._unsupported(request)
        raw = method(select=False, step_id=request.context.step_id)
        return self._normalize_boolean_result(request, raw, success_key="success")

    def _normalize_adapter_result(self, request: SkillRequest, raw: Any) -> Mapping[str, Any]:
        if not isinstance(raw, Mapping):
            return self._error(request, "BACKEND_ERROR", f"adapter returned {type(raw).__name__}, expected mapping")
        data = dict(raw)
        if "status" not in data:
            data["status"] = (
                "ok" if data.get("ok") is True or data.get("success") is True else "error"
            )
        data.setdefault("request_id", request.context.request_id)
        data.setdefault("mission_id", request.context.mission_id)
        data.setdefault("goal_id", request.context.goal_id)
        data.setdefault("step_id", request.context.step_id)
        data.setdefault("metrics", {})
        data.setdefault("state", _safe_snapshot(self.adapter))
        data.setdefault("error_code", None if data.get("status") == "ok" else "BACKEND_ERROR")
        if not isinstance(data.get("verification"), Mapping):
            data["verification"] = _verify(request, data, self.adapter)
        return data

    def _normalize_boolean_result(
        self,
        request: SkillRequest,
        raw: Any,
        *,
        success_key: str = "ok",
    ) -> Mapping[str, Any]:
        if not isinstance(raw, Mapping):
            return self._error(request, "BACKEND_ERROR", f"{request.skill_name} returned no structured result")
        data = dict(raw)
        success = (
            data.get("status") == "ok"
            if "status" in data
            else bool(data.get(success_key, data.get("ok", False)))
        )
        return {
            **data,
            "status": "ok" if success else "error",
            "error_code": None if success else str(data.get("error_code") or "BACKEND_ERROR"),
            "message": str(data.get("message", "")),
            "state": data.get("state") or _safe_snapshot(self.adapter),
            "metrics": data.get("metrics") or {},
            "request_id": request.context.request_id,
            "mission_id": request.context.mission_id,
            "goal_id": request.context.goal_id,
            "step_id": request.context.step_id,
        }

    def _unsupported(self, request: SkillRequest) -> Mapping[str, Any]:
        return self._error(request, "UNSUPPORTED_CAPABILITY", f"adapter does not implement {request.skill_name}")

    @staticmethod
    def _ok(request: SkillRequest, message: str) -> Mapping[str, Any]:
        return {
            "status": "ok",
            "error_code": None,
            "message": message,
            "state": {},
            "metrics": {},
            "request_id": request.context.request_id,
            "mission_id": request.context.mission_id,
            "goal_id": request.context.goal_id,
            "step_id": request.context.step_id,
        }

    @staticmethod
    def _error(request: SkillRequest, code: str, message: str) -> Mapping[str, Any]:
        return {
            "status": "error",
            "error_code": code,
            "message": message,
            "state": {},
            "metrics": {},
            "request_id": request.context.request_id,
            "mission_id": request.context.mission_id,
            "goal_id": request.context.goal_id,
            "step_id": request.context.step_id,
        }


def _safe_snapshot(adapter: Any) -> dict[str, Any]:
    try:
        snapshot = adapter.snapshot()
        return dict(snapshot) if isinstance(snapshot, Mapping) else {}
    except Exception:
        return {}


def _target_pose_snapshot(adapter: Any) -> dict[str, Any]:
    """Project the ROS FoundationPose cache into JSON-safe UI evidence."""

    if not hasattr(adapter, "_latest_pose_result"):
        return {"supported": False, "has_pose": False}
    pose_result = getattr(adapter, "_latest_pose_result", None)
    if pose_result is None or getattr(pose_result, "state", 0) != 2:
        return {"supported": True, "has_pose": False}
    try:
        x = float(pose_result.pose.position.x)
        y = float(pose_result.pose.position.y)
        z = float(pose_result.pose.position.z)
        return {
            "supported": True,
            "has_pose": True,
            "x": x,
            "y": y,
            "z": z,
            "yaw_deg": round(math.degrees(math.atan2(y, x)), 1),
            "distance": round(math.sqrt(x * x + y * y), 3),
            "frame_id": str(getattr(pose_result.header, "frame_id", "")),
            "tracking_frames": int(getattr(pose_result, "tracking_frames", 0)),
        }
    except (AttributeError, TypeError, ValueError):
        return {
            "supported": True,
            "has_pose": False,
            "error": "cached target pose is malformed",
        }


def _verify(request: SkillRequest, result: Mapping[str, Any], adapter: Any) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"status": "not_run", "evidence": {}, "message": "backend did not succeed"}
    state = result.get("state") or _safe_snapshot(adapter)
    if request.skill_name == "move_to":
        # A ROS composite reports the terminal pose in its action result while
        # its generic snapshot may intentionally expose no reliable pose.
        pose = result.get("pose") or state.get("robot_pose") or state.get("pose")
        target = request.args.get("target")
        if isinstance(pose, Mapping) and isinstance(target, Mapping):
            try:
                distance = math.sqrt(sum((float(pose[a]) - float(target[a])) ** 2 for a in ("x", "y", "z")))
                return {
                    "status": "passed" if distance <= 0.1 else "failed",
                    "evidence": {"distance_m": distance, "pose": dict(pose), "target": dict(target)},
                    "message": "pose is within tolerance" if distance <= 0.1 else "pose is outside tolerance",
                }
            except Exception:
                pass
    elif request.skill_name == "pick" and "holding" in state:
        holding = state.get("holding")
        if _is_ros_acceptance(adapter):
            return {
                "status": "unavailable",
                "evidence": {},
                "message": "ROS holding verification must come from the composite gateway sequence",
            }
        return {
            "status": "passed" if holding else "failed",
            "evidence": {"holding": holding},
            "message": "holding state observed" if holding else "backend did not report a held object",
        }
    elif request.skill_name == "place" and "holding" in state:
        if _is_ros_acceptance(adapter):
            return {
                "status": "unavailable",
                "evidence": {},
                "message": "ROS release verification must come from the composite gateway sequence",
            }
        holding = state.get("holding")
        return {
            "status": "passed" if holding is None else "failed",
            "evidence": {"holding": holding},
            "message": "release state observed" if holding is None else "object still reported as held",
        }
    return {"status": "unavailable", "evidence": {}, "message": "backend exposes no reliable verifier evidence"}


def _stock_xy(annotations: Mapping[str, Any]) -> tuple[float | None, float | None]:
    x = annotations.get("slot_nav2_x", annotations.get("stock_nav2_x"))
    y = annotations.get("slot_nav2_y", annotations.get("stock_nav2_y"))
    try:
        return float(x), float(y)
    except (TypeError, ValueError):
        return None, None


def _check_core_precondition(request: SkillRequest, adapter: Any) -> str | None:
    """Apply only preconditions supported by reliable local evidence."""

    if request.skill_name not in {"pick", "place"} or _is_ros_acceptance(adapter):
        # The ROS adapter owns its safety FSM and its generic snapshot contains
        # placeholders, so treating that snapshot as authoritative would be unsafe.
        return None
    if adapter.__class__.__name__ == "MoveToPassthroughAdapter":
        return None

    state = _safe_snapshot(adapter)
    if "holding" not in state:
        return None
    holding = state.get("holding")
    if request.skill_name == "pick" and holding is not None:
        return "robot already reports an object as held"
    if request.skill_name == "place" and holding is None:
        return "robot does not report a held object"

    if request.skill_name == "pick":
        stock_x, stock_y = _stock_xy(request.annotations)
        pose = state.get("robot_pose") or state.get("pose")
        if stock_x is not None and isinstance(pose, Mapping):
            try:
                distance = math.hypot(
                    float(pose["x"]) - stock_x,
                    float(pose["y"]) - stock_y,
                )
            except (KeyError, TypeError, ValueError):
                return "robot pose is not usable for stock-point validation"
            # Planned approach poses may intentionally stop up to 0.6 m from
            # the object; beyond 1 m is not a credible material-point arrival.
            if distance > 1.0:
                return f"robot is {distance:.3f} m from the trusted stock point"
    return None


def _is_ros_acceptance(adapter: Any) -> bool:
    return adapter.__class__.__name__ == "RosAcceptanceAdapter"


def _target_mode_active(adapter: Any) -> bool:
    if hasattr(adapter, "_select_target_active"):
        return bool(getattr(adapter, "_select_target_active"))
    if hasattr(adapter, "select_target_active"):
        return bool(getattr(adapter, "select_target_active"))
    # Unknown adapters get the conservative cleanup request; clear is required
    # to be idempotent by the frozen FoundationPose contract.
    return True
