"""
ServiceManager — 容器生命周期管理器

封装 docker compose 命令，结合 DDS 健康检查，
通过 HTTP API 暴露给前端 ServicePanel。
"""

from __future__ import annotations
import os
import subprocess
import json
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

logger = logging.getLogger("phi_robot.service_manager")


def _expand_env(value: str) -> str:
    """展开字符串里的 ${VAR} / $VAR 环境变量（未定义则保持原样）。

    用于 service_registry.json 里的 ${WAIC_ORIN_HOST} / ${APP_DOMAIN} 等占位符，
    使 registry 随多机器人切换（active_robot.env）而指向正确的 Orin / bridge unit。
    """
    if not isinstance(value, str) or "$" not in value:
        return value
    return os.path.expandvars(value)


@dataclass
class ServiceDef:
    """单个服务定义（对应 docker-compose 中的一个 service）"""
    id: str
    group: str                     # "server" | "robot"
    label: str                     # 前端显示名
    description: str
    compose_service: Optional[str] # docker-compose service 名（null = 非容器服务）
    check: Dict[str, Any]          # 健康检查配置
    manage: Optional[Dict[str, Any]] = None  # 进程管理配置（null = docker compose）
    depends_on: list = field(default_factory=list)


@dataclass
class Registry:
    """服务注册表"""
    compose_file: str
    compose_profile: str
    services: list[ServiceDef]

    @classmethod
    def from_file(cls, path: str) -> "Registry":
        with open(path) as f:
            data = json.load(f)
        # 变量替换：
        #  1) ${orin_host} → data["orin_host"]（历史兼容），
        #     其中 data["orin_host"] 自身可含 ${WAIC_ORIN_HOST} 环境变量。
        #  2) manage.unit / ssh_host 里的 ${WAIC_ORIN_HOST} / ${APP_DOMAIN}
        #     等环境变量按当前进程 os.environ 解析（多机器人切换时随 domain 变化）。
        orin_host = _expand_env(data.get("orin_host", ""))
        for s in data["services"]:
            mgr = s.get("manage")
            if not mgr:
                continue
            if mgr.get("ssh_host") == "${orin_host}":
                mgr["ssh_host"] = orin_host
            # 环境变量替换（unit 名 / ssh_host 里的 ${APP_DOMAIN} 等）
            if "unit" in mgr:
                mgr["unit"] = _expand_env(mgr["unit"])
            if "ssh_host" in mgr:
                mgr["ssh_host"] = _expand_env(mgr["ssh_host"])
        services = [ServiceDef(**s) for s in data["services"]]
        return cls(
            compose_file=data["compose_file"],
            compose_profile=data["compose_profile"],
            services=services,
        )


class ServiceManager:
    """
    封装 docker compose + DDS 健康检查，提供统一的服务生命周期接口。

    使用方式:
        mgr = ServiceManager("/app/phi_robot/service_registry.json",
                             compose_file="/waic/docker-compose.yml",
                             profile="test")
        mgr.start_all()
        status = mgr.get_frontend_data()
        mgr.shutdown()
    """

    def __init__(
        self,
        registry_path: str,
        compose_file: str = "/waic/docker-compose.yml",
        profile: str = "test",
        poll_interval: float = 3.0,
    ):
        self.registry = Registry.from_file(registry_path)
        self.compose_file = compose_file
        self.profile = profile
        self.poll_interval = poll_interval
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._status_cache: Dict[str, Dict[str, Any]] = {}

    # ── docker compose 命令封装 ──────────────────────

    def _dc(self, *args) -> subprocess.CompletedProcess:
        """统一入口: docker compose -f <file> --profile <profile> <args>"""
        cmd = [
            "docker", "compose", "-f", self.compose_file,
            f"--profile", self.profile,
            *args,
        ]
        logger.debug(f"docker compose: {' '.join(args)}")
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    def _sc(self, unit: str, action: str, ssh_host: str = "") -> dict:
        """系统服务管理（本地或 SSH 远端）"""
        if ssh_host:
            cmd = ["ssh", "-o", "ConnectTimeout=10",
                   "-i", "/home/hairo/.ssh/id_ed25519_waic",
                   ssh_host, "systemctl", "--user", action, unit]
        else:
            cmd = ["systemctl", "--user", action, unit]
        logger.debug(f"systemctl: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {"ok": result.returncode == 0, "output": result.stdout.strip() or result.stderr.strip()}

    def _call_ros2_service(self, service: str, srv_type: str, data: str,
                           timeout_sec: float = 10.0,
                           setup_bash: str = "") -> dict:
        """调用 ROS2 服务（通过 ros2 service call 子进程）

        setup_bash: 额外 source 的 ROS2 workspace setup.bash（分号分隔）
        """
        sources = "source /opt/ros/humble/setup.bash"
        if setup_bash:
            for p in setup_bash.split(";"):
                p = p.strip()
                if p:
                    sources += f" && source {p}"
        cmd = [
            "bash", "-c",
            f"{sources} && "
            f"ros2 service call {service} {srv_type} \"{data}\""
        ]
        logger.info(f"ros2 service call: {service} {srv_type} {data}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=timeout_sec)
            ok = result.returncode == 0
            if not ok:
                logger.warning(f"ros2 service call failed: {result.stderr.strip()}")
            return {"ok": ok, "output": result.stdout.strip() or result.stderr.strip()}
        except subprocess.TimeoutExpired:
            logger.warning(f"ros2 service call timeout: {service}")
            return {"ok": False, "output": "timeout"}

    def _post_start_activate(self, svc: ServiceDef) -> dict:
        """启动后激活：等待 DDS 服务可用后调用 ROS2 服务"""
        post = svc.manage.get("post_start") if svc.manage else None
        if not post or post.get("method") != "ros2_service":
            return {"ok": True, "output": "no post_start"}
        svc_name = post["service"]
        svc_type = post["type"]
        svc_data = post.get("data", "{}")
        retries = post.get("retries", 10)
        delay = post.get("delay", 1.0)
        setup_bash = post.get("setup_bash", "")
        for i in range(retries):
            # 刷新 DDS 缓存 + 检查服务是否已上线
            self._dds_cache = []
            self._dds_cache_time = 0.0
            dds_services = self._get_dds_services()
            if svc_name in dds_services:
                logger.info(f"Service {svc_name} is ready, activating...")
                return self._call_ros2_service(svc_name, svc_type, svc_data,
                                               setup_bash=setup_bash)
            logger.debug(f"Waiting for {svc_name}... ({i+1}/{retries})")
            time.sleep(delay)
        logger.warning(f"Service {svc_name} not ready after {retries * delay}s")
        return {"ok": False, "output": f"service {svc_name} not ready"}

    # ── 容器生命周期 ────────────────────────────────

    def start_all(self) -> Dict[str, bool]:
        """一键启动所有测试容器"""
        result = self._dc("up", "-d")
        return {"ok": result.returncode == 0, "output": result.stdout.strip()}

    def start(self, svc_id: str) -> dict:
        """启动单个服务（Docker 容器或 systemd 进程）"""
        svc = self._find_service(svc_id)
        if not svc:
            return {"ok": False, "error": f"unknown service: {svc_id}"}
        if svc.manage and svc.manage.get("method") == "systemd":
            ssh = svc.manage.get("ssh_host", "")
            result = self._sc(svc.manage["unit"], "start", ssh_host=ssh)
            if result["ok"]:
                post = self._post_start_activate(svc)
                if not post["ok"]:
                    logger.warning(f"post_start for {svc_id}: {post['output']}")
            return result
        if svc.compose_service:
            result = self._dc("up", "-d", svc.compose_service)
            return {"ok": result.returncode == 0, "output": result.stdout.strip()}
        return {"ok": False, "error": f"no management method for {svc_id}"}

    def stop(self, svc_id: str) -> dict:
        """停止单个服务"""
        svc = self._find_service(svc_id)
        if not svc:
            return {"ok": False, "error": f"unknown service: {svc_id}"}
        if svc.manage and svc.manage.get("method") == "systemd":
            ssh = svc.manage.get("ssh_host", "")
            return self._sc(svc.manage["unit"], "stop", ssh_host=ssh)
        if svc.compose_service:
            result = self._dc("stop", svc.compose_service)
            return {"ok": result.returncode == 0, "output": result.stdout.strip()}
        return {"ok": False, "error": f"no management method for {svc_id}"}

    def restart(self, svc_id: str) -> dict:
        """重启单个服务"""
        svc = self._find_service(svc_id)
        if not svc:
            return {"ok": False, "error": f"unknown service: {svc_id}"}
        if svc.manage and svc.manage.get("method") == "systemd":
            ssh = svc.manage.get("ssh_host", "")
            result = self._sc(svc.manage["unit"], "restart", ssh_host=ssh)
            if result["ok"]:
                post = self._post_start_activate(svc)
                if not post["ok"]:
                    logger.warning(f"post_start for {svc_id}: {post['output']}")
            return result
        if svc.compose_service:
            result = self._dc("restart", svc.compose_service)
            return {"ok": result.returncode == 0, "output": result.stdout.strip()}
        return {"ok": False, "error": f"no management method for {svc_id}"}

    def shutdown(self) -> Dict[str, bool]:
        """停止所有容器并清理"""
        self._running = False
        result = self._dc("down")
        return {"ok": result.returncode == 0, "output": result.stdout.strip()}

    # ── 状态查询 ────────────────────────────────────

    # DDS 服务列表缓存（避免每次检查都起 subprocess）
    _dds_cache: list = []
    _dds_cache_time: float = 0.0
    _dds_cache_ttl: float = 2.0  # 缓存有效期

    def _get_dds_services(self) -> list:
        """获取当前 DDS 上所有 ROS2 服务名（带缓存）"""
        now = time.time()
        if self._dds_cache and (now - self._dds_cache_time) < self._dds_cache_ttl:
            return self._dds_cache
        try:
            result = subprocess.run(
                ["bash", "-c", "source /opt/ros/humble/setup.bash && ros2 service list"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                self._dds_cache = [line.strip() for line in result.stdout.strip().splitlines()]
                self._dds_cache_time = now
                return self._dds_cache
        except Exception:
            pass
        return self._dds_cache  # 返回过期缓存

    def _check_dds(self, svc: ServiceDef) -> str:
        """检查单个服务的 DDS 状态，返回 "online" | "offline" | "unknown" """
        check = svc.check
        if not check or check.get("method") != "ros2_service":
            return "unknown"
        target = check.get("target", "")
        if not target:
            return "unknown"
        services = self._get_dds_services()
        return "online" if target in services else "offline"

    def _check_systemd(self, unit: str, ssh_host: str = "") -> str:
        """检查 systemd 单元是否 active（本地或 SSH 远端），返回 "online" | "offline" | "unknown" """
        if ssh_host:
            cmd = ["ssh", "-o", "ConnectTimeout=10",
                   "-i", "/home/hairo/.ssh/id_ed25519_waic",
                   ssh_host, "systemctl", "--user", "is-active", unit]
        else:
            cmd = ["systemctl", "--user", "is-active", unit]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            out = result.stdout.strip()
            if out == "active":
                return "online"
            elif out in ("inactive", "failed", "activating", "deactivating"):
                return "offline"
            else:
                return "unknown"
        except Exception:
            return "unknown"

    def get_status(self, svc_id: Optional[str] = None) -> dict:
        """
        合并 docker compose ps + DDS 健康检查。
        如果 svc_id 为 None，返回所有服务状态。
        """
        # 1. 容器存活状态（docker compose ps --format json 输出 NDJSON）
        ps_result = self._dc("ps", "--format", "json")
        containers = {}
        if ps_result.returncode == 0 and ps_result.stdout.strip():
            try:
                # Docker Compose v2 --format json 输出逐行 JSON（NDJSON）
                items = [json.loads(line) for line in ps_result.stdout.strip().splitlines() if line.strip()]
                for c in items:
                    containers[c.get("Service", "")] = c.get("State", "unknown")
            except json.JSONDecodeError:
                pass

        # 2. 遍历注册表，合并容器 + DDS 检查
        results = {}
        targets = [self._find_service(svc_id)] if svc_id else self.registry.services
        for svc in targets:
            if not svc:
                continue

            # 容器状态
            if svc.compose_service:
                container_state = containers.get(svc.compose_service, "not_found")
            else:
                container_state = "no_container"

            # DDS 状态
            dds_state = self._check_dds(svc)

            # systemd 状态（仅对 systemd 管理的服务，本地或 SSH 远端）
            systemd_state = "unknown"
            if svc.manage and svc.manage.get("method") == "systemd":
                ssh = svc.manage.get("ssh_host", "")
                systemd_state = self._check_systemd(svc.manage["unit"], ssh_host=ssh)

            # 综合判定:
            # - systemd 管理：看 systemd 状态（DDS 发现延迟太大，不可靠）
            # - 容器：看容器 running + DDS
            # - 其他：只看 DDS
            if svc.manage and svc.manage.get("method") == "systemd":
                online = systemd_state == "online"
            elif svc.compose_service:
                online = container_state == "running"
            else:
                online = dds_state == "online"

            results[svc.id] = {
                "id": svc.id,
                "label": svc.label,
                "group": svc.group,
                "container": container_state,
                "dds": dds_state,
                "systemd": systemd_state,
                "online": online,
                "manage": svc.manage,
            }

        self._status_cache = results
        return results if svc_id is None else results.get(svc_id, {})

    def get_frontend_data(self) -> dict:
        """返回前端 ServicePanel 需要的结构化数据"""
        status = self.get_status()
        services_list = list(status.values())
        return {
            "services": services_list,
            "summary": {
                "total": len(services_list),
                "online": sum(1 for s in services_list if s["online"]),
                "offline": sum(1 for s in services_list if not s["online"]),
            },
        }

    # ── 后台监控 ────────────────────────────────────

    def start_monitor(self):
        """启动后台监控线程（3 秒轮询）"""
        self._running = True
        self._monitor_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._monitor_thread.start()
        logger.info(f"ServiceManager monitor started (polling every {self.poll_interval}s)")

    def _poll_loop(self):
        """后台轮询: docker compose ps + 更新缓存"""
        while self._running:
            try:
                self.get_status()
            except Exception as e:
                logger.warning(f"ServiceManager poll error: {e}")
            time.sleep(self.poll_interval)

    # ── 内部辅助 ────────────────────────────────────

    def _get_compose_name(self, svc_id: str) -> Optional[str]:
        svc = self._find_service(svc_id)
        return svc.compose_service if svc else None

    def _find_service(self, svc_id: str) -> Optional[ServiceDef]:
        for svc in self.registry.services:
            if svc.id == svc_id:
                return svc
        return None
