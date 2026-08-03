# Phase 3 semantic world-model implementation status

Status date: 2026-08-03. This is an offline contract slice, not Phase 3 exit.

## Implemented

- Versioned `LocationRef` keeps symbolic identity separate from
  `ResolvedLocation`. The initial resolver executes only named `grid/*`
  locations from `scene_coords.json`, validates scene/version, and records map,
  frame, resolver version, resolution ID, and provenance. The schema also
  represents future region, pose-reference, and object-relative kinds.
- Legacy `ne` and `grid/ne` strings translate to `LocationRef`; raw coordinates
  are never inserted into that semantic reference.
- `ObjectRef` separates stable logical identity from `PerceptionRef` provider,
  object ID, tracker session, and observation identity.
- Full `ObjectCandidateSet` values are retained. `ObjectSelectionStore` issues
  a selection token/epoch and rejects stale observations, wrong IDs, old
  epochs, reused tracker sessions, and poses not observed after selection.
- Object re-identification is an explicit `ObjectLineageRecord` with evidence;
  a new perception ID is never silently treated as the same logical object.
- `VisualRingBuffer` keeps bounded timestamped frames with sequence, media type,
  SHA-256, byte length, and synchronization metadata. It creates atomic
  `ObservationBundle` values linked to World State version and complete object
  candidates. Audit serialization excludes image bytes.
- A stopped camera naturally expires because validity derives from capture
  timestamps. `DropEvent` records detector sequence/health, mission/object
  context, evidence frames, and explicit unknown odometry.

## Offline evidence and remaining gates

Nine contract tests cover resolution, extensible-but-not-executable kinds,
identity/epoch/session failures, lineage, synchronized bundles, stale camera
frames, old sequences, world-version invalidation, and structured drop events.

Remaining work includes live FoundationPose select-by-object-ID service wiring,
persisting selection tokens and lineage in the durable ledger, connecting real
camera channels to the ring buffer, and migrating every newly-created mission
and skill boundary to `LocationRef`/`ObjectRef`. These require external interface
confirmation and later HIL; the current coordinate-based legacy plan remains a
compatibility input only.
