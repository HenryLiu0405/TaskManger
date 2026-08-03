#!/usr/bin/env python3
"""Run the offline suite with ROS, network, and child processes hard-disabled."""

from __future__ import annotations

import importlib.abc
import socket
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _forbidden(operation: str):
    def fail(*args, **kwargs):
        raise AssertionError(f"offline test attempted forbidden operation: {operation}")
    return fail


class _BlockRosImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "rclpy" or fullname.startswith("rclpy."):
            raise AssertionError(f"offline test attempted ROS import: {fullname}")
        return None


def main() -> int:
    sys.meta_path.insert(0, _BlockRosImports())
    socket.create_connection = _forbidden("network create_connection")  # type: ignore[assignment]
    socket.socket.connect = _forbidden("network socket.connect")  # type: ignore[assignment]
    urllib.request.urlopen = _forbidden("network urlopen")  # type: ignore[assignment]
    subprocess.Popen = _forbidden("subprocess.Popen")  # type: ignore[assignment,misc]
    subprocess.run = _forbidden("subprocess.run")  # type: ignore[assignment]
    subprocess.call = _forbidden("subprocess.call")  # type: ignore[assignment]
    subprocess.check_call = _forbidden("subprocess.check_call")  # type: ignore[assignment]
    subprocess.check_output = _forbidden("subprocess.check_output")  # type: ignore[assignment]

    suite = unittest.defaultTestLoader.discover(
        start_dir=str(ROOT / "tests" / "offline"),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
