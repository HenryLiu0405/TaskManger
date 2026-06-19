#!/usr/bin/env python3
"""
启动 phi_robot API 服务

使用方式:
  # 内嵌模式（默认，sim 与 API 同进程）
  python run_phi_robot_api.py

  # 分离模式（sim 独立运行在另一个端口）
  python -m phi_robot.adapters.unitree_sim --port 8080 &
  python run_phi_robot_api.py --sim-url http://127.0.0.1:8080

  # HTTP 验收模式（只转发 move_to 到真机，跳过其它工具，不需要 sim）
  python run_phi_robot_api.py --acceptance http --move-to-url http://192.168.50.141:5000

  # ROS2 验收模式（调用 ROS2 路径规划和姿态服务）
  python run_phi_robot_api.py --acceptance ros \\
      --path-plan-service /start_navigation
      # lift/lay_down/stand 使用默认值 /set_lift, /set_lay_down, /set_stand
"""

import sys
import os

# 添加当前目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phi_robot.api_server import PhiRobotAPIServer


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="phi_robot API server",
        epilog="Use --sim-url to connect to a standalone sim server.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Server port (default: 5000)")
    parser.add_argument(
        "--sim-url",
        default=None,
        help="Remote sim server URL (default: use in-process UnitreeSimBackend)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print every backend<->sim request/response to stdout",
    )
    parser.add_argument(
        "--move-to-url",
        default=None,
        help="move_to 独立服务器地址（联调时指向同事的端口）",
    )
    parser.add_argument(
        "--acceptance",
        nargs="?",
        const="http",
        default=None,
        choices=["http", "ros"],
        help="验收模式: 'http' (默认) 转发 move_to 到 HTTP 端点; 'ros' 使用 ROS2 服务",
    )
    parser.add_argument(
        "--path-plan-service",
        default="/path_plan",
        help="ROS2 路径规划服务名 (默认 /path_plan)",
    )
    parser.add_argument(
        "--lift-service",
        default="/set_lift",
        help="ROS2 搬运姿态服务名 (默认 /set_lift)",
    )
    parser.add_argument(
        "--lay-down-service",
        default="/set_lay_down",
        help="ROS2 放下姿态服务名 (默认 /set_lay_down)",
    )
    parser.add_argument(
        "--stand-service",
        default="/set_stand",
        help="ROS2 站立姿态服务名 (默认 /set_stand)",
    )
    parser.add_argument(
        "--request-replay-service",
        default="/request_replay",
        help="ROS2 replay 服务名 (默认 /request_replay)",
    )
    parser.add_argument(
        "--notify-goal-reached-service",
        default="/notify_goal_reached",
        help="ROS2 目标到达通知服务名 (默认 /notify_goal_reached)",
    )
    parser.add_argument(
        "--compose-file",
        default=None,
        help="docker-compose.yml 路径 (启用 ServiceManager)",
    )
    parser.add_argument(
        "--service-registry",
        default=None,
        help="服务注册表 JSON 路径 (启用 ServiceManager)",
    )

    args = parser.parse_args()

    adapter = None
    if args.acceptance == "http":
        if not args.move_to_url:
            print("错误: --acceptance http 模式需要同时提供 --move-to-url")
            sys.exit(1)
        from phi_robot.adapters.move_to_passthrough import MoveToPassthroughAdapter

        adapter = MoveToPassthroughAdapter(move_to_url=args.move_to_url)
        print(f"HTTP 验收模式: move_to → {args.move_to_url}  (pick/place/get_pose 跳过)")
    elif args.acceptance == "ros":
        from phi_robot.adapters.ros_acceptance import RosAcceptanceAdapter

        adapter = RosAcceptanceAdapter(
            path_plan_service=args.path_plan_service,
            lift_service=args.lift_service,
            lay_down_service=args.lay_down_service,
            stand_service=args.stand_service,
            request_replay_service=args.request_replay_service,
            notify_goal_reached_service=args.notify_goal_reached_service,
        )
        print(f"ROS2 验收模式:")
        print(f"  路径规划: {args.path_plan_service}")
        print(f"  搬起(lift): {args.lift_service}")
        print(f"  放下(lay_down): {args.lay_down_service}")
        print(f"  站立(stand): {args.stand_service}")
        print(f"  replay: {args.request_replay_service}")
        print(f"  目标到达: {args.notify_goal_reached_service}")
    elif args.sim_url:
        from phi_robot.adapters.remote_unitree import RemoteUnitreeAdapter
        verbose = args.verbose if args.verbose else True  # --sim-url 默认开启 verbose
        adapter = RemoteUnitreeAdapter(
            base_url=args.sim_url,
            verbose=verbose,
            move_to_base_url=args.move_to_url,
        )
        print(f"模拟环境: 远程 {args.sim_url}  (verbose={verbose})")
        if args.move_to_url:
            print(f"move_to 分流: {args.move_to_url}")
    else:
        print("模拟环境: 内嵌 (同进程)")

    print(f"API 监听: {args.host}:{args.port}")
    print(f"健康检查: http://{args.host}:{args.port}/api/health")
    print()

    # ServiceManager（可选）
    service_manager = None
    if args.compose_file and args.service_registry:
        from phi_robot.service_manager import ServiceManager

        service_manager = ServiceManager(
            registry_path=args.service_registry,
            compose_file=args.compose_file,
            profile="test",
        )
        print(f"ServiceManager: compose={args.compose_file}")
        print(f"  registry={args.service_registry}")

    server = PhiRobotAPIServer(
        host=args.host, port=args.port, adapter=adapter,
        service_manager=service_manager,
    )
    server.run()
