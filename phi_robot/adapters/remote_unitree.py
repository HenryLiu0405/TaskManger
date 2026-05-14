"""
Remote Unitree adapter — HTTP client that talks to a standalone UnitreeSim server.

Use when the simulation environment runs as a separate process.
Set verbose=True to print every request/response to stdout.
"""

from __future__ import annotations
import json
import logging
import sys
from typing import Any, Dict
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger("phi_robot.remote_unitree")


class RemoteUnitreeAdapter:
    """HTTP client adapter implementing the RobotAdapter protocol."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        timeout_s: float = 30.0,
        verbose: bool = False,
        move_to_base_url: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.move_to_base_url = move_to_base_url.rstrip("/") if move_to_base_url else None
        self.timeout_s = timeout_s
        self.verbose = verbose
        self._call_count = 0

    def execute(
        self,
        tool: str,
        args: Dict[str, Any],
        *,
        request_id: str,
        goal_id: str,
        step_id: str,
    ) -> Dict[str, Any]:
        payload = {
            "tool": tool,
            "args": args,
            "request_id": request_id,
            "goal_id": goal_id,
            "step_id": step_id,
        }
        self._call_count += 1
        # 如果配置了 move_to 独立地址，move_to 请求路由到同事的服务器
        target_url = self.move_to_base_url if tool == "move_to" and self.move_to_base_url else self.base_url
        if self.verbose:
            self._print_request(tool, args, step_id, target_url)
        result = self._post("/api/command", payload, base_url=target_url)
        if self.verbose:
            self._print_response(result)
        return result

    def snapshot(self) -> Dict[str, Any]:
        return self._get("/api/snapshot")

    def reset(self) -> None:
        if self.verbose:
            print("\n" + "=" * 56, flush=True)
            print(f"  [adapter] POST /api/reset  (重置模拟环境)", flush=True)
            print("=" * 56, flush=True)
        self._post("/api/reset", {})
        self._call_count = 0

    # ── internal ──────────────────────────────────────────────

    def _print_request(self, tool: str, args: Dict[str, Any], step_id: str, target_url: str = "") -> None:
        via = f" → {target_url}" if target_url and target_url != self.base_url else ""
        print(f"\n  [{self._call_count}] >>> POST /api/command{via}", flush=True)
        print(f"       step_id : {step_id}", flush=True)
        print(f"       tool    : {tool}", flush=True)
        if tool == "move_to":
            target = args.get("target", {})
            current = args.get("current", {})
            action = args.get("action", "?")
            print(
                f"       args    : target=({target.get('x')}, {target.get('y')}, {target.get('z')}, "
                f"θ={target.get('theta', 0)})  "
                f"current=({current.get('x')}, {current.get('y')}, {current.get('z')}, "
                f"θ={current.get('theta', 0)})  action={action}",
                flush=True,
            )
        elif tool in ("pick", "place"):
            keys = [k for k in args if k != "timeout_s"]
            vals = ", ".join(f"{k}={args[k]}" for k in keys)
            print(f"       args    : {vals}", flush=True)

    def _print_response(self, result: Dict[str, Any]) -> None:
        status = result.get("status", "?")
        symbol = "+" if status == "ok" else "!"
        msg = result.get("message", "")
        err = result.get("error_code", "")
        if err:
            print(f"       {symbol} status={status}  error={err}  {msg}", flush=True)
        else:
            print(f"       {symbol} status={status}  {msg}", flush=True)

    def _post(self, path: str, data: Dict[str, Any], *, base_url: str | None = None) -> Dict[str, Any]:
        url = (base_url or self.base_url) + path
        body = json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as e:
            logger.error("Server unreachable at %s: %s", url, e)
            return {
                "status": "error",
                "error_code": "SIM_UNREACHABLE",
                "message": f"Server unreachable: {e}",
            }

    def _get(self, path: str) -> Dict[str, Any]:
        req = Request(f"{self.base_url}{path}", method="GET")
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as e:
            logger.error("Sim server unreachable at %s%s: %s", self.base_url, path, e)
            return {"error": f"Sim server unreachable: {e}"}
