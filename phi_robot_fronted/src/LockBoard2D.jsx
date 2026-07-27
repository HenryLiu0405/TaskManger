import { useCallback, memo } from 'react';

const NODES = [
  { id: '1', x: 21, y: 21 },
  { id: '2', x: 50, y: 21 },
  { id: '3', x: 79, y: 21 },
  { id: '4', x: 21, y: 50 },
  { id: '5', x: 50, y: 50 },
  { id: '6', x: 79, y: 50 },
  { id: '7', x: 21, y: 79 },
  { id: '8', x: 50, y: 79 },
  { id: '9', x: 79, y: 79 },
];

const NODE_R = 6;

// Moon greyish-white for unconnected nodes; cyan on hover; mint when locked.
const MOON = '#c7d1e0';

/* One grid node — click to select, no drag/hover behaviour. */
const LockNode = memo(function LockNode({ node, locked, latest, isRunning, onClick }) {
  const ringR = locked ? 10 : 8;
  const ringStroke = locked ? '#5cd6ee' : MOON;
  const ringOpacity = locked ? 0.4 : 0.25;
  const ringWidth = locked ? 1.8 : 1;

  const dotR = locked ? NODE_R : NODE_R;
  const dotFill = locked ? '#5cd6ee' : 'rgba(183,195,216,0.12)';
  const dotStroke = locked ? '#5cd6ee' : MOON;
  const dotSw = locked ? 2.2 : 1.5;

  return (
    <g>
      {/* Hit area */}
      <circle
        cx={node.x}
        cy={node.y}
        r={12}
        fill="transparent"
        style={{ cursor: isRunning ? 'default' : 'pointer' }}
        onClick={() => { if (!isRunning) onClick(node.id); }}
      />
      {/* Glow ring */}
      <circle
        cx={node.x}
        cy={node.y}
        r={ringR}
        fill="none"
        stroke={ringStroke}
        strokeWidth={ringWidth}
        opacity={ringOpacity}
        style={{ pointerEvents: 'none' }}
      />
      {/* Main dot */}
      <circle
        cx={node.x}
        cy={node.y}
        r={dotR}
        fill={dotFill}
        stroke={dotStroke}
        strokeWidth={dotSw}
        className={locked ? 'lock-board__node--locked' : undefined}
        style={{ pointerEvents: 'none' }}
      />
      {/* Label */}
      <text
        x={node.x}
        y={node.y}
        textAnchor="middle"
        dominantBaseline="central"
        fill={locked ? '#04121a' : '#e4eaf4'}
        fontSize="5.5"
        fontWeight="700"
        fontFamily="'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
        style={{ pointerEvents: 'none', userSelect: 'none' }}
      >
        {node.id}
      </text>
    </g>
  );
});

function LockBoard2D({
  path = [],
  isRunning = false,
  onAppendNode,
  className = '',
}) {
  const lockedSet = new Set(path);
  const latestId = path[path.length - 1] ?? null;

  const handleClick = useCallback(
    (nodeId) => {
      if (isRunning) return;
      onAppendNode(nodeId);
    },
    [isRunning, onAppendNode],
  );

  return (
    <div
      className={`lock-board${isRunning ? ' lock-board--running' : ''}${className ? ` ${className}` : ''}`}
    >
      <svg
        viewBox="0 0 100 100"
        style={{ width: '100%', height: '100%', display: 'block' }}
      >
        {/* Subtle grid guide */}
        <g opacity="0.18">
          {[21, 50, 79].map((cy) =>
            [21, 50, 79].map((cx) => (
              <circle key={`g${cx}-${cy}`} cx={cx} cy={cy} r="0.9" fill="#7fb3d6" />
            )),
          )}
        </g>

        {/* Nodes — click to select */}
        {NODES.map((n) => (
          <LockNode
            key={n.id}
            node={n}
            locked={lockedSet.has(n.id)}
            latest={n.id === latestId}
            isRunning={isRunning}
            onClick={handleClick}
          />
        ))}
      </svg>
    </div>
  );
}

export default LockBoard2D;
