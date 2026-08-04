"""Persistent, one-writer execution supervisor for unattended plans."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol

from phi_robot.skills.models import (
    ErrorCategory,
    SkillContext,
    SkillOutcome,
    SkillRequest,
    SkillResult,
)

from .contracts import (
    ActionLifecycle,
    MissionLifecycle,
    PlanGraph,
    PlanNode,
    RevisionKind,
)
from .interrupts import InterruptLane, StopReceipt, UnavailableInterruptLane
from .ledger import SupervisorLedger
from .world_state import RobotWorldState, StateDimension


class SupervisorObserver(Protocol):
    """Optional compatibility projection; the ledger remains authoritative."""

    def mission_status_changed(
        self,
        mission_id: str,
        status: MissionLifecycle,
        *,
        current_node_id: Optional[str],
        last_error: Optional[str],
    ) -> None:
        ...

    def node_started(self, mission_id: str, node: PlanNode, invocation_id: str) -> None:
        ...

    def node_finished(
        self,
        mission_id: str,
        node: PlanNode,
        lifecycle: ActionLifecycle,
        result: Mapping[str, Any],
    ) -> None:
        ...


class NullSupervisorObserver:
    def mission_status_changed(
        self,
        mission_id: str,
        status: MissionLifecycle,
        *,
        current_node_id: Optional[str],
        last_error: Optional[str],
    ) -> None:
        return None

    def node_started(self, mission_id: str, node: PlanNode, invocation_id: str) -> None:
        return None

    def node_finished(
        self,
        mission_id: str,
        node: PlanNode,
        lifecycle: ActionLifecycle,
        result: Mapping[str, Any],
    ) -> None:
        return None


@dataclass(frozen=True)
class NodeBoundaryDecision:
    """Closed-loop directive evaluated while no new action is being dispatched."""

    action: str = "continue"  # continue | pause | replan | fail | intervention
    reason: str = ""
    replacement_graph: Optional[PlanGraph] = None
    revision_kind: RevisionKind = RevisionKind.REPLAN
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in {"continue", "pause", "replan", "fail", "intervention"}:
            raise ValueError(f"unknown boundary action: {self.action}")
        if self.action == "replan" and self.replacement_graph is None:
            raise ValueError("replan boundary decision requires replacement_graph")
        if self.action != "replan" and self.replacement_graph is not None:
            raise ValueError("replacement_graph is only valid for a replan decision")


class NodeBoundaryGate(Protocol):
    def after_node(
        self,
        supervisor: "ExecutionSupervisor",
        *,
        mission_id: str,
        revision: Any,
        node: PlanNode,
        lifecycle: ActionLifecycle,
        result: Mapping[str, Any],
        world_state: RobotWorldState,
    ) -> NodeBoundaryDecision:
        ...


@dataclass
class _MissionControl:
    pause_requested: threading.Event = field(default_factory=threading.Event)
    frozen: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    active_dispatcher_invocation_id: Optional[str] = None
    active_action_id: Optional[str] = None


class ExecutionSupervisor:
    """Execute one semantic plan at a time for one robot.

    The supervisor never retries a dispatched action.  Unknown results and
    restart residue transition the mission to ``reconciliation_required``.
    """

    def __init__(
        self,
        *,
        robot_id: str,
        dispatcher: Any,
        ledger: SupervisorLedger,
        interrupt_lane: Optional[InterruptLane] = None,
        observer: Optional[SupervisorObserver] = None,
        boundary_gate: Optional[NodeBoundaryGate] = None,
    ) -> None:
        if not robot_id:
            raise ValueError("robot_id must be non-empty")
        self.robot_id = robot_id
        self.dispatcher = dispatcher
        self.ledger = ledger
        self.interrupt_lane = interrupt_lane or UnavailableInterruptLane()
        self.observer: SupervisorObserver = observer or NullSupervisorObserver()
        self.boundary_gate = boundary_gate
        self._guard = threading.RLock()
        self._world_guard = threading.RLock()
        self._controls: dict[str, _MissionControl] = {}
        self._active_mission_id: Optional[str] = None
        self.recovered_mission_ids = self.ledger.recover_incomplete(robot_id=robot_id)
        if self.recovered_mission_ids:
            self._update_world(
                execution=("reconciliation_required", "supervisor.restart", {}),
                motion=(None, "supervisor.restart", {}),
            )

    def submit_plan(
        self,
        graph: PlanGraph,
        *,
        request_id: str,
        mission_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        mission_id = self.ledger.create_mission(
            robot_id=self.robot_id,
            request_id=request_id,
            graph=graph,
            mission_id=mission_id,
            metadata=metadata,
        )
        self._set_status(mission_id, MissionLifecycle.READY)
        return mission_id

    def revise_plan(
        self,
        mission_id: str,
        graph: PlanGraph,
        *,
        kind: RevisionKind = RevisionKind.REPLAN,
        reason: str,
    ) -> str:
        mission = self._require_mission(mission_id)
        if mission["status"] == MissionLifecycle.RUNNING.value:
            raise ValueError("freeze or pause a mission before replacing its plan tail")
        revision = self.ledger.append_revision(
            mission_id,
            graph,
            kind=kind,
            reason=reason,
        )
        return revision.plan_revision_id

    def start(self, mission_id: str, *, background: bool = True) -> dict[str, Any]:
        return self._start(
            mission_id,
            background=background,
            allowed_statuses={MissionLifecycle.READY.value, MissionLifecycle.PAUSED.value},
            recovery=False,
        )

    def start_recovery(self, mission_id: str, *, background: bool = True) -> dict[str, Any]:
        """Start only an explicit recovery revision from a confirmed stopped state."""

        mission = self._require_mission(mission_id)
        revision = self.ledger.active_revision(mission_id)
        if revision.kind != RevisionKind.RECOVERY:
            raise ValueError("start_recovery requires an active recovery plan revision")
        motion = self.ledger.load_world_state(self.robot_id).fact(StateDimension.MOTION)
        if motion.value != "stopped":
            raise ValueError("recovery motion requires a confirmed stopped world state")
        unresolved = {
            ActionLifecycle.PREPARED.value,
            ActionLifecycle.DISPATCHED.value,
            ActionLifecycle.UNKNOWN.value,
            ActionLifecycle.RECONCILIATION_REQUIRED.value,
        }
        if any(item["status"] in unresolved for item in self.ledger.list_invocations(mission_id)):
            raise ValueError("recovery cannot start while a prior physical action is unresolved")
        return self._start(
            mission_id,
            background=background,
            allowed_statuses={
                MissionLifecycle.RECOVERING.value,
                MissionLifecycle.FAILED.value,
                MissionLifecycle.PAUSED.value,
            },
            recovery=True,
        )

    def _start(
        self,
        mission_id: str,
        *,
        background: bool,
        allowed_statuses: set[str],
        recovery: bool,
    ) -> dict[str, Any]:
        mission = self._require_mission(mission_id)
        if mission["robot_id"] != self.robot_id:
            raise ValueError(f"mission belongs to robot {mission['robot_id']}")
        if mission["status"] not in allowed_statuses:
            raise ValueError(f"mission is {mission['status']}, cannot start")

        with self._guard:
            if self._active_mission_id is not None:
                active = self._controls.get(self._active_mission_id)
                if active is not None and active.thread is not None and active.thread.is_alive():
                    raise RuntimeError(
                        f"robot {self.robot_id} already runs mission {self._active_mission_id}"
                    )
            control = _MissionControl()
            self._controls[mission_id] = control
            self._active_mission_id = mission_id
            self._set_status(mission_id, MissionLifecycle.RUNNING)
            if recovery:
                self.ledger.append_event(
                    mission_id,
                    "recovery_execution_started",
                    {
                        "plan_revision_id": self.ledger.active_revision(
                            mission_id
                        ).plan_revision_id
                    },
                )
            if background:
                thread = threading.Thread(
                    target=self._run_guarded,
                    args=(mission_id, control),
                    name=f"supervisor-{self.robot_id}-{mission_id[-8:]}",
                    daemon=True,
                )
                control.thread = thread
                thread.start()
                return self.snapshot(mission_id)

        self._run_guarded(mission_id, control)
        return self.snapshot(mission_id)

    def wait(self, mission_id: str, timeout: Optional[float] = None) -> dict[str, Any]:
        with self._guard:
            control = self._controls.get(mission_id)
            thread = control.thread if control is not None else None
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError(f"mission {mission_id} did not reach a boundary before timeout")
        return self.snapshot(mission_id)

    def active_mission_id(self) -> Optional[str]:
        """Return the process-local mission currently owning this robot."""
        with self._guard:
            return self._active_mission_id

    def pause(self, mission_id: str) -> dict[str, Any]:
        mission = self._require_mission(mission_id)
        if mission["status"] != MissionLifecycle.RUNNING.value:
            raise ValueError(f"mission is {mission['status']}, cannot request pause")
        with self._guard:
            control = self._controls.get(mission_id)
            if control is None:
                raise RuntimeError("mission has no active local execution control")
            control.pause_requested.set()
        self.ledger.append_event(
            mission_id,
            "pause_requested",
            {"boundary_only": True, "physical_stop_confirmed": False},
        )
        return self.snapshot(mission_id)

    def request_stop(self, mission_id: str, *, reason: str) -> StopReceipt:
        """Freeze first, then use the independent local stop lane."""

        mission = self._require_mission(mission_id)
        with self._guard:
            control = self._controls.setdefault(mission_id, _MissionControl())
            control.frozen.set()
            dispatcher_invocation_id = control.active_dispatcher_invocation_id

        drop_suspected = reason.startswith("drop") or "drop" in reason.lower()
        updates: dict[str, tuple[Optional[str], str, Mapping[str, Any]]] = {
            "execution": ("cancel_requested", "supervisor.interrupt", {"reason": reason}),
            "motion": (None, "supervisor.interrupt", {"reason": reason}),
        }
        if drop_suspected:
            updates["payload"] = (
                "drop_suspected",
                "local.drop_detector",
                {"reason": reason},
            )
        self._update_world(**updates)
        self._set_status(
            mission_id,
            MissionLifecycle.RECOVERING,
            current_node_id=mission.get("current_node_id"),
            last_error=reason,
        )
        interrupt_id = self.ledger.begin_interrupt(mission_id, reason)

        cancellation_receipt: Optional[dict[str, Any]] = None
        if dispatcher_invocation_id:
            try:
                cancellation_receipt = self.dispatcher.cancel(
                    dispatcher_invocation_id
                ).to_dict()
            except Exception as exc:
                cancellation_receipt = {
                    "accepted": False,
                    "confirmed": False,
                    "message": str(exc),
                }

        receipt = self.interrupt_lane.stop_motion(
            robot_id=self.robot_id,
            mission_id=mission_id,
            reason=reason,
        )
        receipt_data = {
            **receipt.to_dict(),
            "dispatcher_cancellation": cancellation_receipt,
        }
        if receipt.confirmed:
            self.ledger.finish_interrupt(
                interrupt_id,
                status="stop_confirmed",
                receipt=receipt_data,
            )
            self._update_world(
                motion=("stopped", "interrupt_lane", receipt.evidence),
                execution=("idle", "interrupt_lane", {"interrupt_id": interrupt_id}),
            )
            self._set_status(
                mission_id,
                MissionLifecycle.RECOVERING if drop_suspected else MissionLifecycle.PAUSED,
                current_node_id=mission.get("current_node_id"),
                last_error=reason,
            )
        else:
            self.ledger.finish_interrupt(
                interrupt_id,
                status="stop_unconfirmed",
                receipt=receipt_data,
            )
            self._update_world(
                execution=(
                    "reconciliation_required",
                    "interrupt_lane",
                    {"interrupt_id": interrupt_id, "reason": receipt.message},
                ),
                motion=(None, "interrupt_lane", receipt.evidence),
            )
            self._set_status(
                mission_id,
                MissionLifecycle.INTERVENTION_REQUIRED,
                current_node_id=mission.get("current_node_id"),
                last_error=f"physical stop unconfirmed: {receipt.message}",
            )
        return receipt

    def record_drop_suspected(self, mission_id: str, *, event: Mapping[str, Any]) -> StopReceipt:
        self.ledger.append_event(mission_id, "drop_suspected", dict(event))
        return self.request_stop(mission_id, reason="drop_suspected")

    def reconcile_invocation(
        self,
        mission_id: str,
        invocation_id: str,
        *,
        resolution: ActionLifecycle,
        evidence: Mapping[str, Any],
        principal: str,
        trusted_local_principal: bool = False,
    ) -> dict[str, Any]:
        """Resolve an unknown physical result without re-dispatching it."""

        if not trusted_local_principal:
            raise PermissionError("reconciliation requires a trusted local principal")
        if not evidence:
            raise ValueError("reconciliation requires explicit physical evidence")
        mission = self._require_mission(mission_id)
        if mission["status"] != MissionLifecycle.RECONCILIATION_REQUIRED.value:
            raise ValueError(f"mission is {mission['status']}, not reconciliation_required")
        invocation = self.ledger.get_invocation(invocation_id)
        if invocation is None or invocation["mission_id"] != mission_id:
            raise KeyError(f"invocation {invocation_id} does not belong to mission {mission_id}")
        result = {
            "invocation_id": invocation_id,
            "outcome": resolution.value,
            "evidence": dict(evidence),
            "message": "physical outcome reconciled without dispatch",
        }
        reconciled = self.ledger.resolve_reconciliation(
            invocation_id,
            status=resolution,
            result=result,
            principal=principal,
        )
        revision = self.ledger.get_revision(str(invocation["plan_revision_id"]))
        node = revision.graph.node(str(invocation["node_id"]))
        self._safe_observer_call(
            "node_finished", mission_id, node, resolution, reconciled.get("result") or result
        )
        self._update_world_from_action(node, resolution, reconciled.get("result") or result)
        if resolution == ActionLifecycle.SUCCEEDED:
            self._set_status(mission_id, MissionLifecycle.READY)
        else:
            self._set_status(
                mission_id,
                MissionLifecycle.FAILED,
                current_node_id=node.node_id,
                last_error=f"reconciled action {resolution.value}",
            )
        return self.snapshot(mission_id)

    def snapshot(self, mission_id: str) -> dict[str, Any]:
        return self.ledger.snapshot(mission_id)

    def _run_guarded(self, mission_id: str, control: _MissionControl) -> None:
        try:
            self._execute_loop(mission_id, control)
        except Exception as exc:
            current = self.ledger.get_mission(mission_id)
            if current is not None and current["status"] not in {
                MissionLifecycle.RECONCILIATION_REQUIRED.value,
                MissionLifecycle.INTERVENTION_REQUIRED.value,
            }:
                self._set_status(
                    mission_id,
                    MissionLifecycle.FAILED,
                    current_node_id=current.get("current_node_id"),
                    last_error=f"supervisor error: {exc}",
                )
            self.ledger.append_event(
                mission_id,
                "supervisor_error",
                {"message": str(exc), "exception_type": type(exc).__name__},
            )
        finally:
            with self._guard:
                if self._active_mission_id == mission_id:
                    self._active_mission_id = None
                control.active_action_id = None
                control.active_dispatcher_invocation_id = None

    def _execute_loop(self, mission_id: str, control: _MissionControl) -> None:
        while True:
            mission = self._require_mission(mission_id)
            if control.frozen.is_set():
                return
            if control.pause_requested.is_set():
                self._set_status(
                    mission_id,
                    MissionLifecycle.PAUSED,
                    current_node_id=mission.get("current_node_id"),
                )
                return
            if mission["status"] != MissionLifecycle.RUNNING.value:
                return

            revision = self.ledger.active_revision(mission_id)
            completed = self.ledger.completed_node_ids(mission_id)
            if revision.graph.is_complete(completed):
                self._set_status(mission_id, MissionLifecycle.COMPLETED)
                return

            ready = revision.graph.ready_nodes(completed)
            if not ready:
                self._set_status(
                    mission_id,
                    MissionLifecycle.FAILED,
                    last_error="plan has no executable node and is not complete",
                )
                return
            node = ready[0]
            lifecycle, result = self._execute_node(
                mission_id,
                revision.plan_revision_id,
                node,
                control,
            )

            if (
                lifecycle
                in {ActionLifecycle.SUCCEEDED, ActionLifecycle.FAILED, ActionLifecycle.CANCELLED}
                and not control.frozen.is_set()
                and self.boundary_gate is not None
            ):
                if self._apply_boundary_gate(
                    mission_id,
                    revision,
                    node,
                    lifecycle,
                    result,
                    control,
                ):
                    continue
                return

            if lifecycle == ActionLifecycle.SUCCEEDED:
                if control.frozen.is_set():
                    return
                continue
            if lifecycle == ActionLifecycle.RECONCILIATION_REQUIRED:
                self._set_status(
                    mission_id,
                    MissionLifecycle.RECONCILIATION_REQUIRED,
                    current_node_id=node.node_id,
                    last_error=_result_message(result, "physical outcome requires reconciliation"),
                )
                return
            if lifecycle == ActionLifecycle.CANCELLED and control.frozen.is_set():
                return
            self._set_status(
                mission_id,
                MissionLifecycle.FAILED,
                current_node_id=node.node_id,
                last_error=_result_message(result, f"action {lifecycle.value}"),
            )
            return

    def _apply_boundary_gate(
        self,
        mission_id: str,
        revision: Any,
        node: PlanNode,
        lifecycle: ActionLifecycle,
        result: Mapping[str, Any],
        control: _MissionControl,
    ) -> bool:
        """Return true when the execution loop should continue."""

        try:
            decision = self.boundary_gate.after_node(
                self,
                mission_id=mission_id,
                revision=revision,
                node=node,
                lifecycle=lifecycle,
                result=result,
                world_state=self.ledger.load_world_state(self.robot_id),
            )
        except Exception as exc:
            self.ledger.append_event(
                mission_id,
                "boundary_gate_failed",
                {
                    "node_id": node.node_id,
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                    "safe_boundary": True,
                },
                node_id=node.node_id,
            )
            self._set_status(
                mission_id,
                MissionLifecycle.PAUSED,
                current_node_id=node.node_id,
                last_error=f"closed-loop decision unavailable: {exc}",
            )
            return False

        self.ledger.append_event(
            mission_id,
            "boundary_decision",
            {
                "node_id": node.node_id,
                "action_lifecycle": lifecycle.value,
                "decision": decision.action,
                "reason": decision.reason,
                "evidence": dict(decision.evidence),
            },
            node_id=node.node_id,
        )
        # A local interrupt may arrive while the cloud gate is evaluating.  Its
        # freeze/stop result always wins over a late model decision.
        if control.frozen.is_set():
            return False
        if decision.action == "continue":
            if lifecycle == ActionLifecycle.SUCCEEDED:
                return True
            self._set_status(
                mission_id,
                MissionLifecycle.FAILED,
                current_node_id=node.node_id,
                last_error=_result_message(result, f"action {lifecycle.value}"),
            )
            return False
        if decision.action == "replan":
            assert decision.replacement_graph is not None
            try:
                self.ledger.append_revision(
                    mission_id,
                    decision.replacement_graph,
                    kind=decision.revision_kind,
                    reason=decision.reason or "closed-loop plan-tail replacement",
                )
            except Exception as exc:
                self.ledger.append_event(
                    mission_id,
                    "plan_revision_rejected",
                    {
                        "node_id": node.node_id,
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    },
                    node_id=node.node_id,
                )
                self._set_status(
                    mission_id,
                    MissionLifecycle.PAUSED,
                    current_node_id=node.node_id,
                    last_error=f"unsafe plan revision rejected: {exc}",
                )
                return False
            return True
        if decision.action == "pause":
            self._set_status(
                mission_id,
                MissionLifecycle.PAUSED,
                current_node_id=node.node_id,
                last_error=decision.reason or None,
            )
            return False
        if decision.action == "intervention":
            self._set_status(
                mission_id,
                MissionLifecycle.INTERVENTION_REQUIRED,
                current_node_id=node.node_id,
                last_error=decision.reason or "closed-loop intervention required",
            )
            return False
        self._set_status(
            mission_id,
            MissionLifecycle.FAILED,
            current_node_id=node.node_id,
            last_error=decision.reason or "closed-loop agent aborted mission",
        )
        return False

    def _execute_node(
        self,
        mission_id: str,
        plan_revision_id: str,
        node: PlanNode,
        control: _MissionControl,
    ) -> tuple[ActionLifecycle, Mapping[str, Any]]:
        previous = self.ledger.node_invocation(mission_id, node.node_id)
        if previous is not None:
            status = ActionLifecycle(str(previous["status"]))
            if status == ActionLifecycle.SUCCEEDED:
                return status, previous.get("result") or {}
            if status in {
                ActionLifecycle.PREPARED,
                ActionLifecycle.DISPATCHED,
                ActionLifecycle.UNKNOWN,
                ActionLifecycle.RECONCILIATION_REQUIRED,
            }:
                return ActionLifecycle.RECONCILIATION_REQUIRED, previous.get("result") or {}
            return status, previous.get("result") or {}

        idempotency_key = f"mission:{mission_id}:node:{node.node_id}"
        action_id = f"action-{uuid.uuid4().hex}"
        invocation, created = self.ledger.prepare_invocation(
            mission_id=mission_id,
            plan_revision_id=plan_revision_id,
            node_id=node.node_id,
            skill_name=node.skill_name,
            skill_version=node.skill_version,
            idempotency_key=idempotency_key,
            args=node.args,
            annotations=node.annotations,
            invocation_id=action_id,
        )
        if not created:
            return ActionLifecycle(str(invocation["status"])), invocation.get("result") or {}

        with self._guard:
            control.active_action_id = action_id
        self._set_status(
            mission_id,
            MissionLifecycle.RUNNING,
            current_node_id=node.node_id,
        )
        self._safe_observer_call("node_started", mission_id, node, action_id)
        if node.skill_name in {"move_to", "move_to_location"}:
            self._update_world(
                motion=(
                    "moving",
                    "execution_supervisor",
                    {"mission_id": mission_id, "node_id": node.node_id},
                )
            )
        self.ledger.mark_dispatched(action_id)

        request = SkillRequest(
            skill_name=node.skill_name,
            version=node.skill_version,
            args=_operation_args(node),
            context=SkillContext(
                robot_id=self.robot_id,
                source="execution_supervisor",
                request_id=str(self._require_mission(mission_id)["request_id"]),
                goal_id=str(self._require_mission(mission_id)["goal_id"]),
                step_id=node.node_id,
                mission_id=mission_id,
                invocation_id=action_id,
                idempotency_key=idempotency_key,
                attempt=1,
            ),
            annotations=dict(node.annotations),
        )

        try:
            dispatcher_invocation_id = self.dispatcher.dispatch(request)
            with self._guard:
                control.active_dispatcher_invocation_id = dispatcher_invocation_id
            self.ledger.attach_dispatcher_invocation(action_id, dispatcher_invocation_id)
            result: SkillResult = self.dispatcher.wait(
                dispatcher_invocation_id,
                timeout=node.timeouts.caller_wait_s,
            )
            result_data = result.to_dict()
            lifecycle = _action_lifecycle(result)
            definition = self.dispatcher.registry.get(node.skill_name, node.skill_version)
            if (
                lifecycle == ActionLifecycle.SUCCEEDED
                and definition is not None
                and definition.risk_level in {"high", "critical"}
                and result.verification.status != "passed"
            ):
                lifecycle = ActionLifecycle.FAILED
                result_data = {
                    **result_data,
                    "supervisor_error": {
                        "code": "REQUIRED_VERIFICATION_MISSING",
                        "message": (
                            f"{node.skill_name} requires passed verification; "
                            f"got {result.verification.status}"
                        ),
                    },
                }
            if lifecycle == ActionLifecycle.UNKNOWN:
                lifecycle = ActionLifecycle.RECONCILIATION_REQUIRED
            note = (
                "caller wait expired or backend result was unknown after dispatch"
                if lifecycle == ActionLifecycle.RECONCILIATION_REQUIRED
                else None
            )
            self.ledger.finish_invocation(
                action_id,
                status=lifecycle,
                result=result_data,
                reconciliation_note=note,
            )
        except Exception as exc:
            lifecycle = ActionLifecycle.RECONCILIATION_REQUIRED
            result_data = {
                "invocation_id": action_id,
                "outcome": "unknown",
                "error": {
                    "code": "SUPERVISOR_DISPATCH_UNKNOWN",
                    "category": ErrorCategory.UNKNOWN.value,
                    "message": str(exc),
                    "details": {"exception_type": type(exc).__name__},
                },
            }
            try:
                self.ledger.finish_invocation(
                    action_id,
                    status=lifecycle,
                    result=result_data,
                    reconciliation_note="exception after durable dispatched marker",
                )
            except Exception:
                # The original exception remains evidence in the mission event;
                # a second physical dispatch is still prevented by the durable
                # dispatched row.
                pass
        finally:
            with self._guard:
                control.active_action_id = None
                control.active_dispatcher_invocation_id = None

        self._safe_observer_call(
            "node_finished", mission_id, node, lifecycle, result_data
        )
        if control.frozen.is_set():
            self.ledger.append_event(
                mission_id,
                "post_interrupt_action_result",
                {
                    "node_id": node.node_id,
                    "lifecycle": lifecycle.value,
                    "world_state_applied": False,
                    "reason": "interrupt freeze owns authoritative post-stop state",
                },
                node_id=node.node_id,
            )
        else:
            self._update_world_from_action(node, lifecycle, result_data)
        return lifecycle, result_data

    def _set_status(
        self,
        mission_id: str,
        status: MissionLifecycle,
        *,
        current_node_id: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        self.ledger.set_mission_status(
            mission_id,
            status,
            current_node_id=current_node_id,
            last_error=last_error,
        )
        mission_world_value = {
            MissionLifecycle.READY: "ready",
            MissionLifecycle.RUNNING: "running",
            MissionLifecycle.PAUSED: "paused",
            MissionLifecycle.RECOVERING: "recovering",
            MissionLifecycle.COMPLETED: "completed",
            MissionLifecycle.FAILED: "failed",
            MissionLifecycle.RECONCILIATION_REQUIRED: "failed",
            MissionLifecycle.INTERVENTION_REQUIRED: "failed",
        }[status]
        execution_value = {
            MissionLifecycle.RUNNING: "running",
            MissionLifecycle.RECONCILIATION_REQUIRED: "reconciliation_required",
            MissionLifecycle.INTERVENTION_REQUIRED: "reconciliation_required",
        }.get(status, "idle")
        self._update_world(
            mission=(mission_world_value, "execution_supervisor", {"mission_id": mission_id}),
            execution=(execution_value, "execution_supervisor", {"mission_id": mission_id}),
        )
        self._safe_observer_call(
            "mission_status_changed",
            mission_id,
            status,
            current_node_id=current_node_id,
            last_error=last_error,
        )

    def _update_world_from_action(
        self,
        node: PlanNode,
        lifecycle: ActionLifecycle,
        result: Mapping[str, Any],
    ) -> None:
        evidence = {
            "node_id": node.node_id,
            "skill_name": node.skill_name,
            "invocation_id": result.get("invocation_id"),
            "verification": result.get("verification"),
        }
        if lifecycle == ActionLifecycle.SUCCEEDED:
            updates: dict[str, tuple[Optional[str], str, Mapping[str, Any]]] = {}
            if node.skill_name in {"move_to", "move_to_location"}:
                updates["motion"] = ("arrived", "skill_result", evidence)
            elif node.skill_name in {"pick", "pick_object"}:
                updates["payload"] = ("holding", "skill_result", evidence)
            elif node.skill_name in {"place", "place_at_location"}:
                updates["payload"] = ("empty", "skill_result", evidence)
            if updates:
                self._update_world(**updates)
            return
        if lifecycle == ActionLifecycle.RECONCILIATION_REQUIRED:
            self._update_world(
                motion=(None, "skill_result", evidence),
                execution=("reconciliation_required", "skill_result", evidence),
            )

    def _update_world(
        self,
        **updates: tuple[Optional[str], str, Mapping[str, Any]],
    ) -> RobotWorldState:
        with self._world_guard:
            state = self.ledger.load_world_state(self.robot_id)
            for name, (value, source, evidence) in updates.items():
                state = state.update(
                    StateDimension(name),
                    value,
                    source=source,
                    evidence=evidence,
                )
            self.ledger.save_world_state(state)
            return state

    def _safe_observer_call(self, method: str, *args: Any, **kwargs: Any) -> None:
        try:
            getattr(self.observer, method)(*args, **kwargs)
        except Exception as exc:
            mission_id = str(args[0]) if args else ""
            if mission_id:
                self.ledger.append_event(
                    mission_id,
                    "observer_projection_failed",
                    {
                        "method": method,
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    },
                )

    def _require_mission(self, mission_id: str) -> dict[str, Any]:
        mission = self.ledger.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"unknown mission_id: {mission_id}")
        return mission


def _operation_args(node: PlanNode) -> dict[str, Any]:
    args = dict(node.args)
    if "timeout_s" in args:
        args["timeout_s"] = node.timeouts.operation_s
    return args


def _action_lifecycle(result: SkillResult) -> ActionLifecycle:
    return {
        SkillOutcome.SUCCEEDED: ActionLifecycle.SUCCEEDED,
        SkillOutcome.REJECTED: ActionLifecycle.FAILED,
        SkillOutcome.FAILED: ActionLifecycle.FAILED,
        SkillOutcome.CANCELLED: ActionLifecycle.CANCELLED,
        SkillOutcome.UNKNOWN: ActionLifecycle.UNKNOWN,
    }[result.outcome]


def _result_message(result: Mapping[str, Any], fallback: str) -> str:
    error = result.get("error")
    if isinstance(error, Mapping) and error.get("message"):
        return str(error["message"])
    supervisor_error = result.get("supervisor_error")
    if isinstance(supervisor_error, Mapping) and supervisor_error.get("message"):
        return str(supervisor_error["message"])
    output = result.get("output")
    if isinstance(output, Mapping) and output.get("message"):
        return str(output["message"])
    return fallback
