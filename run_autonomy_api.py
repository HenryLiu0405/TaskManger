#!/usr/bin/env python3
"""Configuration-driven WAIC autonomy server entrypoint."""

from __future__ import annotations

import argparse
import json
import sys

from phi_robot.autonomy.deployment import (
    DeploymentConfig,
    build_deployed_runtime,
    load_project_environment,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="WAIC Gemini autonomy console")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate .env, robot profile, scene coordinates, and frontend build without ROS",
    )
    args = parser.parse_args()

    load_project_environment()
    config = DeploymentConfig.from_env()
    report = config.check()
    if args.check_config:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    if not report["ok"]:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    runtime = build_deployed_runtime(config)
    try:
        runtime.server.run()
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
