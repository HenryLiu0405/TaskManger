"""Legacy Phase 3 runner for monitoring, replanning, and heartbeat.

This module is not part of the current Phase 0/1 release gate.

The runner executes deterministic scenarios against the fake robot backend and
uses the Phase 3 monitor hook to consume safety alerts, new instructions, and
priority changes.  The design mirrors the lifecycle boundaries used by
nanobot's AgentHook and HeartbeatService while remaining standalone and
standard-library friendly.
"""

from __future__ import annotations

import argparse
import copy
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phi_robot.fake_robot_service import DEFAULT_FAKE_SPEC, FakeRobotService
from phi_robot.phase3_monitor import (
    HeartbeatAction,
    Phase3Event,
    Phase3EventQueue,
    Phase3InjectionBuffer,
    Phase3HookContext,
    Phase3HeartbeatController,
    Phase3MonitorHook,
    PlanState,
    PlanStep,
    parse_instruction_target,
)
from phi_robot.robot_tools import RobotToolSuite


@dataclass(slots=True)
class Phase3Scenario:
    name: str
    description: str
    spec: dict[str, Any]
    steps: list[PlanStep]
    events: list[Phase3Event]
    heartbeat_content: str
    expected_status: str
    expected_final_pose: dict[str, Any] | None = None
    expected_stop_reason: str | None = None


@dataclass(slots=True)
class Phase3Result:
    scenario: Phase3Scenario
    status: str
    stop_reason: str | None
    replanning_count: int
    tool_trace: list[dict[str, Any]]
    final_state: dict[str, Any]
    heartbeat_action: HeartbeatAction | None
    heartbeat_tasks: str
    plan_history: list[dict[str, Any]]
    injection_log: list[dict[str, Any]]


def build_scenarios() -> list[Phase3Scenario]:
    base_spec = copy.deepcopy(DEFAULT_FAKE_SPEC)
    base_spec["robot_pose"] = {"x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0}

    safety_spec = copy.deepcopy(base_spec)
    priority_spec = copy.deepcopy(base_spec)
    retry_spec = copy.deepcopy(DEFAULT_FAKE_SPEC)
    retry_spec["fault_rules"] = [
        {
            "tool": "pick",
            "call_index": 1,
            "error_code": "GRIP_FAIL",
            "message": "injected pick failure",
            "repeat": False,
            "args_match": {"object_id": "box-42"},
        },
        {
            "tool": "pick",
            "call_index": 2,
            "error_code": "GRIP_FAIL",
            "message": "injected pick failure again",
            "repeat": False,
            "args_match": {"object_id": "box-42"},
        },
        {
            "tool": "pick",
            "call_index": 3,
            "error_code": "GRIP_FAIL",
            "message": "injected pick failure third time",
            "repeat": False,
            "args_match": {"object_id": "box-42"},
        },
    ]

    return [
        Phase3Scenario(
            name="safety_alert_abort",
            description="A safety alert should abort the turn immediately before place.",
            spec=safety_spec,
            steps=[
                PlanStep("step-001", "move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20}),
                PlanStep("step-002", "pick", {"object_id": "box-42", "timeout_s": 20}),
                PlanStep("step-003", "place", {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}),
            ],
            events=[
                Phase3Event(
                    event_id="evt-safety-001",
                    type="safety_alert",
                    priority="high",
                    content="立即暂停并保持当前位置",
                    ts=1,
                )
            ],
            heartbeat_content="action: skip\n",
            expected_status="ABORTED",
            expected_stop_reason="safety_alert",
        ),
        Phase3Scenario(
            name="priority_change_replan",
            description="Priority change should inject a new place target and replan the tail.",
            spec=priority_spec,
            steps=[
                PlanStep("step-001", "move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20}),
                PlanStep("step-002", "pick", {"object_id": "box-42", "timeout_s": 20}),
                PlanStep("step-003", "place", {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}),
            ],
            events=[
                Phase3Event(
                    event_id="evt-priority-001",
                    type="priority_change",
                    priority="high",
                    content="立即暂停并改为放置到B2",
                    ts=2,
                )
            ],
            heartbeat_content="action: run\ntasks: Move box-42 to the B2 alternate drop point\n",
            expected_status="SUCCESS",
            expected_final_pose={"x": 2.4, "y": 0.9, "z": 0.0, "theta": 0.0},
            expected_stop_reason=None,
        ),
        Phase3Scenario(
            name="heartbeat_and_replan_limit",
            description="Heartbeat should trigger execution and repeated pick failures should stop at max replans.",
            spec=retry_spec,
            steps=[
                PlanStep("step-001", "move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20}),
                PlanStep("step-002", "pick", {"object_id": "box-42", "timeout_s": 20}),
                PlanStep("step-003", "place", {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}),
            ],
            events=[],
            heartbeat_content="action: run\ntasks: Active task queue: move, pick, place\n",
            expected_status="FAILED",
            expected_stop_reason="max_replan_attempts_exceeded",
        ),
        Phase3Scenario(
            name="deterministic_replay",
            description="Two runs with the same events should produce identical traces.",
            spec=base_spec,
            steps=[
                PlanStep("step-001", "move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20}),
                PlanStep("step-002", "pick", {"object_id": "box-42", "timeout_s": 20}),
                PlanStep("step-003", "place", {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}),
            ],
            events=[
                Phase3Event(
                    event_id="evt-deterministic-001",
                    type="new_instruction",
                    priority="normal",
                    content="请优先放到B2",
                    ts=1,
                )
            ],
            heartbeat_content="action: run\ntasks: deterministic replay check\n",
            expected_status="SUCCESS",
            expected_final_pose={"x": 2.4, "y": 0.9, "z": 0.0, "theta": 0.0},
        ),
    ]


def _default_plan() -> list[PlanStep]:
    return [
        PlanStep("step-001", "move_to", {"x": 1.2, "y": 0.3, "z": 0.0, "speed": 0.2, "timeout_s": 20}),
        PlanStep("step-002", "pick", {"object_id": "box-42", "timeout_s": 20}),
        PlanStep("step-003", "move_to", {"x": 2.0, "y": 0.5, "z": 0.0, "speed": 0.2, "timeout_s": 20}),
        PlanStep("step-004", "place", {"x": 2.0, "y": 0.5, "z": 0.0, "timeout_s": 20}),
    ]


def _trace_entry(step: PlanStep, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "tool": step.tool,
        "args": step.args,
        "status": response["status"],
        "error_code": response.get("error_code"),
        "message": response.get("message"),
    }


def run_scenario(scenario: Phase3Scenario) -> Phase3Result:
    service = FakeRobotService.from_spec(scenario.spec)
    suite = RobotToolSuite()
    suite.client.backend = service
    suite.client.max_retries = 0
    registry = suite.build_registry()

    event_queue = Phase3EventQueue(list(scenario.events))
    injection_buffer = Phase3InjectionBuffer()
    hook = Phase3MonitorHook(event_queue, injection_buffer)
    tool_trace: list[dict[str, Any]] = []
    context = Phase3HookContext(iteration=0, messages=[{"role": "user", "content": scenario.description}])

    heartbeat_action: HeartbeatAction | None = None
    heartbeat_tasks = ""
    with tempfile.TemporaryDirectory() as temp_dir:
        heartbeat_file = Path(temp_dir) / "HEARTBEAT.md"
        heartbeat_file.write_text(scenario.heartbeat_content, encoding="utf-8")
        heartbeat = Phase3HeartbeatController(heartbeat_file)
        heartbeat_result = heartbeat.trigger_now()

        plan = list(scenario.steps or _default_plan())
        plan_state = PlanState(goal_id=scenario.name, steps=list(plan), max_replans=2)

        if heartbeat_result is not None:
            heartbeat_action, heartbeat_tasks = heartbeat_result
            if heartbeat_action == "skip":
                plan_state.history.append({"action": "heartbeat_skip", "tasks": heartbeat_tasks})
            else:
                plan_state.history.append({"action": "heartbeat_run", "tasks": heartbeat_tasks})

        i = 0
        while i < len(plan):
            context.iteration = i
            context.tool_calls = [{"name": plan[i].tool, "arguments": plan[i].args, "id": f"call-{i+1}"}]
            context.messages.append({"role": "assistant", "content": f"Executing {plan[i].tool}"})

            import asyncio

            asyncio.run(hook.before_iteration(context))
            if context.stop_reason == "ABORTED":
                plan_state.status = "ABORTED"
                plan_state.stop_reason = "safety_alert"
                tool_trace.append({"step_id": plan[i].step_id, "tool": "monitor", "status": "ABORTED", "error_code": "SAFETY_ALERT", "message": context.error or ""})
                break

            current_step = plan[i]
            if hook.pending_drop_override is not None and current_step.tool == "place":
                current_step = PlanStep(step_id=f"{current_step.step_id}-monitor", tool="place", args=dict(hook.pending_drop_override))
                plan[i] = current_step
                plan_state.history.append({"action": "monitor_override", "step_id": current_step.step_id, "target": dict(current_step.args)})

            asyncio.run(hook.before_execute_tools(context))
            response = asyncio.run(
                registry.execute(
                    current_step.tool,
                    {"request_id": f"{scenario.name}-{current_step.step_id}", "goal_id": scenario.name, "step_id": current_step.step_id, **current_step.args},
                )
            )

            if isinstance(response, str):
                response = {
                    "request_id": f"{scenario.name}-{current_step.step_id}",
                    "status": "error",
                    "error_code": "INTERNAL_ERROR",
                    "message": response,
                    "state": service.snapshot(),
                    "metrics": {},
                }

            tool_trace.append(_trace_entry(current_step, response))
            context.tool_results = [response]
            context.tool_events = [
                {
                    "name": current_step.tool,
                    "status": response["status"],
                    "detail": response.get("message", ""),
                }
            ]
            context.response = response
            if response["status"] == "ok":
                context.final_content = response.get("message")
                asyncio.run(hook.after_iteration(context))
                i += 1
                continue

            plan_action, updated_steps = hook.apply_replan_policy(plan_state, current_step, response, list(scenario.events))
            asyncio.run(hook.after_iteration(context))
            if plan_action == "abort":
                plan_state.status = "FAILED"
                plan_state.stop_reason = plan_state.stop_reason or response.get("error_code")
                break

            if plan_action == "replan":
                plan = plan[:i] + updated_steps + plan[i + 1 :]
                plan_state.steps = list(plan)
                continue

            plan_state.status = "FAILED"
            plan_state.stop_reason = response.get("error_code")
            break

    if plan_state.status == "PENDING":
        plan_state.status = "SUCCESS"

    if plan_state.status == "SUCCESS" and service.snapshot()["holding"] is None:
        pass

    return Phase3Result(
        scenario=scenario,
        status=plan_state.status,
        stop_reason=plan_state.stop_reason,
        replanning_count=plan_state.replanning_count,
        tool_trace=tool_trace,
        final_state=service.snapshot(),
        heartbeat_action=heartbeat_action,
        heartbeat_tasks=heartbeat_tasks,
        plan_history=list(plan_state.history),
        injection_log=list(context.injections),
    )


def run_deterministic_replay(scenario: Phase3Scenario) -> tuple[Phase3Result, Phase3Result, bool]:
    first = run_scenario(scenario)
    second = run_scenario(scenario)
    same = (
        first.status == second.status
        and first.stop_reason == second.stop_reason
        and first.tool_trace == second.tool_trace
        and first.final_state == second.final_state
        and first.plan_history == second.plan_history
    )
    return first, second, same


def run_checks() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    scenarios = build_scenarios()

    safety = run_scenario(scenarios[0])
    results.append(
        {
            "name": safety.scenario.name,
            "passed": safety.status == safety.scenario.expected_status and safety.stop_reason == safety.scenario.expected_stop_reason,
            "details": {
                "status": safety.status,
                "stop_reason": safety.stop_reason,
                "tool_trace": safety.tool_trace,
                "injections": safety.injection_log,
            },
        }
    )

    priority = run_scenario(scenarios[1])
    results.append(
        {
            "name": priority.scenario.name,
            "passed": priority.status == priority.scenario.expected_status and priority.final_state["robot_pose"] == priority.scenario.expected_final_pose,
            "details": {
                "status": priority.status,
                "final_pose": priority.final_state["robot_pose"],
                "heartbeat_action": priority.heartbeat_action,
                "heartbeat_tasks": priority.heartbeat_tasks,
                "plan_history": priority.plan_history,
            },
        }
    )

    heartbeat = run_scenario(scenarios[2])
    results.append(
        {
            "name": heartbeat.scenario.name,
            "passed": heartbeat.status == heartbeat.scenario.expected_status and heartbeat.stop_reason == heartbeat.scenario.expected_stop_reason,
            "details": {
                "status": heartbeat.status,
                "stop_reason": heartbeat.stop_reason,
                "heartbeat_action": heartbeat.heartbeat_action,
                "replanning_count": heartbeat.replanning_count,
                "plan_history": heartbeat.plan_history,
            },
        }
    )

    first, second, same = run_deterministic_replay(scenarios[3])
    results.append(
        {
            "name": scenarios[3].name,
            "passed": same,
            "details": {
                "first_trace": first.tool_trace,
                "second_trace": second.tool_trace,
                "same": same,
            },
        }
    )

    limit_passed = heartbeat.replanning_count <= 2
    results.append(
        {
            "name": "replan_limit_enforced",
            "passed": limit_passed,
            "details": {
                "replanning_count": heartbeat.replanning_count,
                "stop_reason": heartbeat.stop_reason,
            },
        }
    )

    return results


def build_report(results: list[dict[str, Any]]) -> str:
    total = len(results)
    passed = sum(1 for result in results if result["passed"])
    success_rate = passed / total * 100 if total else 0.0

    lines = [
        "# Phase 3 验收结果",
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
                f"### {result['name']}",
                f"- 结果: {'通过' if result['passed'] else '失败'}",
                f"- 详情: {result['details']}",
                "",
            ]
        )

    lines.extend(
        [
            "## 代码依据",
            "",
            "本阶段监控与重规划实现参考了 nanobot 的 Hook / Runner / Heartbeat 约束：",
            "- [nanobot/agent/hook.py](nanobot/agent/hook.py)",
            "- [nanobot/agent/runner.py](nanobot/agent/runner.py)",
            "- [nanobot/heartbeat/service.py](nanobot/heartbeat/service.py)",
            "- [nanobot/agent/loop.py](nanobot/agent/loop.py)",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 3 acceptance checks.")
    parser.add_argument("--write-report", type=Path, default=None, help="Write the markdown report to this file.")
    args = parser.parse_args()

    results = run_checks()
    report = build_report(results)
    print(report)
    if args.write_report is not None:
        args.write_report.write_text(report, encoding="utf-8")

    if not all(result["passed"] for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
