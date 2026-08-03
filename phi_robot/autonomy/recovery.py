"""Autonomous dropped-payload recovery with stop-before-cloud invariants."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from .contracts import ActionLifecycle, MissionLifecycle, PlanGraph, RevisionKind
from .goals import GoalSpec
from .model_gateway import ModelDecision, ModelGateway
from .objects import ObjectLineageRecord, ObjectRef, ObjectSelectionStore, PerceptionRef
from .observations import DropEvent
from .planner_agent import ObservationSource, PlannerAgent
from .world_state import StateDimension


RECOVERY_PROMPT_VERSION = "transport-drop-recovery-v1"


class RecoveryState(str, Enum):
    CARRYING = "carrying"
    DROP_SUSPECTED = "drop_suspected"
    INTERRUPT_REQUESTED = "interrupt_requested"
    STOP_CONFIRMED = "stop_confirmed"
    DIAGNOSING = "diagnosing"
    DROP_CONFIRMED = "drop_confirmed"
    FALSE_ALARM = "false_alarm"
    UNCERTAIN = "uncertain"
    OBJECT_REACQUIRED = "object_reacquired"
    RECOVERY_PLAN_READY = "recovery_plan_ready"
    REPICKING = "repicking"
    HOLDING_VERIFIED = "holding_verified"
    NEW_NAVIGATION = "new_navigation"
    RESUMED = "resumed"
    INTERVENTION_REQUIRED = "intervention_required"


@dataclass(frozen=True)
class RecoveryOutcome:
    mission_id: str
    event_id: str
    state: RecoveryState
    classification: Optional[str] = None
    recovery_revision_id: Optional[str] = None
    message: str = ""
    snapshot: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "event_id": self.event_id,
            "state": self.state.value,
            "classification": self.classification,
            "recovery_revision_id": self.recovery_revision_id,
            "message": self.message,
            "snapshot": dict(self.snapshot) if self.snapshot is not None else None,
        }


class DropRecoveryCoordinator:
    """Coordinates one recovery plan per mission/drop event.

    ``handle_drop`` issues the local interrupt synchronously before its first
    observation/model call.  Cloud failure therefore leaves the robot stopped
    and transitions to intervention rather than delaying or bypassing stop.
    """

    def __init__(
        self,
        *,
        supervisor: Any,
        gateway: ModelGateway,
        provider_name: str,
        observation_source: ObservationSource,
        plan_validator: PlannerAgent,
        selection_store: Optional[ObjectSelectionStore] = None,
        channels: tuple[str, ...] = ("rgb", "overlay", "mask", "depth", "drop"),
        timeout_s: float = 30.0,
        action_drain_timeout_s: float = 10.0,
        max_reobservations: int = 2,
        max_recovery_attempts: int = 2,
    ) -> None:
        self.supervisor = supervisor
        self.gateway = gateway
        self.provider_name = provider_name
        self.observation_source = observation_source
        self.plan_validator = plan_validator
        self.selection_store = selection_store or ObjectSelectionStore()
        self.channels = channels
        self.timeout_s = timeout_s
        self.action_drain_timeout_s = action_drain_timeout_s
        self.max_reobservations = max(0, int(max_reobservations))
        self.max_recovery_attempts = max(1, int(max_recovery_attempts))
        self._guard = threading.RLock()
        self._active: dict[str, str] = {}

    def handle_drop(
        self,
        mission_id: str,
        event: DropEvent | Mapping[str, Any],
        *,
        background: bool = True,
        replay_keys: Optional[Mapping[str, str]] = None,
    ) -> RecoveryOutcome:
        event_data = event.to_dict() if isinstance(event, DropEvent) else dict(event)
        event_id = str(event_data.get("event_id") or "")
        if not event_id:
            raise ValueError("drop event requires event_id")
        if not self._drop_window_armed(mission_id):
            self.supervisor.ledger.append_event(
                mission_id,
                "drop_ignored_unarmed",
                {"event_id": event_id, "reason": "robot is not holding during active transport"},
            )
            return RecoveryOutcome(
                mission_id=mission_id,
                event_id=event_id,
                state=RecoveryState.CARRYING,
                message="drop detector was not armed for the current state",
                snapshot=self.supervisor.snapshot(mission_id),
            )

        with self._guard:
            active_event = self._active.get(mission_id)
            if active_event is not None:
                self.supervisor.ledger.append_event(
                    mission_id,
                    "drop_recovery_deduplicated",
                    {"event_id": event_id, "active_event_id": active_event},
                )
                return RecoveryOutcome(
                    mission_id=mission_id,
                    event_id=event_id,
                    state=RecoveryState.DIAGNOSING,
                    message=f"recovery already active for {active_event}",
                    snapshot=self.supervisor.snapshot(mission_id),
                )
            self._active[mission_id] = event_id

        try:
            self._state(mission_id, event_id, RecoveryState.DROP_SUSPECTED, event_data)
            self._state(mission_id, event_id, RecoveryState.INTERRUPT_REQUESTED, {})
            receipt = self.supervisor.record_drop_suspected(mission_id, event=event_data)
            if not receipt.confirmed:
                return self._intervention(
                    mission_id,
                    event_id,
                    f"physical stop was not confirmed: {receipt.message}",
                    release=True,
                )
            self._state(
                mission_id,
                event_id,
                RecoveryState.STOP_CONFIRMED,
                {"stop_receipt": receipt.to_dict()},
            )
            try:
                self.supervisor.wait(mission_id, timeout=self.action_drain_timeout_s)
            except TimeoutError:
                return self._intervention(
                    mission_id,
                    event_id,
                    "normal action did not drain after confirmed stop",
                    release=True,
                )

            goal = _goal_from_snapshot(self.supervisor.snapshot(mission_id))
            decision, observation, classification = self._diagnose(
                mission_id,
                event_id,
                event_data,
                goal,
                replay_key=(replay_keys or {}).get("diagnose_drop"),
            )
            if classification in {"uncertain", "camera_failure"}:
                return self._intervention(
                    mission_id,
                    event_id,
                    f"drop diagnosis requires intervention: {classification}",
                    classification=classification,
                    release=True,
                )
            state = (
                RecoveryState.DROP_CONFIRMED
                if classification == "confirmed_drop"
                else RecoveryState.FALSE_ALARM
            )
            self._state(
                mission_id,
                event_id,
                state,
                {"model_decision_id": decision.decision_id, "classification": classification},
            )
            graph = self._recovery_graph(
                mission_id,
                event_id,
                goal,
                observation,
                decision,
                classification,
            )
            revision_id = self.supervisor.revise_plan(
                mission_id,
                graph,
                kind=RevisionKind.RECOVERY,
                reason=f"{classification} recovery for drop event {event_id}",
            )
            self._state(
                mission_id,
                event_id,
                RecoveryState.RECOVERY_PLAN_READY,
                {"plan_revision_id": revision_id, "classification": classification},
            )

            if background:
                worker = threading.Thread(
                    target=self._run_attempts_guarded,
                    kwargs={
                        "mission_id": mission_id,
                        "event_id": event_id,
                        "goal": goal,
                        "classification": classification,
                        "replay_keys": dict(replay_keys or {}),
                    },
                    name=f"drop-recovery-{mission_id[-8:]}",
                    daemon=True,
                )
                worker.start()
                return RecoveryOutcome(
                    mission_id=mission_id,
                    event_id=event_id,
                    state=RecoveryState.RECOVERY_PLAN_READY,
                    classification=classification,
                    recovery_revision_id=revision_id,
                    message="recovery execution started in background",
                    snapshot=self.supervisor.snapshot(mission_id),
                )
            return self._run_attempts(
                mission_id=mission_id,
                event_id=event_id,
                goal=goal,
                classification=classification,
                replay_keys=dict(replay_keys or {}),
            )
        except Exception as exc:
            return self._intervention(
                mission_id,
                event_id,
                f"recovery coordinator failed safely: {exc}",
                release=True,
            )

    def _run_attempts_guarded(self, **kwargs: Any) -> None:
        try:
            self._run_attempts(**kwargs)
        except Exception as exc:
            self._intervention(
                kwargs["mission_id"],
                kwargs["event_id"],
                f"background recovery failed safely: {exc}",
                release=True,
            )

    def _run_attempts(
        self,
        *,
        mission_id: str,
        event_id: str,
        goal: GoalSpec,
        classification: str,
        replay_keys: Mapping[str, str],
    ) -> RecoveryOutcome:
        for attempt in range(1, self.max_recovery_attempts + 1):
            self._state(
                mission_id,
                event_id,
                (
                    RecoveryState.REPICKING
                    if classification == "confirmed_drop"
                    else RecoveryState.HOLDING_VERIFIED
                ),
                {"attempt": attempt, "classification": classification},
            )
            snapshot = self.supervisor.start_recovery(mission_id, background=False)
            status = snapshot["mission"]["status"]
            if status == MissionLifecycle.COMPLETED.value:
                self._state(
                    mission_id,
                    event_id,
                    RecoveryState.HOLDING_VERIFIED,
                    {"attempt": attempt},
                )
                self._state(
                    mission_id,
                    event_id,
                    RecoveryState.NEW_NAVIGATION,
                    {"fresh_navigation_invocation": True},
                )
                self._state(
                    mission_id,
                    event_id,
                    RecoveryState.RESUMED,
                    {"original_destination": goal.destination.to_dict()},
                )
                self._release(mission_id, event_id)
                return RecoveryOutcome(
                    mission_id=mission_id,
                    event_id=event_id,
                    state=RecoveryState.RESUMED,
                    classification=classification,
                    recovery_revision_id=snapshot["mission"]["active_revision_id"],
                    message="drop recovery completed the original destination",
                    snapshot=snapshot,
                )
            if attempt >= self.max_recovery_attempts:
                break
            failed = _last_failed_invocation(snapshot)
            if failed is None or failed["skill_name"] not in {
                "select_object", "pick_object", "verify_holding"
            }:
                break
            self._state(
                mission_id,
                event_id,
                RecoveryState.DIAGNOSING,
                {"attempt": attempt + 1, "previous_failure": failed},
            )
            decision, observation, next_classification = self._diagnose(
                mission_id,
                event_id,
                {"recovery_failure": failed, "attempt": attempt + 1},
                goal,
                task="recover_after_failure",
                replay_key=replay_keys.get(f"retry_{attempt}"),
            )
            if next_classification != "confirmed_drop":
                break
            graph = self._recovery_graph(
                mission_id,
                event_id,
                goal,
                observation,
                decision,
                next_classification,
            )
            self.supervisor.revise_plan(
                mission_id,
                graph,
                kind=RevisionKind.RECOVERY,
                reason=f"evidence-based recovery attempt {attempt + 1}",
            )
            # A known failed pick is not an unknown physical action; the new
            # graph uses new node IDs and a fresh selection epoch.
            self.supervisor._set_status(mission_id, MissionLifecycle.RECOVERING)

        return self._intervention(
            mission_id,
            event_id,
            "recovery attempt budget exhausted or failure was unsafe to retry",
            classification=classification,
            release=True,
        )

    def _diagnose(
        self,
        mission_id: str,
        event_id: str,
        event_data: Mapping[str, Any],
        goal: GoalSpec,
        *,
        task: str = "diagnose_drop",
        replay_key: Optional[str] = None,
    ) -> tuple[ModelDecision, Any, str]:
        self._state(mission_id, event_id, RecoveryState.DIAGNOSING, {"task": task})
        attempts = 0
        while True:
            observation = self.observation_source.capture(
                channels=self.channels,
                reason=f"{task} for event {event_id}",
            )
            current_world = int(self.observation_source.current_world_version())
            decision = self.gateway.decide(
                provider_name=self.provider_name,
                task=task,
                prompt=_recovery_prompt(
                    event_id=event_id,
                    event_data=event_data,
                    goal=goal,
                    snapshot=self.supervisor.snapshot(mission_id),
                    attempt=attempts,
                ),
                prompt_version=RECOVERY_PROMPT_VERSION,
                observation=observation,
                current_world_version=current_world,
                plan_revision_id=self.supervisor.ledger.active_revision(
                    mission_id
                ).plan_revision_id,
                timeout_s=self.timeout_s,
                replay_key=replay_key,
            )
            latest_world = int(self.observation_source.current_world_version())
            if latest_world != observation.world_version:
                if attempts >= self.max_reobservations:
                    return decision, observation, "uncertain"
                attempts += 1
                continue
            if decision.decision_type == "request_observation":
                if attempts >= self.max_reobservations:
                    return decision, observation, "uncertain"
                attempts += 1
                continue
            if decision.decision_type != "recover":
                raise ValueError(
                    f"drop diagnosis requires recover/request_observation, got {decision.decision_type}"
                )
            classification = str(decision.payload.get("classification") or "")
            if classification not in {
                "confirmed_drop", "false_alarm", "uncertain", "camera_failure"
            }:
                raise ValueError(f"invalid drop classification: {classification or '<missing>'}")
            return decision, observation, classification

    def _recovery_graph(
        self,
        mission_id: str,
        event_id: str,
        goal: GoalSpec,
        observation: Any,
        decision: ModelDecision,
        classification: str,
    ) -> PlanGraph:
        graph = self.plan_validator._validated_plan(decision, goal, observation)
        parent = self.supervisor.ledger.active_revision(mission_id).graph
        invocations = self.supervisor.ledger.list_invocations(mission_id)
        invoked_ids = {str(item["node_id"]) for item in invocations}
        for node_id in invoked_ids:
            if parent.node(node_id).to_dict() != graph.node(node_id).to_dict():
                raise ValueError(f"recovery plan did not preserve dispatched node {node_id}")
        superseded = parent.superseded_node_ids | {
            str(item["node_id"])
            for item in invocations
            if item["status"] != ActionLifecycle.SUCCEEDED.value
        }
        new_nodes = [node for node in graph.nodes if node.node_id not in invoked_ids]
        destination = goal.destination.to_dict()
        new_navigation = [
            node
            for node in new_nodes
            if node.skill_name == "move_to_location"
            and node.args.get("location_ref") == destination
        ]
        if not new_navigation:
            raise ValueError("recovery requires a new navigation invocation to the saved destination")

        verify_nodes = [node for node in new_nodes if node.skill_name == "verify_holding"]
        if not verify_nodes:
            raise ValueError("recovery requires fresh holding verification")
        if classification == "confirmed_drop":
            select_nodes = [node for node in new_nodes if node.skill_name == "select_object"]
            pick_nodes = [node for node in new_nodes if node.skill_name == "pick_object"]
            if not select_nodes or not pick_nodes:
                raise ValueError("confirmed drop requires reacquisition, fresh selection, and re-pick")
            selected_ref = ObjectRef.from_dict(select_nodes[-1].args["object_ref"])
            if selected_ref.logical_object_id != goal.object_ref.logical_object_id:
                raise ValueError("recovery selected a different logical object")
            if selected_ref.perception_ref is None or not any(
                item.perception_ref.identity == selected_ref.perception_ref.identity
                and item.perception_ref.observation_id == observation.observation_id
                for item in observation.objects
            ):
                raise ValueError("recovery object selection is not grounded in fresh evidence")
            self._record_lineage(
                mission_id,
                event_id,
                goal.object_ref,
                selected_ref,
                observation.observation_id,
            )
            self._state(
                mission_id,
                event_id,
                RecoveryState.OBJECT_REACQUIRED,
                {"object_ref": selected_ref.to_dict()},
            )
            if not any(
                _is_ancestor(graph, pick.node_id, verify.node_id)
                for pick in pick_nodes
                for verify in verify_nodes
            ):
                raise ValueError("holding verification must depend on the new re-pick")
        if not any(
            _is_ancestor(graph, verify.node_id, nav.node_id)
            for verify in verify_nodes
            for nav in new_navigation
        ):
            raise ValueError("new navigation must follow fresh holding verification")

        return PlanGraph(
            schema_version=graph.schema_version,
            plan_id=graph.plan_id,
            goal_id=graph.goal_id,
            nodes=graph.nodes,
            metadata={
                **dict(graph.metadata),
                "superseded_node_ids": sorted(superseded),
                "recovery_event_id": event_id,
                "recovery_classification": classification,
                "original_destination": destination,
                "requires_fresh_selection_epoch": classification == "confirmed_drop",
            },
        )

    def _record_lineage(
        self,
        mission_id: str,
        event_id: str,
        previous: ObjectRef,
        current: ObjectRef,
        observation_id: str,
    ) -> None:
        if previous.perception_ref is None or current.perception_ref is None:
            return
        if previous.perception_ref.identity == current.perception_ref.identity:
            return
        record = ObjectLineageRecord(
            logical_object_id=previous.logical_object_id,
            previous_ref=previous.perception_ref,
            new_ref=current.perception_ref,
            reason=f"reidentified after transport drop {event_id}",
            evidence_observation_ids=(observation_id,),
        )
        self.selection_store.add_lineage(record)
        self.supervisor.ledger.append_event(
            mission_id,
            "object_lineage_created",
            record.to_dict(),
        )

    def _drop_window_armed(self, mission_id: str) -> bool:
        snapshot = self.supervisor.snapshot(mission_id)
        mission = snapshot["mission"]
        if mission["status"] != MissionLifecycle.RUNNING.value:
            return False
        dimensions = snapshot["world_state"]["dimensions"]
        if dimensions["payload"]["value"] != "holding":
            return False
        if dimensions["motion"]["value"] != "moving":
            return False
        node_id = mission.get("current_node_id")
        if not node_id:
            return False
        try:
            node = self.supervisor.ledger.active_revision(mission_id).graph.node(node_id)
        except KeyError:
            return False
        return node.skill_name in {"move_to", "move_to_location"}

    def _state(
        self,
        mission_id: str,
        event_id: str,
        state: RecoveryState,
        evidence: Mapping[str, Any],
    ) -> None:
        self.supervisor.ledger.append_event(
            mission_id,
            "recovery_state_changed",
            {"event_id": event_id, "state": state.value, "evidence": dict(evidence)},
        )

    def _intervention(
        self,
        mission_id: str,
        event_id: str,
        message: str,
        *,
        classification: Optional[str] = None,
        release: bool,
    ) -> RecoveryOutcome:
        self._state(
            mission_id,
            event_id,
            RecoveryState.INTERVENTION_REQUIRED,
            {"message": message, "classification": classification},
        )
        current = self.supervisor.ledger.get_mission(mission_id)
        if current is not None and current["status"] != MissionLifecycle.INTERVENTION_REQUIRED.value:
            self.supervisor._set_status(
                mission_id,
                MissionLifecycle.INTERVENTION_REQUIRED,
                current_node_id=current.get("current_node_id"),
                last_error=message,
            )
        if release:
            self._release(mission_id, event_id)
        return RecoveryOutcome(
            mission_id=mission_id,
            event_id=event_id,
            state=RecoveryState.INTERVENTION_REQUIRED,
            classification=classification,
            message=message,
            snapshot=self.supervisor.snapshot(mission_id),
        )

    def _release(self, mission_id: str, event_id: str) -> None:
        with self._guard:
            if self._active.get(mission_id) == event_id:
                self._active.pop(mission_id, None)


def _goal_from_snapshot(snapshot: Mapping[str, Any]) -> GoalSpec:
    raw = snapshot["mission"].get("metadata", {}).get("goal_spec")
    if not isinstance(raw, Mapping):
        raw = snapshot["active_revision"]["graph"].get("metadata", {}).get("goal_spec")
    if not isinstance(raw, Mapping):
        raise ValueError("drop recovery requires a persisted GoalSpec")
    return GoalSpec.from_dict(raw)


def _last_failed_invocation(snapshot: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    failed = [
        item
        for item in snapshot.get("invocations", [])
        if item.get("status") in {
            ActionLifecycle.FAILED.value, ActionLifecycle.CANCELLED.value
        }
    ]
    return dict(failed[-1]) if failed else None


def _is_ancestor(graph: PlanGraph, ancestor_id: str, node_id: str) -> bool:
    pending = list(graph.node(node_id).depends_on)
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == ancestor_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.node(current).depends_on)
    return False


def _recovery_prompt(
    *,
    event_id: str,
    event_data: Mapping[str, Any],
    goal: GoalSpec,
    snapshot: Mapping[str, Any],
    attempt: int,
) -> str:
    context = {
        "event_id": event_id,
        "event": dict(event_data),
        "goal_spec": goal.to_dict(),
        "mission": snapshot["mission"],
        "active_revision": snapshot["active_revision"],
        "world_state": snapshot["world_state"],
        "invocations": snapshot["invocations"],
        "interrupts": snapshot["interrupts"],
        "diagnostic_attempt": attempt,
    }
    return (
        "The robot has already issued and confirmed a local stop for a suspected box drop during "
        "transport. Classify the evidence as confirmed_drop, false_alarm, camera_failure, or "
        "uncertain. Request a fresh observation when useful. For confirmed_drop, reidentify the "
        "same logical object from the complete candidate list, explicitly select its fresh provider "
        "object_id, plan a feasible re-pick, verify holding, and create a NEW navigation invocation "
        "to the saved LocationRef. Never resume or retry the old navigation invocation. For a false "
        "alarm, verify holding and still create a new navigation invocation after the confirmed stop. "
        "Return decision_type='recover', payload.classification, and payload.plan_graph. Preserve every "
        "dispatched node byte-for-byte, list failed/cancelled retained nodes in "
        "metadata.superseded_node_ids, use new node IDs, and do not use fixed sleeps or unconditional "
        "step-back motions.\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )
