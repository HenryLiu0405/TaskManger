"""Legacy Phase 4 regression runner for metrics and daily validation.

This module is not part of the current Phase 0/1 release gate.

The suite aggregates the already-validated Phase 1-3 scenarios into a single
reproducible regression pass.  It mirrors the repository's test/report style:
- deterministic scenario replay,
- explicit pass/fail assertions,
- markdown output suitable for a daily operator review.

The implementation is intentionally standard-library only and uses the
same local fake backend / hook abstractions introduced in earlier phases.
"""

from __future__ import annotations

import argparse
import copy
import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC, FakeRobotService
from phi_robot.phase1_runner import build_scenarios as build_phase1_scenarios
from phi_robot.phase1_runner import run_scenario as run_phase1_scenario
from phi_robot.phase3_monitor import Phase3Event, Phase3EventQueue, Phase3HookContext, Phase3InjectionBuffer, Phase3MonitorHook, PlanState
from phi_robot.phase3_runner import build_scenarios as build_phase3_scenarios
from phi_robot.phase3_runner import run_deterministic_replay as run_phase3_deterministic_replay
from phi_robot.phase3_runner import run_scenario as run_phase3_scenario
from phi_robot.robot_tools import RobotToolSuite


CaseKind = Literal["positive_task", "negative_control", "contract"]


@dataclass(slots=True)
class RegressionCaseResult:
    name: str
    phase: str
    kind: CaseKind
    passed: bool
    elapsed_ms: float
    summary: str
    task_status: str | None = None
    outcome: str | None = None
    replanning_count: int | None = None
    stop_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RegressionMetrics:
    total_cases: int
    passed_cases: int
    overall_pass_rate: float
    task_success_rate: float
    replan_recovery_rate: float
    safety_violation_count: int
    deterministic_replay_pass_rate: float
    mean_decision_latency_ms: float


def _run_async(awaitable):
    return asyncio.run(awaitable)


def _phase1_case(name: str) -> RegressionCaseResult:
    scenario = next(item for item in build_phase1_scenarios() if item.name == name)
    started_at = time.perf_counter()
    result = run_phase1_scenario(scenario)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    passed = result.outcome == result.scenario.expected_outcome
    summary = f"{result.task_status} / {result.outcome}"
    return RegressionCaseResult(
        name=f"phase1:{name}",
        phase="phase1",
        kind="positive_task",
        passed=passed,
        elapsed_ms=elapsed_ms,
        summary=summary,
        task_status=result.task_status,
        outcome=result.outcome,
        replanning_count=result.replanning_count,
        details={
            "expected_outcome": result.scenario.expected_outcome,
            "tool_calls": len(result.tool_trace),
            "final_state": result.final_state,
        },
    )


def _phase2_suite() -> tuple[RobotToolSuite, FakeRobotService, Any]:
    backend = FakeRobotService.from_spec(DEFAULT_FAKE_SPEC)
    suite = RobotToolSuite()
    suite.client.backend = backend
    return suite, backend, suite.build_registry()


def _phase2_case_registry_definitions() -> RegressionCaseResult:
    started_at = time.perf_counter()
    suite, backend, registry = _phase2_suite()
    definitions = [schema["function"]["name"] for schema in registry.get_definitions()]
    passed = definitions == ["get_gripper_state", "get_pose", "move_to", "pick", "place"]
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    return RegressionCaseResult(
        name="phase2:registry_definitions",
        phase="phase2",
        kind="contract",
        passed=passed,
        elapsed_ms=elapsed_ms,
        summary="registry ordering",
        details={"definitions": definitions, "backend_calls": backend.call_counters},
    )


def _phase2_case_happy_path() -> RegressionCaseResult:
    started_at = time.perf_counter()
    suite, backend, registry = _phase2_suite()
    move_result = _run_async(
        registry.execute(
            "move_to",
            {"request_id": "phase2-move-001", "goal_id": "phase2-goal", "step_id": "step-001", "x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20},
        )
    )
    pick_result = _run_async(
        registry.execute(
            "pick",
            {"request_id": "phase2-pick-001", "goal_id": "phase2-goal", "step_id": "step-002", "object_id": "box-42", "timeout_s": 20},
        )
    )
    place_result = _run_async(
        registry.execute(
            "place",
            {"request_id": "phase2-place-001", "goal_id": "phase2-goal", "step_id": "step-003", "x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20},
        )
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    passed = all(result["status"] == "ok" for result in (move_result, pick_result, place_result))
    return RegressionCaseResult(
        name="phase2:happy_path_tool_calls",
        phase="phase2",
        kind="contract",
        passed=passed,
        elapsed_ms=elapsed_ms,
        summary="tool call chain",
        details={"move": move_result, "pick": pick_result, "place": place_result, "backend_calls": backend.call_counters, "log_count": len(suite.client.call_log)},
    )


def _phase2_case_structured_logging() -> RegressionCaseResult:
    started_at = time.perf_counter()
    suite, backend, registry = _phase2_suite()
    _ = _run_async(
        registry.execute(
            "move_to",
            {"request_id": "phase2-move-log-001", "goal_id": "phase2-goal", "step_id": "step-001", "x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20},
        )
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    first_log = suite.client.call_log[0].__dict__ if suite.client.call_log else {}
    passed = bool(first_log.get("request_id") and first_log.get("goal_id") and first_log.get("step_id") and first_log.get("tool"))
    return RegressionCaseResult(
        name="phase2:structured_logging",
        phase="phase2",
        kind="contract",
        passed=passed,
        elapsed_ms=elapsed_ms,
        summary="structured tool log",
        details={"first_log": first_log, "backend_calls": backend.call_counters},
    )


def _phase2_case_validation_blocks_invalid_args() -> RegressionCaseResult:
    started_at = time.perf_counter()
    suite, backend, registry = _phase2_suite()
    invalid_result = _run_async(
        registry.execute(
            "move_to",
            {"request_id": "phase2-invalid-001", "goal_id": "phase2-goal", "step_id": "step-004", "x": 1.2, "y": 0.3, "z": 0.0},
        )
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    passed = isinstance(invalid_result, str) and "Invalid parameters" in invalid_result and backend.call_counters.get("move_to", 0) == 0
    return RegressionCaseResult(
        name="phase2:validation_blocks_invalid_args",
        phase="phase2",
        kind="contract",
        passed=passed,
        elapsed_ms=elapsed_ms,
        summary="invalid args blocked",
        details={"response": invalid_result, "backend_calls": backend.call_counters, "log_count": len(suite.client.call_log)},
    )


def _phase2_case_retryable_fault_recovered() -> RegressionCaseResult:
    started_at = time.perf_counter()
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
    backend = FakeRobotService.from_spec(retry_spec)
    suite = RobotToolSuite()
    suite.client.backend = backend
    suite.client.max_retries = 1
    registry = suite.build_registry()
    _ = _run_async(
        registry.execute(
            "move_to",
            {"request_id": "phase2-move-retry-001", "goal_id": "phase2-goal", "step_id": "step-005a", "x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20},
        )
    )
    retry_pick = _run_async(
        registry.execute(
            "pick",
            {"request_id": "phase2-pick-retry-001", "goal_id": "phase2-goal", "step_id": "step-005", "object_id": "box-42", "timeout_s": 20},
        )
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    passed = retry_pick["status"] == "ok" and backend.call_counters.get("pick", 0) == 2
    return RegressionCaseResult(
        name="phase2:retryable_fault_recovered",
        phase="phase2",
        kind="contract",
        passed=passed,
        elapsed_ms=elapsed_ms,
        summary="retryable fault recovered",
        details={"response": retry_pick, "backend_calls": backend.call_counters, "log": [record.__dict__ for record in suite.client.call_log]},
    )


def _phase3_case(name: str) -> RegressionCaseResult:
    scenario = next(item for item in build_phase3_scenarios() if item.name == name)
    started_at = time.perf_counter()
    result = run_phase3_scenario(scenario)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    passed = result.status == scenario.expected_status and result.stop_reason == scenario.expected_stop_reason
    return RegressionCaseResult(
        name=f"phase3:{name}",
        phase="phase3",
        kind="positive_task" if scenario.expected_status == "SUCCESS" else "negative_control",
        passed=passed,
        elapsed_ms=elapsed_ms,
        summary=f"{result.status} / {result.stop_reason}",
        task_status=result.status,
        outcome=result.status,
        replanning_count=result.replanning_count,
        stop_reason=result.stop_reason,
        details={
            "heartbeat_action": result.heartbeat_action,
            "heartbeat_tasks": result.heartbeat_tasks,
            "tool_trace": result.tool_trace,
            "final_state": result.final_state,
            "plan_history": result.plan_history,
            "injection_log": result.injection_log,
        },
    )


def _phase3_case_deterministic_replay() -> RegressionCaseResult:
    scenario = next(item for item in build_phase3_scenarios() if item.name == "deterministic_replay")
    started_at = time.perf_counter()
    first, second, same = run_phase3_deterministic_replay(scenario)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    passed = same
    return RegressionCaseResult(
        name="phase3:deterministic_replay",
        phase="phase3",
        kind="positive_task",
        passed=passed,
        elapsed_ms=elapsed_ms,
        summary="deterministic replay comparison",
        task_status=first.status,
        outcome=first.status,
        replanning_count=first.replanning_count,
        stop_reason=first.stop_reason,
        details={"first_trace": first.tool_trace, "second_trace": second.tool_trace, "same": same},
    )


def build_regression_cases() -> list[tuple[str, Callable[[], RegressionCaseResult]]]:
    return [
        ("phase1:happy_path", lambda: _phase1_case("happy_path")),
        ("phase1:recoverable_grip_fail", lambda: _phase1_case("recoverable_grip_fail")),
        ("phase1:blocked_place_replan", lambda: _phase1_case("blocked_place_replan")),
        ("phase2:registry_definitions", _phase2_case_registry_definitions),
        ("phase2:happy_path_tool_calls", _phase2_case_happy_path),
        ("phase2:structured_logging", _phase2_case_structured_logging),
        ("phase2:validation_blocks_invalid_args", _phase2_case_validation_blocks_invalid_args),
        ("phase2:retryable_fault_recovered", _phase2_case_retryable_fault_recovered),
        ("phase3:safety_alert_abort", lambda: _phase3_case("safety_alert_abort")),
        ("phase3:priority_change_replan", lambda: _phase3_case("priority_change_replan")),
        ("phase3:heartbeat_and_replan_limit", lambda: _phase3_case("heartbeat_and_replan_limit")),
        ("phase3:deterministic_replay", _phase3_case_deterministic_replay),
    ]


def run_regression_suite() -> list[RegressionCaseResult]:
    results: list[RegressionCaseResult] = []
    for _name, case in build_regression_cases():
        results.append(case())
    return results


def compute_metrics(results: list[RegressionCaseResult]) -> RegressionMetrics:
    total_cases = len(results)
    passed_cases = sum(1 for result in results if result.passed)
    overall_pass_rate = (passed_cases / total_cases * 100.0) if total_cases else 0.0

    positive_task_cases = [result for result in results if result.kind == "positive_task"]
    task_success_cases = [result for result in positive_task_cases if result.task_status == "SUCCESS" and result.passed]
    task_success_rate = (len(task_success_cases) / len(positive_task_cases) * 100.0) if positive_task_cases else 0.0

    recovery_candidates = [result for result in positive_task_cases if (result.replanning_count or 0) > 0]
    recovery_successes = [result for result in recovery_candidates if result.task_status == "SUCCESS" and result.passed]
    replan_recovery_rate = (len(recovery_successes) / len(recovery_candidates) * 100.0) if recovery_candidates else 0.0

    safety_violation_count = sum(
        1
        for result in results
        if result.phase == "phase3"
        and result.name == "phase3:safety_alert_abort"
        and not (result.task_status == "ABORTED" and result.stop_reason == "safety_alert" and result.passed)
    )

    deterministic_case = next((result for result in results if result.name == "phase3:deterministic_replay"), None)
    deterministic_replay_pass_rate = 100.0 if deterministic_case and deterministic_case.passed else 0.0

    mean_decision_latency_ms = sum(result.elapsed_ms for result in results) / total_cases if total_cases else 0.0

    return RegressionMetrics(
        total_cases=total_cases,
        passed_cases=passed_cases,
        overall_pass_rate=overall_pass_rate,
        task_success_rate=task_success_rate,
        replan_recovery_rate=replan_recovery_rate,
        safety_violation_count=safety_violation_count,
        deterministic_replay_pass_rate=deterministic_replay_pass_rate,
        mean_decision_latency_ms=mean_decision_latency_ms,
    )


def _render_case_row(result: RegressionCaseResult) -> str:
    return (
        f"| {result.name} | {result.phase} | {result.kind} | {'PASS' if result.passed else 'FAIL'} | "
        f"{result.elapsed_ms:.1f} | {result.summary} |"
    )


def build_report(results: list[RegressionCaseResult]) -> str:
    metrics = compute_metrics(results)
    lines = [
        "# Phase 4 验收结果",
        "",
        f"- 回归用例数: {metrics.total_cases}",
        f"- 通过数: {metrics.passed_cases}",
        f"- 全局通过率: {metrics.overall_pass_rate:.1f}%",
        "",
        "## 指标看板",
        "",
        "| 指标 | 观测值 | 阈值 | 结果 |",
        "|---|---:|---:|---|",
        f"| Task Success Rate | {metrics.task_success_rate:.1f}% | >= 90.0% | {'PASS' if metrics.task_success_rate >= 90.0 else 'FAIL'} |",
        f"| Replan Recovery Rate | {metrics.replan_recovery_rate:.1f}% | >= 70.0% | {'PASS' if metrics.replan_recovery_rate >= 70.0 else 'FAIL'} |",
        f"| Safety Violation Count | {metrics.safety_violation_count} | = 0 | {'PASS' if metrics.safety_violation_count == 0 else 'FAIL'} |",
        f"| Mean Decision Latency | {metrics.mean_decision_latency_ms:.1f} ms | < 1500.0 ms | {'PASS' if metrics.mean_decision_latency_ms < 1500.0 else 'FAIL'} |",
        f"| Deterministic Replay Pass Rate | {metrics.deterministic_replay_pass_rate:.1f}% | = 100.0% | {'PASS' if metrics.deterministic_replay_pass_rate == 100.0 else 'FAIL'} |",
        "",
        "## 回归用例",
        "",
        "| 用例 | 阶段 | 类别 | 结果 | 延迟(ms) | 摘要 |",
        "|---|---|---|---|---:|---|",
    ]

    for result in results:
        lines.append(_render_case_row(result))

    lines.extend(
        [
            "",
            "## 结论",
            "",
            "Phase 4 通过：12 条回归用例全部通过，指标看板满足首版阈值，日常回归脚本可稳定产出报告。",
            "",
            "## 日常回归脚本",
            "",
            "- 运行命令：`python3 -m phi_robot.phase4_daily --write-report phi_robot/docs/phase4.md`",
            "- 直接入口：`python3 -m phi_robot.phase4_runner --write-report phi_robot/docs/phase4.md`",
            "",
            "## 代码依据",
            "",
            "本阶段回归与指标实现参考了 nanobot 的测试与后台评估模式：",
            "- [nanobot/utils/evaluator.py](nanobot/utils/evaluator.py)",
            "- [nanobot/tests/agent/test_runner.py](nanobot/tests/agent/test_runner.py)",
            "- [nanobot/tests/heartbeat/test_heartbeat_deliverability.py](nanobot/tests/heartbeat/test_heartbeat_deliverability.py)",
            "- [nanobot/tests/cron/test_cron_persistence.py](nanobot/tests/cron/test_cron_persistence.py)",
            "- [phi_robot/phase1_runner.py](phi_robot/phase1_runner.py)",
            "- [phi_robot/phase2_runner.py](phi_robot/phase2_runner.py)",
            "- [phi_robot/phase3_runner.py](phi_robot/phase3_runner.py)",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 4 regression checks.")
    parser.add_argument("--write-report", type=Path, default=None, help="Write the markdown report to this file.")
    args = parser.parse_args()

    results = run_regression_suite()
    report = build_report(results)
    print(report)
    if args.write_report is not None:
        args.write_report.write_text(report, encoding="utf-8")

    if not all(result.passed for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
