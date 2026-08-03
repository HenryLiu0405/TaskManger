"""Typed contracts shared by robot skills, adapters, and future planners."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional


class SkillKind(str, Enum):
    QUERY = "query"
    PRIMITIVE = "primitive"
    COMPOSITE = "composite"


class SkillOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class InvocationState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    DRAINING = "draining"
    TERMINAL = "terminal"


class ErrorCategory(str, Enum):
    VALIDATION = "validation"
    UNSUPPORTED = "unsupported"
    PRECONDITION = "precondition"
    BUSY = "busy"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class CancellationMode(str, Enum):
    NONE = "none"
    COOPERATIVE = "cooperative"
    CONFIRMED = "confirmed"


class CancellationToken:
    """Thread-safe cooperative cancellation signal."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)


@dataclass(frozen=True)
class SkillError:
    code: str
    category: ErrorCategory
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category.value,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class VerificationResult:
    status: str = "not_run"  # passed | failed | unavailable | not_run
    evidence: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evidence": dict(self.evidence),
            "message": self.message,
        }


@dataclass(frozen=True)
class SkillContext:
    robot_id: str
    source: str
    request_id: str
    goal_id: str
    step_id: str
    mission_id: str = ""
    invocation_id: str = field(default_factory=lambda: f"inv-{uuid.uuid4().hex}")
    idempotency_key: str = ""
    deadline: Optional[float] = None  # Unix timestamp supplied by external callers
    deadline_monotonic: Optional[float] = None
    cancel_token: CancellationToken = field(default_factory=CancellationToken, compare=False)
    attempt: int = 1

    def with_defaults(self, *, skill_name: str) -> "SkillContext":
        key = self.idempotency_key or (
            f"{self.robot_id}:{self.source}:{self.request_id}:"
            f"{self.goal_id}:{self.step_id}:{skill_name}"
        )
        monotonic_deadline = self.deadline_monotonic
        if monotonic_deadline is None and self.deadline is not None:
            monotonic_deadline = time.monotonic() + (self.deadline - time.time())
        return SkillContext(
            robot_id=self.robot_id,
            source=self.source,
            request_id=self.request_id,
            goal_id=self.goal_id,
            step_id=self.step_id,
            mission_id=self.mission_id,
            invocation_id=self.invocation_id,
            idempotency_key=key,
            deadline=self.deadline,
            deadline_monotonic=monotonic_deadline,
            cancel_token=self.cancel_token,
            attempt=self.attempt,
        )


@dataclass(frozen=True)
class SkillRequest:
    skill_name: str
    version: str
    args: Mapping[str, Any]
    context: SkillContext
    annotations: Mapping[str, Any] = field(default_factory=dict)

    def normalized(self) -> "SkillRequest":
        return SkillRequest(
            skill_name=self.skill_name,
            version=self.version,
            args=dict(self.args),
            context=self.context.with_defaults(skill_name=self.skill_name),
            annotations=dict(self.annotations),
        )


@dataclass(frozen=True)
class SkillResult:
    invocation_id: str
    outcome: SkillOutcome
    output: Mapping[str, Any] = field(default_factory=dict)
    state: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[SkillError] = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    verification: VerificationResult = field(default_factory=VerificationResult)
    request_id: str = ""
    mission_id: str = ""
    goal_id: str = ""
    step_id: str = ""
    skill_name: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    @classmethod
    def error_result(
        cls,
        request: SkillRequest,
        *,
        outcome: SkillOutcome,
        code: str,
        category: ErrorCategory,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        started_at: float = 0.0,
    ) -> "SkillResult":
        now = time.time()
        ctx = request.context
        return cls(
            invocation_id=ctx.invocation_id,
            outcome=outcome,
            error=SkillError(code, category, message, details or {}),
            request_id=ctx.request_id,
            mission_id=ctx.mission_id,
            goal_id=ctx.goal_id,
            step_id=ctx.step_id,
            skill_name=request.skill_name,
            started_at=started_at or now,
            finished_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "outcome": self.outcome.value,
            "output": dict(self.output),
            "state": dict(self.state),
            "error": self.error.to_dict() if self.error else None,
            "metrics": dict(self.metrics),
            "verification": self.verification.to_dict(),
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "goal_id": self.goal_id,
            "step_id": self.step_id,
            "skill_name": self.skill_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def to_legacy(self) -> dict[str, Any]:
        """Project the canonical result onto the existing HTTP/controller shape."""

        ok = self.outcome == SkillOutcome.SUCCEEDED
        error_code = None
        message = ""
        if self.error:
            error_code = self.error.code
            message = self.error.message
        elif isinstance(self.output, Mapping):
            message = str(self.output.get("message", ""))

        result = {
            "status": "ok" if ok else "error",
            "error_code": error_code,
            "error_category": self.error.category.value if self.error else None,
            "message": message,
            "state": dict(self.state),
            "metrics": dict(self.metrics),
            "verification": self.verification.to_dict(),
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "goal_id": self.goal_id,
            "step_id": self.step_id,
            "invocation_id": self.invocation_id,
            "outcome": self.outcome.value,
        }
        # Preserve adapter-specific fields used by the debug UI without letting
        # them override the standard envelope.
        for key, value in self.output.items():
            if key not in result:
                result[key] = value
        return result


SkillHandler = Callable[[SkillRequest], SkillResult | Mapping[str, Any]]


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    version: str
    kind: SkillKind
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    planner_visible: bool = False
    operator_only: bool = False
    resources: tuple[str, ...] = ()
    side_effecting: bool = True
    concurrency_safe: bool = False
    risk_level: str = "medium"
    timeout_s: float = 30.0
    retry_policy: str = "none"
    cancellation_mode: CancellationMode = CancellationMode.NONE
    preconditions: tuple[str, ...] = ()
    verifier: str = ""

    def to_function_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.input_schema),
            },
        }


@dataclass
class SkillInvocation:
    request: SkillRequest
    fingerprint: str
    definition: SkillDefinition
    state: InvocationState = InvocationState.QUEUED
    result: Optional[SkillResult] = None
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "invocation_id": self.request.context.invocation_id,
            "idempotency_key": self.request.context.idempotency_key,
            "skill_name": self.request.skill_name,
            "state": self.state.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass(frozen=True)
class CancelReceipt:
    invocation_id: str
    accepted: bool
    confirmed: bool
    state: InvocationState
    message: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data
