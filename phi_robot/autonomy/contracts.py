"""Stable Phase 2 plan and execution contracts.

These types are planner-neutral: a fixed mission, replay provider, or future
cloud planner can all submit the same ``PlanGraph``.  Numeric robot poses remain
skill arguments for Phase 1 compatibility; Phase 3 introduces the semantic
``LocationRef`` boundary used by all newly-created goals.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional


SCHEMA_VERSION = "1.0"


class MissionLifecycle(str, Enum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    INTERVENTION_REQUIRED = "intervention_required"


class ActionLifecycle(str, Enum):
    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class RevisionKind(str, Enum):
    INITIAL = "initial"
    REPLAN = "replan"
    RECOVERY = "recovery"
    RESET = "reset"


@dataclass(frozen=True)
class ActionTimeouts:
    """Three independent action timing budgets.

    ``dispatch_s`` bounds acknowledgement by the local dispatch boundary.
    ``operation_s`` is supplied to the physical skill implementation.
    ``caller_wait_s`` only bounds how long this supervisor waits; expiry after
    dispatch produces an unknown physical result and never authorizes retry.
    """

    dispatch_s: float = 2.0
    operation_s: float = 30.0
    caller_wait_s: float = 35.0

    def __post_init__(self) -> None:
        for name, value in (
            ("dispatch_s", self.dispatch_s),
            ("operation_s", self.operation_s),
            ("caller_wait_s", self.caller_wait_s),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive number")

    def to_dict(self) -> dict[str, float]:
        return {
            "dispatch_s": float(self.dispatch_s),
            "operation_s": float(self.operation_s),
            "caller_wait_s": float(self.caller_wait_s),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActionTimeouts":
        return cls(
            dispatch_s=float(data.get("dispatch_s", 2.0)),
            operation_s=float(data.get("operation_s", 30.0)),
            caller_wait_s=float(data.get("caller_wait_s", 35.0)),
        )


@dataclass(frozen=True)
class PlanNode:
    node_id: str
    skill_name: str
    args: Mapping[str, Any]
    skill_version: str = "1.0"
    depends_on: tuple[str, ...] = ()
    annotations: Mapping[str, Any] = field(default_factory=dict)
    preconditions: tuple[str, ...] = ()
    success_evidence: tuple[str, ...] = ()
    timeouts: ActionTimeouts = field(default_factory=ActionTimeouts)

    def __post_init__(self) -> None:
        if not self.node_id or not isinstance(self.node_id, str):
            raise ValueError("node_id must be a non-empty string")
        if not self.skill_name or not isinstance(self.skill_name, str):
            raise ValueError("skill_name must be a non-empty string")
        if not isinstance(self.args, Mapping):
            raise ValueError("args must be an object")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f"node {self.node_id} has duplicate dependencies")

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "args": dict(self.args),
            "depends_on": list(self.depends_on),
            "annotations": dict(self.annotations),
            "preconditions": list(self.preconditions),
            "success_evidence": list(self.success_evidence),
            "timeouts": self.timeouts.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanNode":
        return cls(
            node_id=str(data["node_id"]),
            skill_name=str(data["skill_name"]),
            skill_version=str(data.get("skill_version", "1.0")),
            args=dict(data.get("args") or {}),
            depends_on=tuple(str(value) for value in data.get("depends_on") or ()),
            annotations=dict(data.get("annotations") or {}),
            preconditions=tuple(str(value) for value in data.get("preconditions") or ()),
            success_evidence=tuple(str(value) for value in data.get("success_evidence") or ()),
            timeouts=ActionTimeouts.from_dict(data.get("timeouts") or {}),
        )


@dataclass(frozen=True)
class PlanGraph:
    plan_id: str
    goal_id: str
    nodes: tuple[PlanNode, ...]
    schema_version: str = SCHEMA_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported PlanGraph schema_version: {self.schema_version}")
        if not self.plan_id or not self.goal_id:
            raise ValueError("plan_id and goal_id must be non-empty")
        if not self.nodes:
            raise ValueError("PlanGraph must contain at least one node")
        ids = [node.node_id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("PlanGraph node_id values must be unique")
        known = set(ids)
        for node in self.nodes:
            missing = set(node.depends_on) - known
            if missing:
                raise ValueError(
                    f"node {node.node_id} depends on unknown nodes: {sorted(missing)}"
                )
            if node.node_id in node.depends_on:
                raise ValueError(f"node {node.node_id} cannot depend on itself")
        superseded = self.superseded_node_ids
        missing_superseded = superseded - known
        if missing_superseded:
            raise ValueError(
                "superseded_node_ids reference unknown nodes: "
                f"{sorted(missing_superseded)}"
            )
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        dependencies = {node.node_id: set(node.depends_on) for node in self.nodes}
        remaining = set(dependencies)
        resolved: set[str] = set()
        while remaining:
            ready = {node_id for node_id in remaining if dependencies[node_id] <= resolved}
            if not ready:
                raise ValueError("PlanGraph dependencies contain a cycle")
            resolved.update(ready)
            remaining.difference_update(ready)

    def node(self, node_id: str) -> PlanNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"unknown plan node: {node_id}")

    def ready_nodes(self, completed_node_ids: Iterable[str]) -> tuple[PlanNode, ...]:
        completed = set(completed_node_ids)
        terminal = completed | self.superseded_node_ids
        return tuple(
            node
            for node in self.nodes
            if node.node_id not in terminal and set(node.depends_on) <= terminal
        )

    @property
    def superseded_node_ids(self) -> set[str]:
        """Nodes retained for immutable history but explicitly skipped by a revision.

        Superseding is not success.  It merely makes dependency traversal possible
        after a failed/cancelled/interrupt-frozen invocation has been retained in
        the durable graph and replaced with a new evidence-based action.
        """

        raw = self.metadata.get("superseded_node_ids", ())
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raise ValueError("metadata.superseded_node_ids must be an array")
        values = {str(value) for value in raw}
        if any(not value for value in values):
            raise ValueError("superseded_node_ids cannot contain empty IDs")
        return values

    def is_complete(self, completed_node_ids: Iterable[str]) -> bool:
        terminal = set(completed_node_ids) | self.superseded_node_ids
        return {node.node_id for node in self.nodes} <= terminal

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "goal_id": self.goal_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanGraph":
        return cls(
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            plan_id=str(data["plan_id"]),
            goal_id=str(data["goal_id"]),
            nodes=tuple(PlanNode.from_dict(node) for node in data.get("nodes") or ()),
            metadata=dict(data.get("metadata") or {}),
        )

    @classmethod
    def sequential(
        cls,
        *,
        goal_id: str,
        nodes: Iterable[PlanNode],
        plan_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "PlanGraph":
        chained: list[PlanNode] = []
        previous: Optional[str] = None
        for node in nodes:
            dependencies = node.depends_on or ((previous,) if previous else ())
            chained.append(
                PlanNode(
                    node_id=node.node_id,
                    skill_name=node.skill_name,
                    skill_version=node.skill_version,
                    args=dict(node.args),
                    depends_on=tuple(value for value in dependencies if value),
                    annotations=dict(node.annotations),
                    preconditions=node.preconditions,
                    success_evidence=node.success_evidence,
                    timeouts=node.timeouts,
                )
            )
            previous = node.node_id
        return cls(
            plan_id=plan_id or f"plan-{uuid.uuid4().hex}",
            goal_id=goal_id,
            nodes=tuple(chained),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class PlanRevision:
    plan_revision_id: str
    mission_id: str
    revision_number: int
    graph: PlanGraph
    kind: RevisionKind = RevisionKind.INITIAL
    parent_revision_id: Optional[str] = None
    reason: str = "initial plan"
    created_at: float = field(default_factory=time.time)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported PlanRevision schema_version: {self.schema_version}")
        if not self.plan_revision_id or not self.mission_id:
            raise ValueError("plan_revision_id and mission_id must be non-empty")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if self.revision_number == 1 and self.parent_revision_id is not None:
            raise ValueError("initial revision cannot have a parent")
        if self.revision_number > 1 and not self.parent_revision_id:
            raise ValueError("non-initial revision requires parent_revision_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_revision_id": self.plan_revision_id,
            "mission_id": self.mission_id,
            "revision_number": self.revision_number,
            "kind": self.kind.value,
            "parent_revision_id": self.parent_revision_id,
            "reason": self.reason,
            "created_at": self.created_at,
            "graph": self.graph.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanRevision":
        return cls(
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            plan_revision_id=str(data["plan_revision_id"]),
            mission_id=str(data["mission_id"]),
            revision_number=int(data["revision_number"]),
            kind=RevisionKind(str(data.get("kind", RevisionKind.INITIAL.value))),
            parent_revision_id=(
                str(data["parent_revision_id"])
                if data.get("parent_revision_id") is not None
                else None
            ),
            reason=str(data.get("reason", "")),
            created_at=float(data.get("created_at", time.time())),
            graph=PlanGraph.from_dict(data["graph"]),
        )
