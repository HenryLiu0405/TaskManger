import { useState } from 'react';
import { Button, Tooltip, Modal } from 'antd';
import {
  PauseCircleOutlined,
  CaretRightOutlined,
  StopOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  SearchOutlined,
  ReloadOutlined,
  CameraOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';

function ConfirmModal({ open, title, message, onConfirm, onCancel, danger }) {
  return (
    <Modal
      open={open}
      onOk={onConfirm}
      onCancel={onCancel}
      okText="确定"
      cancelText="取消"
      okButtonProps={danger ? { danger: true } : {}}
      centered
      width={420}
    >
      <div className="flex items-start gap-3 pt-2">
        <ExclamationCircleOutlined className="text-[#d4a853] text-lg mt-0.5" />
        <div>
          <p className="text-base font-semibold mb-1">{title}</p>
          <p className="text-[#8a8a8a]">{message}</p>
        </div>
      </div>
    </Modal>
  );
}

export default function ControlButtons({
  mode,
  manualState,
  robotState,
  autoRunning,
  autoPaused,
  onPause,
  onStop,
  onPick,
  onPlace,
  onStart,
  onResume,
  autoEnabled,
  autoDestCount,
}) {
  const [stopOpen, setStopOpen] = useState(false);
  const [pickOpen, setPickOpen] = useState(false);
  const [placeOpen, setPlaceOpen] = useState(false);
  const [startOpen, setStartOpen] = useState(false);

  function confirmStop() { setStopOpen(false); onStop(); }
  function confirmPick() { setPickOpen(false); onPick(); }
  function confirmPlace() { setPlaceOpen(false); onPlace(); }
  function confirmStart() { setStartOpen(false); onStart(); }

  if (mode === 'manual') {
    const isStopped = manualState === 'stopped';
    const isPaused = manualState === 'paused';
    const isMoving = manualState === 'moving' || manualState === 'picking' || manualState === 'placing';

    // 使用后端安全状态机 — can_walk/can_pick/can_place 优先于本地状态判断
    const canWalk = robotState?.can_walk !== false && !isStopped;
    const canPick = robotState?.can_pick === true && !isStopped && !isMoving && !isPaused;
    const canPlace = robotState?.can_place === true && !isStopped && !isMoving && !isPaused;

    return (
      <>
        <div className="flex flex-col gap-5">
          {/* 暂停 / 终止 */}
          <div className="flex flex-wrap gap-4">
            <Button
              size="middle"
              icon={isPaused ? <CaretRightOutlined /> : <PauseCircleOutlined />}
              onClick={onPause}
              disabled={isStopped}
              type={isPaused ? 'primary' : 'default'}
            >
              {isPaused ? '继续' : '暂停'}
            </Button>

            <Button
              danger
              size="middle"
              icon={<StopOutlined />}
              onClick={() => setStopOpen(true)}
              disabled={isStopped}
            >
              终止
            </Button>
          </div>

          {/* 搬箱子 / 放箱子 */}
          <div className="flex flex-wrap gap-4">
            <Tooltip title={!canPick ? `安全状态: ${robotState?.state || 'unknown'}，不可搬起` : undefined}>
              <Button
                size="middle"
                icon={<ArrowUpOutlined />}
                onClick={() => setPickOpen(true)}
                disabled={!canPick}
              >
                搬箱子
              </Button>
            </Tooltip>
            <Tooltip title={!canPlace ? `安全状态: ${robotState?.state || 'unknown'}，不可放下` : undefined}>
              <Button
                size="middle"
                icon={<ArrowDownOutlined />}
                onClick={() => setPlaceOpen(true)}
                disabled={!canPlace}
              >
                放箱子
              </Button>
            </Tooltip>
          </div>

          {/* 占位按钮 */}
          <div className="flex flex-wrap gap-4">
            <Tooltip title="功能待实现">
              <Button size="middle" icon={<CameraOutlined />} disabled>
                箱子重识
              </Button>
            </Tooltip>
            <Tooltip title="功能待实现">
              <Button size="middle" icon={<SearchOutlined />} disabled>
                搜寻箱子
              </Button>
            </Tooltip>
            <Tooltip title="功能待实现">
              <Button size="middle" icon={<ReloadOutlined />} disabled>
                重新规划
              </Button>
            </Tooltip>
          </div>

          {isStopped && (
            <div className="text-[#d44a4a]">
              已终止，所有状态已重置（不可恢复）。请重启后端服务。
            </div>
          )}
        </div>

        <ConfirmModal
          open={pickOpen}
          title="确认搬箱子"
          message="将执行 lift + replay 序列，确定要继续吗？"
          onConfirm={confirmPick}
          onCancel={() => setPickOpen(false)}
        />
        <ConfirmModal
          open={placeOpen}
          title="确认放箱子"
          message="将执行 lay_down + replay + stand 序列，确定要继续吗？"
          onConfirm={confirmPlace}
          onCancel={() => setPlaceOpen(false)}
        />
        <ConfirmModal
          open={stopOpen}
          title="确认终止"
          message="终止后所有状态将被重置，不可恢复。确定要继续吗？"
          onConfirm={confirmStop}
          onCancel={() => setStopOpen(false)}
          danger
        />
      </>
    );
  }

  // 自动模式
  if (mode === 'auto') {
    return (
      <>
        <div className="flex flex-wrap gap-4">
          <Button
            type="primary"
            size="middle"
            icon={<CaretRightOutlined />}
            onClick={() => setStartOpen(true)}
            disabled={!autoEnabled || autoRunning}
          >
            开始
          </Button>
          <Button
            size="middle"
            icon={autoPaused ? <CaretRightOutlined /> : <PauseCircleOutlined />}
            onClick={autoPaused ? onResume : onPause}
            disabled={!autoRunning}
            type={autoPaused ? 'primary' : 'default'}
          >
            {autoPaused ? '继续' : '暂停'}
          </Button>
          <Button
            danger
            size="middle"
            icon={<StopOutlined />}
            onClick={() => setStopOpen(true)}
            disabled={!autoRunning}
          >
            终止
          </Button>
        </div>

        <ConfirmModal
          open={startOpen}
          title="确认启动"
          message={`将执行自动搬运任务，共 ${autoDestCount || '?'} 个目标，确定要开始吗？`}
          onConfirm={confirmStart}
          onCancel={() => setStartOpen(false)}
        />
        <ConfirmModal
          open={stopOpen}
          title="确认终止"
          message="终止后所有状态将被重置，不可恢复。确定要继续吗？"
          onConfirm={confirmStop}
          onCancel={() => setStopOpen(false)}
          danger
        />
      </>
    );
  }

  return null;
}
