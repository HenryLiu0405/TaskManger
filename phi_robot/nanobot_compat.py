"""Load selected nanobot tool modules without importing the full package tree.

The repository's top-level ``nanobot`` package imports optional runtime
dependencies that are not available in this environment.  Phase 2 only needs
the tool abstraction and registry, so this module loads those source files
directly and registers the expected module names in ``sys.modules``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _ensure_package(name: str) -> ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = module
    return module


def _load_module(module_name: str, file_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_name} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_tool_base() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[1]
    vendor_root = Path(__file__).resolve().parent / "vendor"
    # Prefer vendor copy if present
    vendor_path = vendor_root / "nanobot" / "agent" / "tools" / "base.py"
    repo_path = repo_root / "nanobot" / "agent" / "tools" / "base.py"
    _ensure_package("nanobot")
    _ensure_package("nanobot.agent")
    _ensure_package("nanobot.agent.tools")
    if vendor_path.exists():
        return _load_module("nanobot.agent.tools.base", vendor_path)
    return _load_module("nanobot.agent.tools.base", repo_path)


def load_tool_registry() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[1]
    vendor_root = Path(__file__).resolve().parent / "vendor"
    if "nanobot.agent.tools.base" not in sys.modules:
        load_tool_base()
    _ensure_package("nanobot")
    _ensure_package("nanobot.agent")
    _ensure_package("nanobot.agent.tools")
    vendor_path = vendor_root / "nanobot" / "agent" / "tools" / "registry.py"
    repo_path = repo_root / "nanobot" / "agent" / "tools" / "registry.py"
    if vendor_path.exists():
        return _load_module("nanobot.agent.tools.registry", vendor_path)
    return _load_module("nanobot.agent.tools.registry", repo_path)


_BASE = load_tool_base()
_REGISTRY = load_tool_registry()

Tool = _BASE.Tool
tool_parameters = _BASE.tool_parameters
ToolRegistry = _REGISTRY.ToolRegistry
