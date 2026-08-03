"""High-priority local stop lane independent of the normal writer lock."""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class StopReceipt:
    accepted: bool
    confirmed: bool
    message: str
    motion_state: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    requested_at: float = field(default_factory=time.time)
    confirmed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "confirmed": self.confirmed,
            "message": self.message,
            "motion_state": self.motion_state,
            "evidence": dict(self.evidence),
            "requested_at": self.requested_at,
            "confirmed_at": self.confirmed_at,
        }


class InterruptLane(Protocol):
    def stop_motion(self, *, robot_id: str, mission_id: str, reason: str) -> StopReceipt:
        ...


class UnavailableInterruptLane:
    def stop_motion(self, *, robot_id: str, mission_id: str, reason: str) -> StopReceipt:
        return StopReceipt(
            accepted=False,
            confirmed=False,
            message="adapter exposes no independent stop-motion interface",
            evidence={"robot_id": robot_id, "mission_id": mission_id, "reason": reason},
        )


class AdapterInterruptLane:
    """Conservative adapter bridge for an out-of-band stop request.

    A successful method return is only request acknowledgement.  Physical stop
    is confirmed solely when the adapter returns an explicit structured
    ``confirmed``/``stop_confirmed``/``stopped`` fact.
    """

    METHOD_NAMES = ("stop_motion", "request_stop_motion", "pause_navigation")

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    def stop_motion(self, *, robot_id: str, mission_id: str, reason: str) -> StopReceipt:
        method = None
        method_name = ""
        for candidate in self.METHOD_NAMES:
            value = getattr(self.adapter, candidate, None)
            if callable(value):
                method = value
                method_name = candidate
                break
        if method is None:
            return UnavailableInterruptLane().stop_motion(
                robot_id=robot_id, mission_id=mission_id, reason=reason
            )

        requested_at = time.time()
        try:
            raw = _invoke_stop_method(
                method,
                robot_id=robot_id,
                mission_id=mission_id,
                reason=reason,
            )
        except Exception as exc:
            return StopReceipt(
                accepted=False,
                confirmed=False,
                message=f"{method_name} raised: {exc}",
                evidence={"method": method_name, "exception_type": type(exc).__name__},
                requested_at=requested_at,
            )

        if isinstance(raw, StopReceipt):
            return raw
        if isinstance(raw, Mapping):
            accepted = bool(raw.get("accepted", raw.get("ok", raw.get("success", True))))
            explicit_confirmation = raw.get(
                "confirmed",
                raw.get("stop_confirmed", raw.get("stopped", False)),
            )
            confirmed = accepted and explicit_confirmation is True
            motion_state = raw.get("motion_state")
            if confirmed and motion_state is None:
                motion_state = "stopped"
            return StopReceipt(
                accepted=accepted,
                confirmed=confirmed,
                message=str(
                    raw.get("message")
                    or ("physical stop confirmed" if confirmed else "stop requested; confirmation unavailable")
                ),
                motion_state=str(motion_state) if motion_state is not None else None,
                evidence={"method": method_name, "adapter_receipt": dict(raw)},
                requested_at=requested_at,
                confirmed_at=time.time() if confirmed else None,
            )

        accepted = raw is not False
        return StopReceipt(
            accepted=accepted,
            confirmed=False,
            message=(
                "stop requested; adapter returned no structured physical confirmation"
                if accepted
                else "adapter rejected stop request"
            ),
            evidence={"method": method_name, "return_type": type(raw).__name__},
            requested_at=requested_at,
        )


def _invoke_stop_method(method: Any, **context: str) -> Any:
    """Call once, choosing supported keywords without unsafe retry."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method()
    parameters = signature.parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    kwargs = {
        name: value
        for name, value in context.items()
        if accepts_kwargs or name in parameters
    }
    return method(**kwargs)
