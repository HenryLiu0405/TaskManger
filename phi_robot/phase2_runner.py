"""Phase 2 acceptance runner.

Validates the tool layer integration built on top of ``nanobot``'s Tool and
ToolRegistry abstractions.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC, FakeRobotService
from phi_robot.robot_tools import RobotToolSuite


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: dict[str, Any]


def _execute_tool(registry, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    tool = registry.get(tool_name)
    if tool is None:
        raise KeyError(tool_name)
    return asyncio_run(registry.execute(tool_name, params))


def asyncio_run(awaitable):
    import asyncio

    return asyncio.run(awaitable)


def _make_suite(spec: dict[str, Any] | None = None, *, max_retries: int = 1) -> tuple[RobotToolSuite, Any]:
    backend = FakeRobotService.from_spec(spec or DEFAULT_FAKE_SPEC)
    suite = RobotToolSuite()
    suite.client.backend = backend
    suite.client.max_retries = max_retries
    return suite, backend


def run_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []

    suite, backend = _make_suite()
    registry = suite.build_registry()

    definitions = [schema["function"]["name"] for schema in registry.get_definitions()]
    checks.append(
        CheckResult(
            name="registry_definitions",
            passed=definitions == ["get_gripper_state", "get_pose", "move_to", "pick", "place"],
            details={"definitions": definitions},
        )
    )

    move_result = _execute_tool(
        registry,
        "move_to",
        {
            "request_id": "phase2-move-001",
            "goal_id": "phase2-goal",
            "step_id": "step-001",
            "x": 1.2,
            "y": 0.3,
            "z": 0.0,
            "speed": 0.2,
            "timeout_s": 20,
        },
    )
    pick_result = _execute_tool(
        registry,
        "pick",
        {
            "request_id": "phase2-pick-001",
            "goal_id": "phase2-goal",
            "step_id": "step-002",
            "object_id": "box-42",
            "timeout_s": 20,
        },
    )
    place_result = _execute_tool(
        registry,
        "place",
        {
            "request_id": "phase2-place-001",
            "goal_id": "phase2-goal",
            "step_id": "step-003",
            "x": 2.0,
            "y": 0.5,
            "z": 0.0,
            "timeout_s": 20,
        },
    )

    checks.append(
        CheckResult(
            name="happy_path_tool_calls",
            passed=all(result["status"] == "ok" for result in (move_result, pick_result, place_result)),
            details={"move": move_result, "pick": pick_result, "place": place_result},
        )
    )

    checks.append(
        CheckResult(
            name="structured_logging",
            passed=all(
                record.request_id and record.goal_id and record.step_id and record.tool
                for record in suite.client.call_log
            ),
            details={
                "log_count": len(suite.client.call_log),
                "first_log": suite.client.call_log[0].__dict__ if suite.client.call_log else {},
            },
        )
    )

    invalid_result = asyncio_run(registry.execute(
        "move_to",
        {
            "request_id": "phase2-invalid-001",
            "goal_id": "phase2-goal",
            "step_id": "step-004",
            "x": 1.2,
            "y": 0.3,
            "z": 0.0,
        },
    ))
    checks.append(
        CheckResult(
            name="validation_blocks_invalid_args",
            passed=isinstance(invalid_result, str)
            and "Invalid parameters" in invalid_result
            and backend.call_counters.get("move_to", 0) == 1,
            details={
                "response": invalid_result,
                "move_call_count": backend.call_counters.get("move_to", 0),
            },
        )
    )

    retry_spec = copy.deepcopy(DEFAULT_FAKE_SPEC)
    retry_spec["fault_rules"] = [
        {
            "tool": "pick",
            "call_index": 1,
            "error_code": "GRIP_FAIL",
            "message": "first pick attempt failed by injection",
            "repeat": False,
            "args_match": {"object_id": "box-42"},
        }
    ]
    retry_suite, retry_backend = _make_suite(retry_spec, max_retries=1)
    retry_registry = retry_suite.build_registry()
    retry_move = _execute_tool(
        retry_registry,
        "move_to",
        {
            "request_id": "phase2-move-retry-001",
            "goal_id": "phase2-goal",
            "step_id": "step-005a",
            "x": 1.2,
            "y": 0.3,
            "z": 0.0,
            "speed": 0.2,
            "timeout_s": 20,
        },
    )
    retry_pick = _execute_tool(
        retry_registry,
        "pick",
        {
            "request_id": "phase2-pick-retry-001",
            "goal_id": "phase2-goal",
            "step_id": "step-005",
            "object_id": "box-42",
            "timeout_s": 20,
        },
    )
    checks.append(
        CheckResult(
            name="retryable_fault_recovered",
            passed=retry_move["status"] == "ok"
            and retry_pick["status"] == "ok"
            and retry_backend.call_counters.get("pick", 0) == 2,
            details={
                "move": retry_move,
                "response": retry_pick,
                "pick_call_count": retry_backend.call_counters.get("pick", 0),
                "retry_log": [record.__dict__ for record in retry_suite.client.call_log],
            },
        )
    )

    return checks


def build_report(results: list[CheckResult]) -> str:
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    success_rate = passed / total * 100 if total else 0.0

    lines = [
        "# Phase 2 验收结果",
        "",
        f"- 检查项数: {total}",
        f"- 通过数: {passed}",
        f"- 通过率: {success_rate:.1f}%",
        "",
        "## 验收明细",
        "",
    ]

    for result in results:
        lines.extend(
            [
                f"### {result.name}",
                f"- 结果: {'通过' if result.passed else '失败'}",
                f"- 详情: {result.details}",
                "",
            ]
        )

    lines.extend(
        [
            "## 代码依据",
            "",
            "本阶段工具层实现参考了 nanobot 的 Tool / ToolRegistry 约束：",
            "- [nanobot/agent/tools/base.py](nanobot/agent/tools/base.py)",
            "- [nanobot/agent/tools/registry.py](nanobot/agent/tools/registry.py)",
            "- [nanobot/agent/tools/shell.py](nanobot/agent/tools/shell.py)",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 2 acceptance checks.")
    parser.add_argument("--write-report", type=Path, default=None, help="Write the markdown report to this file.")
    args = parser.parse_args()

    results = run_checks()
    report = build_report(results)
    print(report)
    if args.write_report is not None:
        args.write_report.write_text(report, encoding="utf-8")

    if not all(result.passed for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())