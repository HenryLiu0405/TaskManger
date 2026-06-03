import { Card, Descriptions, Tag } from 'antd';
import { InboxOutlined, CameraOutlined } from '@ant-design/icons';

export default function BoxPanel({ box, holdingBox }) {
  return (
    <Card
      title={<span><InboxOutlined className="mr-2" />箱子状态</span>}
      size="small"
      className="w-full"
    >
      <Descriptions column={1} size="small" colon={false}>
        <Descriptions.Item label={
          <span><CameraOutlined className="mr-1" />相机状态</span>
        }>
          <Tag>{box?.camera_status || '--'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="箱子状态">
          {holdingBox ? (
            <Tag color="warning">持有箱子</Tag>
          ) : (
            <Tag>未持有</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="箱子位置">
          <code className="text-sm">{box?.box_position || '--'}</code>
        </Descriptions.Item>
      </Descriptions>

      <div className="mt-3 pt-3 border-t border-[#2a2a2a] text-sm text-[#8a8a8a]">
        相机数据、箱子位置逻辑后续补齐
      </div>
    </Card>
  );
}
