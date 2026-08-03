"""Application service for natural-language tasks, monitoring, replay, and intervention."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .evaluation import export_replay_bundle, mission_metrics
from .planner_agent import AgentTask, PlannerAgent


class AutonomyService:
    def __init__(
        self,
        *,
        planner: PlannerAgent,
        model_ledger: Any,
        recovery: Any = None,
    ) -> None:
        self.planner = planner
        self.supervisor = planner.supervisor
        self.model_ledger = model_ledger
        self.recovery = recovery

    def submit_instruction(
        self,
        instruction: str,
        *,
        request_id: Optional[str] = None,
        background: bool = True,
    ) -> dict[str, Any]:
        task = self.planner.submit_instruction(
            instruction,
            request_id=request_id,
            background=background,
        )
        return {
            "task": task.to_dict(),
            "snapshot": self.snapshot(task.mission_id),
        }

    def snapshot(self, mission_id: str) -> dict[str, Any]:
        snapshot = self.supervisor.snapshot(mission_id)
        revisions = [
            revision.to_dict()
            for revision in self.supervisor.ledger.list_revisions(mission_id)
        ]
        replay = export_replay_bundle(
            self.supervisor.ledger,
            mission_id,
            model_ledger=self.model_ledger,
        )
        return {
            **snapshot,
            "revisions": revisions,
            "model_calls": [dict(item) for item in replay.model_calls],
        }

    def pause(self, mission_id: str, *, principal: str) -> dict[str, Any]:
        if not principal:
            raise PermissionError("pause requires a trusted local principal")
        self.supervisor.ledger.append_event(
            mission_id, "operator_pause", {"principal": principal}
        )
        return self.supervisor.pause(mission_id)

    def stop(self, mission_id: str, *, principal: str, reason: str) -> dict[str, Any]:
        if not principal:
            raise PermissionError("stop requires a trusted local principal")
        receipt = self.supervisor.request_stop(
            mission_id, reason=f"operator:{principal}:{reason}"
        )
        return {
            "receipt": receipt.to_dict(),
            "snapshot": self.supervisor.snapshot(mission_id),
        }

    def replay_bundle(self, mission_id: str) -> dict[str, Any]:
        return export_replay_bundle(
            self.supervisor.ledger,
            mission_id,
            model_ledger=self.model_ledger,
        ).to_dict()

    def metrics(self, mission_id: str) -> dict[str, Any]:
        return mission_metrics(
            self.supervisor.ledger,
            mission_id,
            model_ledger=self.model_ledger,
        )

    def handle_drop(
        self,
        mission_id: str,
        event: Mapping[str, Any],
        *,
        background: bool = True,
    ) -> dict[str, Any]:
        if self.recovery is None:
            raise RuntimeError("drop recovery is not configured")
        return self.recovery.handle_drop(
            mission_id, event, background=background
        ).to_dict()

