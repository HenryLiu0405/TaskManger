"""Versioned autonomy runtime contracts and execution components.

The package is deliberately standard-library-only.  Importing it never starts
ROS, opens a network connection, or dispatches a physical action.
"""

from .contracts import (
    ActionLifecycle,
    ActionTimeouts,
    MissionLifecycle,
    PlanGraph,
    PlanNode,
    PlanRevision,
    RevisionKind,
)
from .world_state import RobotWorldState, StateDimension, StateValue
from .ledger import SupervisorLedger
from .runtime import RobotRuntime, RobotRuntimeRegistry
from .supervisor import ExecutionSupervisor
from .locations import LocationRef, ResolvedLocation, NineGridLocationResolver
from .objects import ObjectRef, PerceptionRef, SelectionToken
from .observations import ObservationBundle, VisualRingBuffer, DropEvent
from .goals import GoalSpec

__all__ = [
    "ActionLifecycle",
    "ActionTimeouts",
    "MissionLifecycle",
    "PlanGraph",
    "PlanNode",
    "PlanRevision",
    "RevisionKind",
    "RobotWorldState",
    "StateDimension",
    "StateValue",
    "SupervisorLedger",
    "RobotRuntime",
    "RobotRuntimeRegistry",
    "ExecutionSupervisor",
    "LocationRef",
    "ResolvedLocation",
    "NineGridLocationResolver",
    "ObjectRef",
    "PerceptionRef",
    "SelectionToken",
    "ObservationBundle",
    "VisualRingBuffer",
    "DropEvent",
    "GoalSpec",
]
