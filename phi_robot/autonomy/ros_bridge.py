"""Thin bridge from the Phase 5 semantic vocabulary to the existing ROS adapter.

This module deliberately contains no ROS imports.  The deployed
``RosAcceptanceAdapter`` is injected at startup, while offline tests use a
small fake with the same public methods.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import replace
from typing import Any, Iterable, Mapping, Optional

from phi_robot.skills.catalog import build_skill_registry
from phi_robot.skills.dispatcher import SkillDispatcher
from phi_robot.skills.registry import SkillRegistry

from .agent_tools import SemanticSkillHandler, agent_skill_definitions
from .locations import LocationRef, NineGridLocationResolver
from .objects import (
    ObjectCandidate,
    ObjectCandidateSet,
    ObjectRef,
    ObjectSelectionError,
    ObjectSelectionStore,
    PerceptionRef,
    SelectionToken,
)
from .observations import ObservationBundle, ObservationError, ObservationFrame


class AdapterObservationSource:
    """Build replayable observation bundles from existing adapter caches."""

    def __init__(
        self,
        adapter: Any,
        *,
        robot_id: str,
        frame_max_age_s: float = 2.0,
        decision_ttl_s: float = 60.0,
        sync_tolerance_s: float = 0.75,
        tracker_category: str = "box",
        max_saved_observations: int = 32,
        clock: Any = time.time,
    ) -> None:
        if not robot_id:
            raise ValueError("robot_id must be non-empty")
        if frame_max_age_s <= 0 or decision_ttl_s <= 0 or sync_tolerance_s < 0:
            raise ValueError("invalid observation freshness configuration")
        self.adapter = adapter
        self.robot_id = robot_id
        self.frame_max_age_s = float(frame_max_age_s)
        self.decision_ttl_s = float(decision_ttl_s)
        self.sync_tolerance_s = float(sync_tolerance_s)
        self.tracker_category = tracker_category
        self.max_saved_observations = max(2, int(max_saved_observations))
        self.clock = clock
        self._guard = threading.RLock()
        self._world_version = 0
        self._candidate_sets: "OrderedDict[str, ObjectCandidateSet]" = OrderedDict()
        self._latest_bundle: Optional[ObservationBundle] = None

    def current_world_version(self) -> int:
        with self._guard:
            return self._world_version

    def latest_bundle(self) -> Optional[ObservationBundle]:
        with self._guard:
            return self._latest_bundle

    def candidate_set(self, observation_id: str) -> ObjectCandidateSet:
        with self._guard:
            value = self._candidate_sets.get(observation_id)
        if value is None:
            raise ObjectSelectionError(
                "OBSERVATION_NOT_FOUND",
                f"observation {observation_id!r} is not available in the local evidence cache",
            )
        return value

    def capture(
        self,
        *,
        channels: Iterable[str],
        reason: str,
    ) -> ObservationBundle:
        requested = tuple(dict.fromkeys(str(value) for value in channels))
        if not requested:
            raise ObservationError("CHANNELS_EMPTY", "at least one channel is required")
        now = float(self.clock())
        observation_id = f"obs-{uuid.uuid4().hex}"
        frames = tuple(
            self._observation_frame(channel, now=now, reason=reason)
            for channel in requested
        )
        spread = max(frame.captured_at for frame in frames) - min(
            frame.captured_at for frame in frames
        )
        if spread > self.sync_tolerance_s:
            raise ObservationError(
                "FRAMES_UNSYNCHRONIZED",
                f"frame spread {spread:.3f}s exceeds {self.sync_tolerance_s:.3f}s",
            )

        candidate_set = self._candidate_snapshot(observation_id, now=now)
        gateway_state = _call_mapping(self.adapter, "get_robot_state")
        odometry = _call_optional_mapping(self.adapter, "get_odom_sample")
        robot_state = {
            "gateway": _unknowns_to_none(gateway_state),
            "odometry": _unknowns_to_none(odometry) if odometry is not None else None,
            "foundationpose": {
                "provider": "foundationpose",
                "tracker_session_id": candidate_set.tracker_session_id,
                "candidate_count": len(candidate_set.candidates),
            },
        }
        with self._guard:
            self._world_version += 1
            world_version = self._world_version
            bundle = ObservationBundle(
                observation_id=observation_id,
                world_version=world_version,
                captured_at=max(frame.captured_at for frame in frames),
                valid_until=now + self.decision_ttl_s,
                robot_id=self.robot_id,
                frames=frames,
                objects=candidate_set.candidates,
                robot_state=robot_state,
                active_action={},
                recent_events=(),
                synchronization={
                    "method": "adapter_receive_time",
                    "spread_s": spread,
                    "tolerance_s": self.sync_tolerance_s,
                    "channels": list(requested),
                    "reason": str(reason),
                },
            )
            self._candidate_sets[observation_id] = candidate_set
            while len(self._candidate_sets) > self.max_saved_observations:
                self._candidate_sets.popitem(last=False)
            self._latest_bundle = bundle
        return bundle

    def _observation_frame(
        self,
        channel: str,
        *,
        now: float,
        reason: str,
    ) -> ObservationFrame:
        getter = getattr(self.adapter, "get_fp_video_sample", None)
        if not callable(getter):
            raise ObservationError(
                "FRAME_METADATA_UNAVAILABLE",
                "adapter does not expose timestamped camera samples",
            )
        raw = getter(channel)
        if not isinstance(raw, Mapping):
            raise ObservationError(
                "FRAME_CHANNEL_UNAVAILABLE", f"no frame is available for {channel}"
            )
        data = raw.get("data")
        if not isinstance(data, bytes) or not data:
            raise ObservationError("FRAME_DATA_INVALID", f"invalid frame for {channel}")
        captured_at = float(raw.get("captured_at") or 0.0)
        if captured_at <= 0 or now - captured_at > self.frame_max_age_s:
            raise ObservationError("FRAME_STALE", f"latest {channel} frame is stale")
        if captured_at > now + 0.1:
            raise ObservationError("FRAME_TIMESTAMP_FUTURE", f"{channel} frame is in the future")
        return ObservationFrame(
            frame_id=f"frame-{uuid.uuid4().hex}",
            channel=channel,
            captured_at=captured_at,
            sequence=int(raw.get("sequence") or 0),
            content_type=str(raw.get("content_type") or "image/jpeg"),
            sha256=hashlib.sha256(data).hexdigest(),
            byte_length=len(data),
            synchronization={"source_stamp_ns": int(raw.get("source_stamp_ns") or 0)},
            metadata={"reason": str(reason), "adapter_channel": "rgb" if channel == "overlay" else channel},
            data=data,
        )

    def _candidate_snapshot(self, observation_id: str, *, now: float) -> ObjectCandidateSet:
        sample = _call_optional_mapping(self.adapter, "get_fp_state_sample")
        if sample is None:
            raise ObservationError(
                "OBJECT_STATE_UNAVAILABLE",
                "adapter does not expose timestamped FoundationPose state",
            )
        received_at = float(sample.get("received_at") or 0.0)
        if received_at <= 0 or now - received_at > self.frame_max_age_s:
            raise ObservationError("OBJECTS_STALE", "FoundationPose tracker state is stale")
        tracker_session_id = str(sample.get("tracker_session_id") or "")
        if not tracker_session_id:
            raise ObservationError(
                "TRACKER_SESSION_UNAVAILABLE", "FoundationPose tracker session is missing"
            )
        state = sample.get("state")
        trackers = state.get("trackers") if isinstance(state, Mapping) else None
        candidates: list[ObjectCandidate] = []
        for tracker in trackers or ():
            if not isinstance(tracker, Mapping) or not _tracker_is_candidate(tracker):
                continue
            object_id = tracker.get("object_id", tracker.get("id"))
            if object_id is None or isinstance(object_id, bool):
                continue
            pose = _tracker_pose(tracker)
            attributes = {
                key: value
                for key, value in tracker.items()
                if key not in {"id", "object_id", "x", "y", "z", "pose", "confidence"}
                and isinstance(value, (str, int, float, bool))
            }
            candidates.append(
                ObjectCandidate(
                    perception_ref=PerceptionRef(
                        provider="foundationpose",
                        object_id=object_id,
                        tracker_session_id=tracker_session_id,
                        observation_id=observation_id,
                    ),
                    category=str(tracker.get("category") or self.tracker_category),
                    attributes=attributes,
                    pose=pose,
                    confidence=(
                        float(tracker["confidence"])
                        if isinstance(tracker.get("confidence"), (int, float))
                        and not isinstance(tracker.get("confidence"), bool)
                        else None
                    ),
                )
            )
        return ObjectCandidateSet(
            observation_id=observation_id,
            provider="foundationpose",
            tracker_session_id=tracker_session_id,
            captured_at=received_at,
            valid_until=now + self.decision_ttl_s,
            candidates=tuple(candidates),
        )


class RosSemanticBackend:
    """Map semantic actions to the already deployed acceptance adapter."""

    def __init__(
        self,
        adapter: Any,
        *,
        observation_source: AdapterObservationSource,
        location_resolver: NineGridLocationResolver,
        selection_ttl_s: float = 120.0,
    ) -> None:
        self.adapter = adapter
        self.observation_source = observation_source
        self.location_resolver = location_resolver
        self.selection_ttl_s = float(selection_ttl_s)
        self.selection_store = ObjectSelectionStore()
        self._tokens: dict[str, SelectionToken] = {}
        self._holding_logical_object_id: Optional[str] = None

    def execute_semantic(
        self,
        skill_name: str,
        args: Mapping[str, Any],
        *,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if skill_name in {"observe_scene", "request_reobservation", "inspect_anomaly"}:
            channels = tuple(args.get("channels") or ("rgb", "overlay", "mask", "depth"))
            bundle = self.observation_source.capture(
                channels=channels,
                reason=str(args.get("reason") or skill_name),
            )
            return _ok("fresh observation captured", observation=bundle.to_dict())
        if skill_name == "list_objects":
            bundle = self.observation_source.capture(
                channels=("overlay",), reason="list current objects"
            )
            return _ok(
                "fresh object candidates listed",
                observation_id=bundle.observation_id,
                objects=[candidate.to_dict() for candidate in bundle.objects],
            )
        if skill_name == "move_to_location":
            return self._move_to_location(args, context)
        if skill_name == "select_object":
            return self._select_object(args, context)
        if skill_name == "pick_object":
            return self._pick_object(args, context)
        if skill_name == "place_at_location":
            return self._place_at_location(args, context)
        if skill_name == "verify_holding":
            return self._verify_holding(str(args["logical_object_id"]))
        if skill_name == "verify_placement":
            return self._verify_placement(str(args["logical_object_id"]))
        return _error(
            "UNSUPPORTED_CAPABILITY",
            f"semantic skill {skill_name!r} is not executed by the ROS data-plane bridge",
        )

    def _move_to_location(
        self, args: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        resolved = self.location_resolver.resolve(LocationRef.from_dict(args["location_ref"]))
        raw = self.adapter.execute(
            "move_to",
            {"target": dict(resolved.pose), "timeout_s": float(args["timeout_s"])},
            request_id=str(context["idempotency_key"]),
            goal_id=str(context.get("goal_id") or ""),
            step_id=str(context.get("node_id") or ""),
        )
        if not isinstance(raw, Mapping):
            return _error("MALFORMED_BACKEND_RESPONSE", "navigation returned no structured result")
        return {**dict(raw), "resolved_location": resolved.to_dict()}

    def _select_object(
        self, args: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        logical = ObjectRef.from_dict(args["object_ref"])
        requested = logical.perception_ref
        if requested is None:
            return _error("TARGET_NOT_SELECTED", "ObjectRef has no perception identity")
        fresh = self.observation_source.capture(
            channels=("overlay",), reason="pre-selection object-ID freshness check"
        )
        candidate_set = self.observation_source.candidate_set(fresh.observation_id)
        try:
            candidate = candidate_set.candidate(requested.object_id)
        except ObjectSelectionError as exc:
            return _error(exc.code, str(exc))
        if candidate.perception_ref.tracker_session_id != requested.tracker_session_id:
            return _error(
                "TRACKER_SESSION_MISMATCH",
                "FoundationPose restarted or reset after goal grounding; re-observation is required",
            )
        selector = getattr(self.adapter, "select_object_id_public", None)
        if not callable(selector):
            return _error(
                "UNSUPPORTED_CAPABILITY",
                "adapter does not support exact FoundationPose object-ID selection",
            )
        raw = selector(requested.object_id, step_id=str(context.get("node_id") or ""))
        matched = raw.get("matched_object_id", -1) if isinstance(raw, Mapping) else -1
        if not isinstance(raw, Mapping) or not raw.get("success") or matched != requested.object_id:
            _call_optional_mapping(self.adapter, "clear_object_id_selection")
            return _error(
                "OBJECT_ID_MISMATCH",
                "adapter did not confirm the exact requested FoundationPose object ID",
            )
        try:
            token = self.selection_store.select(
                logical,
                candidate_set,
                object_id=requested.object_id,
                ttl_s=self.selection_ttl_s,
            )
        except ObjectSelectionError as exc:
            _call_optional_mapping(self.adapter, "clear_object_id_selection")
            return _error(exc.code, str(exc))
        self._tokens[logical.logical_object_id] = token
        return _ok(
            "exact FoundationPose object ID selected",
            verification={
                "status": "passed",
                "evidence": {
                    "requested_object_id": requested.object_id,
                    "matched_object_id": matched,
                    "tracker_session_id": requested.tracker_session_id,
                    "selection_epoch": token.selection_epoch,
                },
                "message": "local pose filter is bound to the requested object ID",
            },
            selection_token=token.to_dict(),
        )

    def _pick_object(
        self, args: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        logical_id = str(args["logical_object_id"])
        token = self._tokens.get(logical_id)
        if token is None:
            return _error("TARGET_NOT_SELECTED", "pick requires a current selection token")
        if not token.is_fresh():
            _call_optional_mapping(self.adapter, "clear_object_id_selection")
            self._tokens.pop(logical_id, None)
            return _error("SELECTION_STALE", "object selection token expired")
        wait_pose = getattr(self.adapter, "wait_fp_pose_ready", None)
        pose_identity = getattr(self.adapter, "get_selected_pose_identity", None)
        if not callable(wait_pose) or not callable(pose_identity):
            _call_optional_mapping(self.adapter, "clear_object_id_selection")
            self._tokens.pop(logical_id, None)
            return _error(
                "UNSUPPORTED_CAPABILITY", "adapter cannot prove a fresh selected pose"
            )
        try:
            timeout_s = min(float(args["timeout_s"]), self.selection_ttl_s)
            if not wait_pose(min_wait_s=0.1, timeout_s=timeout_s):
                return _error(
                    "POSE_NOT_READY", "fresh pose for selected object was not observed"
                )
            identity = pose_identity()
            if not isinstance(identity, Mapping):
                return _error("POSE_NOT_READY", "selected pose identity is unavailable")
            pose_ref = PerceptionRef.from_dict(identity)
            try:
                self.selection_store.validate_pick_binding(token, pose_ref)
            except ObjectSelectionError as exc:
                return _error(exc.code, str(exc))
            raw = self.adapter.execute(
                "pick",
                {"object_id": logical_id, "timeout_s": float(args["timeout_s"])},
                request_id=str(context["idempotency_key"]),
                goal_id=str(context.get("goal_id") or ""),
                step_id=str(context.get("node_id") or ""),
            )
        finally:
            _call_optional_mapping(self.adapter, "clear_object_id_selection")
            self._tokens.pop(logical_id, None)
        if isinstance(raw, Mapping) and raw.get("status") == "ok":
            self._holding_logical_object_id = logical_id
        return (
            dict(raw)
            if isinstance(raw, Mapping)
            else _error("MALFORMED_BACKEND_RESPONSE", "pick returned no structured result")
        )

    def _place_at_location(
        self, args: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        logical_id = str(args["logical_object_id"])
        if self._holding_logical_object_id != logical_id:
            return _error(
                "PRECONDITION_FAILED", "local runtime cannot prove it is holding this object"
            )
        resolved = self.location_resolver.resolve(LocationRef.from_dict(args["location_ref"]))
        raw = self.adapter.execute(
            "place",
            {"target": dict(resolved.pose), "timeout_s": float(args["timeout_s"])},
            request_id=str(context["idempotency_key"]),
            goal_id=str(context.get("goal_id") or ""),
            step_id=str(context.get("node_id") or ""),
        )
        if not isinstance(raw, Mapping):
            return _error("MALFORMED_BACKEND_RESPONSE", "place returned no structured result")
        if isinstance(raw, Mapping) and raw.get("status") == "ok":
            self._holding_logical_object_id = None
        return {**dict(raw), "resolved_location": resolved.to_dict()}

    def _verify_holding(self, logical_id: str) -> Mapping[str, Any]:
        state = _call_mapping(self.adapter, "get_robot_state")
        passed = (
            self._holding_logical_object_id == logical_id
            and state.get("hold_pose_active") is True
        )
        if not passed:
            return _error(
                "VERIFICATION_FAILED", "gateway does not confirm the requested payload is held"
            )
        return _ok(
            "holding verified",
            verification={
                "status": "passed",
                "evidence": {"logical_object_id": logical_id, "gateway": state},
            },
        )

    def _verify_placement(self, logical_id: str) -> Mapping[str, Any]:
        state = _call_mapping(self.adapter, "get_robot_state")
        passed = (
            self._holding_logical_object_id is None
            and state.get("box_released") is True
            and str(state.get("posture_state") or "").lower() == "stand"
        )
        if not passed:
            return _error(
                "VERIFICATION_FAILED", "gateway does not confirm release in standing posture"
            )
        return _ok(
            "placement verified",
            verification={
                "status": "passed",
                "evidence": {"logical_object_id": logical_id, "gateway": state},
            },
        )


def build_shared_dispatcher(
    adapter: Any,
    backend: RosSemanticBackend,
    *,
    robot_id: str = "active",
) -> SkillDispatcher:
    """One writer for legacy developer tools and semantic autonomy actions."""
    legacy = build_skill_registry(adapter, mode="legacy-direct")
    registry = SkillRegistry()
    for definition in legacy.definitions():
        # The Web compatibility tools remain registered, but the cloud planner
        # sees only the semantic Phase 5 vocabulary.
        hidden = replace(definition, planner_visible=False)
        handler = legacy.handler(definition.name, definition.version)
        assert handler is not None
        registry.register(hidden, handler)
    semantic_handler = SemanticSkillHandler(backend)
    for definition in agent_skill_definitions():
        registry.register(definition, semantic_handler)
    dispatcher = SkillDispatcher(
        registry,
        mode="legacy-direct",
        audit_enabled=True,
        composite_pick=False,
    )
    dispatcher.default_robot_id = robot_id
    return dispatcher


def _call_mapping(target: Any, method_name: str) -> dict[str, Any]:
    method = getattr(target, method_name, None)
    if not callable(method):
        return {}
    value = method()
    return dict(value) if isinstance(value, Mapping) else {}


def _call_optional_mapping(target: Any, method_name: str) -> Optional[dict[str, Any]]:
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    value = method()
    return dict(value) if isinstance(value, Mapping) else None


def _tracker_is_candidate(tracker: Mapping[str, Any]) -> bool:
    state = tracker.get("state", 1)
    if isinstance(state, str):
        return state.strip().lower() not in {"lost", "paused", "unavailable", "0"}
    return isinstance(state, (int, float)) and not isinstance(state, bool) and state >= 1


def _tracker_pose(tracker: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    raw_pose = tracker.get("pose")
    if isinstance(raw_pose, Mapping):
        return dict(raw_pose)
    values = (tracker.get("x"), tracker.get("y"), tracker.get("z"))
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return None
    return {
        "x": float(values[0]),
        "y": float(values[1]),
        "z": float(values[2]),
        "frame_id": str(tracker.get("frame_id") or "unknown"),
    }


def _unknowns_to_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _unknowns_to_none(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unknowns_to_none(item) for item in value]
    if isinstance(value, str) and value in {"--", "unknown", "UNKNOWN"}:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _ok(message: str, **values: Any) -> dict[str, Any]:
    verification = values.pop("verification", None)
    result = {"status": "ok", "message": message, **values}
    if verification is not None:
        result["verification"] = verification
    return result


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error_code": code, "message": message}
