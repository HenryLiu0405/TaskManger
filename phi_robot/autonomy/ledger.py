"""SQLite-backed mission, plan, action, event, and world-state ledger."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

from .contracts import (
    ActionLifecycle,
    MissionLifecycle,
    PlanGraph,
    PlanRevision,
    RevisionKind,
)
from .world_state import RobotWorldState


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: Optional[str], default: Any) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


class SupervisorLedger:
    """Thread-safe durable ledger with conservative restart recovery.

    An action row is committed as ``dispatched`` *before* the dispatcher is
    called.  A crash in the tiny gap before the backend call therefore creates
    a conservative reconciliation requirement, never an automatic re-dispatch.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._guard:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = FULL")
            self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                robot_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                goal_id TEXT NOT NULL,
                status TEXT NOT NULL,
                active_revision_id TEXT NOT NULL,
                current_node_id TEXT,
                last_error TEXT,
                metadata_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(robot_id, request_id)
            );

            CREATE TABLE IF NOT EXISTS plan_revisions (
                plan_revision_id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                revision_number INTEGER NOT NULL,
                kind TEXT NOT NULL,
                parent_revision_id TEXT,
                reason TEXT NOT NULL,
                graph_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(mission_id, revision_number)
            );

            CREATE TABLE IF NOT EXISTS invocations (
                invocation_id TEXT PRIMARY KEY,
                dispatcher_invocation_id TEXT,
                mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                plan_revision_id TEXT NOT NULL REFERENCES plan_revisions(plan_revision_id),
                node_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                skill_version TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                args_json TEXT NOT NULL,
                annotations_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                reconciliation_note TEXT,
                prepared_at REAL NOT NULL,
                dispatched_at REAL,
                finished_at REAL,
                UNIQUE(mission_id, idempotency_key)
            );

            CREATE INDEX IF NOT EXISTS invocations_mission_node
                ON invocations(mission_id, node_id, prepared_at);

            CREATE TABLE IF NOT EXISTS mission_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                event_type TEXT NOT NULL,
                node_id TEXT,
                payload_json TEXT NOT NULL,
                occurred_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS mission_events_order
                ON mission_events(mission_id, event_id);

            CREATE TABLE IF NOT EXISTS interrupts (
                interrupt_id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                receipt_json TEXT,
                requested_at REAL NOT NULL,
                completed_at REAL
            );

            CREATE TABLE IF NOT EXISTS world_states (
                robot_id TEXT PRIMARY KEY,
                world_version INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._guard:
            self._conn.close()

    # -- Mission and plan revisions -------------------------------------

    def create_mission(
        self,
        *,
        robot_id: str,
        request_id: str,
        graph: PlanGraph,
        mission_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        if not robot_id or not request_id:
            raise ValueError("robot_id and request_id must be non-empty")
        mission_id = mission_id or f"mission-{uuid.uuid4().hex}"
        revision_id = f"revision-{uuid.uuid4().hex}"
        now = time.time()
        graph_json = _json(graph.to_dict())
        with self._guard, self._conn:
            existing = self._conn.execute(
                "SELECT * FROM missions WHERE robot_id = ? AND request_id = ?",
                (robot_id, request_id),
            ).fetchone()
            if existing is not None:
                revision = self._conn.execute(
                    "SELECT graph_json FROM plan_revisions WHERE plan_revision_id = ?",
                    (existing["active_revision_id"],),
                ).fetchone()
                if existing["goal_id"] != graph.goal_id or revision is None or revision["graph_json"] != graph_json:
                    raise ValueError(
                        f"request_id {request_id} already exists with different mission parameters"
                    )
                return str(existing["mission_id"])

            duplicate_id = self._conn.execute(
                "SELECT mission_id FROM missions WHERE mission_id = ?", (mission_id,)
            ).fetchone()
            if duplicate_id is not None:
                raise ValueError(f"mission_id already exists: {mission_id}")

            self._conn.execute(
                """
                INSERT INTO missions (
                    mission_id, robot_id, request_id, goal_id, status,
                    active_revision_id, current_node_id, last_error,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    mission_id,
                    robot_id,
                    request_id,
                    graph.goal_id,
                    MissionLifecycle.READY.value,
                    revision_id,
                    _json(dict(metadata or {})),
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO plan_revisions (
                    plan_revision_id, mission_id, revision_number, kind,
                    parent_revision_id, reason, graph_json, created_at
                ) VALUES (?, ?, 1, ?, NULL, ?, ?, ?)
                """,
                (
                    revision_id,
                    mission_id,
                    RevisionKind.INITIAL.value,
                    "initial plan",
                    graph_json,
                    now,
                ),
            )
            self._insert_event_locked(
                mission_id,
                "mission_created",
                {"plan_revision_id": revision_id, "plan_id": graph.plan_id},
                occurred_at=now,
            )
        return mission_id

    def get_mission(self, mission_id: str) -> Optional[dict[str, Any]]:
        with self._guard:
            row = self._conn.execute(
                "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
            ).fetchone()
        return self._mission_row(row) if row is not None else None

    def get_mission_by_request(self, robot_id: str, request_id: str) -> Optional[dict[str, Any]]:
        with self._guard:
            row = self._conn.execute(
                "SELECT * FROM missions WHERE robot_id = ? AND request_id = ?",
                (robot_id, request_id),
            ).fetchone()
        return self._mission_row(row) if row is not None else None

    def list_missions(self, *, robot_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._guard:
            if robot_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM missions ORDER BY created_at"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM missions WHERE robot_id = ? ORDER BY created_at",
                    (robot_id,),
                ).fetchall()
        return [self._mission_row(row) for row in rows]

    @staticmethod
    def _mission_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = _loads(result.pop("metadata_json"), {})
        return result

    def get_revision(self, plan_revision_id: str) -> PlanRevision:
        with self._guard:
            row = self._conn.execute(
                "SELECT * FROM plan_revisions WHERE plan_revision_id = ?",
                (plan_revision_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown plan_revision_id: {plan_revision_id}")
        return PlanRevision(
            plan_revision_id=str(row["plan_revision_id"]),
            mission_id=str(row["mission_id"]),
            revision_number=int(row["revision_number"]),
            kind=RevisionKind(str(row["kind"])),
            parent_revision_id=row["parent_revision_id"],
            reason=str(row["reason"]),
            graph=PlanGraph.from_dict(_loads(row["graph_json"], {})),
            created_at=float(row["created_at"]),
        )

    def active_revision(self, mission_id: str) -> PlanRevision:
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"unknown mission_id: {mission_id}")
        return self.get_revision(str(mission["active_revision_id"]))

    def list_revisions(self, mission_id: str) -> list[PlanRevision]:
        with self._guard:
            rows = self._conn.execute(
                """
                SELECT plan_revision_id FROM plan_revisions
                WHERE mission_id = ? ORDER BY revision_number
                """,
                (mission_id,),
            ).fetchall()
        return [self.get_revision(str(row["plan_revision_id"])) for row in rows]

    def append_revision(
        self,
        mission_id: str,
        graph: PlanGraph,
        *,
        kind: RevisionKind,
        reason: str,
    ) -> PlanRevision:
        """Persist an explicit tail revision without mutating dispatched nodes."""

        with self._guard, self._conn:
            mission = self._conn.execute(
                "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
            ).fetchone()
            if mission is None:
                raise KeyError(f"unknown mission_id: {mission_id}")
            parent_row = self._conn.execute(
                "SELECT * FROM plan_revisions WHERE plan_revision_id = ?",
                (mission["active_revision_id"],),
            ).fetchone()
            if parent_row is None:
                raise RuntimeError("active plan revision is missing")
            parent_graph = PlanGraph.from_dict(_loads(parent_row["graph_json"], {}))

            dispatched_rows = self._conn.execute(
                """
                SELECT DISTINCT node_id FROM invocations
                WHERE mission_id = ? AND status != ?
                """,
                (mission_id, ActionLifecycle.PREPARED.value),
            ).fetchall()
            for dispatched in dispatched_rows:
                node_id = str(dispatched["node_id"])
                try:
                    old_node = parent_graph.node(node_id)
                    new_node = graph.node(node_id)
                except KeyError as exc:
                    raise ValueError(
                        f"dispatched node {node_id} must remain in every later revision"
                    ) from exc
                if old_node.to_dict() != new_node.to_dict():
                    raise ValueError(f"dispatched node {node_id} is immutable")

            revision_number = int(parent_row["revision_number"]) + 1
            revision_id = f"revision-{uuid.uuid4().hex}"
            now = time.time()
            self._conn.execute(
                """
                INSERT INTO plan_revisions (
                    plan_revision_id, mission_id, revision_number, kind,
                    parent_revision_id, reason, graph_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    mission_id,
                    revision_number,
                    kind.value,
                    parent_row["plan_revision_id"],
                    reason,
                    _json(graph.to_dict()),
                    now,
                ),
            )
            self._conn.execute(
                """
                UPDATE missions SET active_revision_id = ?, updated_at = ?
                WHERE mission_id = ?
                """,
                (revision_id, now, mission_id),
            )
            self._insert_event_locked(
                mission_id,
                "plan_revised",
                {
                    "plan_revision_id": revision_id,
                    "parent_revision_id": parent_row["plan_revision_id"],
                    "revision_number": revision_number,
                    "kind": kind.value,
                    "reason": reason,
                },
                occurred_at=now,
            )
        return self.get_revision(revision_id)

    def set_mission_status(
        self,
        mission_id: str,
        status: MissionLifecycle,
        *,
        current_node_id: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        now = time.time()
        with self._guard, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE missions
                SET status = ?, current_node_id = ?, last_error = ?, updated_at = ?
                WHERE mission_id = ?
                """,
                (status.value, current_node_id, last_error, now, mission_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown mission_id: {mission_id}")
            self._insert_event_locked(
                mission_id,
                "mission_status_changed",
                {
                    "status": status.value,
                    "current_node_id": current_node_id,
                    "last_error": last_error,
                },
                node_id=current_node_id,
                occurred_at=now,
            )

    # -- Invocation lifecycle ------------------------------------------

    def prepare_invocation(
        self,
        *,
        mission_id: str,
        plan_revision_id: str,
        node_id: str,
        skill_name: str,
        skill_version: str,
        idempotency_key: str,
        args: Mapping[str, Any],
        annotations: Mapping[str, Any],
        invocation_id: Optional[str] = None,
    ) -> tuple[dict[str, Any], bool]:
        now = time.time()
        invocation_id = invocation_id or f"action-{uuid.uuid4().hex}"
        with self._guard, self._conn:
            existing = self._conn.execute(
                """
                SELECT * FROM invocations
                WHERE mission_id = ? AND idempotency_key = ?
                """,
                (mission_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["skill_name"] != skill_name
                    or existing["skill_version"] != skill_version
                    or existing["args_json"] != _json(dict(args))
                    or existing["annotations_json"] != _json(dict(annotations))
                ):
                    raise ValueError("idempotency key was reused with different action parameters")
                return self._invocation_row(existing), False
            self._conn.execute(
                """
                INSERT INTO invocations (
                    invocation_id, dispatcher_invocation_id, mission_id,
                    plan_revision_id, node_id, skill_name, skill_version,
                    idempotency_key, args_json, annotations_json, status,
                    result_json, reconciliation_note, prepared_at,
                    dispatched_at, finished_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL)
                """,
                (
                    invocation_id,
                    mission_id,
                    plan_revision_id,
                    node_id,
                    skill_name,
                    skill_version,
                    idempotency_key,
                    _json(dict(args)),
                    _json(dict(annotations)),
                    ActionLifecycle.PREPARED.value,
                    now,
                ),
            )
            self._insert_event_locked(
                mission_id,
                "invocation_prepared",
                {
                    "invocation_id": invocation_id,
                    "plan_revision_id": plan_revision_id,
                    "skill_name": skill_name,
                },
                node_id=node_id,
                occurred_at=now,
            )
            row = self._conn.execute(
                "SELECT * FROM invocations WHERE invocation_id = ?", (invocation_id,)
            ).fetchone()
        assert row is not None
        return self._invocation_row(row), True

    def mark_dispatched(
        self,
        invocation_id: str,
        *,
        dispatcher_invocation_id: Optional[str] = None,
    ) -> None:
        now = time.time()
        with self._guard, self._conn:
            row = self._conn.execute(
                "SELECT * FROM invocations WHERE invocation_id = ?", (invocation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown invocation_id: {invocation_id}")
            if row["status"] != ActionLifecycle.PREPARED.value:
                raise ValueError(
                    f"invocation {invocation_id} is {row['status']}, expected prepared"
                )
            self._conn.execute(
                """
                UPDATE invocations
                SET status = ?, dispatcher_invocation_id = ?, dispatched_at = ?
                WHERE invocation_id = ?
                """,
                (
                    ActionLifecycle.DISPATCHED.value,
                    dispatcher_invocation_id,
                    now,
                    invocation_id,
                ),
            )
            self._insert_event_locked(
                str(row["mission_id"]),
                "invocation_dispatched",
                {"invocation_id": invocation_id, "dispatcher_invocation_id": dispatcher_invocation_id},
                node_id=str(row["node_id"]),
                occurred_at=now,
            )

    def attach_dispatcher_invocation(self, invocation_id: str, dispatcher_invocation_id: str) -> None:
        with self._guard, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE invocations SET dispatcher_invocation_id = ?
                WHERE invocation_id = ? AND status = ?
                """,
                (
                    dispatcher_invocation_id,
                    invocation_id,
                    ActionLifecycle.DISPATCHED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"dispatched invocation not found: {invocation_id}")

    def finish_invocation(
        self,
        invocation_id: str,
        *,
        status: ActionLifecycle,
        result: Mapping[str, Any],
        reconciliation_note: Optional[str] = None,
    ) -> None:
        if status in {ActionLifecycle.PREPARED, ActionLifecycle.DISPATCHED}:
            raise ValueError("finish_invocation requires a terminal or reconciliation status")
        now = time.time()
        with self._guard, self._conn:
            row = self._conn.execute(
                "SELECT * FROM invocations WHERE invocation_id = ?", (invocation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown invocation_id: {invocation_id}")
            if row["status"] not in {
                ActionLifecycle.DISPATCHED.value,
                ActionLifecycle.UNKNOWN.value,
            }:
                raise ValueError(
                    f"invocation {invocation_id} cannot finish from {row['status']}"
                )
            self._conn.execute(
                """
                UPDATE invocations
                SET status = ?, result_json = ?, reconciliation_note = ?, finished_at = ?
                WHERE invocation_id = ?
                """,
                (status.value, _json(dict(result)), reconciliation_note, now, invocation_id),
            )
            self._insert_event_locked(
                str(row["mission_id"]),
                "invocation_finished",
                {
                    "invocation_id": invocation_id,
                    "status": status.value,
                    "reconciliation_note": reconciliation_note,
                    "result": dict(result),
                },
                node_id=str(row["node_id"]),
                occurred_at=now,
            )

    def get_invocation(self, invocation_id: str) -> Optional[dict[str, Any]]:
        with self._guard:
            row = self._conn.execute(
                "SELECT * FROM invocations WHERE invocation_id = ?", (invocation_id,)
            ).fetchone()
        return self._invocation_row(row) if row is not None else None

    def list_invocations(self, mission_id: str) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._conn.execute(
                """
                SELECT * FROM invocations WHERE mission_id = ?
                ORDER BY prepared_at, invocation_id
                """,
                (mission_id,),
            ).fetchall()
        return [self._invocation_row(row) for row in rows]

    @staticmethod
    def _invocation_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["args"] = _loads(result.pop("args_json"), {})
        result["annotations"] = _loads(result.pop("annotations_json"), {})
        result["result"] = _loads(result.pop("result_json"), None)
        return result

    def completed_node_ids(self, mission_id: str) -> set[str]:
        with self._guard:
            rows = self._conn.execute(
                """
                SELECT DISTINCT node_id FROM invocations
                WHERE mission_id = ? AND status = ?
                """,
                (mission_id, ActionLifecycle.SUCCEEDED.value),
            ).fetchall()
        return {str(row["node_id"]) for row in rows}

    def node_invocation(self, mission_id: str, node_id: str) -> Optional[dict[str, Any]]:
        with self._guard:
            row = self._conn.execute(
                """
                SELECT * FROM invocations
                WHERE mission_id = ? AND node_id = ?
                ORDER BY prepared_at DESC LIMIT 1
                """,
                (mission_id, node_id),
            ).fetchone()
        return self._invocation_row(row) if row is not None else None

    def recover_incomplete(self, *, robot_id: str) -> list[str]:
        """Fail closed after restart and return affected mission IDs."""

        affected: set[str] = set()
        now = time.time()
        with self._guard, self._conn:
            rows = self._conn.execute(
                """
                SELECT i.*, m.robot_id FROM invocations i
                JOIN missions m ON m.mission_id = i.mission_id
                WHERE m.robot_id = ? AND i.status IN (?, ?, ?)
                """,
                (
                    robot_id,
                    ActionLifecycle.PREPARED.value,
                    ActionLifecycle.DISPATCHED.value,
                    ActionLifecycle.UNKNOWN.value,
                ),
            ).fetchall()
            for row in rows:
                mission_id = str(row["mission_id"])
                affected.add(mission_id)
                self._conn.execute(
                    """
                    UPDATE invocations
                    SET status = ?, reconciliation_note = ?, finished_at = COALESCE(finished_at, ?)
                    WHERE invocation_id = ?
                    """,
                    (
                        ActionLifecycle.RECONCILIATION_REQUIRED.value,
                        "process restarted before a trusted terminal physical result was persisted",
                        now,
                        row["invocation_id"],
                    ),
                )
                self._insert_event_locked(
                    mission_id,
                    "restart_reconciliation_required",
                    {
                        "invocation_id": row["invocation_id"],
                        "previous_status": row["status"],
                    },
                    node_id=str(row["node_id"]),
                    occurred_at=now,
                )

            running = self._conn.execute(
                """
                SELECT mission_id FROM missions
                WHERE robot_id = ? AND status IN (?, ?)
                """,
                (robot_id, MissionLifecycle.RUNNING.value, MissionLifecycle.RECOVERING.value),
            ).fetchall()
            affected.update(str(row["mission_id"]) for row in running)
            for mission_id in affected:
                self._conn.execute(
                    """
                    UPDATE missions SET status = ?, last_error = ?, updated_at = ?
                    WHERE mission_id = ?
                    """,
                    (
                        MissionLifecycle.RECONCILIATION_REQUIRED.value,
                        "runtime restarted with an incomplete physical action",
                        now,
                        mission_id,
                    ),
                )
        return sorted(affected)

    def resolve_reconciliation(
        self,
        invocation_id: str,
        *,
        status: ActionLifecycle,
        result: Mapping[str, Any],
        principal: str,
    ) -> dict[str, Any]:
        """Persist an explicit trusted resolution of an uncertain action."""

        if status not in {
            ActionLifecycle.SUCCEEDED,
            ActionLifecycle.FAILED,
            ActionLifecycle.CANCELLED,
        }:
            raise ValueError("reconciliation resolution must be succeeded, failed, or cancelled")
        if not principal:
            raise ValueError("reconciliation principal must be non-empty")
        now = time.time()
        with self._guard, self._conn:
            row = self._conn.execute(
                "SELECT * FROM invocations WHERE invocation_id = ?", (invocation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown invocation_id: {invocation_id}")
            if row["status"] != ActionLifecycle.RECONCILIATION_REQUIRED.value:
                raise ValueError(
                    f"invocation {invocation_id} is {row['status']}, not reconciliation_required"
                )
            reconciled_result = {
                **dict(result),
                "reconciliation": {
                    "principal": principal,
                    "resolved_at": now,
                    "resolution": status.value,
                },
            }
            self._conn.execute(
                """
                UPDATE invocations
                SET status = ?, result_json = ?, reconciliation_note = ?, finished_at = ?
                WHERE invocation_id = ?
                """,
                (
                    status.value,
                    _json(reconciled_result),
                    f"resolved by trusted principal {principal}",
                    now,
                    invocation_id,
                ),
            )
            self._insert_event_locked(
                str(row["mission_id"]),
                "invocation_reconciled",
                {
                    "invocation_id": invocation_id,
                    "resolution": status.value,
                    "principal": principal,
                    "result": reconciled_result,
                },
                node_id=str(row["node_id"]),
                occurred_at=now,
            )
            updated = self._conn.execute(
                "SELECT * FROM invocations WHERE invocation_id = ?", (invocation_id,)
            ).fetchone()
        assert updated is not None
        return self._invocation_row(updated)

    # -- Events, interrupts, and world state ----------------------------

    def append_event(
        self,
        mission_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        node_id: Optional[str] = None,
        occurred_at: Optional[float] = None,
    ) -> int:
        with self._guard, self._conn:
            return self._insert_event_locked(
                mission_id,
                event_type,
                payload,
                node_id=node_id,
                occurred_at=occurred_at,
            )

    def _insert_event_locked(
        self,
        mission_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        node_id: Optional[str] = None,
        occurred_at: Optional[float] = None,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO mission_events (
                mission_id, event_type, node_id, payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                event_type,
                node_id,
                _json(dict(payload)),
                occurred_at if occurred_at is not None else time.time(),
            ),
        )
        return int(cursor.lastrowid)

    def list_events(self, mission_id: str, *, after_event_id: int = 0) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._conn.execute(
                """
                SELECT * FROM mission_events
                WHERE mission_id = ? AND event_id > ? ORDER BY event_id
                """,
                (mission_id, after_event_id),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json"), {})
            result.append(item)
        return result

    def begin_interrupt(self, mission_id: str, reason: str) -> str:
        interrupt_id = f"interrupt-{uuid.uuid4().hex}"
        now = time.time()
        with self._guard, self._conn:
            self._conn.execute(
                """
                INSERT INTO interrupts (
                    interrupt_id, mission_id, reason, status, receipt_json,
                    requested_at, completed_at
                ) VALUES (?, ?, ?, 'requested', NULL, ?, NULL)
                """,
                (interrupt_id, mission_id, reason, now),
            )
            self._insert_event_locked(
                mission_id,
                "interrupt_requested",
                {"interrupt_id": interrupt_id, "reason": reason},
                occurred_at=now,
            )
        return interrupt_id

    def finish_interrupt(
        self,
        interrupt_id: str,
        *,
        status: str,
        receipt: Mapping[str, Any],
    ) -> None:
        now = time.time()
        with self._guard, self._conn:
            row = self._conn.execute(
                "SELECT * FROM interrupts WHERE interrupt_id = ?", (interrupt_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown interrupt_id: {interrupt_id}")
            self._conn.execute(
                """
                UPDATE interrupts
                SET status = ?, receipt_json = ?, completed_at = ?
                WHERE interrupt_id = ?
                """,
                (status, _json(dict(receipt)), now, interrupt_id),
            )
            self._insert_event_locked(
                str(row["mission_id"]),
                "interrupt_finished",
                {"interrupt_id": interrupt_id, "status": status, "receipt": dict(receipt)},
                occurred_at=now,
            )

    def list_interrupts(self, mission_id: str) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._conn.execute(
                "SELECT * FROM interrupts WHERE mission_id = ? ORDER BY requested_at",
                (mission_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["receipt"] = _loads(item.pop("receipt_json"), None)
            result.append(item)
        return result

    def load_world_state(self, robot_id: str) -> RobotWorldState:
        with self._guard:
            row = self._conn.execute(
                "SELECT state_json FROM world_states WHERE robot_id = ?", (robot_id,)
            ).fetchone()
        if row is None:
            return RobotWorldState(robot_id=robot_id)
        return RobotWorldState.from_dict(_loads(row["state_json"], {}))

    def save_world_state(self, state: RobotWorldState) -> None:
        with self._guard, self._conn:
            current = self._conn.execute(
                "SELECT world_version FROM world_states WHERE robot_id = ?",
                (state.robot_id,),
            ).fetchone()
            if current is not None and int(current["world_version"]) >= state.world_version:
                raise ValueError(
                    "world state update must advance world_version monotonically"
                )
            self._conn.execute(
                """
                INSERT INTO world_states (robot_id, world_version, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(robot_id) DO UPDATE SET
                    world_version = excluded.world_version,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (state.robot_id, state.world_version, _json(state.to_dict()), state.updated_at),
            )

    def snapshot(self, mission_id: str) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"unknown mission_id: {mission_id}")
        revision = self.active_revision(mission_id)
        return {
            "mission": mission,
            "active_revision": revision.to_dict(),
            "invocations": self.list_invocations(mission_id),
            "interrupts": self.list_interrupts(mission_id),
            "events": self.list_events(mission_id),
            "world_state": self.load_world_state(str(mission["robot_id"])).to_dict(),
        }
