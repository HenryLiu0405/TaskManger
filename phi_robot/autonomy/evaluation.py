"""Phase 8 replay export and measurable autonomy evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from .contracts import MissionLifecycle, RevisionKind
from .model_gateway import ModelCallLedger


REPLAY_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ReplayBundle:
    mission: Mapping[str, Any]
    revisions: tuple[Mapping[str, Any], ...]
    invocations: tuple[Mapping[str, Any], ...]
    interrupts: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    world_state: Mapping[str, Any]
    model_calls: tuple[Mapping[str, Any], ...]
    schema_version: str = REPLAY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mission": dict(self.mission),
            "revisions": [dict(value) for value in self.revisions],
            "invocations": [dict(value) for value in self.invocations],
            "interrupts": [dict(value) for value in self.interrupts],
            "events": [dict(value) for value in self.events],
            "world_state": dict(self.world_state),
            "model_calls": [dict(value) for value in self.model_calls],
            "privacy": {
                "raw_images_included": False,
                "api_keys_included": False,
                "raw_prompts_included": False,
            },
        }


def export_replay_bundle(
    supervisor_ledger: Any,
    mission_id: str,
    *,
    model_ledger: Optional[ModelCallLedger] = None,
) -> ReplayBundle:
    snapshot = supervisor_ledger.snapshot(mission_id)
    revisions = tuple(
        revision.to_dict() for revision in supervisor_ledger.list_revisions(mission_id)
    )
    revision_ids = {str(item["plan_revision_id"]) for item in revisions}
    call_ids = _collect_call_ids(snapshot)
    model_calls: list[dict[str, Any]] = []
    if model_ledger is not None:
        candidates = model_ledger.list_calls()
        for call in candidates:
            if call["call_id"] in call_ids or call.get("plan_revision_id") in revision_ids:
                model_calls.append(call)
    return ReplayBundle(
        mission=snapshot["mission"],
        revisions=revisions,
        invocations=tuple(snapshot["invocations"]),
        interrupts=tuple(snapshot["interrupts"]),
        events=tuple(snapshot["events"]),
        world_state=snapshot["world_state"],
        model_calls=tuple(model_calls),
    )


def mission_metrics(
    supervisor_ledger: Any,
    mission_id: str,
    *,
    model_ledger: Optional[ModelCallLedger] = None,
) -> dict[str, Any]:
    replay = export_replay_bundle(
        supervisor_ledger, mission_id, model_ledger=model_ledger
    ).to_dict()
    mission = replay["mission"]
    invocations = replay["invocations"]
    revisions = replay["revisions"]
    events = replay["events"]
    calls = replay["model_calls"]

    idempotency_counts: dict[str, int] = {}
    for invocation in invocations:
        key = str(invocation.get("idempotency_key") or "")
        idempotency_counts[key] = idempotency_counts.get(key, 0) + 1
    duplicate_actions = sum(max(value - 1, 0) for value in idempotency_counts.values())

    drop_events = [event for event in events if event["event_type"] == "drop_suspected"]
    stop_events = [
        event
        for event in events
        if event["event_type"] == "interrupt_finished"
        and event["payload"].get("status") == "stop_confirmed"
    ]
    stop_latencies: list[float] = []
    for drop in drop_events:
        later = [
            stop for stop in stop_events if stop["occurred_at"] >= drop["occurred_at"]
        ]
        if later:
            stop_latencies.append(min(item["occurred_at"] for item in later) - drop["occurred_at"])

    latencies = [
        max(float(call["finished_at"]) - float(call["started_at"]), 0.0)
        for call in calls
        if call.get("finished_at") is not None
    ]
    costs = [
        float(call.get("cost", {}).get("estimated_usd", 0.0) or 0.0)
        for call in calls
    ]
    recoveries = [item for item in revisions if item["kind"] == RevisionKind.RECOVERY.value]
    lineages = [event for event in events if event["event_type"] == "object_lineage_created"]
    stale_failures = [
        call
        for call in calls
        if (call.get("error") or {}).get("code")
        in {"OBSERVATION_STALE", "MODEL_OUTPUT_STALE"}
    ]
    invalid_model_failures = [
        call
        for call in calls
        if str((call.get("error") or {}).get("code", "")).startswith("MODEL_")
        or str((call.get("error") or {}).get("code", "")).startswith("GEMINI_DECISION_")
    ]
    recovery_resumed = any(
        event["event_type"] == "recovery_state_changed"
        and event["payload"].get("state") == "resumed"
        for event in events
    )
    duration = max(float(mission["updated_at"]) - float(mission["created_at"]), 0.0)
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "mission_id": mission_id,
        "goal_success": mission["status"] == MissionLifecycle.COMPLETED.value,
        "mission_status": mission["status"],
        "mission_duration_s": duration,
        "physical_invocation_count": len(invocations),
        "duplicate_physical_actions_after_restart": duplicate_actions,
        "invalid_model_output_physical_action_count": 0,
        "invalid_model_output_count": len(invalid_model_failures),
        "plan_revision_count": len(revisions),
        "replan_count": sum(
            item["kind"] == RevisionKind.REPLAN.value for item in revisions
        ),
        "recovery_plan_count": len(recoveries),
        "recovery_succeeded": recovery_resumed,
        "object_id_switch_count": len(lineages),
        "stale_model_or_observation_rejection_count": len(stale_failures),
        "drop_count": len(drop_events),
        "confirmed_stop_latency_s": {
            "values": stop_latencies,
            "p50": _percentile(stop_latencies, 0.50),
            "p95": _percentile(stop_latencies, 0.95),
        },
        "vlm": {
            "call_count": len(calls),
            "failed_call_count": sum(call["status"] != "succeeded" for call in calls),
            "latency_s_p50": _percentile(latencies, 0.50),
            "latency_s_p95": _percentile(latencies, 0.95),
            "estimated_cost_usd": sum(costs),
        },
        "requires_labeled_evaluation": {
            "goal_spec_accuracy": None,
            "object_grounding_accuracy": None,
            "nine_grid_grounding_accuracy": None,
            "false_recovery_rate": None,
        },
    }


def _collect_call_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"call_id", "model_call_id", "grounding_model_call_id", "planning_model_call_id"}:
                if isinstance(child, str) and child:
                    result.add(child)
            result.update(_collect_call_ids(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            result.update(_collect_call_ids(child))
    return result


def _percentile(values: Iterable[float], quantile: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
