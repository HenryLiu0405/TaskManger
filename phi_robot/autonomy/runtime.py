"""Composition root enforcing one autonomy runtime per physical robot."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Optional

from phi_robot.models import MissionRecord
from phi_robot.skills import build_skill_dispatcher

from .interrupts import AdapterInterruptLane, InterruptLane
from .ledger import SupervisorLedger
from .legacy import LegacyMissionProjector, legacy_mission_plan
from .supervisor import ExecutionSupervisor, SupervisorObserver
from .contracts import MissionLifecycle, PlanGraph, PlanNode, RevisionKind


class RobotRuntime:
    """Own the dispatcher, supervisor, ledger, and interrupt lane for a robot."""

    def __init__(
        self,
        *,
        robot_id: str,
        adapter: Any,
        ledger_path: str | Path | None = None,
        ledger: Optional[SupervisorLedger] = None,
        dispatcher: Any = None,
        interrupt_lane: Optional[InterruptLane] = None,
        observer: Optional[SupervisorObserver] = None,
    ) -> None:
        if ledger is not None and ledger_path is not None:
            raise ValueError("provide ledger or ledger_path, not both")
        self.robot_id = robot_id
        self.adapter = adapter
        self.dispatcher = dispatcher or build_skill_dispatcher(adapter)
        default_path = Path(
            os.path.expanduser(f"~/.phi_robot/data/supervisor-{robot_id}.sqlite3")
        )
        self._owns_ledger = ledger is None
        self.ledger = ledger or SupervisorLedger(ledger_path or default_path)
        self.interrupt_lane = interrupt_lane or AdapterInterruptLane(adapter)
        self.observer = observer
        self.supervisor = ExecutionSupervisor(
            robot_id=robot_id,
            dispatcher=self.dispatcher,
            ledger=self.ledger,
            interrupt_lane=self.interrupt_lane,
            observer=observer,
        )

    def register_legacy_mission(self, record: MissionRecord) -> str:
        graph = legacy_mission_plan(record)
        if isinstance(self.observer, LegacyMissionProjector):
            self.observer.register(record.mission_id, graph)
        existing = self.ledger.get_mission(record.mission_id)
        if existing is not None:
            active = self.ledger.active_revision(record.mission_id)
            active_legacy_plan = active.graph.metadata.get("active_legacy_plan_id")
            if active.graph.plan_id == graph.plan_id or active_legacy_plan == graph.plan_id:
                return record.mission_id
            if existing["status"] != MissionLifecycle.COMPLETED.value:
                raise ValueError(
                    "legacy mission already has durable physical history; "
                    "only a completed mission may start a new reset epoch without reconciliation"
                )
            combined = _append_completed_epoch(active.graph, graph)
            self.ledger.append_revision(
                record.mission_id,
                combined,
                kind=RevisionKind.RESET,
                reason=f"explicit legacy reset to execution epoch {record.execution_epoch}",
            )
            self.ledger.set_mission_status(record.mission_id, MissionLifecycle.READY)
            return record.mission_id
        return self.supervisor.submit_plan(
            graph,
            request_id=record.request_id,
            mission_id=record.mission_id,
            metadata={
                "compatibility": "legacy_mission_record",
                "execution_epoch": record.execution_epoch,
            },
        )

    def start_legacy_mission(self, record: MissionRecord, *, background: bool = True) -> dict[str, Any]:
        mission_id = self.register_legacy_mission(record)
        return self.supervisor.start(mission_id, background=background)

    def close(self) -> None:
        if self._owns_ledger:
            self.ledger.close()


def _append_completed_epoch(previous: PlanGraph, current: PlanGraph) -> PlanGraph:
    """Keep completed physical history immutable and append a fresh epoch."""

    if previous.goal_id != current.goal_id:
        raise ValueError("legacy reset cannot silently replace the durable goal")
    appended: list[PlanNode] = []
    prior_node_id = previous.nodes[-1].node_id
    for index, node in enumerate(current.nodes):
        dependencies = node.depends_on
        if index == 0:
            dependencies = (prior_node_id,)
        appended.append(
            PlanNode(
                node_id=node.node_id,
                skill_name=node.skill_name,
                args=dict(node.args),
                skill_version=node.skill_version,
                depends_on=dependencies,
                annotations=dict(node.annotations),
                preconditions=node.preconditions,
                success_evidence=node.success_evidence,
                timeouts=node.timeouts,
            )
        )
    return PlanGraph(
        plan_id=f"{previous.plan_id}+{current.plan_id}",
        goal_id=previous.goal_id,
        nodes=tuple(previous.nodes) + tuple(appended),
        metadata={
            **dict(previous.metadata),
            "active_legacy_plan_id": current.plan_id,
            "execution_epoch": current.metadata.get("execution_epoch"),
        },
    )


class RobotRuntimeRegistry:
    """Process-local guard against multiple composition roots for one robot."""

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._runtimes: dict[str, RobotRuntime] = {}

    def register(self, runtime: RobotRuntime) -> None:
        with self._guard:
            existing = self._runtimes.get(runtime.robot_id)
            if existing is not None and existing is not runtime:
                raise ValueError(f"robot {runtime.robot_id} already has a RobotRuntime")
            self._runtimes[runtime.robot_id] = runtime

    def get(self, robot_id: str) -> RobotRuntime:
        with self._guard:
            runtime = self._runtimes.get(robot_id)
        if runtime is None:
            raise KeyError(f"unknown robot runtime: {robot_id}")
        return runtime

    def unregister(self, robot_id: str) -> None:
        with self._guard:
            self._runtimes.pop(robot_id, None)
