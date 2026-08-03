"""Compatibility projection between legacy MissionRecord and PlanGraph."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional

from phi_robot.mission_service import MissionService
from phi_robot.models import MissionRecord

from .contracts import (
    ActionLifecycle,
    ActionTimeouts,
    MissionLifecycle,
    PlanGraph,
    PlanNode,
)


def legacy_mission_plan(record: MissionRecord) -> PlanGraph:
    """Translate once at the execution boundary; never mutate legacy steps."""

    nodes: list[PlanNode] = []
    for index, step in enumerate(record.plan):
        try:
            operation_s = float(step.args.get("timeout_s", 30.0))
        except (TypeError, ValueError):
            operation_s = 30.0
        operation_s = max(operation_s, 0.001)
        annotations = {
            **dict(step.annotations),
            "legacy_step_id": step.step_id,
            "legacy_step_index": index,
            "legacy_execution_epoch": record.execution_epoch,
        }
        nodes.append(
            PlanNode(
                node_id=f"epoch-{record.execution_epoch}:{step.step_id}",
                skill_name=step.tool,
                skill_version=step.skill_version,
                args=dict(step.args),
                annotations=annotations,
                preconditions=(),
                success_evidence=("verification.status=passed",),
                timeouts=ActionTimeouts(
                    dispatch_s=2.0,
                    operation_s=operation_s,
                    caller_wait_s=operation_s + 5.0,
                ),
            )
        )
    return PlanGraph.sequential(
        goal_id=record.goal_id,
        plan_id=f"legacy-{record.mission_id}-epoch-{record.execution_epoch}",
        nodes=nodes,
        metadata={
            "source": "legacy_fixed_mission",
            "scene_id": record.scene_id,
            "scene_version": record.scene_version,
            "stock_layout_version": record.stock_layout_version,
            "execution_epoch": record.execution_epoch,
        },
    )


class LegacyMissionProjector:
    """Mirror authoritative supervisor progress into existing HTTP records."""

    def __init__(self, service: MissionService) -> None:
        self.service = service
        self._indices: dict[str, dict[str, int]] = {}

    def register(self, mission_id: str, graph: PlanGraph) -> None:
        self._indices[mission_id] = {
            node.node_id: int(node.annotations["legacy_step_index"])
            for node in graph.nodes
            if "legacy_step_index" in node.annotations
        }

    def mission_status_changed(
        self,
        mission_id: str,
        status: MissionLifecycle,
        *,
        current_node_id: Optional[str],
        last_error: Optional[str],
    ) -> None:
        record = self.service.get_mission(mission_id)
        if record is None:
            return
        index = record.current_step_index
        if current_node_id is not None:
            index = self._indices.get(mission_id, {}).get(current_node_id, index)
        if status == MissionLifecycle.COMPLETED:
            index = len(record.plan)
        projected = replace(
            record,
            status=status.value,
            current_step_index=index,
            last_error=last_error,
            pause_requested=False if status != MissionLifecycle.RUNNING else record.pause_requested,
            abort_requested=False if status != MissionLifecycle.RUNNING else record.abort_requested,
        )
        self.service.store.update(mission_id, projected)

    def node_started(self, mission_id: str, node: PlanNode, invocation_id: str) -> None:
        record = self.service.get_mission(mission_id)
        if record is None:
            return
        index = self._indices.get(mission_id, {}).get(node.node_id)
        if index is None:
            return
        if record.current_step_index != index:
            self.service.store.update(
                mission_id,
                replace(record, current_step_index=index, status="running"),
            )
        self.service.mark_step_running(mission_id)

    def node_finished(
        self,
        mission_id: str,
        node: PlanNode,
        lifecycle: ActionLifecycle,
        result: Mapping[str, Any],
    ) -> None:
        record = self.service.get_mission(mission_id)
        if record is None:
            return
        index = self._indices.get(mission_id, {}).get(node.node_id)
        if index is None:
            return
        if record.current_step_index != index:
            self.service.store.update(
                mission_id,
                replace(record, current_step_index=index),
            )
        legacy = _legacy_result(lifecycle, result, node.node_id)
        if lifecycle == ActionLifecycle.SUCCEEDED:
            self.service.mark_step_succeeded(mission_id, legacy, attempt=1)
            self.service.advance_step(mission_id)
        else:
            self.service.mark_step_failed(
                mission_id,
                str(legacy.get("error_code") or lifecycle.value.upper()),
                str(legacy.get("message") or f"action {lifecycle.value}"),
                result_dict=legacy,
                attempt=1,
            )


def _legacy_result(
    lifecycle: ActionLifecycle,
    result: Mapping[str, Any],
    step_id: str,
) -> dict[str, Any]:
    error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
    supervisor_error = (
        result.get("supervisor_error")
        if isinstance(result.get("supervisor_error"), Mapping)
        else {}
    )
    output = result.get("output") if isinstance(result.get("output"), Mapping) else {}
    succeeded = lifecycle == ActionLifecycle.SUCCEEDED
    projected = {
        "status": "ok" if succeeded else "error",
        "error_code": None if succeeded else str(
            supervisor_error.get("code") or error.get("code") or lifecycle.value.upper()
        ),
        "error_category": None if succeeded else str(error.get("category") or "execution"),
        "message": str(
            supervisor_error.get("message")
            or error.get("message")
            or output.get("message")
            or ("action completed" if succeeded else f"action {lifecycle.value}")
        ),
        "state": dict(result.get("state") or {}),
        "metrics": dict(result.get("metrics") or {}),
        "verification": dict(result.get("verification") or {}),
        "request_id": result.get("request_id"),
        "mission_id": result.get("mission_id"),
        "goal_id": result.get("goal_id"),
        "step_id": step_id,
        "invocation_id": result.get("invocation_id"),
        "outcome": result.get("outcome", lifecycle.value),
    }
    for key, value in output.items():
        projected.setdefault(key, value)
    return projected
