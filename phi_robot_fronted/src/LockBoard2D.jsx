import { useRef, useState, useCallback } from 'react';

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
const HIT_R = 9;
const NODE_BY_ID = Object.fromEntries(NODES.map((n) => [n.id, n]));

function LockBoard2D({
  path = [],
  isRunning = false,
  isDrawing,
  onDrawStart,
  onDrawEnd,
  onAppendNode,
  className = '',
}) {
  const svgRef = useRef(null);
  const [hoveredId, setHoveredId] = useState(null);
  const [mousePos, setMousePos] = useState(null);

  const lockedSet = new Set(path);
  const latestId = path[path.length - 1] ?? null;

  const svgCoords = useCallback((e) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const r = svg.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 };
  }, []);

  const hitNode = useCallback((pos) => {
    if (!pos) return null;
    for (const n of NODES) {
      if (Math.hypot(n.x - pos.x, n.y - pos.y) < HIT_R) return n.id;
    }
    return null;
  }, []);

  const handleNodeDown = useCallback(
    (nodeId) => {
      if (isRunning) return;
      onDrawStart();
      onAppendNode(nodeId);
    },
    [isRunning, onDrawStart, onAppendNode],
  );

  const handleNodeEnter = useCallback(
    (nodeId) => {
      setHoveredId(nodeId);
      if (!isDrawing) return;
      onAppendNode(nodeId);
    },
    [isDrawing, onAppendNode],
  );

  const handleNodeLeave = useCallback(() => setHoveredId(null), []);

  const drawingRef = useRef(isDrawing);
  drawingRef.current = isDrawing;

  const handleSvgMove = useCallback(
    (e) => {
      const pos = svgCoords(e);
      setMousePos(pos);
      const id = hitNode(pos);
      setHoveredId(id);
      if (drawingRef.current && id) {
        onAppendNode(id);
      }
    },
    [svgCoords, hitNode, onAppendNode],
  );

  const handleSvgUp = useCallback(() => {
    if (!isDrawing) return;
    onDrawEnd();
    setMousePos(null);
  }, [isDrawing, onDrawEnd]);

  const connections = path.slice(1).map((toId, i) => ({
    from: NODE_BY_ID[path[i]],
    to: NODE_BY_ID[toId],
    key: `${path[i]}-${toId}`,
  }));

  const showPreview = isDrawing && latestId && mousePos;
  const previewFrom = latestId ? NODE_BY_ID[latestId] : null;

  /* ── Palette (mirrors index.css :root) ── */
  const C = {
    idleDotFill: 'rgba(8,12,20,0.85)',
    idleDotStroke: 'rgba(64,110,142,0.45)',
    hoverDotFill: 'rgba(0,229,255,0.06)',
    hoverDotStroke: 'rgba(0,229,255,0.55)',
    lockedDotFill: 'rgba(0,229,255,0.12)',
    lockedDotStroke: '#00e5ff',
    idleLabel: 'rgba(255,255,255,0.12)',
    lockedLabel: '#ffffff',
    connectionLine: 'rgba(0,229,255,0.45)',
    previewLine: 'rgba(0,229,255,0.35)',
    gridDot: 'rgba(64,110,142,0.2)',
  };

  return (
    <div
      className={`lock-board${isDrawing ? ' lock-board--drawing' : ''}${isRunning ? ' lock-board--running' : ''}${className ? ` ${className}` : ''}`}
    >
      <svg
        ref={svgRef}
        viewBox="0 0 100 100"
        style={{ width: '100%', height: '100%', display: 'block', touchAction: 'none' }}
        onPointerMove={handleSvgMove}
        onPointerUp={handleSvgUp}
        onPointerLeave={handleSvgUp}
      >
        {/* Subtle grid guide */}
        <g opacity="1">
          {[21, 50, 79].map((cy) =>
            [21, 50, 79].map((cx) => (
              <circle key={`g${cx}-${cy}`} cx={cx} cy={cy} r="0.6" fill={C.gridDot} />
            )),
          )}
        </g>

        {/* Connections */}
        {connections.map(({ from, to, key }) => (
          <line
            key={key}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke={C.connectionLine}
            strokeWidth="2"
            strokeLinecap="round"
          />
        ))}

        {/* Preview line */}
        {showPreview && previewFrom && (
          <line
            x1={previewFrom.x}
            y1={previewFrom.y}
            x2={mousePos.x}
            y2={mousePos.y}
            stroke={C.previewLine}
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray="2 4"
          />
        )}

        {/* Nodes */}
        {NODES.map((n) => {
          const locked = lockedSet.has(n.id);
          const latest = n.id === latestId;
          const hovered = hoveredId === n.id && !locked;

          // Main dot
          const dotR = locked ? NODE_R : hovered ? NODE_R + 1 : NODE_R;
          const dotFill = locked ? C.lockedDotFill : hovered ? C.hoverDotFill : C.idleDotFill;
          const dotStroke = locked ? C.lockedDotStroke : hovered ? C.hoverDotStroke : C.idleDotStroke;
          const dotSw = locked ? 2.5 : hovered ? 2 : 2;

          return (
            <g key={n.id}>
              {/* Hit area (invisible) */}
              <circle
                cx={n.x}
                cy={n.y}
                r={12}
                fill="transparent"
                style={{ cursor: isRunning ? 'default' : 'crosshair' }}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  handleNodeDown(n.id);
                }}
                onPointerEnter={() => handleNodeEnter(n.id)}
                onPointerLeave={handleNodeLeave}
              />
              {/* Main dot */}
              <circle
                cx={n.x}
                cy={n.y}
                r={dotR}
                fill={dotFill}
                stroke={dotStroke}
                strokeWidth={dotSw}
                style={{ pointerEvents: 'none' }}
              />
              {/* Label */}
              <text
                x={n.x}
                y={n.y}
                textAnchor="middle"
                dominantBaseline="central"
                fill={locked ? C.lockedLabel : C.idleLabel}
                fontSize="5.5"
                fontWeight="400"
                fontFamily="Helvetica Neue, Arial, sans-serif"
                style={{ pointerEvents: 'none', userSelect: 'none' }}
              >
                {n.id}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export default LockBoard2D;
