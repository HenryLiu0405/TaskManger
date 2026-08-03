"""Natural-language, visually grounded planner loop for Phase 5."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Protocol

from phi_robot.skills.models import SkillContext, SkillRequest

from .agent_tools import SUPERVISOR_CONTROL_TOOLS
from .contracts import PlanGraph
from .goals import GoalGroundingError, GoalSpec
from .locations import LocationResolutionError, NineGridLocationResolver
from .model_gateway import ModelDecision, ModelGateway, ModelGatewayError
from .observations import ObservationBundle


GROUNDING_PROMPT_VERSION = "goal-grounding-v1"
PLANNING_PROMPT_VERSION = "semantic-plan-v1"
DEFAULT_CHANNELS = ("rgb", "overlay", "mask", "depth")


class AgentPlanningError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class ObservationSource(Protocol):
    def capture(
        self,
        *,
        channels: Iterable[str],
        reason: str,
    ) -> ObservationBundle:
        ...

    def current_world_version(self) -> int:
        ...


@dataclass(frozen=True)
class AgentTask:
    mission_id: str
    request_id: str
    goal: GoalSpec
    initial_observation_id: str
    grounding_decision_id: str
    planning_decision_id: str
    initial_plan_revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "request_id": self.request_id,
            "goal_spec": self.goal.to_dict(),
            "initial_observation_id": self.initial_observation_id,
            "grounding_decision_id": self.grounding_decision_id,
            "planning_decision_id": self.planning_decision_id,
            "initial_plan_revision_id": self.initial_plan_revision_id,
        }


class PlannerAgent:
    """Ground one instruction, create a complete semantic plan, and submit it.

    The model never receives the physical dispatcher.  It sees only the
    planner-visible semantic registry and the resulting graph is submitted to
    the one authoritative ``ExecutionSupervisor``.
    """

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        provider_name: str,
        observation_source: ObservationSource,
        location_resolver: NineGridLocationResolver,
        supervisor: Any,
        skill_registry: Any,
        channels: Iterable[str] = DEFAULT_CHANNELS,
        model_timeout_s: float = 30.0,
    ) -> None:
        self.gateway = gateway
        self.provider_name = provider_name
        self.observation_source = observation_source
        self.location_resolver = location_resolver
        self.supervisor = supervisor
        self.skill_registry = skill_registry
        self.channels = tuple(channels)
        self.model_timeout_s = model_timeout_s
        if not self.channels:
            raise ValueError("PlannerAgent requires at least one observation channel")

    def submit_instruction(
        self,
        instruction: str,
        *,
        request_id: Optional[str] = None,
        background: bool = True,
        replay_keys: Optional[Mapping[str, str]] = None,
    ) -> AgentTask:
        instruction = str(instruction).strip()
        if not instruction:
            raise AgentPlanningError("INSTRUCTION_EMPTY", "instruction must be non-empty")
        request_id = request_id or f"agent-request-{uuid.uuid4().hex}"
        replay_keys = dict(replay_keys or {})
        observation = self.observation_source.capture(
            channels=self.channels,
            reason="initial natural-language goal grounding",
        )
        self._assert_current_observation(observation)

        grounding = self.gateway.decide(
            provider_name=self.provider_name,
            task="ground_goal",
            prompt=_grounding_prompt(instruction),
            prompt_version=GROUNDING_PROMPT_VERSION,
            observation=observation,
            current_world_version=observation.world_version,
            timeout_s=self.model_timeout_s,
            replay_key=replay_keys.get("ground_goal"),
        )
        if grounding.decision_type != "goal":
            raise AgentPlanningError(
                "GROUNDING_DECISION_INVALID",
                f"expected goal decision, got {grounding.decision_type}",
            )
        try:
            goal = GoalSpec.from_model_payload(
                grounding.payload,
                instruction=instruction,
                observation=observation,
            )
            # Resolve only to prove this deployment can execute the symbolic
            # destination.  Numeric coordinates are not copied into GoalSpec.
            self.location_resolver.resolve(goal.destination)
        except (GoalGroundingError, LocationResolutionError, ValueError) as exc:
            code = getattr(exc, "code", "GOAL_GROUNDING_INVALID")
            raise AgentPlanningError(str(code), str(exc)) from exc

        self._assert_current_observation(observation)
        planning = self.gateway.decide(
            provider_name=self.provider_name,
            task="create_plan",
            prompt=_planning_prompt(goal, self.skill_registry),
            prompt_version=PLANNING_PROMPT_VERSION,
            observation=observation,
            current_world_version=observation.world_version,
            timeout_s=self.model_timeout_s,
            replay_key=replay_keys.get("create_plan"),
        )
        self._assert_current_observation(observation)
        if planning.decision_type != "plan":
            raise AgentPlanningError(
                "PLAN_DECISION_INVALID",
                f"expected plan decision, got {planning.decision_type}",
            )
        graph = self._validated_plan(planning, goal, observation)
        mission_id = self.supervisor.submit_plan(
            graph,
            request_id=request_id,
            metadata={
                "source": "natural_language_agent",
                "goal_spec": goal.to_dict(),
                "initial_observation_id": observation.observation_id,
                "grounding_model_call_id": grounding.call_id,
                "planning_model_call_id": planning.call_id,
                "provider": planning.provider,
                "model": planning.model,
            },
        )
        revision = self.supervisor.ledger.active_revision(mission_id)
        self.supervisor.ledger.append_event(
            mission_id,
            "agent_task_submitted",
            {
                "instruction": instruction,
                "goal_spec": goal.to_dict(),
                "grounding_decision_id": grounding.decision_id,
                "planning_decision_id": planning.decision_id,
                "observation_id": observation.observation_id,
                "world_version": observation.world_version,
            },
        )
        self.supervisor.start(mission_id, background=background)
        return AgentTask(
            mission_id=mission_id,
            request_id=request_id,
            goal=goal,
            initial_observation_id=observation.observation_id,
            grounding_decision_id=grounding.decision_id,
            planning_decision_id=planning.decision_id,
            initial_plan_revision_id=revision.plan_revision_id,
        )

    def _assert_current_observation(self, observation: ObservationBundle) -> None:
        current = int(self.observation_source.current_world_version())
        if not observation.is_fresh(current_world_version=current):
            raise AgentPlanningError(
                "OBSERVATION_STALE",
                "world changed during grounding/planning; capture fresh evidence before execution",
                details={
                    "observation_world_version": observation.world_version,
                    "current_world_version": current,
                },
            )

    def _validated_plan(
        self,
        decision: ModelDecision,
        goal: GoalSpec,
        observation: ObservationBundle,
    ) -> PlanGraph:
        raw = decision.payload.get("plan_graph")
        if not isinstance(raw, Mapping):
            raise AgentPlanningError("PLAN_PAYLOAD_INVALID", "plan payload requires plan_graph")
        try:
            graph = PlanGraph.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentPlanningError("PLAN_GRAPH_INVALID", str(exc)) from exc
        if graph.goal_id != goal.goal_id:
            raise AgentPlanningError("PLAN_GOAL_MISMATCH", "plan goal_id differs from GoalSpec")

        dependents = {dependency for node in graph.nodes for dependency in node.depends_on}
        terminal_nodes = [node for node in graph.nodes if node.node_id not in dependents]
        if not terminal_nodes or any(not node.success_evidence for node in terminal_nodes):
            raise AgentPlanningError(
                "PLAN_SUCCESS_EVIDENCE_MISSING",
                "every terminal plan branch requires explicit success evidence",
            )
        for node in graph.nodes:
            if node.skill_name in SUPERVISOR_CONTROL_TOOLS:
                raise AgentPlanningError(
                    "PLAN_INTERRUPT_TOOL_FORBIDDEN",
                    f"{node.skill_name} must use the Supervisor out-of-band tool bridge",
                )
            definition = self.skill_registry.get(node.skill_name, node.skill_version)
            if definition is None or not definition.planner_visible or definition.operator_only:
                raise AgentPlanningError(
                    "PLAN_TOOL_FORBIDDEN",
                    f"plan references non-agent skill {node.skill_name}@{node.skill_version}",
                )
            request = SkillRequest(
                skill_name=node.skill_name,
                version=node.skill_version,
                args=dict(node.args),
                context=SkillContext(
                    robot_id=observation.robot_id,
                    source="planner_validation",
                    request_id=decision.call_id,
                    goal_id=goal.goal_id,
                    step_id=node.node_id,
                ),
            )
            errors = self.skill_registry.validate(request)
            if errors:
                raise AgentPlanningError(
                    "PLAN_TOOL_ARGS_INVALID",
                    f"{node.node_id}: {'; '.join(errors)}",
                )
            self._validate_goal_binding(node, goal)

        metadata = {
            **dict(graph.metadata),
            "semantic": True,
            "goal_spec": goal.to_dict(),
            "observation_id": observation.observation_id,
            "world_version": observation.world_version,
            "model_decision": {
                "decision_id": decision.decision_id,
                "call_id": decision.call_id,
                "provider": decision.provider,
                "model": decision.model,
            },
        }
        return PlanGraph(
            schema_version=graph.schema_version,
            plan_id=graph.plan_id,
            goal_id=graph.goal_id,
            nodes=graph.nodes,
            metadata=metadata,
        )

    @staticmethod
    def _validate_goal_binding(node: Any, goal: GoalSpec) -> None:
        logical_id = node.args.get("logical_object_id")
        if logical_id is not None and str(logical_id) != goal.object_ref.logical_object_id:
            raise AgentPlanningError(
                "PLAN_OBJECT_MISMATCH",
                f"node {node.node_id} references another logical object",
            )
        object_ref = node.args.get("object_ref")
        if isinstance(object_ref, Mapping):
            if object_ref.get("logical_object_id") != goal.object_ref.logical_object_id:
                raise AgentPlanningError(
                    "PLAN_OBJECT_MISMATCH",
                    f"node {node.node_id} selects another logical object",
                )
        if node.skill_name in {"place_at_location", "verify_placement"}:
            location = node.args.get("location_ref")
            if location != goal.destination.to_dict():
                raise AgentPlanningError(
                    "PLAN_DESTINATION_MISMATCH",
                    f"node {node.node_id} final destination differs from GoalSpec",
                )


def _grounding_prompt(instruction: str) -> str:
    return (
        "Ground the user's instruction against the complete observed object candidate set. "
        "Choose exactly one provider perception object_id, preserve provider, tracker_session_id, "
        "and observation_id in ObjectRef, and represent the destination as a semantic LocationRef. "
        "The currently executable destination namespace is grid with IDs nw,n,ne,w,c,e,sw,s,se; "
        "do not invent numeric destination coordinates. Return decision_type='goal' and payload "
        "with goal_spec containing object_ref, destination, constraints, and explicit success_criteria. "
        f"Original user instruction: {instruction}"
    )


def _planning_prompt(goal: GoalSpec, registry: Any) -> str:
    tools = [
        definition.to_function_schema()
        for definition in registry.definitions(planner_visible_only=True)
        if not definition.operator_only
    ]
    return (
        "Create a complete semantic PlanGraph for the grounded GoalSpec. The graph is not a fixed "
        "pick/move/place template: choose whatever observation, selection, motion, manipulation, "
        "verification, or anomaly tools the evidence requires. Use only listed semantic tools. "
        "Every terminal branch must declare success_evidence. Never include raw ROS topics, joint "
        "commands, velocity commands, safety bypass, service administration, or operator authority. "
        "Do not copy resolved numeric coordinates into the durable goal. Return decision_type='plan' "
        "and payload.plan_graph matching PlanGraph schema.\n"
        f"GoalSpec: {json.dumps(goal.to_dict(), ensure_ascii=False, sort_keys=True)}\n"
        f"Semantic tools: {json.dumps(tools, ensure_ascii=False, sort_keys=True)}"
    )
