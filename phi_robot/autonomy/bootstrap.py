"""Explicit composition root for Gemini-driven autonomy.

This module is never selected by the existing ROS/legacy startup path.  A
deployment must explicitly construct it with a semantic backend, observation
source, location resolver, interrupt lane, and credentials configuration after
its replay/simulation/HIL gate is approved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .agent_tools import build_agent_skill_dispatcher
from .closed_loop import ClosedLoopAgentController
from .gemini_provider import (
    GeminiJSONTransport,
    GeminiRoboticsER2Config,
    GeminiRoboticsER2Provider,
)
from .ledger import SupervisorLedger
from .locations import NineGridLocationResolver
from .model_gateway import ModelCallLedger, ModelGateway
from .planner_agent import ObservationSource, PlannerAgent
from .recovery import DropRecoveryCoordinator
from .service import AutonomyService
from .supervisor import ExecutionSupervisor


@dataclass
class GeminiAutonomyRuntime:
    robot_id: str
    supervisor_ledger: SupervisorLedger
    model_ledger: ModelCallLedger
    dispatcher: Any
    supervisor: ExecutionSupervisor
    gateway: ModelGateway
    planner: PlannerAgent
    closed_loop: ClosedLoopAgentController
    recovery: DropRecoveryCoordinator
    service: AutonomyService
    owns_supervisor_ledger: bool = True

    def close(self) -> None:
        self.model_ledger.close()
        if self.owns_supervisor_ledger:
            self.supervisor_ledger.close()


def build_gemini_autonomy_runtime(
    *,
    robot_id: str,
    semantic_backend: Any,
    observation_source: ObservationSource,
    location_resolver: NineGridLocationResolver,
    interrupt_lane: Any,
    supervisor_ledger_path: str | Path | None,
    model_ledger_path: str | Path,
    config: Optional[GeminiRoboticsER2Config] = None,
    transport: Optional[GeminiJSONTransport] = None,
    audit_enabled: bool = True,
    dispatcher: Any = None,
    supervisor: Optional[ExecutionSupervisor] = None,
) -> GeminiAutonomyRuntime:
    """Build but do not start or invoke the cloud/robot runtime."""

    resolved_config = config or GeminiRoboticsER2Config.from_env()
    provider = GeminiRoboticsER2Provider(resolved_config, transport=transport)
    owns_supervisor_ledger = supervisor is None
    if supervisor is not None:
        if dispatcher is not None and dispatcher is not supervisor.dispatcher:
            raise ValueError("dispatcher must be the existing Supervisor dispatcher")
        dispatcher = supervisor.dispatcher
        supervisor_ledger = supervisor.ledger
    else:
        dispatcher = dispatcher or build_agent_skill_dispatcher(
            semantic_backend, audit_enabled=audit_enabled
        )
        if supervisor_ledger_path is None:
            raise ValueError("supervisor_ledger_path is required without an existing Supervisor")
        supervisor_ledger = SupervisorLedger(supervisor_ledger_path)
    model_ledger = ModelCallLedger(model_ledger_path)
    if supervisor is None:
        supervisor = ExecutionSupervisor(
            robot_id=robot_id,
            dispatcher=dispatcher,
            ledger=supervisor_ledger,
            interrupt_lane=interrupt_lane,
        )
    elif supervisor.robot_id != robot_id:
        raise ValueError("existing Supervisor belongs to a different robot")
    gateway = ModelGateway(
        providers={provider.name: provider},
        ledger=model_ledger,
        skill_registry=dispatcher.registry,
    )
    planner = PlannerAgent(
        gateway=gateway,
        provider_name=provider.name,
        observation_source=observation_source,
        location_resolver=location_resolver,
        supervisor=supervisor,
        skill_registry=dispatcher.registry,
    )
    closed_loop = ClosedLoopAgentController(
        gateway=gateway,
        provider_name=provider.name,
        observation_source=observation_source,
        plan_validator=planner,
    )
    supervisor.boundary_gate = closed_loop
    recovery = DropRecoveryCoordinator(
        supervisor=supervisor,
        gateway=gateway,
        provider_name=provider.name,
        observation_source=observation_source,
        plan_validator=planner,
    )
    service = AutonomyService(
        planner=planner,
        model_ledger=model_ledger,
        recovery=recovery,
    )
    return GeminiAutonomyRuntime(
        robot_id=robot_id,
        supervisor_ledger=supervisor_ledger,
        model_ledger=model_ledger,
        dispatcher=dispatcher,
        supervisor=supervisor,
        gateway=gateway,
        planner=planner,
        closed_loop=closed_loop,
        recovery=recovery,
        service=service,
        owns_supervisor_ledger=owns_supervisor_ledger,
    )
