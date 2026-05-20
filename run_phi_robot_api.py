#!/usr/bin/env python3
"""
启动 phi_robot API 服务

使用方式:
  # 内嵌模式（默认，sim 与 API 同进程）
  python run_phi_robot_api.py

  # 分离模式（sim 独立运行在另一个端口）
  python -m phi_robot.adapters.unitree_sim --port 8080 &
  python run_phi_robot_api.py --sim-url http://127.0.0.1:8080
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
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
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

    args = parser.parse_args()

    adapter = None
    if args.sim_url:
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

    server = PhiRobotAPIServer(host=args.host, port=args.port, adapter=adapter)
    server.run()
