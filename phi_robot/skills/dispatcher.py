"""Thread-safe skill dispatcher with one-writer and idempotency guarantees."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Mapping, Optional

from phi_robot.audit import audit_logger

from .models import (
    CancelReceipt,
    ErrorCategory,
    InvocationState,
    SkillError,
    SkillInvocation,
    SkillOutcome,
    SkillRequest,
    SkillResult,
    VerificationResult,
)
from .registry import SkillRegistry


_TRANSPORT_ERROR_CODES = {
    "SERVICE_UNAVAILABLE", "TRANSPORT_ERROR", "CONNECTION_ERROR",
    "SIM_UNREACHABLE", "MOVE_TO_UNREACHABLE",
    "PATH_PLAN_TIMEOUT", "NAV_TIMEOUT", "TIMEOUT",
}
_PRECONDITION_ERROR_CODES = {"PRECONDITION_FAILED", "NO_POSE", "POSE_NOT_READY"}
_VERIFICATION_ERROR_CODES = {
    "VERIFICATION_FAILED", "PICK_NOT_READY", "PLACE_NOT_READY", "REPLAY_NOT_DONE",
}


class SkillDispatcher:
    """Dispatch skill invocations without ever duplicating a physical call.

    The dispatcher owns a non-blocking, per-robot mutating lock.  A timed-out
    waiter does not cancel the worker or release the lock; the invocation stays
    in ``draining`` until the underlying handler actually returns.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        mode: str = "skills",
        max_invocations: int = 10_000,
        audit_enabled: bool = True,
        composite_pick: bool = False,
    ) -> None:
        if mode not in {"skills", "legacy-direct"}:
            raise ValueError(f"unknown skill dispatcher mode: {mode}")
        self.registry = registry
        self.mode = mode
        self.max_invocations = max(100, int(max_invocations))
        self.audit_enabled = audit_enabled
        self.composite_pick = composite_pick and mode == "skills"

        self._guard = threading.RLock()
        self._robot_locks: dict[str, threading.Lock] = {}
        self._invocations: "OrderedDict[str, SkillInvocation]" = OrderedDict()
        self._idempotency: dict[tuple[str, str], str] = {}

    def dispatch(self, request: SkillRequest) -> str:
        request = request.normalized()
        definition = self.registry.get(request.skill_name, request.version)
        fingerprint = _fingerprint(request)

        if definition is None:
            invocation = self._terminal_invocation(
                request,
                fingerprint=fingerprint,
                result=SkillResult.error_result(
                    request,
                    outcome=SkillOutcome.REJECTED,
                    code="UNSUPPORTED_SKILL",
                    category=ErrorCategory.UNSUPPORTED,
                    message=f"unsupported skill: {request.skill_name}@{request.version}",
                ),
                register_idempotency=False,
            )
            return invocation.request.context.invocation_id

        errors = self.registry.validate(request)
        if errors:
            invocation = self._terminal_invocation(
                request,
                fingerprint=fingerprint,
                definition=definition,
                result=SkillResult.error_result(
                    request,
                    outcome=SkillOutcome.REJECTED,
                    code="INVALID_ARGS",
                    category=ErrorCategory.VALIDATION,
                    message="; ".join(errors),
                    details={"errors": errors},
                ),
                register_idempotency=False,
            )
            return invocation.request.context.invocation_id

        if (
            request.context.deadline_monotonic is not None
            and request.context.deadline_monotonic <= time.monotonic()
        ):
            invocation = self._terminal_invocation(
                request,
                fingerprint=fingerprint,
                definition=definition,
                result=SkillResult.error_result(
                    request,
                    outcome=SkillOutcome.REJECTED,
                    code="DEADLINE_EXCEEDED_BEFORE_DISPATCH",
                    category=ErrorCategory.TIMEOUT,
                    message="request deadline elapsed before backend dispatch",
                ),
                register_idempotency=False,
            )
            return invocation.request.context.invocation_id

        key = request.context.idempotency_key
        dedupe_key = (request.context.robot_id, key)
        with self._guard:
            existing_id = self._idempotency.get(dedupe_key)
            if existing_id:
                existing = self._invocations.get(existing_id)
                if existing is not None:
                    if existing.fingerprint == fingerprint:
                        return existing_id
                    conflict = self._terminal_invocation_locked(
                        request,
                        fingerprint=fingerprint,
                        definition=definition,
                        result=SkillResult.error_result(
                            request,
                            outcome=SkillOutcome.REJECTED,
                            code="IDEMPOTENCY_CONFLICT",
                            category=ErrorCategory.CONFLICT,
                            message="idempotency key was already used with different arguments",
                            details={"existing_invocation_id": existing_id},
                        ),
                        register_idempotency=False,
                    )
                    return conflict.request.context.invocation_id

            if request.context.cancel_token.cancelled:
                cancelled = self._terminal_invocation_locked(
                    request,
                    fingerprint=fingerprint,
                    definition=definition,
                    result=SkillResult.error_result(
                        request,
                        outcome=SkillOutcome.CANCELLED,
                        code="CANCELLED_BEFORE_DISPATCH",
                        category=ErrorCategory.CANCELLED,
                        message="invocation was cancelled before the backend call",
                    ),
                    register_idempotency=False,
                )
                return cancelled.request.context.invocation_id

            invocation = SkillInvocation(
                request=request,
                fingerprint=fingerprint,
                definition=definition,
            )
            self._invocations[request.context.invocation_id] = invocation
            self._idempotency[dedupe_key] = request.context.invocation_id
            self._evict_locked()

            robot_lock: Optional[threading.Lock] = None
            if definition.side_effecting or not definition.concurrency_safe:
                robot_lock = self._robot_locks.setdefault(request.context.robot_id, threading.Lock())
                if not robot_lock.acquire(blocking=False):
                    invocation.state = InvocationState.TERMINAL
                    invocation.finished_at = time.time()
                    invocation.result = SkillResult.error_result(
                        request,
                        outcome=SkillOutcome.REJECTED,
                        code="RESOURCE_BUSY",
                        category=ErrorCategory.BUSY,
                        message=f"robot {request.context.robot_id} already has an active mutating skill",
                    )
                    # RESOURCE_BUSY is a pre-dispatch rejection and must not
                    # poison the idempotency key; the caller may safely retry
                    # the same logical invocation after the active writer drains.
                    if self._idempotency.get(dedupe_key) == request.context.invocation_id:
                        self._idempotency.pop(dedupe_key, None)
                    invocation.done.set()
                    return request.context.invocation_id

        worker = threading.Thread(
            target=self._run,
            args=(invocation, robot_lock),
            name=f"skill-{request.skill_name}-{request.context.invocation_id[-8:]}",
            daemon=True,
        )
        worker.start()
        return request.context.invocation_id

    def execute(self, request: SkillRequest, *, timeout: Optional[float] = None) -> SkillResult:
        if timeout is None:
            definition = self.registry.get(request.skill_name, request.version)
            configured = request.args.get("timeout_s") if isinstance(request.args, Mapping) else None
            if configured is None and definition is not None:
                configured = definition.timeout_s
            try:
                timeout = max(float(configured), 0.0) if configured is not None else None
            except (TypeError, ValueError):
                # Invalid timeout values are rejected by schema validation.  Do
                # not let parsing here bypass that deterministic result.
                timeout = None
        if request.context.deadline_monotonic is not None:
            remaining = max(
                request.context.deadline_monotonic - time.monotonic(),
                0.0,
            )
            timeout = remaining if timeout is None else min(timeout, remaining)
        invocation_id = self.dispatch(request)
        return self.wait(invocation_id, timeout=timeout)

    def wait(self, invocation_id: str, *, timeout: Optional[float] = None) -> SkillResult:
        invocation = self._require_invocation(invocation_id)
        if timeout is None and invocation.request.context.deadline_monotonic is not None:
            timeout = max(invocation.request.context.deadline_monotonic - time.monotonic(), 0.0)
        completed = invocation.done.wait(timeout)
        if completed and invocation.result is not None:
            return invocation.result

        with self._guard:
            # Close the event/result race: a worker may have completed between
            # Event.wait() returning and this lock acquisition.
            if invocation.state == InvocationState.TERMINAL and invocation.result is not None:
                return invocation.result
            if invocation.state in (InvocationState.QUEUED, InvocationState.RUNNING):
                invocation.state = InvocationState.DRAINING
        return SkillResult.error_result(
            invocation.request,
            outcome=SkillOutcome.UNKNOWN,
            code="UNKNOWN_OUTCOME",
            category=ErrorCategory.TIMEOUT,
            message="caller deadline elapsed after dispatch; physical outcome is not yet known",
            details={"invocation_state": invocation.state.value},
            started_at=invocation.started_at,
        )

    def status(self, invocation_id: str) -> dict[str, Any]:
        return self._require_invocation(invocation_id).snapshot()

    def cancel(self, invocation_id: str) -> CancelReceipt:
        invocation = self._require_invocation(invocation_id)
        with self._guard:
            if invocation.state == InvocationState.TERMINAL:
                confirmed = bool(
                    invocation.result and invocation.result.outcome == SkillOutcome.CANCELLED
                )
                return CancelReceipt(
                    invocation_id=invocation_id,
                    accepted=False,
                    confirmed=confirmed,
                    state=invocation.state,
                    message="invocation is already terminal",
                )
            invocation.request.context.cancel_token.cancel()
            invocation.state = InvocationState.CANCEL_REQUESTED
            return CancelReceipt(
                invocation_id=invocation_id,
                accepted=True,
                confirmed=False,
                state=invocation.state,
                message=(
                    "cancellation requested; completion is unconfirmed until the "
                    "underlying skill acknowledges or drains"
                ),
            )

    def execute_legacy(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        version: str = "1.0",
        request_id: str,
        goal_id: str,
        step_id: str,
        mission_id: str = "",
        robot_id: str = "active",
        source: str = "legacy",
        annotations: Optional[Mapping[str, Any]] = None,
        idempotency_key: str = "",
        attempt: int = 1,
        wait_timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        from .compat import normalize_legacy_call
        from .models import SkillContext

        canonical_args, extracted_annotations = normalize_legacy_call(tool, args)
        merged_annotations = dict(extracted_annotations)
        merged_annotations.update(dict(annotations or {}))
        request = SkillRequest(
            skill_name=tool,
            version=version,
            args=canonical_args,
            context=SkillContext(
                robot_id=robot_id,
                source=source,
                request_id=request_id,
                mission_id=mission_id,
                goal_id=goal_id,
                step_id=step_id,
                idempotency_key=idempotency_key,
                attempt=attempt,
            ),
            annotations=merged_annotations,
        )
        return self.execute(request, timeout=wait_timeout).to_legacy()

    def _run(self, invocation: SkillInvocation, robot_lock: Optional[threading.Lock]) -> None:
        request = invocation.request
        invocation.started_at = time.time()
        invocation.state = InvocationState.RUNNING
        if self.audit_enabled:
            audit_logger.log_action_start(
                tool=request.skill_name,
                request_id=request.context.request_id,
                goal_id=request.context.goal_id,
                step_id=request.context.step_id,
                args=dict(request.args),
                invocation_id=request.context.invocation_id,
                mission_id=request.context.mission_id,
                idempotency_key=request.context.idempotency_key,
                source=request.context.source,
            )

        try:
            if (
                request.context.deadline_monotonic is not None
                and request.context.deadline_monotonic <= time.monotonic()
            ):
                result = SkillResult.error_result(
                    request,
                    outcome=SkillOutcome.REJECTED,
                    code="DEADLINE_EXCEEDED_BEFORE_DISPATCH",
                    category=ErrorCategory.TIMEOUT,
                    message="request deadline elapsed before backend dispatch",
                    started_at=invocation.started_at,
                )
            elif request.context.cancel_token.cancelled:
                result = SkillResult.error_result(
                    request,
                    outcome=SkillOutcome.CANCELLED,
                    code="CANCELLED_BEFORE_DISPATCH",
                    category=ErrorCategory.CANCELLED,
                    message="invocation was cancelled before the backend call",
                    started_at=invocation.started_at,
                )
            else:
                handler = self.registry.handler(request.skill_name, request.version)
                if handler is None:
                    result = SkillResult.error_result(
                        request,
                        outcome=SkillOutcome.REJECTED,
                        code="UNSUPPORTED_CAPABILITY",
                        category=ErrorCategory.UNSUPPORTED,
                        message=f"no handler for {request.skill_name}@{request.version}",
                        started_at=invocation.started_at,
                    )
                else:
                    raw = handler(request)
                    result = raw if isinstance(raw, SkillResult) else _normalize_handler_result(
                        request, raw, started_at=invocation.started_at
                    )
        except Exception as exc:
            physical_outcome_unknown = bool(invocation.definition.side_effecting)
            result = SkillResult.error_result(
                request,
                outcome=(
                    SkillOutcome.UNKNOWN
                    if physical_outcome_unknown
                    else SkillOutcome.FAILED
                ),
                code=(
                    "ADAPTER_EXCEPTION_UNKNOWN"
                    if physical_outcome_unknown
                    else "INTERNAL_ERROR"
                ),
                category=(
                    ErrorCategory.UNKNOWN
                    if physical_outcome_unknown
                    else ErrorCategory.EXECUTION
                ),
                message=str(exc),
                started_at=invocation.started_at,
            )
        finally:
            if robot_lock is not None:
                robot_lock.release()

        invocation.finished_at = time.time()
        if result.finished_at == 0.0:
            result = SkillResult(
                **{
                    **result.__dict__,
                    "finished_at": invocation.finished_at,
                }
            )
        invocation.result = result
        invocation.state = InvocationState.TERMINAL

        # Pre-dispatch/handler rejections and cancellations have no physical
        # side effect to deduplicate.  Releasing their key lets callers correct
        # a precondition or validation issue without inventing a new identity.
        if result.outcome in {SkillOutcome.REJECTED, SkillOutcome.CANCELLED}:
            dedupe_key = (
                request.context.robot_id,
                request.context.idempotency_key,
            )
            with self._guard:
                if self._idempotency.get(dedupe_key) == request.context.invocation_id:
                    self._idempotency.pop(dedupe_key, None)
        invocation.done.set()

        if self.audit_enabled:
            audit_logger.log_action_end(
                tool=request.skill_name,
                request_id=request.context.request_id,
                goal_id=request.context.goal_id,
                step_id=request.context.step_id,
                status=result.outcome.value,
                error_code=result.error.code if result.error else None,
                elapsed_ms=max(invocation.finished_at - invocation.started_at, 0.0) * 1000.0,
                invocation_id=request.context.invocation_id,
                mission_id=request.context.mission_id,
                idempotency_key=request.context.idempotency_key,
                verification=result.verification.to_dict(),
            )

    def _terminal_invocation(
        self,
        request: SkillRequest,
        *,
        fingerprint: str,
        result: SkillResult,
        definition: Any = None,
        register_idempotency: bool = True,
    ) -> SkillInvocation:
        with self._guard:
            return self._terminal_invocation_locked(
                request,
                fingerprint=fingerprint,
                result=result,
                definition=definition,
                register_idempotency=register_idempotency,
            )

    def _terminal_invocation_locked(
        self,
        request: SkillRequest,
        *,
        fingerprint: str,
        result: SkillResult,
        definition: Any = None,
        register_idempotency: bool = True,
    ) -> SkillInvocation:
        if definition is None:
            from .models import SkillDefinition, SkillKind

            definition = SkillDefinition(
                name=request.skill_name,
                version=request.version,
                kind=SkillKind.PRIMITIVE,
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        invocation = SkillInvocation(
            request=request,
            fingerprint=fingerprint,
            definition=definition,
            state=InvocationState.TERMINAL,
            result=result,
            finished_at=time.time(),
        )
        invocation.done.set()
        self._invocations[request.context.invocation_id] = invocation
        if register_idempotency:
            dedupe_key = (
                request.context.robot_id,
                request.context.idempotency_key,
            )
            self._idempotency[dedupe_key] = request.context.invocation_id
        self._evict_locked()
        return invocation

    def _require_invocation(self, invocation_id: str) -> SkillInvocation:
        with self._guard:
            invocation = self._invocations.get(invocation_id)
        if invocation is None:
            raise KeyError(f"unknown invocation_id: {invocation_id}")
        return invocation

    def _evict_locked(self) -> None:
        if len(self._invocations) <= self.max_invocations:
            return
        for invocation_id, invocation in list(self._invocations.items()):
            if len(self._invocations) <= self.max_invocations:
                break
            if invocation.state != InvocationState.TERMINAL:
                continue
            self._invocations.pop(invocation_id, None)
            dedupe_key = (
                invocation.request.context.robot_id,
                invocation.request.context.idempotency_key,
            )
            if self._idempotency.get(dedupe_key) == invocation_id:
                self._idempotency.pop(dedupe_key, None)


def _fingerprint(request: SkillRequest) -> str:
    payload = {
        "skill": request.skill_name,
        "version": request.version,
        "args": dict(request.args),
        "annotations": dict(request.annotations),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_handler_result(
    request: SkillRequest,
    raw: Mapping[str, Any],
    *,
    started_at: float,
) -> SkillResult:
    data = dict(raw)
    status = data.get("status")
    backend_succeeded = (
        status == "ok"
        if status is not None
        else (data.get("ok") is True or data.get("success") is True)
    )
    verification_raw = data.get("verification") or {}
    verification_status = str(verification_raw.get("status", "not_run"))
    if backend_succeeded and verification_status == "failed":
        outcome = SkillOutcome.FAILED
        error = SkillError(
            code="VERIFICATION_FAILED",
            category=ErrorCategory.VERIFICATION,
            message=str(verification_raw.get("message") or "skill verification failed"),
            details=dict(verification_raw.get("evidence") or {}),
        )
    elif backend_succeeded:
        outcome = SkillOutcome.SUCCEEDED
        error = None
    else:
        error_code = str(data.get("error_code") or "BACKEND_ERROR")
        category = _error_category(error_code)
        if category in {ErrorCategory.TIMEOUT, ErrorCategory.TRANSPORT}:
            outcome = SkillOutcome.UNKNOWN
        elif category == ErrorCategory.UNKNOWN:
            outcome = SkillOutcome.UNKNOWN
        elif category == ErrorCategory.CANCELLED:
            outcome = SkillOutcome.CANCELLED
        elif category in {
            ErrorCategory.VALIDATION,
            ErrorCategory.UNSUPPORTED,
            ErrorCategory.PRECONDITION,
            ErrorCategory.BUSY,
            ErrorCategory.CONFLICT,
        }:
            outcome = SkillOutcome.REJECTED
        else:
            outcome = SkillOutcome.FAILED
        error = SkillError(
            code=error_code,
            category=category,
            message=str(data.get("message", "backend execution failed")),
            details=dict(data.get("error_details") or {}),
        )

    reserved = {
        "status", "ok", "success", "error_code", "error_category", "error_details",
        "state", "metrics", "verification", "request_id", "mission_id", "goal_id",
        "step_id", "invocation_id", "outcome",
    }
    output = {key: value for key, value in data.items() if key not in reserved}
    if "message" in data:
        output["message"] = data["message"]
    verification = VerificationResult(
        status=str(verification_raw.get("status", "not_run")),
        evidence=dict(verification_raw.get("evidence") or {}),
        message=str(verification_raw.get("message", "")),
    )
    return SkillResult(
        invocation_id=request.context.invocation_id,
        outcome=outcome,
        output=output,
        state=dict(data.get("state") or {}),
        error=error,
        metrics=dict(data.get("metrics") or {}),
        verification=verification,
        request_id=request.context.request_id,
        mission_id=request.context.mission_id,
        goal_id=request.context.goal_id,
        step_id=request.context.step_id,
        skill_name=request.skill_name,
        started_at=started_at,
        finished_at=time.time(),
    )


def _error_category(error_code: str) -> ErrorCategory:
    if error_code in {"BACKEND_ERROR", "MALFORMED_BACKEND_RESPONSE"}:
        return ErrorCategory.UNKNOWN
    if (
        error_code in _TRANSPORT_ERROR_CODES
        or "TIMEOUT" in error_code
        or "UNREACHABLE" in error_code
        or "CONNECTION" in error_code
    ):
        return ErrorCategory.TIMEOUT if "TIMEOUT" in error_code else ErrorCategory.TRANSPORT
    if error_code in _PRECONDITION_ERROR_CODES or error_code in {
        "FP_LOCK_FAILED", "POSE_FRAME_MISMATCH", "TARGET_NOT_SELECTED",
    }:
        return ErrorCategory.PRECONDITION
    if error_code in _VERIFICATION_ERROR_CODES or "NOT_READY" in error_code:
        return ErrorCategory.VERIFICATION
    if error_code in {"INVALID_ARGS", "VALIDATION_ERROR"}:
        return ErrorCategory.VALIDATION
    if error_code in {"UNSUPPORTED_TOOL", "UNSUPPORTED_CAPABILITY"}:
        return ErrorCategory.UNSUPPORTED
    if error_code in {"RESOURCE_BUSY", "BUSY"}:
        return ErrorCategory.BUSY
    if error_code in {"IDEMPOTENCY_CONFLICT", "CONFLICT"}:
        return ErrorCategory.CONFLICT
    if error_code in {"CANCELLED", "CANCEL_REQUESTED", "CANCELLED_BEFORE_DISPATCH"}:
        return ErrorCategory.CANCELLED
    return ErrorCategory.EXECUTION
