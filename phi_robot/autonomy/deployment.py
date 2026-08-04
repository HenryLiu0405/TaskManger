"""Configuration-only composition for the deployed WAIC workstation."""

from __future__ import annotations

import hmac
import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from flask import abort, jsonify, request, send_from_directory

from phi_robot.api_server import PhiRobotAPIServer
from phi_robot.autonomy.bootstrap import GeminiAutonomyRuntime, build_gemini_autonomy_runtime
from phi_robot.autonomy.gemini_provider import (
    PROJECT_DOTENV,
    GeminiRoboticsER2Config,
    _read_dotenv,
)
from phi_robot.autonomy.interrupts import AdapterInterruptLane
from phi_robot.autonomy.locations import NineGridLocationResolver
from phi_robot.autonomy.ros_bridge import (
    AdapterObservationSource,
    RosSemanticBackend,
    build_shared_dispatcher,
)
from phi_robot.autonomy.runtime import RobotRuntime
from phi_robot.robot_profile import get_active


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_project_environment(path: str | Path = PROJECT_DOTENV) -> None:
    """Load the ignored project .env, preserving process/systemd overrides."""
    for key, value in _read_dotenv(Path(path)).items():
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class DeploymentConfig:
    robot_id: str
    camera_host: str
    scene_config_path: Path
    scene_id: str
    scene_version: str
    map_version: str
    data_dir: Path
    frontend_dist: Path
    api_host: str = "0.0.0.0"
    api_port: int = 5000
    path_plan_service: str = "/start_navigation"
    lift_service: str = "/set_lift"
    lay_down_service: str = "/set_lay_down"
    stand_service: str = "/set_stand"
    request_replay_service: str = "/request_replay"
    notify_goal_reached_service: str = "/notify_goal_reached"
    frame_max_age_s: float = 2.0
    observation_ttl_s: float = 60.0
    frame_sync_tolerance_s: float = 0.75
    selection_ttl_s: float = 120.0
    operator_cidrs: tuple[str, ...] = ("127.0.0.0/8", "::1/128")
    operator_token: str = field(default="", repr=False)
    compose_file: Optional[Path] = None
    service_registry_path: Optional[Path] = None

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "DeploymentConfig":
        values = dict(os.environ if environ is None else environ)
        robots_path = _path(
            values.get("PHI_ROBOTS_CONFIG", "phi_robot/robots.json"),
            root=REPOSITORY_ROOT,
        )
        profile = get_active(str(robots_path)) or {}
        robot_id = str(values.get("WAIC_ROBOT_ID") or profile.get("id") or "").strip()
        camera_host = str(
            values.get("WAIC_CAMERA_HOST") or profile.get("camera_host") or ""
        ).strip()
        cidrs = tuple(
            item.strip()
            for item in str(
                values.get("PHI_OPERATOR_CIDRS", "127.0.0.0/8,::1/128")
            ).split(",")
            if item.strip()
        )
        return cls(
            robot_id=robot_id,
            camera_host=camera_host,
            scene_config_path=_path(
                values.get("PHI_SCENE_CONFIG", "phi_robot/scene_coords.json"),
                root=REPOSITORY_ROOT,
            ),
            scene_id=str(values.get("PHI_SCENE_ID", "scene-001")).strip(),
            scene_version=str(values.get("PHI_SCENE_VERSION", "scene-v1")).strip(),
            map_version=str(values.get("PHI_MAP_VERSION", "waic-map-v1")).strip(),
            data_dir=_path(
                values.get("PHI_DATA_DIR", "~/.phi_robot/data"),
                root=REPOSITORY_ROOT,
            ),
            frontend_dist=_path(
                values.get("PHI_FRONTEND_DIST", "phi_robot_fronted/dist"),
                root=REPOSITORY_ROOT,
            ),
            api_host=str(values.get("PHI_API_HOST", "0.0.0.0")).strip(),
            api_port=_integer(values.get("PHI_API_PORT", "5000"), "PHI_API_PORT"),
            path_plan_service=str(values.get("PHI_NAV_SERVICE", "/start_navigation")),
            lift_service=str(values.get("PHI_LIFT_SERVICE", "/set_lift")),
            lay_down_service=str(values.get("PHI_LAY_DOWN_SERVICE", "/set_lay_down")),
            stand_service=str(values.get("PHI_STAND_SERVICE", "/set_stand")),
            request_replay_service=str(
                values.get("PHI_REPLAY_SERVICE", "/request_replay")
            ),
            notify_goal_reached_service=str(
                values.get("PHI_NOTIFY_GOAL_SERVICE", "/notify_goal_reached")
            ),
            frame_max_age_s=_number(
                values.get("PHI_FRAME_MAX_AGE_S", "2.0"), "PHI_FRAME_MAX_AGE_S"
            ),
            observation_ttl_s=_number(
                values.get("PHI_OBSERVATION_TTL_S", "60.0"),
                "PHI_OBSERVATION_TTL_S",
            ),
            frame_sync_tolerance_s=_number(
                values.get("PHI_FRAME_SYNC_TOLERANCE_S", "0.75"),
                "PHI_FRAME_SYNC_TOLERANCE_S",
            ),
            selection_ttl_s=_number(
                values.get("PHI_SELECTION_TTL_S", "120.0"),
                "PHI_SELECTION_TTL_S",
            ),
            operator_cidrs=cidrs,
            operator_token=str(values.get("PHI_OPERATOR_TOKEN", "")),
            compose_file=_optional_path(
                values.get("PHI_COMPOSE_FILE", "/home/hairo/waic/docker-compose.yml"),
                root=REPOSITORY_ROOT,
            ),
            service_registry_path=_optional_path(
                values.get(
                    "PHI_SERVICE_REGISTRY",
                    "phi_robot/service_registry.json",
                ),
                root=REPOSITORY_ROOT,
            ),
        )

    def resolver(self) -> NineGridLocationResolver:
        return NineGridLocationResolver.from_json_file(
            self.scene_config_path,
            scene_id=self.scene_id,
            scene_version=self.scene_version,
            map_version=self.map_version,
        )

    def check(self, environ: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        model_name = ""
        api_mode = ""
        api_key_configured = False
        if not self.robot_id:
            errors.append("WAIC_ROBOT_ID is missing and robots.json has no active robot")
        if not self.camera_host:
            errors.append("WAIC_CAMERA_HOST is missing")
        if not self.scene_config_path.is_file():
            errors.append(f"scene config not found: {self.scene_config_path}")
        else:
            try:
                self.resolver()
            except Exception as exc:
                errors.append(f"scene config is invalid: {exc}")
        if not (self.frontend_dist / "index.html").is_file():
            warnings.append("frontend build is missing; run npm run build before deployment")
        for value in self.operator_cidrs:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError:
                errors.append(f"invalid PHI_OPERATOR_CIDRS entry: {value}")
        try:
            model = GeminiRoboticsER2Config.from_env(
                None if environ is None else dict(environ)
            )
            model_name = model.model
            api_mode = model.api_mode
            api_key_configured = bool(model.api_key)
            if "robotics-er" not in model.model.lower():
                errors.append(
                    "GEMINI_ROBOTICS_MODEL is not a Gemini Robotics-ER model"
                )
        except Exception as exc:
            errors.append(str(exc))
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "configuration": {
                "robot_id": self.robot_id,
                "camera_host": self.camera_host,
                "scene_config": str(self.scene_config_path),
                "scene": f"{self.scene_id}@{self.scene_version}",
                "map_version": self.map_version,
                "data_dir": str(self.data_dir),
                "frontend_dist": str(self.frontend_dist),
                "listen": f"{self.api_host}:{self.api_port}",
                "gemini_model": model_name,
                "gemini_api_mode": api_mode,
                "gemini_api_key_configured": api_key_configured,
            },
        }


@dataclass
class DeployedAutonomyRuntime:
    config: DeploymentConfig
    adapter: Any
    robot_runtime: RobotRuntime
    autonomy_runtime: GeminiAutonomyRuntime
    server: PhiRobotAPIServer

    def close(self) -> None:
        self.autonomy_runtime.close()
        self.robot_runtime.close()
        shutdown = getattr(self.adapter, "shutdown", None)
        if callable(shutdown):
            shutdown()


def build_deployed_runtime(config: DeploymentConfig) -> DeployedAutonomyRuntime:
    """Construct the real runtime; no cloud call or robot action occurs here."""
    report = config.check()
    if not report["ok"]:
        raise ValueError("; ".join(report["errors"]))
    config.data_dir.mkdir(parents=True, exist_ok=True)

    # Delayed import keeps Mac/offline validation independent of ROS packages.
    from phi_robot.adapters.ros_acceptance import RosAcceptanceAdapter

    adapter = RosAcceptanceAdapter(
        path_plan_service=config.path_plan_service,
        lift_service=config.lift_service,
        lay_down_service=config.lay_down_service,
        stand_service=config.stand_service,
        request_replay_service=config.request_replay_service,
        notify_goal_reached_service=config.notify_goal_reached_service,
        camera_host=config.camera_host,
    )
    resolver = config.resolver()
    observations = AdapterObservationSource(
        adapter,
        robot_id=config.robot_id,
        frame_max_age_s=config.frame_max_age_s,
        decision_ttl_s=config.observation_ttl_s,
        sync_tolerance_s=config.frame_sync_tolerance_s,
    )
    semantic_backend = RosSemanticBackend(
        adapter,
        observation_source=observations,
        location_resolver=resolver,
        selection_ttl_s=config.selection_ttl_s,
    )
    dispatcher = build_shared_dispatcher(
        adapter, semantic_backend, robot_id=config.robot_id
    )
    interrupt_lane = AdapterInterruptLane(adapter)
    robot_runtime = RobotRuntime(
        robot_id=config.robot_id,
        adapter=adapter,
        ledger_path=config.data_dir / "supervisor.sqlite3",
        dispatcher=dispatcher,
        interrupt_lane=interrupt_lane,
    )
    autonomy_runtime = build_gemini_autonomy_runtime(
        robot_id=config.robot_id,
        semantic_backend=semantic_backend,
        observation_source=observations,
        location_resolver=resolver,
        interrupt_lane=interrupt_lane,
        supervisor_ledger_path=None,
        model_ledger_path=config.data_dir / "model_calls.sqlite3",
        dispatcher=dispatcher,
        supervisor=robot_runtime.supervisor,
    )
    service_manager = None
    if (
        config.compose_file is not None
        and config.compose_file.is_file()
        and config.service_registry_path is not None
        and config.service_registry_path.is_file()
    ):
        from phi_robot.service_manager import ServiceManager

        service_manager = ServiceManager(
            registry_path=str(config.service_registry_path),
            compose_file=str(config.compose_file),
            profile="test",
        )
    operator_authorizer = _operator_authorizer(config)
    server = PhiRobotAPIServer(
        host=config.api_host,
        port=config.api_port,
        adapter=adapter,
        skill_dispatcher=dispatcher,
        robot_runtime=robot_runtime,
        autonomy_service=autonomy_runtime.service,
        autonomy_operator_authorizer=operator_authorizer,
        service_manager=service_manager,
    )
    _protect_mutating_routes(server, operator_authorizer)
    _serve_frontend(server, config.frontend_dist)
    _wire_drop_events(adapter, autonomy_runtime)
    return DeployedAutonomyRuntime(
        config=config,
        adapter=adapter,
        robot_runtime=robot_runtime,
        autonomy_runtime=autonomy_runtime,
        server=server,
    )


def _wire_drop_events(adapter: Any, runtime: GeminiAutonomyRuntime) -> None:
    register = getattr(adapter, "set_drop_callback", None)
    if not callable(register):
        return

    def handle(event: Mapping[str, Any]) -> None:
        mission_id = runtime.supervisor.active_mission_id()
        if not mission_id:
            return
        runtime.service.handle_drop(
            mission_id,
            {**dict(event), "robot_id": runtime.robot_id, "mission_id": mission_id},
            background=True,
        )

    register(handle)


def _operator_authorizer(config: DeploymentConfig):
    networks = tuple(
        ipaddress.ip_network(value, strict=False) for value in config.operator_cidrs
    )

    def authorize(request: Any) -> Optional[str]:
        supplied = str(request.headers.get("X-Operator-Token") or "")
        if config.operator_token and hmac.compare_digest(supplied, config.operator_token):
            return "operator-token"
        origin = str(request.headers.get("Origin") or "")
        if origin:
            parsed = urlsplit(origin)
            if parsed.netloc != str(request.host or ""):
                return None
        try:
            address = ipaddress.ip_address(str(request.remote_addr or ""))
        except ValueError:
            return None
        if any(address in network for network in networks):
            return f"trusted-network:{address}"
        return None

    return authorize


def _protect_mutating_routes(server: PhiRobotAPIServer, authorizer: Any) -> None:
    """Apply the same trusted-client gate to every production mutation."""

    @server.app.before_request
    def require_trusted_mutation():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if not request.path.startswith("/api/"):
            return None
        if authorizer(request):
            return None
        return jsonify(
            {
                "error_code": "TRUSTED_OPERATOR_REQUIRED",
                "message": "this mutating endpoint requires a trusted client",
            }
        ), 403


def _serve_frontend(server: PhiRobotAPIServer, dist: Path) -> None:
    if not (dist / "index.html").is_file():
        return

    @server.app.route("/")
    def autonomy_index():
        return send_from_directory(dist, "index.html")

    @server.app.route("/<path:asset_path>")
    def autonomy_asset(asset_path: str):
        if asset_path.startswith("api/"):
            abort(404)
        candidate = dist / asset_path
        if candidate.is_file():
            return send_from_directory(dist, asset_path)
        return send_from_directory(dist, "index.html")


def _path(value: str | Path, *, root: Path) -> Path:
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _optional_path(value: Any, *, root: Path) -> Optional[Path]:
    text = str(value or "").strip()
    return _path(text, root=root) if text else None


def _integer(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result
