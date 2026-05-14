"""
phi_robot 快速开始指南

展示如何使用新的任务级 API 提交、运行和监控任务。
"""

import asyncio
from phi_robot.mission_service import MissionService
from phi_robot.mission_runner import MissionRunner
from phi_robot.mission_event_hub import MissionEventHub
from phi_robot.adapters.unitree_sim import UnitreeSimBackend


async def example_happy_path():
    """
    示例 1: Happy Path - 按序完成 3 个任务（搬运 3 个方块）
    """
    print("\n" + "="*60)
    print("示例 1: Happy Path - 完成 3 个任务")
    print("="*60)

    # 初始化服务
    service = MissionService()
    adapter = UnitreeSimBackend()
    event_hub = MissionEventHub(service.store)

    # 步骤 1: 提交任务
    print("\n[步骤 1] 提交任务...")
    mission_id = service.submit(
        request_id="req-001",
        scene_id="scene-001",
        goal_id="goal-001",
        scene_version="scene-v1",
        stock_layout_version="stock-v1",
        destination_order=["nw", "n", "ne"],  # 左上、上、右上
        options={"max_replans": 2, "timeout_s": 30},
    )
    print(f"✓ 任务已创建: {mission_id}")

    # 步骤 2: 查询任务状态
    print("\n[步骤 2] 查询初始状态...")
    record = service.get_mission(mission_id)
    print(f"✓ 状态: {record.status}")
    print(f"✓ 计划步骤数: {len(record.plan)}")

    # 步骤 3: 启动任务执行
    print("\n[步骤 3] 启动任务执行...")
    runner = MissionRunner(service, adapter)
    service.run(mission_id)
    
    # 异步运行（简化版，实际应该后台运行）
    final_record = await runner.run(mission_id)
    
    print(f"✓ 最终状态: {final_record.status}")
    print(f"✓ 完成步骤: {sum(1 for s in final_record.plan if s.status == 'succeeded')}/{len(final_record.plan)}")

    # 步骤 4: 查询事件日志
    print("\n[步骤 4] 查询事件日志...")
    events = service.store.get_all_events(mission_id)
    print(f"✓ 事件总数: {len(events)}")


async def example_pause_resume():
    """
    示例 2: 暂停和恢复 - 演示任务控制
    """
    print("\n" + "="*60)
    print("示例 2: 暂停和恢复")
    print("="*60)

    service = MissionService()

    mission_id = service.submit(
        request_id="req-002",
        scene_id="scene-001",
        goal_id="goal-002",
        scene_version="scene-v1",
        stock_layout_version="stock-v1",
        destination_order=["nw", "n"],
    )
    print(f"✓ 任务已创建: {mission_id}")

    # 启动
    service.run(mission_id)
    record = service.get_mission(mission_id)
    print(f"✓ 任务启动: {record.status}")

    # 暂停
    service.pause(mission_id)
    record = service.get_mission(mission_id)
    print(f"✓ 任务暂停: {record.status}")

    # 恢复
    service.resume(mission_id)
    record = service.get_mission(mission_id)
    print(f"✓ 任务恢复: {record.status}")

    # 重置
    service.pause(mission_id)
    service.reset(mission_id)
    record = service.get_mission(mission_id)
    print(f"✓ 任务重置: {record.status}")


async def example_api_service():
    """
    示例 3: 使用 HTTP API 服务（如何在 FastAPI 中使用）
    """
    print("\n" + "="*60)
    print("示例 3: HTTP API 服务集成")
    print("="*60)

    from phi_robot.api_service import MissionApiService
    
    service = MissionService()
    adapter = UnitreeSimBackend()
    api_service = MissionApiService(service, adapter)

    # API 调用 1: 提交任务
    print("\n[API] POST /api/mission/submit")
    result = api_service.submit_mission(
        request_id="req-003",
        scene_id="scene-001",
        goal_id="goal-003",
        scene_version="scene-v1",
        stock_layout_version="stock-v1",
        destination_order=["w", "c", "e"],
    )
    mission_id = result["mission_id"]
    print(f"✓ 响应: {result}")

    # API 调用 2: 查询状态
    print("\n[API] GET /api/mission/{mission_id}")
    result = api_service.get_mission_status(mission_id)
    print(f"✓ 响应状态: {result['status']}")

    # API 调用 3: 启动任务
    print("\n[API] POST /api/mission/{mission_id}/run")
    result = await api_service.run_mission(mission_id)
    print(f"✓ 响应: {result}")

    # API 调用 4: 暂停任务
    print("\n[API] POST /api/mission/{mission_id}/pause")
    result = api_service.pause_mission(mission_id)
    print(f"✓ 响应: {result}")


def example_web_integration():
    """
    示例 4: 与 FastAPI 应用集成
    """
    print("\n" + "="*60)
    print("示例 4: FastAPI 集成代码")
    print("="*60)

    code_example = """
from fastapi import FastAPI
from phi_robot.webapi import create_phi_robot_api

app = FastAPI()

# 创建并注册 phi_robot API 路由
phi_robot_api = create_phi_robot_api(app=app)

# 现在可以访问以下端点：
# POST /api/mission/submit
# GET /api/mission/{mission_id}
# POST /api/mission/{mission_id}/run
# POST /api/mission/{mission_id}/pause
# POST /api/mission/{mission_id}/resume
# POST /api/mission/{mission_id}/reset
# GET /api/mission/{mission_id}/events (SSE)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
"""
    print(code_example)


def example_scenario_validation():
    """
    示例 5: 场景验收检查清单
    """
    print("\n" + "="*60)
    print("示例 5: 场景验收检查清单")
    print("="*60)

    checklist = """
✓ Happy Path - 9 个方块按序全部搬运完成
  - 检验: 任务状态 = completed
  - 检验: 所有步骤状态 = succeeded
  - 检验: 最终机器人状态不持握任何物体

⏳ 抓取失败 - 可重试或在限制内重规划成功
  - 检验: GRIP_FAIL 触发重规划
  - 检验: 重规划次数 <= max_replans

⏳ 放置阻挡 - 切换备用目标位或失败退出
  - 检验: OBSTRUCTED 状态处理
  - 检验: 备用位选择

⏳ 不可达 - 插入中间点重试
  - 检验: NOT_REACHABLE 状态处理
  - 检验: 中间路点规划

⏳ 安全告警 - 立即中止，不继续执行
  - 检验: safety_alert 立即转入 aborted
  - 检验: 保留现场（不清理方块）

✓ 确定性回放 - 同样输入必须得到同样轨迹
  - 检验: 相同 request_id + destination_order 重放
  - 检验: 工具调用序列完全一致

验收指标:

- Task Success Rate: >= 90%
- Replan Recovery Rate: >= 70%
- Safety Violation Count: == 0
- Mean Decision Latency: < 1500 ms
- Deterministic Replay Pass Rate: == 100%
"""

    print(checklist)


async def main():
    print("\n" + "="*70)
    print("phi_robot 任务 API 快速开始指南")
    print("="*70)

    # 运行示例
    await example_happy_path()
    await example_pause_resume()
    await example_api_service()
    example_web_integration()
    example_scenario_validation()

    print("\n" + "="*70)
    print("所有示例完成！")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
