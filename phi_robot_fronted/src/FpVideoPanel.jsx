import { Card } from 'antd';
import { CameraOutlined } from '@ant-design/icons';

const CHANNELS = [
  { key: 'rgb', label: 'RGB + 线框' },
  { key: 'depth', label: '深度图' },
  { key: 'mask', label: '掩码' },
];

const STYLE_IMG = {
  width: '100%',
  borderRadius: 4,
  minHeight: 120,
  background: '#1a1a1a',
};

export default function FpVideoPanel() {
  return (
    <Card
      title={<span><CameraOutlined className="mr-2" />FoundationPose 实时画面</span>}
      size="small"
      className="w-full"
      styles={{ body: { padding: 8 } }}
    >
      <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between' }}>
        {CHANNELS.map(ch => (
          <div key={ch.key} style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ fontSize: 12, color: '#8a8a8a', marginBottom: 4 }}>
              {ch.label}
            </div>
            <img
              src={`/api/fp/video/${ch.key}/stream`}
              alt={ch.label}
              style={STYLE_IMG}
            />
          </div>
        ))}
      </div>
    </Card>
  );
}
