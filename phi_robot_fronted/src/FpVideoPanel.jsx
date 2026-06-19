import { Card } from 'antd';
import { CameraOutlined } from '@ant-design/icons';

export default function FpVideoPanel() {
  return (
    <Card
      title={<span><CameraOutlined className="mr-2" />FoundationPose 实时画面</span>}
      size="small"
      className="w-full"
      styles={{ body: { padding: 8 } }}
    >
      <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between' }}>
        <div style={{ flex: 1, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: '#8a8a8a', marginBottom: 4 }}>RGB + 线框</div>
          <img src="/api/fp/video/rgb" alt="RGB" style={{ width: '100%', borderRadius: 4 }} />
        </div>
        <div style={{ flex: 1, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: '#8a8a8a', marginBottom: 4 }}>深度图</div>
          <img src="/api/fp/video/depth" alt="Depth" style={{ width: '100%', borderRadius: 4 }} />
        </div>
        <div style={{ flex: 1, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: '#8a8a8a', marginBottom: 4 }}>掩码</div>
          <img src="/api/fp/video/mask" alt="Mask" style={{ width: '100%', borderRadius: 4 }} />
        </div>
      </div>
    </Card>
  );
}
