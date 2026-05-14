import { DEFAULT_SITE_GOAL, INITIAL_TASKS, SITE_ZONE_MAP, SITE_ZONES } from './data.js';

function cloneTasks(tasks) {
  return tasks.map((task) => ({ ...task }));
}

function makeId(prefix) {
  return `${prefix}-${Math.random().toString(16).slice(2, 10)}`;
}

function createLog(type, message, detail = '') {
  return {
    id: makeId('log'),
    at: new Date().toISOString(),
    type,
    message,
    detail,
  };
}

function arrayMove(array, fromIndex, toIndex) {
  const next = [...array];
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
}

export function getZoneById(zoneId) {
  return SITE_ZONE_MAP[zoneId] || null;
}

export function createInitialState() {
  const draftTasks = cloneTasks(INITIAL_TASKS);

  return {
    goal: DEFAULT_SITE_GOAL,
    zones: SITE_ZONES,
    draftTasks,
    committedTasks: cloneTasks(draftTasks),
    dirty: false,
    execution: {
      status: 'idle',
      index: 0,
      currentTaskId: null,
      completedTaskIds: [],
    },
    logs: [createLog('system', '等待客户编排任务', '通过拖拽完成顺序与方位分配')],
    lastSubmittedAt: null,
  };
}

export function reorderDraftTasks(state, activeId, overId) {
  if (!activeId || !overId || activeId === overId) {
    return state;
  }

  const fromIndex = state.draftTasks.findIndex((task) => task.id === activeId);
  const toIndex = state.draftTasks.findIndex((task) => task.id === overId);

  if (fromIndex === -1 || toIndex === -1) {
    return state;
  }

  return {
    ...state,
    draftTasks: arrayMove(state.draftTasks, fromIndex, toIndex),
    dirty: true,
    logs: [
      ...state.logs,
      createLog('draft', '已调整任务顺序', `第 ${fromIndex + 1} 位移动到第 ${toIndex + 1} 位`),
    ],
  };
}

export function assignTaskToZone(state, taskId, zoneId) {
  if (!taskId || !zoneId) {
    return state;
  }

  const targetZone = getZoneById(zoneId);
  if (!targetZone) {
    return {
      ...state,
      logs: [...state.logs, createLog('error', '目标宫格不存在', zoneId)],
    };
  }

  const taskIndex = state.draftTasks.findIndex((task) => task.id === taskId);
  if (taskIndex === -1) {
    return state;
  }

  const nextTasks = state.draftTasks.map((task) =>
    task.id === taskId ? { ...task, destinationId: zoneId } : task,
  );
  const movedTask = state.draftTasks[taskIndex];

  return {
    ...state,
    draftTasks: nextTasks,
    dirty: true,
    logs: [
      ...state.logs,
      createLog('draft', '已修改方位分配', `${movedTask.label} -> ${targetZone.label}`),
    ],
  };
}

export function submitDraft(state) {
  const missingTasks = state.draftTasks.filter((task) => !task.destinationId);
  if (missingTasks.length > 0) {
    return {
      state,
      ok: false,
      message: '还有任务未分配到六宫格，不能提交。',
    };
  }

  const submittedTasks = cloneTasks(state.draftTasks);

  return {
    state: {
      ...state,
      committedTasks: submittedTasks,
      dirty: false,
      execution: {
        status: 'ready',
        index: 0,
        currentTaskId: null,
        completedTaskIds: [],
      },
      logs: [
        ...state.logs,
        createLog('submit', '修改已提交', `共 ${submittedTasks.length} 个任务`),
      ],
      lastSubmittedAt: new Date().toISOString(),
    },
    ok: true,
    message: '修改已提交。',
  };
}

export function startExecution(state) {
  if (state.dirty) {
    return {
      state: {
        ...state,
        logs: [...state.logs, createLog('error', '请先提交修改', '当前草稿还未同步到执行队列')],
      },
      ok: false,
      message: '请先提交修改。',
    };
  }

  if (state.committedTasks.some((task) => !task.destinationId)) {
    return {
      state: {
        ...state,
        logs: [...state.logs, createLog('error', '存在未分配任务', '请先将所有箱子拖入对应宫格')],
      },
      ok: false,
      message: '存在未分配任务。',
    };
  }

  const nextTasks = cloneTasks(state.committedTasks);

  return {
    state: {
      ...state,
      execution: {
        status: 'running',
        index: 0,
        currentTaskId: nextTasks[0]?.id ?? null,
        completedTaskIds: [],
      },
      logs: [
        ...state.logs,
        createLog('run', '开始执行任务', '顺序将按照已提交的队列顺序执行'),
      ],
    },
    ok: true,
    message: '开始执行。',
  };
}

export function tickExecution(state) {
  if (state.execution.status !== 'running') {
    return state;
  }

  const currentTask = state.committedTasks[state.execution.index];
  if (!currentTask) {
    return {
      ...state,
      execution: {
        ...state.execution,
        status: 'done',
        currentTaskId: null,
      },
      logs: [...state.logs, createLog('run', '任务已完成', '全部箱子都已按顺序执行完成')],
    };
  }

  const completedTaskIds = [...state.execution.completedTaskIds, currentTask.id];
  const nextIndex = state.execution.index + 1;
  const nextTask = state.committedTasks[nextIndex] || null;
  const zone = getZoneById(currentTask.destinationId);

  return {
    ...state,
    execution: {
      status: nextIndex >= state.committedTasks.length ? 'done' : 'running',
      index: nextIndex,
      currentTaskId: nextTask?.id ?? null,
      completedTaskIds,
    },
    logs: [
      ...state.logs,
      createLog(
        'execute',
        `${currentTask.label} 已完成`,
        zone ? `送达 ${zone.label}` : '已送达对应方位',
      ),
      ...(nextIndex >= state.committedTasks.length
        ? [createLog('run', '全部任务执行完成', '客户可重新修改并再次提交')]
        : []),
    ],
  };
}

export function resetState() {
  return createInitialState();
}

export function getAssignedTasksByZone(tasks) {
  return SITE_ZONES.reduce((accumulator, zone) => {
    accumulator[zone.id] = tasks.filter((task) => task.destinationId === zone.id);
    return accumulator;
  }, {});
}

export function hasAllTasksAssigned(tasks) {
  return tasks.every((task) => Boolean(task.destinationId));
}

export function getTaskById(tasks, taskId) {
  return tasks.find((task) => task.id === taskId) || null;
}

