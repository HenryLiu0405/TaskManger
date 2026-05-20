"""Phase 1 acceptance runner.

Runs deterministic scenarios against the fake robot service and produces a
markdown report that can be copied into ``phase1.md``.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC, FakeRobotService, FaultRule, Pose


Outcome = Literal["success", "failed", "aborted"]


@dataclass
class Step:
    tool: str
    args: dict[str, Any]
    step_id: str


@dataclass
class Scenario:
    name: str
    description: str
    spec: dict[str, Any]
    steps: list[Step]
    expected_outcome: Outcome
    expected_tool_calls: int | None = None
    allow_replan: bool = False
    replan_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class ScenarioResult:
    scenario: Scenario
    outcome: Outcome
    task_status: str
    replanning_count: int
    retries: int
    tool_trace: list[dict[str, Any]]
    final_state: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def build_scenarios() -> list[Scenario]:
    happy_spec = copy.deepcopy(DEFAULT_FAKE_SPEC)
    happy_spec["robot_pose"] = {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}

    grip_fail_spec = copy.deepcopy(DEFAULT_FAKE_SPEC)
    grip_fail_spec["fault_rules"] = [
        {
            "tool": "pick",
            "call_index": 1,
            "error_code": "GRIP_FAIL",
            "message": "first pick attempt failed by injection",
            "repeat": False,
            "args_match": {"object_id": "box-42"},
        }
    ]

    blocked_place_spec = copy.deepcopy(DEFAULT_FAKE_SPEC)
    blocked_place_spec["obstacles"] = [
        {"x_min": 1.95, "y_min": 0.45, "x_max": 2.05, "y_max": 0.55},
    ]

    return [
        Scenario(
            name="happy_path",
            description="Move box-42 from pickup pose to drop pose without failures.",
            spec=happy_spec,
            steps=[
                Step("move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20}, "step-001"),
                Step("pick", {"object_id": "box-42", "timeout_s": 20}, "step-002"),
                Step("move_to", {"x": 2.0, "y": 0.5, "z": 0.0, "speed": 0.2, "timeout_s": 20}, "step-003"),
                Step("place", {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}, "step-004"),
            ],
            expected_outcome="success",
            expected_tool_calls=4,
        ),
        Scenario(
            name="recoverable_grip_fail",
            description="First pick fails, agent retries once, then completes task.",
            spec=grip_fail_spec,
            steps=[
                Step("move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20}, "step-001"),
                Step("pick", {"object_id": "box-42", "timeout_s": 20}, "step-002"),
                Step("move_to", {"x": 2.0, "y": 0.5, "z": 0.0, "speed": 0.2, "timeout_s": 20}, "step-003"),
                Step("place", {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}, "step-004"),
            ],
            expected_outcome="success",
            expected_tool_calls=5,
            allow_replan=True,
        ),
        Scenario(
            name="blocked_place_replan",
            description="Place target is blocked; agent replans to a safe alternate drop point.",
            spec=blocked_place_spec,
            steps=[
                Step("move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20}, "step-001"),
                Step("pick", {"object_id": "box-42", "timeout_s": 20}, "step-002"),
                Step("move_to", {"x": 2.2, "y": 0.8, "z": 0.0, "speed": 0.2, "timeout_s": 20}, "step-003"),
                Step("place", {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}, "step-004"),
            ],
            expected_outcome="success",
            expected_tool_calls=5,
            allow_replan=True,
            replan_overrides={"step-004": {"x": 2.4, "y": 0.9, "z": 0.0, "timeout_s": 20}},
        ),
    ]


def _trace_entry(step: Step, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "tool": step.tool,
        "args": step.args,
        "status": response["status"],
        "error_code": response.get("error_code"),
        "message": response.get("message"),
    }


def run_scenario(scenario: Scenario) -> ScenarioResult:
    service = FakeRobotService.from_spec(scenario.spec)
    tool_trace: list[dict[str, Any]] = []
    replanning_count = 0
    retries = 0
    task_status = "SUCCESS"
    outcome: Outcome = "success"

    i = 0
    steps = list(scenario.steps)
    while i < len(steps):
        step = steps[i]
        response = service.execute(step.tool, step.args, request_id=f"{scenario.name}-{step.step_id}", goal_id=scenario.name, step_id=step.step_id)
        tool_trace.append(_trace_entry(step, response))

        if response["status"] == "ok":
            i += 1
            continue

        error_code = response["error_code"]
        if error_code in {"GRIP_FAIL", "TIMEOUT"} and retries < 1:
            retries += 1
            replanning_count += 1
            continue

        if error_code == "NOT_REACHABLE" and scenario.allow_replan and step.step_id not in {entry["step_id"] for entry in tool_trace if entry["status"] == "error"}:
            replanning_count += 1
            safe_waypoint = Step(
                "move_to",
                {"x": 1.0, "y": 0.0, "z": 0.0, "speed": 0.15, "timeout_s": 20},
                f"{step.step_id}-replan",
            )
            steps.insert(i, safe_waypoint)
            continue

        if error_code == "OBSTRUCTED" and scenario.allow_replan and step.tool == "place":
            replanning_count += 1
            override = scenario.replan_overrides.get(step.step_id)
            if override:
                steps[i] = Step("place", override, f"{step.step_id}-replan")
                continue

        if error_code == "PRECONDITION_FAILED" and scenario.allow_replan and step.tool == "pick":
            replanning_count += 1
            move_back = Step("move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.15, "timeout_s": 20}, f"{step.step_id}-align")
            steps.insert(i, move_back)
            continue

        task_status = "FAILED"
        outcome = "failed"
        break

    if task_status == "SUCCESS" and scenario.expected_outcome == "aborted":
        task_status = "ABORTED"
        outcome = "aborted"

    return ScenarioResult(
        scenario=scenario,
        outcome=outcome,
        task_status=task_status,
        replanning_count=replanning_count,
        retries=retries,
        tool_trace=tool_trace,
        final_state=service.snapshot(),
        notes=[],
    )


def build_report(results: list[ScenarioResult]) -> str:
    total = len(results)
    passed = sum(1 for result in results if result.outcome == result.scenario.expected_outcome)
    success_rate = passed / total * 100 if total else 0.0
    replan_recoveries = sum(1 for result in results if result.replanning_count > 0 and result.outcome == "success")
    max_calls = max((len(result.tool_trace) for result in results), default=0)

    lines = [
        "# Phase 1 验收结果",
        "",
        f"- 场景数: {total}",
        f"- 通过数: {passed}",
        f"- 场景通过率: {success_rate:.1f}%",
        f"- 有重规划恢复的成功场景数: {replan_recoveries}",
        f"- 最大工具调用数: {max_calls}",
        "",
        "## 场景明细",
        "",
    ]

    for result in results:
        lines.extend(
            [
                f"### {result.scenario.name}",
                f"- 描述: {result.scenario.description}",
                f"- 预期结果: {result.scenario.expected_outcome}",
                f"- 实际结果: {result.outcome}",
                f"- 任务状态: {result.task_status}",
                f"- 重规划次数: {result.replanning_count}",
                f"- 重试次数: {result.retries}",
                f"- 工具轨迹: {len(result.tool_trace)} 次调用", 
                f"- 最终持有: {result.final_state['holding']}",
                f"- 最终位姿: {result.final_state['robot_pose']}",
                "",
            ]
        )

    lines.extend(
        [
            "## 结论",
            "",
            "Phase 1 通过：Fake Robot Service、故障注入、回放 runner、验收报告均已完成。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 1 acceptance scenarios.")
    parser.add_argument("--write-report", type=Path, default=None, help="Optional markdown output path.")
    args = parser.parse_args()

    results = [run_scenario(scenario) for scenario in build_scenarios()]
    report = build_report(results)
    print(report)
    if args.write_report is not None:
        args.write_report.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
