import React, { useEffect, useMemo, useState, useCallback, useRef } from "react";
import {
  ReactFlow,
  Controls,
  Background,
  BackgroundVariant,
  BaseEdge,
  Handle,
  Position,
  useReactFlow,
  useNodesInitialized,
  ReactFlowProvider,
  type Node,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  getProjectGroups,
  indexProjectComponents,
  learningTree,
  projectTree,
  projectUsesPhases,
  PROJECT_NODE_PREFIX,
  type Project,
  type Phase,
  type Component,
  type LearningPath,
  type Topic,
  type Subtopic,
  type TreeItem as MapItem,
  type TreeGroup as MapGroup,
} from "@workbench-core";
import { KindBadge, PhaseNumberBadge, StatusNodeGlyph, statusColorVar, statusTint } from "./status";

// The map lays its phases out as columns, as many per row as the panel's width holds at a readable
// zoom, so it uses the width it has before it grows downwards. It never grows past MAX_FIT_ZOOM;
// if it is taller than the panel it starts at the top and scrolls vertically. Below
// MIN_READABLE_ZOOM it stops shrinking and the user pans instead.
const MIN_READABLE_ZOOM = 0.85;
const MAX_FIT_ZOOM = 1.05;
const FIT_PADDING = 24;
// Space kept clear at the top for the floating toolbar (Expand all / Collapse all / Fit to view)
const TOP_CLEARANCE = 48;

const EDGE_IDLE = "color-mix(in srgb, var(--text-muted) 38%, transparent)";

/** "+N" when folded, "−" when open. Clicking it folds/unfolds without selecting the node. */
function FoldBadge({
  count,
  expanded,
  onToggle,
  testId,
  label,
}: {
  count: number;
  expanded: boolean;
  onToggle?: () => void;
  testId: string;
  label: string;
}) {
  return (
    <button
      type="button"
      className="nodrag"
      data-testid={testId}
      aria-label={expanded ? `Hide the parts of ${label}` : `Show ${count} parts of ${label}`}
      title={expanded ? "Fold" : "Unfold"}
      onClick={(e) => {
        e.stopPropagation();
        onToggle?.();
      }}
      style={{
        flexShrink: 0,
        minWidth: 22,
        height: 18,
        padding: "0 5px",
        borderRadius: 5,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 11,
        fontWeight: 600,
        cursor: "pointer",
        color: expanded ? "var(--text-muted)" : "var(--accent-bright)",
        background: expanded
          ? "var(--bg-elevated)"
          : "color-mix(in srgb, var(--accent) 14%, transparent)",
        border: "1px solid var(--border)",
      }}
    >
      {expanded ? "−" : `+${count}`}
    </button>
  );
}
const HANDLE_BORDER = "2px solid var(--bg-panel)";
const HIDDEN_HANDLE = {
  width: 1,
  height: 1,
  minWidth: 0,
  minHeight: 0,
  opacity: 0,
  border: "none",
};

/** SVG path through the points with right-angle corners rounded by `radius`. */
function roundedPath(points: [number, number][], radius: number): string {
  const pts = points.filter(
    (p, i) => i === 0 || p[0] !== points[i - 1][0] || p[1] !== points[i - 1][1],
  );
  if (pts.length < 2) return "";
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [px, py] = pts[i - 1];
    const [x, y] = pts[i];
    const [nx, ny] = pts[i + 1];
    const inLen = Math.hypot(x - px, y - py);
    const outLen = Math.hypot(nx - x, ny - y);
    const r = Math.min(radius, inLen / 2, outLen / 2);
    d += ` L ${x - ((x - px) / inLen) * r} ${y - ((y - py) / inLen) * r}`;
    d += ` Q ${x} ${y} ${x + ((nx - x) / outLen) * r} ${y + ((ny - y) / outLen) * r}`;
  }
  const last = pts[pts.length - 1];
  return `${d} L ${last[0]} ${last[1]}`;
}

/** Parent to part: straight down from the parent's bottom edge, then right into the part. */
function ElbowEdge({ sourceX, sourceY, targetX, targetY, style }: EdgeProps) {
  const path = roundedPath(
    [
      [sourceX, sourceY],
      [sourceX, targetY],
      [targetX, targetY],
    ],
    6,
  );
  return <BaseEdge path={path} style={style} />;
}

/**
 * Root to phase. Phases in the first row hang off one horizontal line under the root; phases in
 * wrapped rows are reached down a gutter left of the columns, so no connector crosses a column.
 */
function BusEdge({ sourceX, sourceY, targetX, targetY, style, data }: EdgeProps) {
  const { busY, gutterX, rowBusY } = (data ?? {}) as {
    busY?: number;
    gutterX?: number;
    rowBusY?: number;
  };
  const firstBus = busY ?? (sourceY + targetY) / 2;
  const points: [number, number][] =
    gutterX === undefined || rowBusY === undefined
      ? [
          [sourceX, sourceY],
          [sourceX, firstBus],
          [targetX, firstBus],
          [targetX, targetY],
        ]
      : [
          [sourceX, sourceY],
          [sourceX, firstBus],
          [gutterX, firstBus],
          [gutterX, rowBusY],
          [targetX, rowBusY],
          [targetX, targetY],
        ];
  return <BaseEdge path={roundedPath(points, 8)} style={style} />;
}

const edgeTypes = { elbow: ElbowEdge, bus: BusEdge };

function RootCardNode({ data }: { data: { label: string; isLearning?: boolean } }) {
  return (
    <div
      data-testid="visual-root-node"
      title={data.label}
      style={{
        width: ROOT_W,
        height: ROOT_H,
        background: "var(--bg-card)",
        border: "1.5px solid var(--accent)",
        boxShadow: "0 4px 18px color-mix(in srgb, var(--accent) 18%, transparent)",
        borderRadius: 10,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 4,
        color: "var(--text-primary)",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--accent)",
        }}
      >
        {data.isLearning ? "Learning path" : "Project"}
      </div>
      <div
        style={{
          fontSize: 13.5,
          fontWeight: 650,
          lineHeight: 1.3,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {data.label}
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{
          background: "var(--accent)",
          border: HANDLE_BORDER,
          width: 8,
          height: 8,
          bottom: -4,
        }}
      />
    </div>
  );
}

function PhaseGroupNode({
  data,
}: {
  data: {
    label: string;
    status: string;
    phaseId: string;
    index: number;
    numbered: boolean;
    selected: boolean;
    childCount: number;
    expanded: boolean;
    onToggle?: () => void;
  };
}) {
  const { status, selected } = data;
  return (
    <div
      data-testid={`visual-phase-${data.phaseId}`}
      title={data.label}
      style={{
        width: PHASE_W,
        height: PHASE_H,
        background: selected ? "var(--bg-selected)" : "var(--bg-card)",
        border: `1.5px solid ${selected ? "var(--accent)" : statusTint(status, 55)}`,
        boxShadow: selected ? "0 4px 16px rgba(0, 0, 0, 0.45)" : "0 2px 6px rgba(0, 0, 0, 0.25)",
        borderRadius: 9,
        padding: "0 11px",
        display: "flex",
        alignItems: "center",
        gap: 9,
        boxSizing: "border-box",
        cursor: "pointer",
        transition: "background-color 140ms ease, border-color 140ms ease",
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{
          background: statusColorVar(status),
          border: HANDLE_BORDER,
          width: 8,
          height: 8,
          top: -4,
        }}
      />
      {data.numbered ? (
        <PhaseNumberBadge index={data.index} status={status} />
      ) : (
        <StatusNodeGlyph status={status} size={16} />
      )}
      <span
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--text-primary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          flex: 1,
        }}
      >
        {data.label}
      </span>
      {data.childCount > 0 && (
        <FoldBadge
          count={data.childCount}
          expanded={data.expanded}
          onToggle={data.onToggle}
          testId={`visual-phase-toggle-${data.phaseId}`}
          label={data.label}
        />
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ ...HIDDEN_HANDLE, left: ELBOW_X, bottom: 0 }}
      />
    </div>
  );
}

function ComponentCardNode({
  data,
}: {
  data: {
    label: string;
    status: string;
    selected: boolean;
    compId: string;
    childCount: number;
    expanded: boolean;
    onToggle?: () => void;
  };
}) {
  return (
    <div
      data-testid={`visual-node-${data.compId}`}
      title={
        data.childCount > 0
          ? `${data.label} — use the badge to ${data.expanded ? "fold" : "unfold"} its parts`
          : data.label
      }
      style={{
        width: COMP_W,
        height: 34,
        background: data.selected ? "var(--bg-selected)" : "var(--bg-panel-alt)",
        border: data.selected ? "1.5px solid var(--accent)" : "1px solid var(--border)",
        boxShadow: data.selected ? "0 3px 12px rgba(0, 0, 0, 0.4)" : "0 1px 4px rgba(0, 0, 0, 0.2)",
        borderRadius: 8,
        padding: "0 11px",
        display: "flex",
        alignItems: "center",
        gap: 9,
        color: data.selected ? "var(--text-primary)" : "var(--text-secondary)",
        fontSize: 12.5,
        fontWeight: data.selected ? 600 : 500,
        boxSizing: "border-box",
        cursor: "pointer",
        transition: "background-color 140ms ease, border-color 140ms ease",
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{
          background: statusColorVar(data.status),
          border: HANDLE_BORDER,
          width: 7,
          height: 7,
          left: -3.5,
        }}
      />
      <StatusNodeGlyph status={data.status} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
        {data.label}
      </span>
      {data.childCount > 0 && (
        <FoldBadge
          count={data.childCount}
          expanded={data.expanded}
          onToggle={data.onToggle}
          testId={`visual-node-toggle-${data.compId}`}
          label={data.label}
        />
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ ...HIDDEN_HANDLE, left: ELBOW_X, bottom: 0 }}
      />
    </div>
  );
}

/** A child project sitting under a phase or component: its own build, opened on click. */
function ChildProjectNode({
  data,
}: {
  data: {
    label: string;
    status: string;
    projectId: string;
    childCount: number;
    expanded: boolean;
    onOpen?: () => void;
    onToggle?: () => void;
  };
}) {
  return (
    <div
      data-testid={`visual-child-project-${data.projectId}`}
      title={`${data.label} — its own project; click to ${data.expanded ? "hide" : "show"} it here, or use the arrow to open it`}
      style={{
        width: COMP_W,
        height: 34,
        background: "color-mix(in srgb, var(--accent) 8%, var(--bg-panel-alt))",
        border: "1px dashed color-mix(in srgb, var(--accent) 60%, transparent)",
        borderRadius: 8,
        padding: "0 9px",
        display: "flex",
        alignItems: "center",
        gap: 8,
        color: "var(--text-primary)",
        fontSize: 12.5,
        fontWeight: 600,
        boxSizing: "border-box",
        cursor: "pointer",
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{
          background: "var(--accent)",
          border: HANDLE_BORDER,
          width: 7,
          height: 7,
          left: -3.5,
        }}
      />
      <KindBadge isLearning={false} status={data.status} size={20} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
        {data.label}
      </span>
      <StatusNodeGlyph status={data.status} />
      {data.childCount > 0 && (
        <FoldBadge
          count={data.childCount}
          expanded={data.expanded}
          onToggle={data.onToggle}
          testId={`visual-child-project-toggle-${data.projectId}`}
          label={data.label}
        />
      )}
      <button
        type="button"
        className="nodrag"
        data-testid={`visual-child-project-open-${data.projectId}`}
        aria-label={`Open ${data.label}`}
        title={`Open ${data.label}`}
        onClick={(e) => {
          e.stopPropagation();
          data.onOpen?.();
        }}
        style={{
          display: "inline-flex",
          padding: 2,
          background: "none",
          border: "none",
          cursor: "pointer",
        }}
      >
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M7 17L17 7M8 7h9v9" />
        </svg>
      </button>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ ...HIDDEN_HANDLE, left: ELBOW_X, bottom: 0 }}
      />
    </div>
  );
}

const nodeTypes = {
  childProject: ChildProjectNode,
  rootNode: RootCardNode,
  phaseGroup: PhaseGroupNode,
  componentNode: ComponentCardNode,
};

interface VisualGraphProps {
  project?: Project;
  learningPath?: LearningPath;
  selectedId: string | null;
  selectedPhaseId?: string | null;
  onSelectPhase?: (phase: Phase) => void;
  onSelectComponent?: (comp: Component, phase: Phase) => void;
  onSelectLearningItem?: (item: Topic | Subtopic, parentTopic?: Topic) => void;
  /** Child projects of `project`, keyed by the phase or component id they sit under */
  childProjects?: Map<string, Project[]>;
  /** Open a child project, optionally focused on one of its phases or components */
  onOpenProject?: (projectId: string, itemId?: string) => void;
}

// Layout: the root sits on top, each phase (or topic) is a column with its parts listed under it
// as an indented outline, and the columns wrap into as many rows as the panel's width needs.
const ROW_H = 40; // one part: a 34px card plus the gap to the next
const PHASE_H = 42;
const PHASE_W = 258;
const COMP_W = 236;
const INDENT = 22; // each nested level moves this far right
const ROOT_W = 200;
const ROOT_H = 64;
const COL_GAP = 30;
const ROW_GAP = 40; // between wrapped rows of phases; the connector to the next row runs in it
const BUS_DROP = 20; // how far above a row of phases its horizontal connector runs
const PHASE_TO_PARTS = 8;
const ELBOW_X = 14; // where a parent's connector leaves its bottom edge, from its left side

/** True when an item has parts and every part (and everything under it) is done. */
function isComplete(item: MapItem): boolean {
  const kids = item.children ?? [];
  return (
    kids.length > 0 &&
    kids.every((k) => k.status === "done" && (!k.children?.length || isComplete(k)))
  );
}

/** Toggle keys of every foldable item: groups (as "phase:<id>") and anything with children. */
function foldableKeys(groups: MapGroup[]): { key: string; isProject: boolean }[] {
  const out: { key: string; isProject: boolean }[] = [];
  const walk = (items: MapItem[]) => {
    for (const item of items) {
      if (item.children?.length) {
        out.push({
          key: item.id,
          isProject: Boolean(item.isProject) || item.id.startsWith(PROJECT_NODE_PREFIX),
        });
        walk(item.children);
      }
    }
  };
  for (const g of groups) {
    if (g.children?.length) out.push({ key: `phase:${g.id}`, isProject: false });
    walk(g.children ?? []);
  }
  return out;
}

/** Ids of the item and all its ancestors, if the item is somewhere in these groups. */
function pathTo(groups: MapGroup[], id: string | null): Set<string> {
  const found = new Set<string>();
  if (!id) return found;
  const walk = (items: MapItem[], trail: string[]): boolean => {
    for (const item of items) {
      if (item.id === id) {
        for (const x of [...trail, item.id]) found.add(x);
        return true;
      }
      if (item.children && walk(item.children, [...trail, item.id])) return true;
    }
    return false;
  };
  walk(groups, []);
  return found;
}

interface LayoutBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

const NODE_SIZE: Record<string, { w: number; h: number }> = {
  rootNode: { w: ROOT_W, h: ROOT_H },
  phaseGroup: { w: PHASE_W, h: PHASE_H },
  componentNode: { w: COMP_W, h: 34 },
  childProject: { w: COMP_W, h: 34 },
};

/** The box around a layout. Node sizes are fixed, so this needs no measuring. */
function boundsOf(nodes: Node[]): LayoutBounds | null {
  if (nodes.length === 0) return null;
  let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const node of nodes) {
    const size = NODE_SIZE[node.type ?? ""] ?? NODE_SIZE.componentNode;
    minX = Math.min(minX, node.position.x);
    minY = Math.min(minY, node.position.y);
    maxX = Math.max(maxX, node.position.x + size.w);
    maxY = Math.max(maxY, node.position.y + size.h);
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

/** Deepest nesting below the groups (0 = groups only have direct parts), whether open or not. */
function maxNesting(groups: MapGroup[]): number {
  const depthOf = (items: MapItem[] | undefined): number =>
    items?.length ? 1 + Math.max(...items.map((i) => depthOf(i.children))) : 0;
  return Math.max(0, ...groups.map((g) => depthOf(g.children) - 1));
}

/**
 * How many phase columns fit side by side in a panel `panelWidth` px wide without shrinking below
 * the readable zoom. The column width allows for the deepest nesting, so folding and unfolding
 * never changes the number of columns.
 */
function columnsFor(
  panelWidth: number,
  groupCount: number,
  nesting: number,
): { cols: number; colW: number } {
  const colW = PHASE_W + INDENT * nesting;
  const usable = (panelWidth - 2 * FIT_PADDING) / MIN_READABLE_ZOOM;
  const fit = Math.floor((usable + COL_GAP) / (colW + COL_GAP));
  return { cols: Math.max(1, Math.min(groupCount, fit)), colW };
}

/**
 * Root on top, phases as columns (wrapping to `cols` per row), parts as an indented outline under
 * each phase. Every top-level part shows one nested level; deeper levels open when toggled (or
 * when they hold the selection).
 */
function buildTreeNodesAndEdges(
  rootTitle: string,
  isLearning: boolean,
  numbered: boolean,
  groups: MapGroup[],
  selectedId: string | null,
  selectedPhaseId: string | null | undefined,
  toggled: Record<string, boolean>,
  onToggle: (key: string, open: boolean) => void,
  panelWidth: number,
  onOpenProject?: (projectId: string, itemId?: string) => void,
): { nodes: Node[]; edges: Edge[]; openIds: Set<string> } {
  const rfNodes: Node[] = [];
  const rfEdges: Edge[] = [];
  const openIds = new Set<string>();
  const selectedPath = pathTo(groups, selectedId);
  const { cols, colW } = columnsFor(panelWidth, groups.length, maxNesting(groups));

  const isOpen = (item: MapItem, depth: number) => {
    if (!item.children?.length) return false;
    // Ancestors of the selection stay open so the selected node is always on the map
    if (selectedPath.has(item.id) && item.id !== selectedId) return true;
    if (item.id in toggled) return toggled[item.id];
    if (item.isProject) return false; // child projects start collapsed; click to unfold them here
    if (isComplete(item)) return false; // finished work starts folded
    return item.defaultOpen ?? depth === 0;
  };

  const edgeStyle = (highlighted: boolean, strong: boolean) => ({
    stroke: highlighted ? "var(--accent)" : EDGE_IDLE,
    strokeWidth: strong ? 2 : 1.5,
  });

  /** Places an item at row `topY` of column `colX` and its open parts below it; returns the rows used. */
  const place = (
    item: MapItem,
    depth: number,
    topY: number,
    colX: number,
    sourceId: string,
    phaseHighlighted: boolean,
  ): number => {
    const kids = item.children || [];
    const open = isOpen(item, depth);
    if (open) openIds.add(item.id);
    const position = { x: colX + INDENT * (depth + 1), y: topY };

    if (item.isProject) {
      const projectId = item.id.slice(PROJECT_NODE_PREFIX.length);
      rfNodes.push({
        id: item.id,
        type: "childProject",
        position,
        data: {
          label: item.name,
          status: item.status,
          projectId,
          childCount: kids.length,
          expanded: open,
          onOpen: () => onOpenProject?.(projectId),
          onToggle: () => onToggle(item.id, !open),
        },
      });
      rfEdges.push({
        id: `e:${sourceId}->${item.id}`,
        source: sourceId,
        target: item.id,
        type: "elbow",
        style: { stroke: "var(--accent)", strokeWidth: 1.5, strokeDasharray: "5 4" },
      });
    } else {
      const isSelected = selectedId === item.id;
      rfNodes.push({
        id: item.id,
        type: "componentNode",
        position,
        data: {
          label: item.name,
          status: item.status,
          selected: isSelected,
          compId: item.id,
          childCount: kids.length,
          expanded: open,
          onToggle: () => onToggle(item.id, !open),
        },
      });
      rfEdges.push({
        id: `e:${sourceId}->${item.id}`,
        source: sourceId,
        target: item.id,
        type: "elbow",
        style: edgeStyle(isSelected || selectedPath.has(item.id) || phaseHighlighted, isSelected),
      });
    }

    let rows = 1;
    if (open) {
      for (const kid of kids)
        rows += place(kid, depth + 1, topY + rows * ROW_H, colX, item.id, false);
    }
    return rows;
  };

  const gridW = cols * colW + (cols - 1) * COL_GAP;
  const rootX = (gridW - ROOT_W) / 2;
  const firstBusY = ROOT_H + BUS_DROP;
  // Connectors to wrapped rows run down a gutter left of the first column
  const gutterX = -COL_GAP / 2;

  let rowTop = ROOT_H + 2 * BUS_DROP;
  for (let start = 0; start < groups.length; start += cols) {
    const rowIndex = start / cols;
    let rowHeight = PHASE_H;

    groups.slice(start, start + cols).forEach((grp, col) => {
      const idx = start + col;
      const kids = grp.children || [];
      const colX = col * (colW + COL_GAP);
      const isPhaseSelected =
        selectedId === grp.id || selectedPhaseId === grp.id || selectedPath.has(grp.id);
      const pNodeId = `phase:${grp.id}`;
      // Groups fold like components: open unless finished, but always open around the selection
      const groupOpen =
        kids.length > 0 &&
        ((selectedPath.has(grp.id) && selectedId !== grp.id) ||
          (toggled[pNodeId] ?? !isComplete(grp)));
      if (groupOpen) openIds.add(pNodeId);

      let rows = 0;
      if (groupOpen) {
        const partsTop = rowTop + PHASE_H + PHASE_TO_PARTS;
        for (const kid of kids)
          rows += place(kid, 0, partsTop + rows * ROW_H, colX, pNodeId, isPhaseSelected);
      }
      const blockHeight = PHASE_H + (rows > 0 ? PHASE_TO_PARTS + rows * ROW_H - (ROW_H - 34) : 0);
      rowHeight = Math.max(rowHeight, blockHeight);

      rfNodes.push({
        id: pNodeId,
        type: "phaseGroup",
        position: { x: colX, y: rowTop },
        data: {
          label: grp.name,
          status: grp.status,
          phaseId: grp.id,
          index: idx,
          numbered,
          selected: isPhaseSelected,
          childCount: kids.length,
          expanded: groupOpen,
          onToggle: () => onToggle(pNodeId, !groupOpen),
        },
      });
      rfEdges.push({
        id: `e:root->${pNodeId}`,
        source: "root:node",
        target: pNodeId,
        type: "bus",
        // Selected connectors draw above the idle ones they share segments with
        zIndex: isPhaseSelected ? 1 : 0,
        data:
          rowIndex === 0
            ? { busY: firstBusY }
            : { busY: firstBusY, gutterX, rowBusY: rowTop - BUS_DROP },
        style: edgeStyle(isPhaseSelected, isPhaseSelected),
      });
    });

    rowTop += rowHeight + ROW_GAP;
  }

  rfNodes.unshift({
    id: "root:node",
    type: "rootNode",
    position: { x: rootX, y: 0 },
    data: { label: rootTitle, isLearning },
  });

  return { nodes: rfNodes, edges: rfEdges, openIds };
}

function VisualGraphInner({
  project,
  learningPath,
  selectedId,
  selectedPhaseId,
  onSelectPhase,
  onSelectComponent,
  onSelectLearningItem,
  childProjects,
  onOpenProject,
}: VisualGraphProps) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const { getNodes, getInternalNode, getNodesBounds, getViewport, setViewport } = useReactFlow();
  const nodesInitialized = useNodesInitialized();
  // Set when the next layout should be fitted to the panel: a new project, a new panel width,
  // expand all / collapse all. Folding one node keeps the view where it is.
  const fitNextLayout = useRef(true);
  const layoutBounds = useRef<LayoutBounds | null>(null);
  // The panel's width decides how many phase columns fit per row
  const [panelWidth, setPanelWidth] = useState(0);
  // Nested levels the user opened or closed by clicking a node (reset per project)
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const openIdsRef = useRef<Set<string>>(new Set());

  const targetKey = project?.id || learningPath?.id || "";

  const groups: MapGroup[] = useMemo(
    () =>
      project
        ? projectTree(project, childProjects)
        : learningPath
          ? learningTree(learningPath)
          : [],
    [project, learningPath, childProjects],
  );

  const setFold = useCallback((key: string, open: boolean) => {
    setToggled((prev) => ({ ...prev, [key]: open }));
  }, []);

  useEffect(() => {
    if ((!project && !learningPath) || panelWidth === 0) return;
    const built = buildTreeNodesAndEdges(
      project ? project.name : learningPath!.name,
      !project,
      project ? projectUsesPhases(project) : false,
      groups,
      selectedId,
      selectedPhaseId,
      toggled,
      setFold,
      panelWidth,
      project ? onOpenProject : undefined,
    );
    openIdsRef.current = built.openIds;
    layoutBounds.current = boundsOf(built.nodes);
    setNodes(built.nodes);
    setEdges(built.edges);
  }, [
    project,
    learningPath,
    groups,
    selectedId,
    selectedPhaseId,
    toggled,
    setFold,
    panelWidth,
    onOpenProject,
  ]);

  /**
   * Fit the map to the panel, but never below a readable zoom: past that, anchor the left edge and
   * pan. The bounds come from the layout itself (every node has a fixed size), not from what React
   * Flow has measured, so a fit can never use the positions of the layout before this one.
   */
  const fitReadable = useCallback(() => {
    const el = wrapperRef.current;
    const bounds = layoutBounds.current;
    // Nothing to fit without a layout, or while the panel is hidden (a tab on small screens)
    if (!el || !bounds || el.clientWidth === 0 || el.clientHeight === 0) return;

    const w = el.clientWidth;
    const h = el.clientHeight;
    // Fit the width: the layout already chose how many columns this width holds
    const widthZoom = (w - 2 * FIT_PADDING) / bounds.width;
    const zoom = Math.min(Math.max(widthZoom, MIN_READABLE_ZOOM), MAX_FIT_ZOOM);
    const contentW = bounds.width * zoom;
    const contentH = bounds.height * zoom;
    const x =
      contentW <= w - 2 * FIT_PADDING
        ? (w - contentW) / 2 - bounds.x * zoom
        : FIT_PADDING - bounds.x * zoom;
    const usableH = h - TOP_CLEARANCE - FIT_PADDING;
    // A map shorter than the panel sits a little above the middle; a taller one starts at the top
    const y =
      contentH <= usableH
        ? TOP_CLEARANCE + (usableH - contentH) / 3 - bounds.y * zoom
        : TOP_CLEARANCE - bounds.y * zoom;
    setViewport({ x, y, zoom }, { duration: 160 });
  }, [setViewport]);

  // A different project or learning path starts with its default folds and a fresh fit
  // biome-ignore lint/correctness/useExhaustiveDependencies: targetKey is the trigger, not a value read inside
  useEffect(() => {
    fitNextLayout.current = true;
    setToggled({});
  }, [targetKey]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a new layout (nodes) is the trigger
  useEffect(() => {
    if (fitNextLayout.current && nodesInitialized && nodes.length > 0) {
      fitNextLayout.current = false;
      fitReadable();
    }
  }, [nodesInitialized, nodes, fitReadable]);

  /** After a child project unfolds, pan so it and its parts are on screen (once they are measured). */
  const panToSubtree = useCallback(
    (id: string, tries = 0) => {
      requestAnimationFrame(() => {
        const el = wrapperRef.current;
        if (!el) return;
        const subtree = getNodes().filter((n) => n.id === id || n.id.startsWith(`${id}::`));
        // Sizes live on React Flow's internal nodes (this map doesn't feed measurements back into state)
        const measured =
          subtree.length > 1 &&
          subtree.every((n) => {
            const m = getInternalNode(n.id)?.measured;
            return Boolean(m?.width && m?.height);
          });
        if (!measured) {
          if (tries < 30) panToSubtree(id, tries + 1);
          return;
        }
        const b = getNodesBounds(subtree);
        const { zoom } = getViewport();
        const h = el.clientHeight;
        const contentH = b.height * zoom;
        setViewport(
          {
            x: FIT_PADDING - b.x * zoom,
            y:
              contentH <= h - TOP_CLEARANCE - FIT_PADDING
                ? TOP_CLEARANCE + (h - TOP_CLEARANCE - FIT_PADDING - contentH) / 2 - b.y * zoom
                : TOP_CLEARANCE - b.y * zoom,
            zoom,
          },
          { duration: 220 },
        );
      });
    },
    [getNodes, getInternalNode, getNodesBounds, getViewport, setViewport],
  );

  const foldable = useMemo(() => foldableKeys(groups), [groups]);

  /** Expand all opens every phase/topic and nested part (child projects stay as they are); collapse all folds everything. */
  const setAllFolds = useCallback(
    (open: boolean) => {
      setToggled((prev) => {
        const next = { ...prev };
        for (const { key, isProject } of foldable) {
          if (open && isProject) continue;
          next[key] = open;
        }
        return next;
      });
      fitNextLayout.current = true;
    },
    [foldable],
  );

  // Lay out again and refit when the panel is resized (dividers, window, sidebar, phone rotation)
  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    setPanelWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    const observer = new ResizeObserver(() => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        setPanelWidth((previous) => {
          if (previous === el.clientWidth) {
            fitReadable(); // only the height changed: same layout, new fit
            return previous;
          }
          fitNextLayout.current = true;
          return el.clientWidth;
        });
      }, 90);
    });
    observer.observe(el);
    return () => {
      if (debounceTimer) clearTimeout(debounceTimer);
      observer.disconnect();
    };
  }, [fitReadable]);

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (node.id === "root:node") return;
      if (node.id.startsWith(PROJECT_NODE_PREFIX)) {
        // A child project, or an item inside one: nodes with children unfold here; leaves open the child there
        const hasKids = Number((node.data as { childCount?: number }).childCount ?? 0) > 0;
        if (hasKids) {
          if (!openIdsRef.current.has(node.id)) {
            setFold(node.id, true);
            if (node.type === "childProject") panToSubtree(node.id);
          }
          return;
        }
        if (node.type === "childProject") return; // an empty child project opens via its arrow
        const [projectId, itemId] = node.id.slice(PROJECT_NODE_PREFIX.length).split("::");
        onOpenProject?.(projectId, itemId);
        return;
      }

      if (project) {
        const rawGroups = getProjectGroups(project);
        if (node.id.startsWith("phase:")) {
          const pid = node.id.slice("phase:".length);
          const ph = rawGroups.find((p) => p.id === pid);
          if (!openIdsRef.current.has(node.id)) setFold(node.id, true);
          if (ph && onSelectPhase) onSelectPhase(ph);
          return;
        }
        const entry = indexProjectComponents(project).get(node.id);
        if (!entry) return;
        // Selecting a folded node opens it; folding is done with its badge
        if (entry.comp.components?.length && !openIdsRef.current.has(node.id))
          setFold(node.id, true);
        if (onSelectComponent) onSelectComponent(entry.comp, entry.phase);
      } else if (learningPath && onSelectLearningItem) {
        if (node.id.startsWith("phase:")) {
          const tid = node.id.slice("phase:".length);
          const top = (learningPath.topics || []).find((t) => t.id === tid);
          if (!openIdsRef.current.has(node.id)) setFold(node.id, true);
          if (top) onSelectLearningItem(top, top);
          return;
        }
        for (const top of learningPath.topics || []) {
          const sub = (top.subtopics || []).find((s) => s.id === node.id);
          if (sub) {
            onSelectLearningItem(sub, top);
            return;
          }
        }
      }
    },
    [
      project,
      learningPath,
      onSelectPhase,
      onSelectComponent,
      onSelectLearningItem,
      onOpenProject,
      panToSubtree,
      setFold,
    ],
  );

  return (
    <div ref={wrapperRef} className="visual-graph-wrapper" data-testid="visual-graph-canvas">
      <div className="visual-toolbar">
        {foldable.length > 0 && (
          <>
            <button
              type="button"
              className="canvas-tool-btn"
              onClick={() => setAllFolds(true)}
              data-testid="map-expand-all"
              title="Open every phase and nested part (child projects stay folded)"
            >
              Expand all
            </button>
            <button
              type="button"
              className="canvas-tool-btn"
              onClick={() => setAllFolds(false)}
              data-testid="map-collapse-all"
              title="Fold every phase and nested part"
            >
              Collapse all
            </button>
          </>
        )}
        <button
          type="button"
          className="canvas-tool-btn"
          onClick={fitReadable}
          data-testid="fit-view-button"
        >
          Fit to view
        </button>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={handleNodeClick}
        // Wheel/trackpad scrolls the map (it may be taller than the panel); pinch or the controls zoom
        panOnScroll
        zoomOnScroll={false}
        zoomOnPinch
        minZoom={0.3}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="var(--border)" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}

export function VisualGraph(props: VisualGraphProps) {
  return (
    <ReactFlowProvider>
      <VisualGraphInner {...props} />
    </ReactFlowProvider>
  );
}
