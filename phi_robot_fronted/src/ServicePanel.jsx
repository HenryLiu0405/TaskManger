import { useState, useEffect, useCallback, useRef } from 'react';
import { Tag, Button, Card, Space, Tooltip } from 'antd';
import {
  ReloadOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  SyncOutlined,
  CameraOutlined,
} from '@ant-design/icons';

const API = '/api/services';

export default function ServicePanel() {
  const [services, setServices] = useState([]);
  const [summary, setSummary] = useState({ total: 0, online: 0, offline: 0 });
  const [loading, setLoading] = useState({});
  const [previewSvc, setPreviewSvc] = useState(null); // 当前展开画面预览的服务 id
  const previewTs = useRef(Date.now());               // 稳定时间戳，避免 MJPEG 重连

  // ── 拉取服务状态 ──────────────────────────────────
  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(API);
      const data = await res.json();
      setServices(data.services || []);
      setSummary(data.summary || { total: 0, online: 0, offline: 0 });
    } catch (_) {}
  }, []);

  // ── SSE 实时更新 ───────────────────────────────────
  useEffect(() => {
    fetchStatus();
    let es;
    let reconnectTimer;

    function connect() {
      es = new EventSource(`${API}/stream`);
      es.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          setServices(data.services || []);
          setSummary(data.summary || { total: 0, online: 0, offline: 0 });
        } catch (_) {}
      };
      es.onerror = () => {
        es.close();
        reconnectTimer = setTimeout(connect, 3000);
      };
    }

    connect();
    return () => {
      if (es) es.close();
      clearTimeout(reconnectTimer);
    };
  }, []);

  // ── 操作 ──────────────────────────────────────────
  const action = useCallback(async (svcId, op) => {
    setLoading((prev) => ({ ...prev, [svcId]: op }));
    try {
      await fetch(`${API}/${svcId}/${op}`, { method: 'POST' });
      // 等容器状态变化后再刷新
      setTimeout(fetchStatus, 1500);
    } catch (_) {}
    setLoading((prev) => ({ ...prev, [svcId]: null }));
  }, [fetchStatus]);

  // ── 渲染单个服务 ──────────────────────────────────
  function renderService(svc) {
    const isContainer = svc.container !== 'no_container';
    const isManaged = svc.manage && svc.manage.method === 'systemd';
    const canControl = isContainer || isManaged;
    const online = svc.online;
    const busy = loading[svc.id];
    const hasPreview = svc.id === 'foundationpose'; // FP 有多物体识别画面
    const showPreview = previewSvc === svc.id;
    const previewUrl = hasPreview ? `/api/fp/video/rgb/stream?t=${previewTs.current}` : null;

    return (
      <div key={svc.id}>
        <div className="flex items-center justify-between py-1.5 border-b border-[#1a1a22] last:border-0">
          {/* 左侧：状态点 + 名称 */}
          <div className="flex items-center gap-2 min-w-0">
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: online ? '#52c41a' : '#ff4d4f' }}
              title={online ? 'online' : 'offline'}
            />
            <div className="min-w-0">
              <div className="text-xs font-medium text-white truncate">
                {svc.label}
              </div>
              <Tag
                color={online ? 'success' : 'error'}
                className="text-[10px] leading-none px-1"
              >
                {online ? 'ON' : 'OFF'}
              </Tag>
            </div>
          </div>

          {/* 右侧：操作按钮 */}
          <Space size={2}>
            {hasPreview && online && (
              <Tooltip title={showPreview ? '隐藏画面' : '查看实时画面'}>
                <Button
                  type="text"
                  size="small"
                  icon={<CameraOutlined />}
                  onClick={() => setPreviewSvc(showPreview ? null : svc.id)}
                  style={{ color: showPreview ? '#1677ff' : '#8a8a8a' }}
                />
              </Tooltip>
            )}
            {canControl && (
              <>
                <Tooltip title="启动">
                  <Button
                    type="text"
                    size="small"
                    icon={<PlayCircleOutlined />}
                    loading={busy === 'start'}
                    onClick={() => action(svc.id, 'start')}
                    disabled={online}
                    style={{ color: online ? '#555' : '#52c41a' }}
                  />
                </Tooltip>
                <Tooltip title="停止">
                  <Button
                    type="text"
                    size="small"
                    icon={<PauseCircleOutlined />}
                    loading={busy === 'stop'}
                    onClick={() => action(svc.id, 'stop')}
                    disabled={!online}
                    style={{ color: online ? '#ff4d4f' : '#555' }}
                  />
                </Tooltip>
                <Tooltip title="重启">
                  <Button
                    type="text"
                    size="small"
                    icon={<SyncOutlined />}
                    loading={busy === 'restart'}
                    onClick={() => action(svc.id, 'restart')}
                    disabled={!online}
                  />
                </Tooltip>
              </>
            )}
          </Space>
        </div>

        {/* 内嵌实时画面 */}
        {showPreview && (
          <div className="mb-1 px-1">
            <img
              src={previewUrl}
              alt={`${svc.label} 实时画面`}
              style={{ width: '100%', borderRadius: 4, background: '#1a1a1a' }}
            />
          </div>
        )}
      </div>
    );
  }

  return (
    <Card
      size="small"
      title={
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold text-white">SERVICES</span>
          <Tooltip title="刷新">
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              onClick={fetchStatus}
            />
          </Tooltip>
        </div>
      }
      className="shrink-0"
      styles={{
        body: { padding: '4px 8px' },
        header: { padding: '6px 10px', minHeight: 'auto' },
      }}
    >
      {/* 概览 */}
      <div className="flex items-center gap-3 mb-2 text-[11px] text-[#8a8a8a]">
        <span>
          <span className="text-[#52c41a] font-bold">{summary.online}</span>
          {' '}在线
        </span>
        <span>
          <span className="text-[#ff4d4f] font-bold">{summary.offline}</span>
          {' '}离线
        </span>
        <span>共 {summary.total}</span>
      </div>

      {/* 服务列表 */}
      {services.map(renderService)}
    </Card>
  );
}
