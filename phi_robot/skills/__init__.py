"""Unified robot skill contracts and dispatching.

The skills package is deliberately independent from ROS imports.  Production
adapters are injected at the composition root, while tests can use scripted
or in-memory backends without touching hardware.
"""

from .catalog import build_skill_dispatcher, build_skill_registry
from .dispatcher import SkillDispatcher
from .models import (
    CancellationMode,
    CancellationToken,
    ErrorCategory,
    InvocationState,
    SkillContext,
    SkillDefinition,
    SkillError,
    SkillKind,
    SkillOutcome,
    SkillRequest,
    SkillResult,
    VerificationResult,
)
from .registry import SkillRegistry

__all__ = [
    "CancellationMode",
    "CancellationToken",
    "ErrorCategory",
    "InvocationState",
    "SkillContext",
    "SkillDefinition",
    "SkillDispatcher",
    "SkillError",
    "SkillKind",
    "SkillOutcome",
    "SkillRegistry",
    "SkillRequest",
    "SkillResult",
    "VerificationResult",
    "build_skill_dispatcher",
    "build_skill_registry",
]
