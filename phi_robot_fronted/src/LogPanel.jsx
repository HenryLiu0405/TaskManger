import { useRef, useEffect, useState } from 'react';
import { Button, Card, Segmented, Modal } from 'antd';
import { DownloadOutlined, DeleteOutlined } from '@ant-design/icons';

const LEVEL_COLORS = {
  ok: '#52c41a',
  error: '#ff4d4f',
  warn: '#faad14',
  info: '#1677ff',
};

const LEVELS = ['all', 'ok', 'error', 'warn', 'info'];

export default function LogPanel({ logs = [], onClear }) {
  const bottomRef = useRef(null);
  const [filter, setFilter] = useState('all');
  const [clearOpen, setClearOpen] = useState(false);

  const filtered = filter === 'all'
    ? logs
    : logs.filter((e) => e.level === filter);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [filtered.length]);

  function handleExport() {
    fetch('/api/dev/logs/export')
      .then((res) => res.blob())
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `robot_logs_${new Date().toISOString().slice(0, 19).replace(/:/g, '')}.json`;
        a.click();
        URL.revokeObjectURL(url);
      })
      .catch(console.error);
  }

  function handleClear() {
    setClearOpen(true);
  }

  function handleClearOk() {
    onClear();
    setClearOpen(false);
  }

  return (
    <>
      <Modal
        title="确认清空"
        open={clearOpen}
        onOk={handleClearOk}
        onCancel={() => setClearOpen(false)}
        okText="确认清空"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        centered
      >
        将清空全部操作日志，此操作不可撤销。
      </Modal>

      <Card
        title="操作日志"
        size="small"
        extra={
          <div className="flex items-center gap-2">
            <Segmented
              size="small"
              value={filter}
              onChange={setFilter}
              options={LEVELS.map((l) => ({
                label: l === 'all' ? '全部' : l,
                value: l,
              }))}
            />
            <Button size="small" icon={<DownloadOutlined />} onClick={handleExport}>
              导出
            </Button>
            <Button size="small" danger icon={<DeleteOutlined />} onClick={handleClear}>
              清空
            </Button>
          </div>
        }
        className="flex-1 min-h-0 flex flex-col"
        styles={{ body: { flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' } }}
      >
        <div className="flex-1 overflow-y-auto font-mono text-sm leading-relaxed min-h-0">
          {filtered.length === 0 && (
            <div className="text-[#8a8a8a]">
              {logs.length === 0 ? '等待操作...' : `无 ${filter} 级别日志`}
            </div>
          )}
          {filtered.map((entry, i) => (
            <div key={i} className="flex gap-3 py-0.5 border-b border-[#2a2a2a] last:border-0">
              <span className="text-[#8a8a8a] shrink-0" style={{ width: 72 }}>{entry.timestamp}</span>
              <span
                className="shrink-0 font-semibold"
                style={{ color: LEVEL_COLORS[entry.level] || '#888', width: 65 }}
              >
                [{entry.level}]
              </span>
              <span className="shrink-0 text-[#d4a853]" style={{ width: 88 }}>{entry.action}</span>
              <span className="text-[#c8c8c8] truncate">{entry.detail}</span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </Card>
    </>
  );
}
