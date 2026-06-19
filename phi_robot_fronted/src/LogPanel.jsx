import { useRef, useEffect, useState } from 'react';
import { Button, Card, Segmented, Modal, Tooltip, message } from 'antd';
import { DownloadOutlined, DeleteOutlined, FileTextOutlined, CopyOutlined } from '@ant-design/icons';

const LEVEL_COLORS = {
  ok: '#52c41a',
  error: '#ff4d4f',
  warn: '#faad14',
  info: '#1677ff',
};

const LEVELS = ['all', 'ok', 'error', 'warn', 'info'];

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function LogPanel({ logs = [], onClear }) {
  const bottomRef = useRef(null);
  const [filter, setFilter] = useState('all');
  const [clearOpen, setClearOpen] = useState(false);
  const [auditInfo, setAuditInfo] = useState(null);

  const filtered = filter === 'all'
    ? logs
    : logs.filter((e) => e.level === filter);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [filtered.length]);

  // 加载审计日志文件信息
  useEffect(() => {
    fetch('/api/dev/audit/today')
      .then((res) => res.json())
      .then((data) => setAuditInfo(data))
      .catch(() => {});
  }, []);

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

  function handleDownloadAudit() {
    fetch('/api/dev/audit/download')
      .then((res) => {
        if (!res.ok) throw new Error('no file');
        return res.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `audit_${new Date().toISOString().slice(0, 10)}.jsonl`;
        a.click();
        URL.revokeObjectURL(url);
      })
      .catch(() => message.warning('无审计日志文件'));
  }

  function handleCopyPath() {
    if (auditInfo?.path) {
      navigator.clipboard.writeText(auditInfo.path).then(
        () => message.success('路径已复制'),
        () => message.error('复制失败'),
      );
    }
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
        {/* 审计日志信息栏 */}
        {auditInfo && auditInfo.date && (
          <div className="flex items-center gap-3 mb-2 px-2 py-1 rounded text-xs"
               style={{ background: '#1a1a24', border: '1px solid #2a2a2a' }}>
            <FileTextOutlined className="text-[#8a8a8a]" />
            <Tooltip title={auditInfo.path}>
              <span className="text-[#8a8a8a] cursor-pointer hover:text-[#d4a853] truncate max-w-[200px]"
                    onClick={handleCopyPath}>
                audit_{auditInfo.date}.jsonl
              </span>
            </Tooltip>
            <span className="text-[#6a6a6a]">{formatSize(auditInfo.size_bytes || 0)}</span>
            <span className="text-[#6a6a6a]">{auditInfo.line_count || 0} 条</span>
            <div className="flex-1" />
            <Tooltip title="复制文件路径">
              <Button size="small" type="text" icon={<CopyOutlined />} onClick={handleCopyPath} />
            </Tooltip>
            <Tooltip title="下载审计日志">
              <Button size="small" type="text" icon={<DownloadOutlined />} onClick={handleDownloadAudit} />
            </Tooltip>
          </div>
        )}

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
