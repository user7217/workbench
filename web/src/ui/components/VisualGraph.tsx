import type React from "react";
import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import {
  ReactFlow,
  Controls,
  Background,
  BackgroundVariant,
  BaseEdge,
  Handle,
  Position,
  useReactFlow,
  ReactFlowProvider,
  type Node,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  ITEM_STATUSES,
  LEARNING_ITEM_STATUSES,
  pendingKeys,
  useWorkbench,
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
import { type MapLayout, useLook } from "../lookState";
import { KindBadge, PhaseNumberBadge, StatusNodeGlyph, statusColorVar, statusTint } from "./status";
import { StatusMenu } from "./StatusMenu";

/** Whose status a map node changes: a phase/component of a project or a learning topic/subtopic. */
interface MapStatusTarget {
  kind: "project" | "learning";
  /** Project or learning path id (the child project's id for items inside a child project) */
  ownerId: string;
  itemId: string;
}

/**
 * A node's status glyph as a status menu, wired to the same actions as the lists. `nodrag nopan`
 * keeps React Flow from starting a drag or a pan on it; its clicks stop before the node's own.
 */
function MapStatus({
  target,
  status,
  label,
  size = 14,
  glyph,
}: {
  target: MapStatusTarget;
  status: string;
  label: string;
  size?: number;
  glyph?: React.ReactNode;
}) {
  const wb = useWorkbench();
  const { kind, ownerId, itemId } = target;
  const testId = `visual-status-menu-${itemId}`;
  if (kind === "learning") {
    return (
      <StatusMenu
        status={status}
        options={LEARNING_ITEM_STATUSES}
        busy={wb.isPending(pendingKeys.learningItem(ownerId, itemId))}
        testId={testId}
        subject={label}
        size={size}
        glyph={glyph}
        className="nodrag nopan"
        onSelect={(st) => wb.setLearningItemStatus(ownerId, itemId, st)}
      />
    );
  }
  return (
    <StatusMenu
      status={status}
      options={ITEM_STATUSES}
      busy={wb.isPending(pendingKeys.projectItem(ownerId, itemId))}
      testId={testId}
      subject={label}
      size={size}
      glyph={glyph}
      className="nodrag nopan"
      onSelect={(st) => wb.setItemStatus(ownerId, itemId, st)}
    />
  );
}

// The map has two layouts (a look preference, see lookState). Vertical: the phases are columns, as
// many per row as the panel's width holds at a readable zoom, so it uses the width it has before it
// grows downwards. Horizontal: the project on the left, its phases in a column, and each nested
// level of parts one more column to the right. Either way it never grows past MAX_FIT_ZOOM;
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

/**
 * Where a node's outgoing lines leave it. Vertical: an invisible anchor on the bottom edge, near the
 * left side, that the elbow lines drop from. Horizontal: a dot on the right edge (shown only while
 * the node has open parts), the lines curving right into the next column.
 */
function SourceHandle({
  horizontal,
  visible,
  color,
  size = 7,
}: {
  horizontal?: boolean;
  visible: boolean;
  color: string;
  size?: number;
}) {
  if (!horizontal)
    return (
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ ...HIDDEN_HANDLE, left: ELBOW_X, bottom: 0 }}
      />
    );
  return (
    <Handle
      type="source"
      position={Position.Right}
      style={
        visible
          ? {
              background: color,
              border: HANDLE_BORDER,
              width: size,
              height: size,
              right: -size / 2,
            }
          : { ...HIDDEN_HANDLE, right: 0 }
      }
    />
  );
}

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
 * Project to phase. Each column has its own rail: it drops from the bottom of the project header
 * down the gap left of that column and branches right into every phase stacked in it. The rail
 * stays outside the phase cards and apart from the phase-to-part lines, so a phase further down
 * a column never looks like part of the phase above it.
 */
function RailEdge({ targetX, targetY, style, data }: EdgeProps) {
  const { railX, fromY } = (data ?? {}) as { railX: number; fromY: number };
  const path = roundedPath(
    [
      [railX, fromY],
      [railX, targetY],
      [targetX, targetY],
    ],
    8,
  );
  return <BaseEdge path={path} style={style} />;
}

const edgeTypes = { elbow: ElbowEdge, rail: RailEdge };

/**
 * The project (or learning path) as a header across the top of the map, as wide as the phase
 * columns below it (and the rails beside them). A rail per column links it to its phases.
 */
function RootCardNode({
  data,
}: {
  data: { label: string; isLearning?: boolean; width?: number; horizontal?: boolean };
}) {
  return (
    <div
      data-testid="visual-root-node"
      title={data.label}
      style={{
        width: data.width ?? ROOT_MIN_W,
        height: ROOT_H,
        background: "color-mix(in srgb, var(--accent) 7%, var(--bg-card))",
        border: "1px solid color-mix(in srgb, var(--accent) 45%, transparent)",
        borderRadius: 10,
        padding: "0 16px",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 2,
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
      {data.horizontal ? (
        <SourceHandle horizontal visible color="var(--accent)" size={8} />
      ) : (
        // The rails draw their own path from the header's bottom edge; this anchors them
        <Handle type="source" position={Position.Bottom} style={HIDDEN_HANDLE} />
      )}
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
    horizontal?: boolean;
    statusTarget?: MapStatusTarget;
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
        position={Position.Left}
        style={
          data.horizontal
            ? {
                background: statusColorVar(status),
                border: HANDLE_BORDER,
                width: 8,
                height: 8,
                left: -4,
              }
            : { ...HIDDEN_HANDLE, left: 0 }
        }
      />
      {data.statusTarget ? (
        <MapStatus
          target={data.statusTarget}
          status={status}
          label={data.label}
          size={16}
          glyph={
            data.numbered ? <PhaseNumberBadge index={data.index} status={status} /> : undefined
          }
        />
      ) : data.numbered ? (
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
      <SourceHandle
        horizontal={data.horizontal}
        visible={data.childCount > 0 && data.expanded}
        color={statusColorVar(status)}
        size={8}
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
    horizontal?: boolean;
    statusTarget?: MapStatusTarget;
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
      {data.statusTarget ? (
        <MapStatus target={data.statusTarget} status={data.status} label={data.label} />
      ) : (
        <StatusNodeGlyph status={data.status} />
      )}
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
      <SourceHandle
        horizontal={data.horizontal}
        visible={data.childCount > 0 && data.expanded}
        color={statusColorVar(data.status)}
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
    horizontal?: boolean;
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
      <SourceHandle
        horizontal={data.horizontal}
        visible={data.childCount > 0 && data.expanded}
        color="var(--accent)"
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

// Layout: the project is a header across the top, linked to its phases by one rail per column
// (see RailEdge). Below it the phases (or topics) are dealt into
// as many columns as the panel's width holds, in reading order (1, 2, 3 across, then 4 under 1),
// and each phase lists its parts under it as an indented outline. A column stacks its own phases,
// so a folded phase leaves no hole next to a long one.
const ROW_H = 40; // one part: a 34px card plus the gap to the next
const PHASE_H = 42;
const PHASE_W = 258;
const COMP_W = 236;
const INDENT = 22; // each nested level moves this far right
const ROOT_MIN_W = 240;
const ROOT_H = 56;
const COL_GAP = 30;
const ROOT_TO_PHASES = 22;
const RAIL_OFFSET = COL_GAP / 2; // a column's rail runs this far left of the column
const PHASE_GAP = 30; // between two phases stacked in one column
const PHASE_TO_PARTS = 8;
const ELBOW_X = 14; // where a parent's connector leaves its bottom edge, from its left side

// Horizontal layout: the project on the left, then a column of phases, then one column per nested
// level of parts, with lines flowing left to right. Node sizes are the same as in the vertical one.
const H_ROW_H = 42; // one part: a 34px card plus the gap to the next
const H_GROUP_GAP = 22; // between the blocks of two phases
const H_ROOT_W = 200;
const H_COL_GAP = 56; // between two columns, room for the curved lines
const H_X_PHASE = H_ROOT_W + H_COL_GAP;
const H_X_COMP = H_X_PHASE + PHASE_W + H_COL_GAP;
const H_COMP_COL_W = COMP_W + H_COL_GAP; // each nested level is one more column right

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
  rootNode: { w: ROOT_MIN_W, h: ROOT_H },
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
    const w = (node.data as { width?: number }).width ?? size.w;
    minX = Math.min(minX, node.position.x);
    minY = Math.min(minY, node.position.y);
    maxX = Math.max(maxX, node.position.x + w);
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

const EDGE_STYLE = (highlighted: boolean, strong: boolean) => ({
  stroke: highlighted ? "var(--accent)" : EDGE_IDLE,
  strokeWidth: strong ? 2 : 1.5,
});

const CHILD_PROJECT_EDGE_STYLE = {
  stroke: "var(--accent)",
  strokeWidth: 1.5,
  strokeDasharray: "5 4",
};

interface MapInput {
  rootTitle: string;
  isLearning: boolean;
  /** The project or learning path the map shows, for status changes */
  ownerId: string;
  numbered: boolean;
  groups: MapGroup[];
  selectedId: string | null;
  selectedPhaseId: string | null | undefined;
  toggled: Record<string, boolean>;
  onToggle: (key: string, open: boolean) => void;
  panelWidth: number;
  onOpenProject?: (projectId: string, itemId?: string) => void;
}

interface MapResult {
  nodes: Node[];
  edges: Edge[];
  openIds: Set<string>;
}

/**
 * What both layouts share: the fold rules, the selection, and how a phase or part becomes a node
 * and the line into it. A layout only decides where each node goes and how its lines are routed.
 */
function mapBuilder(input: MapInput, layout: MapLayout) {
  const { groups, selectedId, selectedPhaseId, toggled, onToggle, onOpenProject } = input;
  const horizontal = layout === "horizontal";
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const openIds = new Set<string>();
  const selectedPath = pathTo(groups, selectedId);
  const partEdgeType = horizontal ? "default" : "elbow";

  /** Whose status an item's node changes; items inside a child project belong to that project. */
  const statusTarget = (item: MapItem): MapStatusTarget =>
    item.foreign
      ? { kind: "project", ownerId: item.foreign.projectId, itemId: item.foreign.itemId }
      : {
          kind: input.isLearning ? "learning" : "project",
          ownerId: input.ownerId,
          itemId: item.id,
        };

  const isOpen = (item: MapItem, depth: number) => {
    if (!item.children?.length) return false;
    // Ancestors of the selection stay open so the selected node is always on the map
    if (selectedPath.has(item.id) && item.id !== selectedId) return true;
    if (item.id in toggled) return toggled[item.id];
    if (item.isProject) return false; // child projects start collapsed; click to unfold them here
    if (isComplete(item)) return false; // finished work starts folded
    return item.defaultOpen ?? depth === 0;
  };

  /** Fold state of a phase (or topic) node; records it in openIds when open. */
  const openGroup = (grp: MapGroup) => {
    const pNodeId = `phase:${grp.id}`;
    // Groups fold like components: open unless finished, but always open around the selection
    const open =
      (grp.children?.length ?? 0) > 0 &&
      ((selectedPath.has(grp.id) && selectedId !== grp.id) ||
        (toggled[pNodeId] ?? !isComplete(grp)));
    if (open) openIds.add(pNodeId);
    return open;
  };

  /** Fold state of a part; records it in openIds when open. */
  const openItem = (item: MapItem, depth: number) => {
    const open = isOpen(item, depth);
    if (open) openIds.add(item.id);
    return open;
  };

  const isPhaseSelected = (grp: MapGroup) =>
    selectedId === grp.id || selectedPhaseId === grp.id || selectedPath.has(grp.id);

  /** A part (or child project) node at `position`, and the line into it from `sourceId`. */
  const addItem = (
    item: MapItem,
    open: boolean,
    position: { x: number; y: number },
    sourceId: string,
    phaseHighlighted: boolean,
  ) => {
    const kids = item.children || [];
    if (item.isProject) {
      const projectId = item.id.slice(PROJECT_NODE_PREFIX.length);
      nodes.push({
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
          horizontal,
        },
      });
      edges.push({
        id: `e:${sourceId}->${item.id}`,
        source: sourceId,
        target: item.id,
        type: partEdgeType,
        style: CHILD_PROJECT_EDGE_STYLE,
      });
      return;
    }
    const isSelected = selectedId === item.id;
    nodes.push({
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
        horizontal,
        statusTarget: statusTarget(item),
      },
    });
    edges.push({
      id: `e:${sourceId}->${item.id}`,
      source: sourceId,
      target: item.id,
      type: partEdgeType,
      style: EDGE_STYLE(isSelected || selectedPath.has(item.id) || phaseHighlighted, isSelected),
    });
  };

  /** A phase (or topic) node at `position`, and the line into it from the root. */
  const addGroup = (
    grp: MapGroup,
    idx: number,
    open: boolean,
    position: { x: number; y: number },
    rootEdge: Pick<Edge, "type" | "data" | "zIndex">,
  ) => {
    const pNodeId = `phase:${grp.id}`;
    const selected = isPhaseSelected(grp);
    nodes.push({
      id: pNodeId,
      type: "phaseGroup",
      position,
      data: {
        label: grp.name,
        status: grp.status,
        phaseId: grp.id,
        index: idx,
        numbered: input.numbered,
        selected,
        childCount: (grp.children || []).length,
        expanded: open,
        onToggle: () => onToggle(pNodeId, !open),
        horizontal,
        statusTarget: statusTarget(grp),
      },
    });
    edges.push({
      id: `e:root->${pNodeId}`,
      source: "root:node",
      target: pNodeId,
      ...rootEdge,
      style: EDGE_STYLE(selected, selected),
    });
  };

  /** The project (or learning path) node; it goes first, under everything else. */
  const addRoot = (position: { x: number; y: number }, width: number) => {
    nodes.unshift({
      id: "root:node",
      type: "rootNode",
      position,
      data: { label: input.rootTitle, isLearning: input.isLearning, width, horizontal },
    });
  };

  const result = (): MapResult => ({ nodes, edges, openIds });

  return { openGroup, openItem, isPhaseSelected, addItem, addGroup, addRoot, result };
}

/**
 * Vertical: the project as a header, phases dealt into columns (as many as `panelWidth` holds) in
 * reading order, parts as an indented outline under each phase. Every top-level part shows one
 * nested level; deeper levels open when toggled (or when they hold the selection).
 */
function buildVerticalMap(input: MapInput): MapResult {
  const { groups, panelWidth } = input;
  const map = mapBuilder(input, "vertical");
  const { cols, colW } = columnsFor(panelWidth, groups.length, maxNesting(groups));

  /** Places an item at row `topY` of column `colX` and its open parts below it; returns the rows used. */
  const place = (
    item: MapItem,
    depth: number,
    topY: number,
    colX: number,
    sourceId: string,
    phaseHighlighted: boolean,
  ): number => {
    const open = map.openItem(item, depth);
    map.addItem(
      item,
      open,
      { x: colX + INDENT * (depth + 1), y: topY },
      sourceId,
      phaseHighlighted,
    );
    let rows = 1;
    if (open) {
      for (const kid of item.children || [])
        rows += place(kid, depth + 1, topY + rows * ROW_H, colX, item.id, false);
    }
    return rows;
  };

  const gridW = cols * colW + (cols - 1) * COL_GAP;
  // Where the next phase of each column starts
  const columnTop = Array.from({ length: cols }, () => ROOT_H + ROOT_TO_PHASES);

  groups.forEach((grp, idx) => {
    const col = idx % cols;
    const top = columnTop[col];
    const colX = col * (colW + COL_GAP);
    const isPhaseSelected = map.isPhaseSelected(grp);
    const pNodeId = `phase:${grp.id}`;
    const groupOpen = map.openGroup(grp);

    let rows = 0;
    if (groupOpen) {
      const partsTop = top + PHASE_H + PHASE_TO_PARTS;
      for (const kid of grp.children || [])
        rows += place(kid, 0, partsTop + rows * ROW_H, colX, pNodeId, isPhaseSelected);
    }
    const blockHeight = PHASE_H + (rows > 0 ? PHASE_TO_PARTS + rows * ROW_H - (ROW_H - 34) : 0);
    columnTop[col] = top + blockHeight + PHASE_GAP;

    map.addGroup(
      grp,
      idx,
      groupOpen,
      { x: colX, y: top },
      {
        type: "rail",
        data: { railX: colX - RAIL_OFFSET, fromY: ROOT_H },
        // The rails of one column overlap; the selected phase's branch is drawn on top
        zIndex: isPhaseSelected ? 1 : 0,
      },
    );
  });

  // Starts left of the first column's rail, so every rail hangs from the header
  map.addRoot({ x: -2 * RAIL_OFFSET, y: 0 }, Math.max(gridW + 2 * RAIL_OFFSET, ROOT_MIN_W));
  return map.result();
}

/**
 * Horizontal: the project on the left, its phases in a column, each phase's parts in the column to
 * its right and every nested level one more column right, each parent centred on its open parts.
 */
function buildHorizontalMap(input: MapInput): MapResult {
  const map = mapBuilder(input, "horizontal");

  /** Lays out an item and its open descendants from `topY`; returns how many rows it takes. */
  const place = (
    item: MapItem,
    depth: number,
    topY: number,
    sourceId: string,
    phaseHighlighted: boolean,
  ): number => {
    const open = map.openItem(item, depth);
    let rows = 1;
    if (open) {
      let used = 0;
      for (const kid of item.children || [])
        used += place(kid, depth + 1, topY + used * H_ROW_H, item.id, false);
      rows = Math.max(1, used);
    }
    const position = { x: H_X_COMP + depth * H_COMP_COL_W, y: topY + ((rows - 1) * H_ROW_H) / 2 };
    map.addItem(item, open, position, sourceId, phaseHighlighted);
    return rows;
  };

  let currentY = 0;
  const phaseCenters: number[] = [];

  input.groups.forEach((grp, idx) => {
    const isPhaseSelected = map.isPhaseSelected(grp);
    const pNodeId = `phase:${grp.id}`;
    const groupOpen = map.openGroup(grp);

    let rows = 0;
    if (groupOpen) {
      for (const kid of grp.children || [])
        rows += place(kid, 0, currentY + rows * H_ROW_H, pNodeId, isPhaseSelected);
    }
    const blockHeight = Math.max(rows * H_ROW_H - (H_ROW_H - 34), PHASE_H);
    const phaseY = currentY + blockHeight / 2 - PHASE_H / 2;
    phaseCenters.push(phaseY + PHASE_H / 2);

    map.addGroup(grp, idx, groupOpen, { x: H_X_PHASE, y: phaseY }, { type: "default" });
    currentY += blockHeight + H_GROUP_GAP;
  });

  const rootCenterY =
    phaseCenters.length > 0 ? (phaseCenters[0] + phaseCenters[phaseCenters.length - 1]) / 2 : 0;
  map.addRoot({ x: 0, y: rootCenterY - ROOT_H / 2 }, H_ROOT_W);
  return map.result();
}

function buildMap(layout: MapLayout, input: MapInput): MapResult {
  return layout === "horizontal" ? buildHorizontalMap(input) : buildVerticalMap(input);
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
  const { mapLayout, setMapLayout } = useLook();

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
    const built = buildMap(mapLayout, {
      rootTitle: project ? project.name : learningPath!.name,
      isLearning: !project,
      ownerId: project ? project.id : learningPath!.id,
      numbered: project ? projectUsesPhases(project) : false,
      groups,
      selectedId,
      selectedPhaseId,
      toggled,
      onToggle: setFold,
      panelWidth,
      onOpenProject: project ? onOpenProject : undefined,
    });
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
    mapLayout,
  ]);

  /**
   * Fit the map to the panel, but never below a readable zoom: past that, anchor the left edge and
   * pan. The bounds come from the layout itself (every node has a fixed size), not from what React
   * Flow has measured, so a fit can never use the positions of the layout before this one.
   */
  const fitReadable = useCallback(
    (animate = false): boolean => {
      const el = wrapperRef.current;
      const bounds = layoutBounds.current;
      // Nothing to fit without a layout, or while the panel is hidden (a tab on small screens);
      // the caller keeps the fit pending and tries again when the panel is shown
      if (!el || !bounds || el.clientWidth === 0 || el.clientHeight === 0) return false;

      const w = el.clientWidth;
      const h = el.clientHeight;
      // Fit the width: the vertical layout already chose how many columns this width holds
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
      // A map that just appeared is placed at once; a refit the user asked for glides
      setViewport({ x, y, zoom }, { duration: animate ? 160 : 0 });
      return true;
    },
    [setViewport],
  );

  // A different project or learning path starts with its default folds and a fresh fit
  // biome-ignore lint/correctness/useExhaustiveDependencies: targetKey is the trigger, not a value read inside
  useEffect(() => {
    fitNextLayout.current = true;
    setToggled({});
  }, [targetKey]);

  // Switching between the vertical and horizontal layouts (here or from a settings menu) refits
  // biome-ignore lint/correctness/useExhaustiveDependencies: mapLayout is the trigger, not a value read inside
  useEffect(() => {
    fitNextLayout.current = true;
  }, [mapLayout]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a new layout (nodes) is the trigger
  useEffect(() => {
    // The fit uses the layout's own bounds (node sizes are fixed), so it does not wait for React
    // Flow to measure the nodes
    if (fitNextLayout.current && nodes.length > 0 && fitReadable()) fitNextLayout.current = false;
  }, [nodes, fitReadable]);

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
    if (el.clientWidth > 0) setPanelWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    const observer = new ResizeObserver(() => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        const width = el.clientWidth;
        if (width === 0) return; // hidden: keep the layout for this width until it is shown again
        setPanelWidth((previous) => {
          if (previous === width) {
            // Only the height changed (or the panel was shown again): same layout, new fit
            if (fitReadable()) fitNextLayout.current = false;
            return previous;
          }
          fitNextLayout.current = true;
          return width;
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
          onClick={() => fitReadable(true)}
          data-testid="fit-view-button"
        >
          Fit to view
        </button>
        <MapLayoutButton layout={mapLayout} onChange={setMapLayout} />
      </div>
      {/* React Flow sets up its zoom when it mounts and cannot do that in a hidden panel (a tab on
          small screens), so it mounts once the panel has a width and then stays mounted */}
      {panelWidth > 0 && (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          // A fit asked for before React Flow was ready happens as soon as it is
          onInit={() => {
            if (fitNextLayout.current && fitReadable()) fitNextLayout.current = false;
          }}
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
      )}
    </div>
  );
}

/** Switches the map between its vertical and horizontal layouts; shows the layout it switches to. */
function MapLayoutButton({
  layout,
  onChange,
}: {
  layout: MapLayout;
  onChange: (layout: MapLayout) => void;
}) {
  const next: MapLayout = layout === "vertical" ? "horizontal" : "vertical";
  const label = next === "horizontal" ? "Horizontal layout" : "Vertical layout";
  return (
    <button
      type="button"
      className="canvas-tool-btn"
      onClick={() => onChange(next)}
      data-testid="map-layout-toggle"
      data-layout={layout}
      aria-label={label}
      title={label}
      style={{ display: "inline-flex", alignItems: "center", padding: "4px 7px" }}
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {next === "horizontal" ? (
          <>
            <rect x="2" y="9" width="6" height="6" rx="1" />
            <rect x="16" y="3" width="6" height="6" rx="1" />
            <rect x="16" y="15" width="6" height="6" rx="1" />
            <path d="M8 12h4M12 6v12M12 6h4M12 18h4" />
          </>
        ) : (
          <>
            <rect x="9" y="2" width="6" height="6" rx="1" />
            <rect x="3" y="16" width="6" height="6" rx="1" />
            <rect x="15" y="16" width="6" height="6" rx="1" />
            <path d="M12 8v4M6 12h12M6 12v4M18 12v4" />
          </>
        )}
      </svg>
    </button>
  );
}

export function VisualGraph(props: VisualGraphProps) {
  return (
    <ReactFlowProvider>
      <VisualGraphInner {...props} />
    </ReactFlowProvider>
  );
}
