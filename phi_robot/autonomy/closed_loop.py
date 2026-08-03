"""Phase 6 observe-decide-act-verify gate and explicit plan-tail revisions."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .contracts import ActionLifecycle, PlanGraph, RevisionKind
from .goals import GoalSpec
from .model_gateway import ModelDecision, ModelGateway
from .planner_agent import ObservationSource, PlannerAgent
from .supervisor import NodeBoundaryDecision


VERIFY_PROMPT_VERSION = "closed-loop-verify-v1"


class ClosedLoopAgentController:
    """Supervisor boundary gate backed by fresh multimodal evidence.

    The gate runs only after a dispatched action has a known terminal result.
    A timeout/unknown action never reaches this class; it remains in mandatory
    reconciliation instead of being reinterpreted by a cloud model.
    """

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        provider_name: str,
        observation_source: ObservationSource,
        plan_validator: PlannerAgent,
        channels: tuple[str, ...] = ("rgb", "overlay", "mask", "depth"),
        timeout_s: float = 30.0,
        max_reobservations: int = 2,
    ) -> None:
        self.gateway = gateway
        self.provider_name = provider_name
        self.observation_source = observation_source
        self.plan_validator = plan_validator
        self.channels = channels
        self.timeout_s = timeout_s
        self.max_reobservations = max(0, int(max_reobservations))

    def after_node(
        self,
        supervisor: Any,
        *,
        mission_id: str,
        revision: Any,
        node: Any,
        lifecycle: ActionLifecycle,
        result: Mapping[str, Any],
        world_state: Any,
    ) -> NodeBoundaryDecision:
        goal_raw = revision.graph.metadata.get("goal_spec")
        if not isinstance(goal_raw, Mapping):
            # Compatibility/fixed plans remain continuous without a VLM gate.
            return NodeBoundaryDecision(
                action="continue",
                reason="non-agent compatibility plan has no GoalSpec",
            )
        goal = GoalSpec.from_dict(goal_raw)
        observation = self.observation_source.capture(
            channels=self.channels,
            reason=f"post-action verification for {node.node_id}",
        )
        current_world = int(self.observation_source.current_world_version())
        attempts = 0
        while True:
            decision = self.gateway.decide(
                provider_name=self.provider_name,
                task="verify_action",
                prompt=_verification_prompt(
                    goal=goal,
                    graph=revision.graph,
                    node=node,
                    lifecycle=lifecycle,
                    result=result,
                    world_state=world_state.to_dict(),
                    reobservation_attempt=attempts,
                ),
                prompt_version=VERIFY_PROMPT_VERSION,
                observation=observation,
                current_world_version=current_world,
                plan_revision_id=revision.plan_revision_id,
                timeout_s=self.timeout_s,
            )
            latest_world = int(self.observation_source.current_world_version())
            if latest_world != observation.world_version:
                if attempts >= self.max_reobservations:
                    return NodeBoundaryDecision(
                        action="pause",
                        reason="world changed while verification was in flight",
                        evidence={
                            "observation_world_version": observation.world_version,
                            "latest_world_version": latest_world,
                            "discarded_model_call_id": decision.call_id,
                        },
                    )
                attempts += 1
                observation = self.observation_source.capture(
                    channels=self.channels,
                    reason="world changed while model response was in flight",
                )
                current_world = int(self.observation_source.current_world_version())
                continue
            conflict = decision.payload.get("evidence_conflict")
            if isinstance(conflict, Mapping) and conflict:
                supervisor.ledger.append_event(
                    mission_id,
                    "evidence_conflict",
                    {
                        "node_id": node.node_id,
                        "model_call_id": decision.call_id,
                        "observation_id": observation.observation_id,
                        "deterministic_and_visual": dict(conflict),
                    },
                    node_id=node.node_id,
                )

            if decision.decision_type == "request_observation" or (
                decision.decision_type == "verify"
                and decision.payload.get("verdict") == "reobserve"
            ):
                if attempts >= self.max_reobservations:
                    return NodeBoundaryDecision(
                        action="intervention",
                        reason="model exhausted the fresh-observation budget",
                        evidence=_decision_evidence(decision, observation.observation_id),
                    )
                attempts += 1
                requested = decision.payload.get("channels")
                channels = (
                    tuple(str(value) for value in requested)
                    if isinstance(requested, list) and requested
                    else self.channels
                )
                observation = self.observation_source.capture(
                    channels=channels,
                    reason=str(decision.payload.get("reason") or "model requested fresh evidence"),
                )
                current_world = int(self.observation_source.current_world_version())
                continue
            return self._directive(
                supervisor,
                mission_id=mission_id,
                revision=revision,
                node=node,
                lifecycle=lifecycle,
                goal=goal,
                observation=observation,
                decision=decision,
            )

    def _directive(
        self,
        supervisor: Any,
        *,
        mission_id: str,
        revision: Any,
        node: Any,
        lifecycle: ActionLifecycle,
        goal: GoalSpec,
        observation: Any,
        decision: ModelDecision,
    ) -> NodeBoundaryDecision:
        evidence = _decision_evidence(decision, observation.observation_id)
        if decision.decision_type == "verify":
            verdict = str(decision.payload.get("verdict") or "")
            if verdict == "continue" and lifecycle == ActionLifecycle.SUCCEEDED:
                return NodeBoundaryDecision(
                    action="continue", reason=decision.summary, evidence=evidence
                )
            if verdict == "finish" and lifecycle == ActionLifecycle.SUCCEEDED:
                return NodeBoundaryDecision(
                    action="replan",
                    reason="visual and deterministic evidence satisfy GoalSpec",
                    replacement_graph=_completed_prefix_graph(
                        supervisor, mission_id, revision.graph, decision
                    ),
                    evidence=evidence,
                )
            if verdict in {"abort", "impossible"}:
                return NodeBoundaryDecision(
                    action="fail", reason=decision.summary, evidence=evidence
                )
            if verdict in {"intervention", "uncertain"}:
                return NodeBoundaryDecision(
                    action="intervention", reason=decision.summary, evidence=evidence
                )
            # A failed physical action may never be waved through as continue.
            if lifecycle != ActionLifecycle.SUCCEEDED:
                return NodeBoundaryDecision(
                    action="pause",
                    reason="failed action requires an explicit evidence-based replan",
                    evidence=evidence,
                )
            return NodeBoundaryDecision(
                action="pause",
                reason=f"unsupported verification verdict: {verdict or '<missing>'}",
                evidence=evidence,
            )

        if decision.decision_type in {"replan", "recover"}:
            graph = self.plan_validator._validated_plan(decision, goal, observation)
            if lifecycle != ActionLifecycle.SUCCEEDED:
                graph = _with_superseded_node(graph, node.node_id)
            return NodeBoundaryDecision(
                action="replan",
                reason=decision.summary,
                replacement_graph=graph,
                revision_kind=(
                    RevisionKind.RECOVERY
                    if decision.decision_type == "recover"
                    else RevisionKind.REPLAN
                ),
                evidence=evidence,
            )
        if decision.decision_type == "finish" and lifecycle == ActionLifecycle.SUCCEEDED:
            return NodeBoundaryDecision(
                action="replan",
                reason=decision.summary,
                replacement_graph=_completed_prefix_graph(
                    supervisor, mission_id, revision.graph, decision
                ),
                evidence=evidence,
            )
        if decision.decision_type == "abort":
            return NodeBoundaryDecision(action="fail", reason=decision.summary, evidence=evidence)
        return NodeBoundaryDecision(
            action="pause",
            reason=f"unsupported closed-loop decision type: {decision.decision_type}",
            evidence=evidence,
        )


def _with_superseded_node(graph: PlanGraph, node_id: str) -> PlanGraph:
    if node_id not in {node.node_id for node in graph.nodes}:
        raise ValueError("replan must preserve the failed dispatched node")
    superseded = graph.superseded_node_ids | {node_id}
    return PlanGraph(
        schema_version=graph.schema_version,
        plan_id=graph.plan_id,
        goal_id=graph.goal_id,
        nodes=graph.nodes,
        metadata={**dict(graph.metadata), "superseded_node_ids": sorted(superseded)},
    )


def _completed_prefix_graph(
    supervisor: Any,
    mission_id: str,
    graph: PlanGraph,
    decision: ModelDecision,
) -> PlanGraph:
    invoked = {item["node_id"] for item in supervisor.ledger.list_invocations(mission_id)}
    nodes = tuple(node for node in graph.nodes if node.node_id in invoked)
    if not nodes:
        raise ValueError("cannot finish a mission without persisted action evidence")
    retained = {node.node_id for node in nodes}
    for node in nodes:
        if not set(node.depends_on) <= retained:
            raise ValueError("cannot prune a completed prefix with missing dependencies")
    superseded = graph.superseded_node_ids & retained
    return PlanGraph(
        plan_id=f"{graph.plan_id}-finished-{decision.decision_id}",
        goal_id=graph.goal_id,
        nodes=nodes,
        metadata={
            **dict(graph.metadata),
            "superseded_node_ids": sorted(superseded),
            "finished_by_model_decision": decision.decision_id,
        },
    )


def _decision_evidence(decision: ModelDecision, observation_id: str) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "model_call_id": decision.call_id,
        "provider": decision.provider,
        "model": decision.model,
        "observation_id": observation_id,
        "world_version": decision.world_version,
    }


def _verification_prompt(
    *,
    goal: GoalSpec,
    graph: PlanGraph,
    node: Any,
    lifecycle: ActionLifecycle,
    result: Mapping[str, Any],
    world_state: Mapping[str, Any],
    reobservation_attempt: int,
) -> str:
    payload = {
        "goal_spec": goal.to_dict(),
        "active_plan": graph.to_dict(),
        "completed_node": node.to_dict(),
        "action_lifecycle": lifecycle.value,
        "structured_action_result": dict(result),
        "deterministic_world_state": dict(world_state),
        "reobservation_attempt": reobservation_attempt,
    }
    return (
        "Compare the new visual evidence with deterministic state and the action's required success "
        "evidence. Choose continue, request_observation, replan, recover, finish, or abort. Never "
        "reinterpret an unknown physical outcome as success. If deterministic and visual evidence "
        "conflict, preserve both in payload.evidence_conflict. For replan/recover return a complete "
        "PlanGraph that preserves every dispatched node byte-for-byte and replaces only the "
        "unexecuted tail; a failed/cancelled retained node must be listed in "
        "metadata.superseded_node_ids. Already successful physical nodes must not be repeated.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
