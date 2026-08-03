from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _attribute_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _attribute_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class OfflineSafetyBoundaryTests(unittest.TestCase):
    def test_offline_suite_has_no_ros_network_or_subprocess_imports(self) -> None:
        forbidden = {"rclpy", "requests", "urllib", "httpx", "socket", "subprocess"}
        violations: list[str] = []
        for path in sorted((ROOT / "tests" / "offline").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                for name in names:
                    if name in forbidden:
                        violations.append(f"{path.name}:{node.lineno}:{name}")
        self.assertEqual(violations, [])
        self.assertNotIn("rclpy", sys.modules)

    def test_production_controllers_do_not_call_adapter_execute_directly(self) -> None:
        targets = [
            ROOT / "phi_robot" / "step_debug.py",
            ROOT / "phi_robot" / "mission_runner.py",
            ROOT / "phi_robot" / "dev_console.py",
            ROOT / "phi_robot" / "api_service.py",
            ROOT / "phi_robot" / "api_server.py",
        ]
        violations: list[str] = []
        for path in targets:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                call = _attribute_name(node.func)
                if call.endswith("adapter.execute") or call.endswith("_adapter.execute"):
                    violations.append(f"{path.name}:{node.lineno}:{call}")
                if call.endswith((
                    "adapter.set_sonic_input_source",
                    "adapter.get_sonic_input_source",
                    "adapter.set_safety_bypass",
                )):
                    violations.append(f"{path.name}:{node.lineno}:{call}")
        self.assertEqual(violations, [])
        dev_console = (ROOT / "phi_robot" / "dev_console.py").read_text(encoding="utf-8")
        self.assertNotIn('getattr(self._adapter, "get_robot_state"', dev_console)
        self.assertNotIn("from .api_server import APIHook", dev_console)
        runtime_readers = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "phi_robot" / "step_debug.py",
                ROOT / "phi_robot" / "api_server.py",
            )
        )
        for bypass in (
            "self.adapter.snapshot(",
            "self.adapter.get_odom(",
            "self.adapter._latest_pose_result",
            "self._adapter._fp_target_object_id",
            "self._adapter.nav_reached",
            "skip_nav_resend",
        ):
            self.assertNotIn(bypass, runtime_readers)

    def test_physical_execution_path_has_no_detached_timeout_retry_helper(self) -> None:
        targets = [
            ROOT / "phi_robot" / "skills" / "dispatcher.py",
            ROOT / "phi_robot" / "mission_runner.py",
            ROOT / "phi_robot" / "robot_tools.py",
        ]
        for path in targets:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("asyncio.wait_for", source, path.name)
            self.assertNotIn("asyncio.to_thread", source, path.name)

    def test_hardware_scripts_require_flag_and_exact_robot_identity_before_ros_import(self) -> None:
        for name in ("navigation_hil.py", "locomotion_hil.py"):
            source = (ROOT / "tests" / "hardware" / name).read_text(encoding="utf-8")
            self.assertIn('PHI_ALLOW_HARDWARE_TESTS") != "1"', source)
            self.assertIn("PHI_HARDWARE_ROBOT_ID", source)
            self.assertIn("robot_id != expected", source)
            self.assertLess(source.index("_authorize(args.robot_id)"), source.index("import rclpy"))
        navigation = (ROOT / "tests" / "hardware" / "navigation_hil.py").read_text(encoding="utf-8")
        self.assertIn('"target_x": args.x', navigation)
        self.assertIn('"target_y": args.y', navigation)
        self.assertNotIn('"cmd_vel"', navigation)
        self.assertIn('"/nav_reached"', navigation)
        self.assertIn('nav_state["saw_false"]', navigation)
        self.assertFalse((ROOT / "test_navigation.py").exists())
        self.assertFalse((ROOT / "test_locomotion.py").exists())

    def test_machine_readable_interface_catalogs_are_valid_and_resolved(self) -> None:
        interface_catalog = json.loads(
            (ROOT / "docs" / "autonomy" / "phase0" / "interface-catalog.json")
            .read_text(encoding="utf-8")
        )
        ui_catalog = json.loads(
            (ROOT / "docs" / "autonomy" / "phase0" / "http-ui-matrix.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(interface_catalog["target_runtime"], {
            "os": "Ubuntu 22.04", "ros_distribution": "Humble", "python": "3.10",
        })
        decisions = " ".join(item["decision"] for item in interface_catalog["resolved_conflicts"])
        self.assertIn("/start_navigation", decisions)
        self.assertIn("/odom", decisions)
        self.assertIn("bool success and string message", decisions)
        model = interface_catalog["interface_record_model"]
        for interface in interface_catalog["interfaces"]:
            effective = {**model["defaults"], **interface}
            missing = set(model["required_effective_fields"]) - set(effective)
            self.assertEqual(missing, set(), interface["id"])
        actions = {item["ui_action"]: item for item in ui_catalog["actions"]}
        self.assertEqual(actions["Home RUN"]["robot_side_effect"], "none")
        self.assertIn("SkillDispatcher", actions["REST run mission"]["backend_entry"])

    def test_runtime_defaults_match_frozen_ros_contract(self) -> None:
        launcher = (ROOT / "run_phi_robot_api.py").read_text(encoding="utf-8")
        ros_script = (ROOT / "run_ros_acceptance.sh").read_text(encoding="utf-8")
        service = (ROOT / "robot_interfaces" / "srv" / "ExecuteTrajectory.srv").read_text(encoding="utf-8")
        workstation_bridge = (
            ROOT / "bridge" / "workstation" / "goal_bridge.py"
        ).read_text(encoding="utf-8")
        odom = (ROOT / "systemd" / "orin" / "waic-zmq-odom.service").read_text(encoding="utf-8")
        offline_gate = (ROOT / "scripts" / "check_offline.sh").read_text(encoding="utf-8")
        conda_env = (ROOT / "environment.yml").read_text(encoding="utf-8")
        self.assertIn('default="/start_navigation"', launcher)
        self.assertIn("/opt/ros/humble/setup.bash", ros_script)
        self.assertNotIn("/opt/ros/jazzy", ros_script)
        self.assertEqual(service.splitlines()[-2:], ["bool success", "string message"])
        self.assertIn("response.message =", workstation_bridge)
        self.assertIn("/Odometry", odom)
        self.assertIn("/odom", odom)
        ros_adapter = (ROOT / "phi_robot" / "adapters" / "ros_acceptance.py").read_text(encoding="utf-8")
        self.assertIn("self._sonic_input_source == source", ros_adapter)
        self.assertIn('"error_code": None if confirmed else "VERIFICATION_FAILED"', ros_adapter)
        self.assertIn('"${VERSION_ID:-}" != "22.04"', offline_gate)
        self.assertIn('"${ROS_DISTRO:-}" != "humble"', offline_gate)
        self.assertIn("Python 3.10 required", offline_gate)
        self.assertIn("unset PHI_MOVE_TO_URL PHI_SIM_BASE_URL", offline_gate)
        self.assertIn("name: AgenticRobot", conda_env)
        self.assertIn('"python=3.10.*"', conda_env)
        for dependency in ("flask", "flask-cors", "jsonschema", "pytest"):
            self.assertIn(dependency, conda_env)

    def test_legacy_runner_cannot_report_failed_scenarios_as_success(self) -> None:
        source = (ROOT / "phi_robot" / "phase1_runner.py").read_text(encoding="utf-8")
        self.assertIn("Legacy Phase 1", source)
        self.assertIn("else 1", source)
        self.assertNotIn("0/3", source)


if __name__ == "__main__":
    unittest.main()
