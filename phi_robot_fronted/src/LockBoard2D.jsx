import { useRef, useState, useCallback, useEffect, memo } from 'react';

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

// Moon greyish-white for unconnected nodes; cyan on hover; mint when locked.
const MOON = '#c7d1e0';
const MOON_SOFT = '#a9b4c8';

/* One grid node. Memoized so a path append only re-renders the couple of
   nodes whose state actually changed (new latest / newly locked / hover),
   not all nine — keeps dragging smooth. Handlers must be stable (they are:
   useCallback in the parent + useCallback'd props from App). */
const LockNode = memo(function LockNode({ node, locked, latest, hovered, isRunning, onDown, onEnter, onLeave }) {
  const ringR = locked || hovered ? 10 : 8;
  const ringStroke = locked ? '#5cd6ee' : hovered ? '#7fe3f2' : MOON_SOFT;
  const ringOpacity = locked ? 0.4 : hovered ? 0.5 : 0.34;
  const ringWidth = locked || hovered ? 1.8 : 1;

  const dotR = hovered && !locked ? NODE_R + 1 : NODE_R;
  const dotFill = locked ? '#5cd6ee' : hovered ? 'rgba(127,227,242,0.22)' : 'rgba(183,195,216,0.12)';
  const dotStroke = locked ? '#5cd6ee' : hovered ? '#7fe3f2' : MOON;
  const dotSw = locked || hovered ? 2.2 : 1.5;

  return (
    <g>
      {/* Hit area (invisible) */}
      <circle
        cx={node.x}
        cy={node.y}
        r={12}
        fill="transparent"
        style={{ cursor: isRunning ? 'default' : 'pointer' }}
        onPointerDown={(e) => {
          e.stopPropagation();
          onDown(node.id);
        }}
        onPointerEnter={() => onEnter(node.id)}
        onPointerLeave={onLeave}
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
        className={latest ? 'lock-board__node--latest' : ''}
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
  isDrawing,
  onDrawStart,
  onDrawEnd,
  onAppendNode,
  className = '',
}) {
  const svgRef = useRef(null);
  const previewRef = useRef(null);   // preview <line>, updated imperatively
  const rectRef = useRef(null);      // cached board rect (read once per drag)
  const rafRef = useRef(0);          // pending animation frame
  const lastPtRef = useRef(null);    // latest pointer position to process
  const [hoveredId, setHoveredId] = useState(null);

  const lockedSet = new Set(path);
  const latestId = path[path.length - 1] ?? null;

  const drawingRef = useRef(isDrawing);
  drawingRef.current = isDrawing;

  const readRect = useCallback(() => {
    const svg = svgRef.current;
    rectRef.current = svg ? svg.getBoundingClientRect() : null;
    return rectRef.current;
  }, []);

  const hitNodeXY = useCallback((x, y) => {
    for (const n of NODES) {
      if (Math.hypot(n.x - x, n.y - y) < HIT_R) return n.id;
    }
    return null;
  }, []);

  // Runs at most once per frame (rAF-coalesced). The cursor-tracking preview
  // line is updated via DOM attributes so moving the pointer triggers NO React
  // re-render — only crossing into a new node (setHoveredId / append) does.
  const processMove = useCallback(
    (clientX, clientY) => {
      const r = rectRef.current || readRect();
      if (!r || r.width === 0) return;
      const x = ((clientX - r.left) / r.width) * 100;
      const y = ((clientY - r.top) / r.height) * 100;
      const id = hitNodeXY(x, y);
      setHoveredId(id); // React bails out when the value is unchanged
      if (drawingRef.current) {
        const line = previewRef.current;
        if (line) {
          line.setAttribute('x2', x);
          line.setAttribute('y2', y);
        }
        if (id) onAppendNode(id);
      }
    },
    [readRect, hitNodeXY, onAppendNode],
  );

  const handleSvgMove = useCallback(
    (e) => {
      lastPtRef.current = { x: e.clientX, y: e.clientY };
      if (rafRef.current) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        const pt = lastPtRef.current;
        if (pt) processMove(pt.x, pt.y);
      });
    },
    [processMove],
  );

  const endDraw = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    if (drawingRef.current) onDrawEnd();
  }, [onDrawEnd]);

  useEffect(() => () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  }, []);

  const handleNodeDown = useCallback(
    (nodeId) => {
      if (isRunning) return;
      readRect(); // cache rect once at draw start — avoids per-move layout reads
      onDrawStart();
      onAppendNode(nodeId);
    },
    [isRunning, readRect, onDrawStart, onAppendNode],
  );

  const handleNodeEnter = useCallback(
    (nodeId) => {
      setHoveredId(nodeId);
      if (!drawingRef.current) return;
      onAppendNode(nodeId);
    },
    [onAppendNode],
  );

  const handleNodeLeave = useCallback(() => setHoveredId(null), []);

  const connections = path.slice(1).map((toId, i) => ({
    from: NODE_BY_ID[path[i]],
    to: NODE_BY_ID[toId],
    key: `${path[i]}-${toId}`,
  }));

  const previewFrom = latestId ? NODE_BY_ID[latestId] : null;

  return (
    <div
      className={`lock-board${isDrawing ? ' lock-board--drawing' : ''}${isRunning ? ' lock-board--running' : ''}${className ? ` ${className}` : ''}`}
    >
      <svg
        ref={svgRef}
        viewBox="0 0 100 100"
        style={{ width: '100%', height: '100%', display: 'block', touchAction: 'none' }}
        onPointerMove={handleSvgMove}
        onPointerUp={endDraw}
        onPointerLeave={endDraw}
      >
        <defs>
          {/* userSpaceOnUse so axis-aligned (horizontal/vertical) lines,
              whose bounding box is zero-area, still get a valid gradient */}
          <linearGradient id="lock-line" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="100" y2="100">
            <stop offset="0%" stopColor="#5cd6ee" />
            <stop offset="100%" stopColor="#5cd6ee" />
          </linearGradient>
        </defs>

        {/* Subtle grid guide */}
        <g opacity="0.18">
          {[21, 50, 79].map((cy) =>
            [21, 50, 79].map((cx) => (
              <circle key={`g${cx}-${cy}`} cx={cx} cy={cy} r="0.9" fill="#7fb3d6" />
            )),
          )}
        </g>

        {/* Connections — one CSS drop-shadow on the group replaces the
            per-line SVG gaussian-blur glow */}
        <g className="lock-board__wires">
          {connections.map(({ from, to, key }) => (
            <line
              key={key}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke="url(#lock-line)"
              strokeWidth="1.6"
              strokeLinecap="round"
              opacity="0.95"
            />
          ))}
        </g>

        {/* Preview line — x2/y2 are updated imperatively on pointer move
            (see processMove) so tracking the cursor causes no React re-render */}
        {isDrawing && previewFrom && (
          <line
            ref={previewRef}
            x1={previewFrom.x}
            y1={previewFrom.y}
            x2={previewFrom.x}
            y2={previewFrom.y}
            stroke="#7fe3f2"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeDasharray="2 3"
            opacity="0.5"
          />
        )}

        {/* Nodes — memoized; only the changed ones re-render on append */}
        {NODES.map((n) => (
          <LockNode
            key={n.id}
            node={n}
            locked={lockedSet.has(n.id)}
            latest={n.id === latestId}
            hovered={hoveredId === n.id && !lockedSet.has(n.id)}
            isRunning={isRunning}
            onDown={handleNodeDown}
            onEnter={handleNodeEnter}
            onLeave={handleNodeLeave}
          />
        ))}
      </svg>
    </div>
  );
}

export default LockBoard2D;
