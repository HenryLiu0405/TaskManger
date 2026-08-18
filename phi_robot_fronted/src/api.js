/**
 * phi_robot 前端 API 客户端
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || "/api";

class PhiRobotAPIClient {
  constructor(baseURL = API_BASE_URL) {
    this.baseURL = baseURL;
    this.missionId = null;
    this.pollInterval = null;
  }

  /**
   * 检查服务器健康状态
   */
  async health() {
    const response = await fetch(`${this.baseURL}/health`);
    return response.json();
  }

  async getAutonomyReadiness() {
    const response = await fetch(`${this.baseURL}/autonomy/readiness`);
    return this._json(response, 'Unable to read runtime readiness');
  }

  async _json(response, fallback) {
    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      // Use the transport status below.
    }
    if (!response.ok) {
      const error = new Error(body?.message || body?.error || fallback || response.statusText);
      error.status = response.status;
      error.code = body?.error_code;
      throw error;
    }
    return body;
  }

  async submitAutonomyTask(instruction, options = {}) {
    const response = await fetch(`${this.baseURL}/autonomy/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction, background: true, ...options }),
    });
    const body = await this._json(response, 'Autonomy task submission failed');
    this.missionId = body?.task?.mission_id || null;
    return body;
  }

  async getAutonomyTask(missionId = this.missionId) {
    if (!missionId) throw new Error('No autonomy mission ID provided');
    const response = await fetch(`${this.baseURL}/autonomy/tasks/${missionId}`);
    return this._json(response, 'Unable to read autonomy task');
  }

  async getAutonomyReplay(missionId = this.missionId) {
    if (!missionId) throw new Error('No autonomy mission ID provided');
    const response = await fetch(`${this.baseURL}/autonomy/tasks/${missionId}/replay`);
    return this._json(response, 'Unable to read mission replay');
  }

  async getAutonomyMetrics(missionId = this.missionId) {
    if (!missionId) throw new Error('No autonomy mission ID provided');
    const response = await fetch(`${this.baseURL}/autonomy/tasks/${missionId}/metrics`);
    return this._json(response, 'Unable to read mission metrics');
  }

  async pauseAutonomyTask(missionId = this.missionId) {
    if (!missionId) throw new Error('No autonomy mission ID provided');
    const response = await fetch(`${this.baseURL}/autonomy/tasks/${missionId}/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    return this._json(response, 'Trusted pause request failed');
  }

  async stopAutonomyTask(missionId = this.missionId, reason = 'operator stop') {
    if (!missionId) throw new Error('No autonomy mission ID provided');
    const response = await fetch(`${this.baseURL}/autonomy/tasks/${missionId}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
    return this._json(response, 'Trusted stop request failed');
  }

  /**
   * 提交任务
   * @param {Array<string>} destinationOrder - 目标位置顺序 (nw, n, ne, w, c, e, sw, s, se)
   * @param {Object} options - 任务选项
   */
  async submitMission(destinationOrder, options = {}) {
    const requestId = this._hashRequestId(destinationOrder);
    const payload = {
      request_id: requestId,
      scene_id: "scene-001",
      goal_id: `goal-${this._generateId()}`,
      scene_version: "scene-v1",
      stock_layout_version: "stock-v1",
      destination_order: destinationOrder,
      options: {
        max_replans: 2,
        timeout_s: 60,
        ...options
      }
    };

    const response = await fetch(`${this.baseURL}/missions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(`Failed to submit mission: ${response.statusText}`);
    }

    const data = await response.json();
    this.missionId = data.mission_id;
    return data;
  }

  /**
   * 获取任务状态
   */
  async getMissionStatus(missionId = this.missionId) {
    if (!missionId) throw new Error("No mission ID provided");

    const response = await fetch(`${this.baseURL}/missions/${missionId}`);
    if (!response.ok) {
      throw new Error(`Failed to get mission status: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * 启动任务执行
   */
  async runMission(missionId = this.missionId) {
    if (!missionId) throw new Error("No mission ID provided");

    const response = await fetch(`${this.baseURL}/missions/${missionId}/run`, {
      method: "POST"
    });

    if (!response.ok) {
      // Prefer structured JSON error from server when available
      try {
        const body = await response.json();
        const msg = body && body.error ? body.error : response.statusText;
        const err = new Error(msg);
        err.status = response.status;
        throw err;
      } catch (err) {
        // non-JSON response fallback
        const fallback = new Error(response.statusText);
        fallback.status = response.status;
        throw fallback;
      }
    }

    return response.json();
  }

  /**
   * 暂停任务
   */
  async pauseMission(missionId = this.missionId) {
    if (!missionId) throw new Error("No mission ID provided");

    const response = await fetch(`${this.baseURL}/missions/${missionId}/pause`, {
      method: "POST"
    });

    if (!response.ok) {
      throw new Error(`Failed to pause mission: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * 中止任务
   */
  async abortMission(missionId = this.missionId) {
    if (!missionId) throw new Error("No mission ID provided");

    const response = await fetch(`${this.baseURL}/missions/${missionId}/abort`, {
      method: "POST"
    });

    if (!response.ok) {
      throw new Error(`Failed to abort mission: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * 获取任务事件
   */
  async getMissionEvents(missionId = this.missionId) {
    if (!missionId) throw new Error("No mission ID provided");

    const response = await fetch(`${this.baseURL}/missions/${missionId}/events`);
    if (!response.ok) {
      throw new Error(`Failed to get mission events: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * 获取仿真环境快照
   */
  async getSnapshot() {
    const response = await fetch(`${this.baseURL}/snapshot`);
    if (!response.ok) {
      throw new Error(`Failed to get snapshot: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * 启动轮询监控任务状态
   */
  startPolling(callback, interval = 500) {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
    }

    this.pollInterval = setInterval(async () => {
      try {
        const status = await this.getMissionStatus();
        callback(status);

        // 如果任务完成，停止轮询
        if (["completed", "failed", "aborted"].includes(status.status)) {
          this.stopPolling();
        }
      } catch (error) {
        console.error("Polling error:", error);
      }
    }, interval);
  }

  /**
   * 停止轮询
   */
  stopPolling() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  /**
   * 恢复已暂停的任务
   */
  async resumeMission(missionId = this.missionId) {
    if (!missionId) throw new Error("No mission ID provided");

    const response = await fetch(`${this.baseURL}/missions/${missionId}/resume`, {
      method: "POST"
    });

    if (!response.ok) {
      throw new Error(`Failed to resume mission: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * 重置任务
   */
  async resetMission(missionId = this.missionId) {
    if (!missionId) throw new Error("No mission ID provided");

    const response = await fetch(`${this.baseURL}/missions/${missionId}/reset`, {
      method: "POST"
    });

    if (!response.ok) {
      throw new Error(`Failed to reset mission: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * 订阅 SSE 事件流（替代轮询）
   * @param {Function} onEvent - 事件回调
   * @param {Function} onError - 错误回调
   * @returns {EventSource} 可调用 .close() 取消订阅
   */
  subscribeEvents(missionId = this.missionId, onEvent, onError) {
    if (!missionId) throw new Error("No mission ID provided");

    const es = new EventSource(`${this.baseURL}/missions/${missionId}/stream`);
    es.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        onEvent(event);
        if (["completed", "failed", "aborted"].includes(event.status)) {
          es.close();
        }
      } catch (e) {
        // non-JSON heartbeat, ignore
      }
    };
    es.onerror = (err) => {
      if (onError) onError(err);
      es.close();
    };
    return es;
  }

  /**
   * 从 destination_order 生成确定性 request_id（用于幂等）
   */
  _hashRequestId(destinationOrder) {
    const joined = destinationOrder.join(",");
    let hash = 0;
    for (let i = 0; i < joined.length; i++) {
      const ch = joined.charCodeAt(i);
      hash = ((hash << 5) - hash) + ch;
      hash |= 0;  // 32-bit int
    }
    return `req-${Math.abs(hash).toString(36)}`;
  }

  /**
   * 生成短 ID
   */
  _generateId() {
    return Math.random().toString(36).substr(2, 8);
  }

  /**
   * 查询 SONIC 输入源
   * @returns {Promise<{active_source: string}>}
   */
  async getSonicInputSource() {
    const response = await fetch(`${this.baseURL}/dev/sonic/input_source`);
    if (!response.ok) {
      throw new Error(`Failed to get SONIC input source: ${response.statusText}`);
    }
    return response.json();
  }

  /**
   * 切换 SONIC 输入源
   * @param {boolean} gamepad - true=手柄接管, false=ROS2 自动控制
   */
  async setSonicInputSource(gamepad) {
    const response = await fetch(`${this.baseURL}/dev/sonic/input_source`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gamepad }),
    });
    if (!response.ok) {
      throw new Error(`Failed to set SONIC input source: ${response.statusText}`);
    }
    return response.json();
  }
}

export default PhiRobotAPIClient;
