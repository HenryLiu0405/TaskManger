import { SCENARIOS, WAREHOUSE } from './data.js';
import { applyEvent, createInitialState, getScenarioById, resetState } from './state.js';

const app = document.querySelector('#app');
let state = createInitialState();
let timerId = null;

const STATUS_LABELS = {
  idle: '待命',
  running: '运行中',
  paused: '暂停',
  finished: '已完成',
};

const STEP_LABELS = {
  pending: '待执行',
  active: '进行中',
  done: '完成',
  blocked: '阻断',
  superseded: '已替换',
};

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function setState(nextState) {
  state = nextState;
  render();
}

function scheduleNextStep(delayMs) {
  if (timerId) {
    clearTimeout(timerId);
  }
  timerId = setTimeout(() => {
    advanceScenario(true);
  }, delayMs);
}

function startScenario() {
  if (state.runtime.status === 'running') {
    return;
  }
  const baseState = state.runtime.status === 'finished' ? resetState(state.scenarioId) : state;
  const nextState = {
    ...baseState,
    runtime: { ...baseState.runtime, status: 'running' },
  };
  setState(nextState);
  scheduleNextStep(260);
}

function pauseScenario() {
  if (timerId) {
    clearTimeout(timerId);
    timerId = null;
  }
  setState({
    ...state,
    runtime: { ...state.runtime, status: 'paused' },
  });
}

function resetScenario() {
  if (timerId) {
    clearTimeout(timerId);
    timerId = null;
  }
  setState(resetState(state.scenarioId));
}

function changeScenario(scenarioId) {
  if (timerId) {
    clearTimeout(timerId);
    timerId = null;
  }
  setState(resetState(scenarioId));
}

function stepScenario() {
  if (timerId) {
    clearTimeout(timerId);
    timerId = null;
  }
  advanceScenario(false);
}

function updateTaskGoal() {
  const input = document.querySelector('#goal-input');
  const goal = input?.value.trim();
  if (!goal) {
    return;
  }
  const nextState = applyEvent(state, {
    type: 'task_updated',
    goal,
    message: '用户更新了高层目标。',
  });
  setState(nextState);
}

function advanceScenario(autoSchedule) {
  const baseState =
    state.runtime.status === 'idle' && !autoSchedule
      ? { ...state, runtime: { ...state.runtime, status: 'paused' } }
      : state;
  const scenario = getScenarioById(baseState.scenarioId);
  const step = scenario.script[baseState.runtime.scriptIndex];
  if (!step) {
    setState({
      ...baseState,
      runtime: { ...baseState.runtime, status: 'finished' },
    });
    return;
  }

  const nextState = applyEvent(baseState, step.event);
  nextState.runtime.scriptIndex += 1;

  if (nextState.runtime.status === 'running' && autoSchedule) {
    scheduleNextStep(step.delayMs ?? 600);
  }

  setState(nextState);
}

function formatTime(value) {
  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false });
}

function describeEvent(event) {
  if (event.message) {
    return event.message;
  }
  switch (event.type) {
    case 'task_started':
      return '任务启动。';
    case 'plan_created':
      return `计划已生成 (v${event.plan?.version ?? 1})。`;
    case 'tool_called':
      return `工具 ${event.tool} 已调用。`;
    case 'obstacle_added':
      return `检测到障碍物：${event.obstacle?.label || '未知区域'}。`;
    case 'route_blocked':
      return `路线 ${event.routeId} 被阻断。`;
    case 'replan_started':
      return '开始重规划。';
    case 'replan_finished':
      return `重规划完成 (v${event.plan?.version ?? 2})。`;
    case 'priority_updated':
      return `优先级更新：${event.boxId} -> ${event.priority}。`;
    case 'box_picked':
      return `箱子 ${event.boxId} 已装载。`;
    case 'box_placed':
      return `箱子 ${event.boxId} 已放置。`;
    case 'task_finished':
      return '任务结束。';
    default:
      return '事件已记录。';
  }
}

function renderStatusPill() {
  const label = STATUS_LABELS[state.runtime.status] || state.runtime.status;
  return `<span class="status-pill status-pill--${state.runtime.status}">${escapeHtml(label)}</span>`;
}

function renderMetrics() {
  const delivered = state.world.crates.filter((crate) => crate.status === 'delivered').length;
  return `
    <div class="status-row">
      <article class="metric-card">
        <p>任务状态</p>
        <strong>${renderStatusPill()}</strong>
      </article>
      <article class="metric-card">
        <p>已完成箱子</p>
        <strong>${delivered}/${state.world.crates.length}</strong>
      </article>
      <article class="metric-card">
        <p>重规划次数</p>
        <strong>${state.metrics.replans}</strong>
      </article>
      <article class="metric-card">
        <p>工具调用</p>
        <strong>${state.metrics.toolCalls}</strong>
      </article>
    </div>
  `;
}

function toPoint(value, size, padding) {
  return padding + value * size + size / 2;
}

function renderMap() {
  const cellSize = 52;
  const padding = 18;
  const { cols, rows } = state.world.size;
  const width = cols * cellSize + padding * 2;
  const height = rows * cellSize + padding * 2;
  const routes = Object.values(state.routes);

  const gridCells = Array.from({ length: cols * rows }, (_, index) => {
    const x = index % cols;
    const y = Math.floor(index / cols);
    return `<rect class="grid-cell" x="${padding + x * cellSize}" y="${padding + y * cellSize}" width="${cellSize}" height="${cellSize}" />`;
  });

  const zones = Object.values(WAREHOUSE.zones).map((zone) => {
    return `
      <g class="zone zone--${zone.id}">
        <rect x="${padding + zone.x * cellSize}" y="${padding + zone.y * cellSize}" width="${zone.w * cellSize}" height="${zone.h * cellSize}" />
        <text x="${padding + zone.x * cellSize + 12}" y="${padding + zone.y * cellSize + 22}">${escapeHtml(zone.label)}</text>
      </g>
    `;
  });

  const obstacles = state.world.obstacles.map((obstacle) => {
    const cells = obstacle.cells
      .map(
        (cell) => `<rect class="obstacle" x="${padding + cell.x * cellSize}" y="${padding + cell.y * cellSize}" width="${cellSize}" height="${cellSize}" />`,
      )
      .join('');
    return `
      <g class="obstacle-group">
        ${cells}
        <text x="${padding + obstacle.cells[0].x * cellSize + 10}" y="${padding + obstacle.cells[0].y * cellSize + 22}">${escapeHtml(obstacle.label || '障碍')}
        </text>
      </g>
    `;
  });

  const crates = state.world.crates.map((crate) => {
    const cx = toPoint(crate.x, cellSize, padding);
    const cy = toPoint(crate.y, cellSize, padding);
    return `
      <g class="crate crate--${crate.status}">
        <circle cx="${cx}" cy="${cy}" r="14" style="--crate-color:${crate.color}"></circle>
        <text x="${cx}" y="${cy + 4}">${escapeHtml(crate.id)}</text>
      </g>
    `;
  });

  const robotX = toPoint(state.world.robot.x, cellSize, padding);
  const robotY = toPoint(state.world.robot.y, cellSize, padding);
  const robot = `
    <g class="robot">
      <polygon points="${robotX},${robotY - 16} ${robotX + 14},${robotY} ${robotX},${robotY + 16} ${robotX - 14},${robotY}" />
      <text x="${robotX}" y="${robotY + 34}">R-01</text>
    </g>
  `;

  const routeLines = routes
    .map((route) => {
      const points = route.points
        .map((point) => `${toPoint(point.x, cellSize, padding)},${toPoint(point.y, cellSize, padding)}`)
        .join(' ');
      const isActive = route.id === state.activeRouteId;
      const classes = ['route'];
      if (isActive) classes.push('route--active');
      if (!isActive) classes.push('route--ghost');
      if (route.blocked) classes.push('route--blocked');
      return `<polyline class="${classes.join(' ')}" points="${points}" />`;
    })
    .join('');

  const activeRoute = state.activeRouteId ? state.routes[state.activeRouteId] : null;

  return `
    <section class="panel panel--map">
      <header class="panel__head">
        <div>
          <h2>仓库视图</h2>
          <p>路线由工具事件注入与改写</p>
        </div>
        <span class="panel__badge">${escapeHtml(activeRoute?.label || '无活动路线')}</span>
      </header>
      <div class="map-stage">
        <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="仓库示意图">
          <rect class="map-base" x="0" y="0" width="${width}" height="${height}" />
          ${gridCells.join('')}
          ${zones.join('')}
          ${routeLines}
          ${obstacles.join('')}
          ${crates.join('')}
          ${robot}
        </svg>
      </div>
      <div class="map-legend">
        <span><i class="legend legend--robot"></i>机器人</span>
        <span><i class="legend legend--crate"></i>箱子</span>
        <span><i class="legend legend--route"></i>可行路线</span>
        <span><i class="legend legend--blocked"></i>阻断路线</span>
        <span><i class="legend legend--obstacle"></i>工具注入障碍</span>
      </div>
    </section>
  `;
}

function renderPlan() {
  const steps = state.plan.steps
    .map((step, index) => {
      const status = step.status || 'pending';
      return `
        <li class="plan-step plan-step--${status}">
          <span class="plan-step__index">${index + 1}</span>
          <div class="plan-step__body">
            <strong>${escapeHtml(step.label)}</strong>
            <span>${escapeHtml(step.tool || 'manual')} · ${escapeHtml(step.routeId || '—')}</span>
          </div>
          <em>${escapeHtml(STEP_LABELS[status] || status)}</em>
        </li>
      `;
    })
    .join('');

  const notes = state.agentNotes
    .slice(-3)
    .map((note) => `<li>${escapeHtml(note)}</li>`)
    .join('');

  return `
    <section class="panel panel--plan">
      <header class="panel__head">
        <div>
          <h2>Agent 计划</h2>
          <p>v${state.plan.version} · ${escapeHtml(state.plan.status)}</p>
        </div>
        <span class="panel__badge">${state.plan.steps.length} 步</span>
      </header>
      <ol class="plan-list">${steps}</ol>
      <div class="note-block">
        <h3>Agent 备注</h3>
        <ul>${notes || '<li>暂无备注</li>'}</ul>
      </div>
    </section>
  `;
}

function renderToolLog() {
  const recent = state.toolLog.slice(-6).reverse();
  return `
    <section class="panel panel--tools">
      <header class="panel__head">
        <div>
          <h2>工具调用</h2>
          <p>工具编排与执行结果</p>
        </div>
        <span class="panel__badge">${state.toolLog.length}</span>
      </header>
      <div class="tool-list">
        ${recent.length === 0 ? '<p class="empty">等待工具事件...</p>' : ''}
        ${recent
          .map(
            (entry) => `
              <article class="tool-item">
                <div>
                  <strong>${escapeHtml(entry.tool)}</strong>
                  <span>${escapeHtml(entry.target || '未指定')}</span>
                </div>
                <p>${escapeHtml(entry.result || '执行完成')}</p>
                <time>${formatTime(entry.at)} · ${entry.durationMs ?? '--'}ms</time>
              </article>
            `,
          )
          .join('')}
      </div>
    </section>
  `;
}

function renderEvents() {
  const recent = state.events.slice(-10).reverse();
  return `
    <section class="panel panel--events">
      <header class="panel__head">
        <div>
          <h2>事件流</h2>
          <p>来自 tool 与环境的实时反馈</p>
        </div>
        <span class="panel__badge">${state.events.length}</span>
      </header>
      <div class="event-list">
        ${recent.length === 0 ? '<p class="empty">等待事件...</p>' : ''}
        ${recent
          .map(
            (event) => `
              <article class="event-item event-item--${event.type}">
                <div>
                  <strong>${escapeHtml(event.type)}</strong>
                  <span>${escapeHtml(describeEvent(event))}</span>
                </div>
                <time>${formatTime(event.at)}</time>
              </article>
            `,
          )
          .join('')}
      </div>
    </section>
  `;
}

function renderControls() {
  const scenario = getScenarioById(state.scenarioId);
  const options = SCENARIOS.map(
    (item) => `<option value="${item.id}" ${item.id === state.scenarioId ? 'selected' : ''}>${escapeHtml(item.name)}</option>`,
  ).join('');

  return `
    <section class="panel panel--controls">
      <header class="panel__head">
        <div>
          <h2>调度控制台</h2>
          <p>${escapeHtml(scenario.summary)}</p>
        </div>
        ${renderStatusPill()}
      </header>
      <div class="control-row">
        <label>
          场景
          <select id="scenario-select">${options}</select>
        </label>
        <div class="control-actions">
          <button data-action="start">启动</button>
          <button data-action="pause">暂停</button>
          <button data-action="step">单步</button>
          <button data-action="reset">重置</button>
        </div>
      </div>
      <div class="control-row">
        <label class="goal-input">
          高层目标
          <input id="goal-input" type="text" value="${escapeHtml(state.task.goal)}" />
        </label>
        <button data-action="apply-goal">应用目标</button>
      </div>
    </section>
  `;
}

function render() {
  app.innerHTML = `
    <div class="shell">
      <header class="topbar">
        <div class="hero">
          <p class="hero__eyebrow">phi_robot fronted</p>
          <h1>搬运调度型 Demo</h1>
          <p class="hero__desc">展示 agentic 在执行中遇到阻塞后的重规划与工具编排能力。</p>
        </div>
        ${renderControls()}
      </header>

      ${renderMetrics()}

      <main class="main-grid">
        ${renderMap()}
        ${renderPlan()}
        ${renderToolLog()}
        ${renderEvents()}
      </main>
    </div>
  `;

  const scenarioSelect = document.querySelector('#scenario-select');
  scenarioSelect?.addEventListener('change', (event) => {
    changeScenario(event.target.value);
  });

  document.querySelectorAll('[data-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.dataset.action;
      if (action === 'start') startScenario();
      if (action === 'pause') pauseScenario();
      if (action === 'step') stepScenario();
      if (action === 'reset') resetScenario();
      if (action === 'apply-goal') updateTaskGoal();
    });
  });
}

render();
