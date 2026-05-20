# Phase 1 验收结果

- 场景数: 3
- 通过数: 3
- 场景通过率: 100.0%
- 有重规划恢复的成功场景数: 2
- 最大工具调用数: 5

## 场景明细

### happy_path
- 描述: Move box-42 from pickup pose to drop pose without failures.
- 预期结果: success
- 实际结果: success
- 任务状态: SUCCESS
- 重规划次数: 0
- 重试次数: 0
- 工具轨迹: 4 次调用
- 最终持有: None
- 最终位姿: {'x': 2.0, 'y': 0.5, 'z': 0.0, 'theta': 0.0}

### recoverable_grip_fail
- 描述: c
- 预期结果: success
- 实际结果: success
- 任务状态: SUCCESS
- 重规划次数: 1
- 重试次数: 1
- 工具轨迹: 5 次调用
- 最终持有: None
- 最终位姿: {'x': 2.0, 'y': 0.5, 'z': 0.0, 'theta': 0.0}

### blocked_place_replan
- 描述: Place target is blocked; agent replans to a safe alternate drop point.
- 预期结果: success
- 实际结果: success
- 任务状态: SUCCESS
- 重规划次数: 1
- 重试次数: 0
- 工具轨迹: 5 次调用
- 最终持有: None
- 最终位姿: {'x': 2.4, 'y': 0.9, 'z': 0.0, 'theta': 0.0}

## 结论

Phase 1 通过：Fake Robot Service、故障注入、回放 runner、验收报告均已完成。
