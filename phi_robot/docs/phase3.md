# Phase 3 验收结果

- 检查项数: 5
- 通过数: 5
- 通过率: 100.0%

## 验收明细

### safety_alert_abort
- 结果: 通过
- 详情: {'status': 'ABORTED', 'stop_reason': 'safety_alert', 'tool_trace': [{'step_id': 'step-001', 'tool': 'monitor', 'status': 'ABORTED', 'error_code': 'SAFETY_ALERT', 'message': '立即暂停并保持当前位置'}], 'injections': [{'role': 'system', 'content': 'Safety alert received: 立即暂停并保持当前位置', 'meta': {'event_id': 'evt-safety-001', 'priority': 'high'}}]}

### priority_change_replan
- 结果: 通过
- 详情: {'status': 'SUCCESS', 'final_pose': {'x': 2.4, 'y': 0.9, 'z': 0.0, 'theta': 0.0}, 'heartbeat_action': 'run', 'heartbeat_tasks': 'Move box-42 to the B2 alternate drop point', 'plan_history': [{'action': 'heartbeat_run', 'tasks': 'Move box-42 to the B2 alternate drop point'}, {'action': 'monitor_override', 'step_id': 'step-003-monitor', 'target': {'x': 2.4, 'y': 0.9, 'z': 0.0, 'timeout_s': 20}}]}

### heartbeat_and_replan_limit
- 结果: 通过
- 详情: {'status': 'FAILED', 'stop_reason': 'max_replan_attempts_exceeded', 'heartbeat_action': 'run', 'replanning_count': 2, 'plan_history': [{'action': 'heartbeat_run', 'tasks': 'Active task queue: move, pick, place'}, {'action': 'retry_pick', 'step_id': 'step-002', 'error_code': 'GRIP_FAIL'}, {'action': 'retry_pick', 'step_id': 'step-002', 'error_code': 'GRIP_FAIL'}]}

### deterministic_replay
- 结果: 通过
- 详情: {'first_trace': [{'step_id': 'step-001', 'tool': 'move_to', 'args': {'x': 1.2, 'y': 0.3, 'z': 0.0, 'speed': 0.2, 'timeout_s': 20}, 'status': 'ok', 'error_code': None, 'message': 'arrived'}, {'step_id': 'step-002', 'tool': 'pick', 'args': {'object_id': 'box-42', 'timeout_s': 20}, 'status': 'ok', 'error_code': None, 'message': 'picked box-42'}, {'step_id': 'step-003-monitor', 'tool': 'place', 'args': {'x': 2.4, 'y': 0.9, 'z': 0.0, 'timeout_s': 20}, 'status': 'ok', 'error_code': None, 'message': 'placed box-42'}], 'second_trace': [{'step_id': 'step-001', 'tool': 'move_to', 'args': {'x': 1.2, 'y': 0.3, 'z': 0.0, 'speed': 0.2, 'timeout_s': 20}, 'status': 'ok', 'error_code': None, 'message': 'arrived'}, {'step_id': 'step-002', 'tool': 'pick', 'args': {'object_id': 'box-42', 'timeout_s': 20}, 'status': 'ok', 'error_code': None, 'message': 'picked box-42'}, {'step_id': 'step-003-monitor', 'tool': 'place', 'args': {'x': 2.4, 'y': 0.9, 'z': 0.0, 'timeout_s': 20}, 'status': 'ok', 'error_code': None, 'message': 'placed box-42'}], 'same': True}

### replan_limit_enforced
- 结果: 通过
- 详情: {'replanning_count': 2, 'stop_reason': 'max_replan_attempts_exceeded'}

## 代码依据

本阶段监控与重规划实现参考了 nanobot 的 Hook / Runner / Heartbeat 约束：
- [nanobot/agent/hook.py](nanobot/agent/hook.py)
- [nanobot/agent/runner.py](nanobot/agent/runner.py)
- [nanobot/heartbeat/service.py](nanobot/heartbeat/service.py)
- [nanobot/agent/loop.py](nanobot/agent/loop.py)
