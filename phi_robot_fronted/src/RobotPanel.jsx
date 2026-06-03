import { Card, Descriptions, Tag, Tooltip } from 'antd';
import {
  AimOutlined,
  CompassOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
} from '@ant-design/icons';

const STATE_COLORS = {
  idle: 'default',
  moving: 'processing',
  arrived: 'success',
  picking: 'processing',
  holding: 'warning',
  placing: 'processing',
  paused: 'warning',
  stopped: 'error',
};

const STATE_LABELS = {
  idle: '空闲',
  moving: '导航中',
  arrived: '已到达',
  picking: '搬起中',
  holding: '持箱中',
  placing: '放置中',
  paused: '已暂停',
  stopped: '已终止',
};

function ServiceDot({ name, online }) {
  return (
    <Tooltip title={`${name}: ${online ? '在线' : '离线'}`}>
      {online ? (
        <CheckCircleFilled style={{ color: '#52c41a', fontSize: 12 }} />
      ) : (
        <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: 12 }} />
      )}
    </Tooltip>
  );
}

const NAV_STATUS_COLORS = {
  IDLE: 'default',
  NAVIGATING: 'processing',
  PAUSED: 'warning',
  ARRIVED: 'success',
  FAILED: 'error',
};

const SERVICE_LABELS = {
  '/start_navigation': '导航',
  '/set_lift': '搬起',
  '/set_lay_down': '放下',
  '/set_stand': '站立',
  '/request_replay': '回放',
  '/notify_goal_reached': '目标通知',
  '/get_locomotion_mode': '状态',
  '/pause_navigation': '暂停导航',
};

export default function RobotPanel({ manual, robot, services }) {
  const stateColor = STATE_COLORS[manual?.state] || 'default';
  const stateLabel = STATE_LABELS[manual?.state] || manual?.state || '--';
  const allOnline = Object.keys(services || {}).length > 0
    && Object.values(services || {}).every(Boolean);

  return (
    <Card
      title={<span><CompassOutlined className="mr-2" />机器人状态</span>}
      size="small"
      className="w-full"
    >
      <Descriptions column={1} size="small" colon={false}>
        <Descriptions.Item label="任务状态">
          <Tag color={stateColor}>{stateLabel}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="导航状态">
          <Tag color={NAV_STATUS_COLORS[robot?.nav_status] || 'default'}>
            {robot?.nav_status || '--'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="持箱">
          {manual?.holding_box ? (
            <Tag color="warning">是</Tag>
          ) : (
            <Tag>否</Tag>
          )}
        </Descriptions.Item>

        {/* ROS 服务状态 */}
        <Descriptions.Item label="服务">
          <div className="flex flex-wrap gap-2">
            {Object.keys(services || {}).length === 0 && (
              <Tag>ROS 未连接</Tag>
            )}
            {Object.entries(services || {}).map(([name, online]) => (
              <Tooltip key={name} title={`${name}: ${online ? '在线' : '离线'}`}>
                <Tag
                  color={online ? 'success' : 'error'}
                  className="text-sm"
                  style={{ padding: '0 4px', lineHeight: '18px' }}
                >
                  {online ? '✓' : '✗'} {SERVICE_LABELS[name] || name.split('/').pop()}
                </Tag>
              </Tooltip>
            ))}
          </div>
        </Descriptions.Item>

        <Descriptions.Item label="carry_state">
          <code className="text-sm">{robot?.carry_state || '--'}</code>
        </Descriptions.Item>
        <Descriptions.Item label="posture">
          <code className="text-sm">{robot?.posture_state || '--'}</code>
        </Descriptions.Item>
        <Descriptions.Item label="hold_pose">
          {robot?.hold_pose_active ? (
            <Tag color="success">稳定</Tag>
          ) : (
            <Tag>等待</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="replay">
          {robot?.replay_active ? (
            <Tag color="processing">执行中</Tag>
          ) : (
            <Tag>空闲</Tag>
          )}
        </Descriptions.Item>
      </Descriptions>

      <div className="mt-3 pt-3 border-t border-[#2a2a2a]">
        <div className="text-sm text-[#8a8a8a] mb-1">
          <AimOutlined className="mr-1" />当前位置
        </div>
        <div className="text-base font-mono">
          x: {manual?.current_position?.x?.toFixed(1) ?? '--'}
          {'  '}y: {manual?.current_position?.y?.toFixed(1) ?? '--'}
        </div>
        {manual?.target_position && (
          <div className="mt-2">
            <div className="text-sm text-[#8a8a8a] mb-1">
              <AimOutlined className="mr-1" />目标位置
            </div>
            <div className="text-base font-mono text-amber-400">
              x: {manual?.target_position?.x?.toFixed(1) ?? '--'}
              {'  '}y: {manual?.target_position?.y?.toFixed(1) ?? '--'}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
