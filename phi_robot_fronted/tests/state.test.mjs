import test from 'node:test';
import assert from 'node:assert/strict';

import {
  assignTaskToZone,
  createInitialState,
  getZoneById,
  hasAllTasksAssigned,
  reorderDraftTasks,
  startExecution,
  submitDraft,
  tickExecution,
} from '../src/state.js';

function assignAllTasks(state) {
  let nextState = state;
  const zoneIds = ['nw', 'n', 'ne', 'sw', 's', 'se'];

  nextState.draftTasks.forEach((task, index) => {
    nextState = assignTaskToZone(nextState, task.id, zoneIds[index]);
  });

  return nextState;
}

test('creates an initial planning state', () => {
  const state = createInitialState();

  assert.equal(state.draftTasks.length, 6);
  assert.equal(state.committedTasks.length, 6);
  assert.equal(state.dirty, false);
  assert.equal(state.execution.status, 'idle');
  assert.equal(hasAllTasksAssigned(state.draftTasks), false);
});

test('reorders the draft queue and marks it dirty', () => {
  const state = createInitialState();
  const next = reorderDraftTasks(state, 'box-a', 'box-c');

  assert.equal(next.dirty, true);
  assert.equal(next.draftTasks[0].id, 'box-b');
  assert.equal(next.draftTasks[1].id, 'box-c');
  assert.equal(next.draftTasks[2].id, 'box-a');
});

test('assigns a task to a zone', () => {
  const state = createInitialState();
  const next = assignTaskToZone(state, 'box-a', 'nw');

  assert.equal(next.dirty, true);
  assert.equal(next.draftTasks.find((task) => task.id === 'box-a').destinationId, 'nw');
  assert.equal(getZoneById('nw').label, '西北宫格');
});

test('rejects submitting when tasks are still unassigned', () => {
  const state = createInitialState();
  const result = submitDraft(state);

  assert.equal(result.ok, false);
  assert.match(result.message, /未分配/);
});

test('submits a fully assigned draft and starts execution', () => {
  const assigned = assignAllTasks(createInitialState());
  const submitted = submitDraft(assigned);

  assert.equal(submitted.ok, true);
  assert.equal(submitted.state.dirty, false);
  assert.equal(submitted.state.committedTasks.every((task) => Boolean(task.destinationId)), true);

  const running = startExecution(submitted.state);
  assert.equal(running.ok, true);
  assert.equal(running.state.execution.status, 'running');
  assert.equal(running.state.execution.currentTaskId, running.state.committedTasks[0].id);
});

test('ticks execution forward until completion', () => {
  const assigned = assignAllTasks(createInitialState());
  const submitted = submitDraft(assigned).state;
  const started = startExecution(submitted).state;
  const firstTick = tickExecution(started);

  assert.equal(firstTick.execution.completedTaskIds.includes(started.committedTasks[0].id), true);
  assert.equal(firstTick.execution.index, 1);
  assert.equal(firstTick.execution.status, 'running');
});
