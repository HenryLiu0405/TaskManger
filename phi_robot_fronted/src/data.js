export const SITE_ZONES = [
  {
    id: 'nw',
    label: '西北宫格',
    shortLabel: '西北',
    note: '靠近北侧仓门',
    row: 0,
    col: 0,
    color: '#f59e0b',
  },
  {
    id: 'n',
    label: '北宫格',
    shortLabel: '北',
    note: '主通道北端',
    row: 0,
    col: 1,
    color: '#06b6d4',
  },
  {
    id: 'ne',
    label: '东北宫格',
    shortLabel: '东北',
    note: '靠近东侧月台',
    row: 0,
    col: 2,
    color: '#3b82f6',
  },
  {
    id: 'sw',
    label: '西南宫格',
    shortLabel: '西南',
    note: '西侧卸货口',
    row: 1,
    col: 0,
    color: '#10b981',
  },
  {
    id: 's',
    label: '南宫格',
    shortLabel: '南',
    note: '中央装卸区',
    row: 1,
    col: 1,
    color: '#ef4444',
  },
  {
    id: 'se',
    label: '东南宫格',
    shortLabel: '东南',
    note: '东侧出库口',
    row: 1,
    col: 2,
    color: '#8b5cf6',
  },
];

export const SITE_ZONE_MAP = SITE_ZONES.reduce((accumulator, zone) => {
  accumulator[zone.id] = zone;
  return accumulator;
}, {});

export const INITIAL_TASKS = [
  {
    id: 'box-a',
    label: '箱子 A',
    priority: 1,
    suggestedZoneId: 'nw',
    destinationId: null,
    color: '#f97316',
    note: '轻量件',
  },
  {
    id: 'box-b',
    label: '箱子 B',
    priority: 2,
    suggestedZoneId: 'n',
    destinationId: null,
    color: '#06b6d4',
    note: '易碎品',
  },
  {
    id: 'box-c',
    label: '箱子 C',
    priority: 3,
    suggestedZoneId: 'ne',
    destinationId: null,
    color: '#3b82f6',
    note: '常规货',
  },
  {
    id: 'box-d',
    label: '箱子 D',
    priority: 4,
    suggestedZoneId: 'sw',
    destinationId: null,
    color: '#10b981',
    note: '重货',
  },
  {
    id: 'box-e',
    label: '箱子 E',
    priority: 5,
    suggestedZoneId: 's',
    destinationId: null,
    color: '#ef4444',
    note: '急件',
  },
  {
    id: 'box-f',
    label: '箱子 F',
    priority: 6,
    suggestedZoneId: 'se',
    destinationId: null,
    color: '#8b5cf6',
    note: '备用件',
  },
];

export const DEFAULT_SITE_GOAL = '将 6 个箱子拖拽到对应方位宫格，按顺序提交并执行。';
