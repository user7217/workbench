import React, { useEffect, useMemo, useState, useCallback, useRef } from "react";
import {
  ReactFlow,
  Controls,
  Background,
  BackgroundVariant,
  Handle,
  Position,
  useReactFlow,
  useNodesInitialized,
  ReactFlowProvider,
  type Node,
  type Edge,
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

// The map fills the panel's width (there is usually more width than height) without growing past
// MAX_FIT_ZOOM; if that makes it taller than the panel, it starts at the top and scrolls vertically.
// Below MIN_READABLE_ZOOM it stops shrinking and the user pans instead.
const MIN_READABLE_ZOOM = 0.85;
const MAX_FIT_ZOOM = 1.15;
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
        background: expanded ? "var(--bg-elevated)" : "color-mix(in srgb, var(--accent) 14%, transparent)",
        border: "1px solid var(--border)",
      }}
    >
      {expanded ? "−" : `+${count}`}
    </button>
  );
}
const HANDLE_BORDER = "2px solid var(--bg-panel)";

function RootCardNode({ data }: { data: { label: string; isLearning?: boolean } }) {
  return (
    <div
      data-testid="visual-root-node"
      title={data.label}
      style={{
        width: 176,
        minHeight: 64,
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
      <div style={{ fontSize: 13.5, fontWeight: 650, lineHeight: 1.3, wordBreak: "break-word" }}>
        {data.label}
      </div>
      <Handle
        type="source"
        position={Position.Right}
        style={{ background: "var(--accent)", border: HANDLE_BORDER, width: 8, height: 8, right: -4 }}
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
        width: 240,
        height: 42,
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
        style={{ background: statusColorVar(status), border: HANDLE_BORDER, width: 8, height: 8, left: -4 }}
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
        position={Position.Right}
        style={{ background: statusColorVar(status), border: HANDLE_BORDER, width: 8, height: 8, right: -4 }}
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
        width: 256,
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
        style={{ background: statusColorVar(data.status), border: HANDLE_BORDER, width: 7, height: 7, left: -3.5 }}
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
        position={Position.Right}
        style={
          data.childCount > 0 && data.expanded
            ? { background: statusColorVar(data.status), border: HANDLE_BORDER, width: 7, height: 7, right: -3.5 }
            : { width: 5, height: 5, opacity: 0 }
        }
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
        width: 256,
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
      <Handle type="target" position={Position.Left} style={{ background: "var(--accent)", border: HANDLE_BORDER, width: 7, height: 7, left: -3.5 }} />
      <KindBadge isLearning={false} status={data.status} size={20} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{data.label}</span>
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
        style={{ display: "inline-flex", padding: 2, background: "none", border: "none", cursor: "pointer" }}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M7 17L17 7M8 7h9v9" />
        </svg>
      </button>
      {data.childCount > 0 && data.expanded && (
        <Handle type="source" position={Position.Right} style={{ background: "var(--accent)", border: HANDLE_BORDER, width: 7, height: 7, right: -3.5 }} />
      )}
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

const ROW_H = 42;
const GROUP_GAP = 22;
const X_ROOT = 28;
const X_PHASE = 268;
const X_COMP = 576;
const COMP_COL_W = 308; // 256px card + gap: each nested level is one more column to the right

/** True when an item has parts and every part (and everything under it) is done. */
function isComplete(item: MapItem): boolean {
  const kids = item.children ?? [];
  return kids.length > 0 && kids.every((k) => k.status === "done" && (!k.children?.length || isComplete(k)));
}

/** Toggle keys of every foldable item: groups (as "phase:<id>") and anything with children. */
function foldableKeys(groups: MapGroup[]): { key: string; isProject: boolean }[] {
  const out: { key: string; isProject: boolean }[] = [];
  const walk = (items: MapItem[]) => {
    for (const item of items) {
      if (item.children?.length) {
        out.push({ key: item.id, isProject: Boolean(item.isProject) || item.id.startsWith(PROJECT_NODE_PREFIX) });
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
        [...trail, item.id].forEach((x) => found.add(x));
        return true;
      }
      if (item.children && walk(item.children, [...trail, item.id])) return true;
    }
    return false;
  };
  walk(groups, []);
  return found;
}

/**
 * Root -> phases -> components, with nested components as further columns. Every top-level
 * component shows one nested level; deeper levels open when toggled (or when they hold the selection).
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
  onOpenProject?: (projectId: string, itemId?: string) => void
): { nodes: Node[]; edges: Edge[]; openIds: Set<string> } {
  const rfNodes: Node[] = [];
  const rfEdges: Edge[] = [];
  const openIds = new Set<string>();
  const selectedPath = pathTo(groups, selectedId);

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

  /** Lays out an item and its open descendants from `topY`; returns how many rows it takes. */
  const place = (item: MapItem, depth: number, topY: number, sourceId: string, phaseHighlighted: boolean): number => {
    const kids = item.children || [];
    const open = isOpen(item, depth);
    if (open) openIds.add(item.id);

    let rows = 1;
    if (open) {
      let used = 0;
      for (const kid of kids) used += place(kid, depth + 1, topY + used * ROW_H, item.id, false);
      rows = Math.max(1, used);
    }

    if (item.isProject) {
      const projectId = item.id.slice(PROJECT_NODE_PREFIX.length);
      rfNodes.push({
        id: item.id,
        type: "childProject",
        position: { x: X_COMP + depth * COMP_COL_W, y: topY + ((rows - 1) * ROW_H) / 2 },
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
        type: "default",
        style: { stroke: "var(--accent)", strokeWidth: 1.5, strokeDasharray: "5 4" },
      });
      return rows;
    }

    const isSelected = selectedId === item.id;
    rfNodes.push({
      id: item.id,
      type: "componentNode",
      position: { x: X_COMP + depth * COMP_COL_W, y: topY + ((rows - 1) * ROW_H) / 2 },
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
      type: "default",
      style: edgeStyle(isSelected || selectedPath.has(item.id) || phaseHighlighted, isSelected),
    });
    return rows;
  };

  let currentY = 24;
  const phaseCenters: number[] = [];

  groups.forEach((grp, idx) => {
    const kids = grp.children || [];
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
      for (const kid of kids) rows += place(kid, 0, currentY + rows * ROW_H, pNodeId, isPhaseSelected);
    }

    const blockHeight = Math.max(rows * ROW_H - (ROW_H - 34), 42);
    const phaseY = currentY + blockHeight / 2 - 21;
    phaseCenters.push(phaseY + 21);

    rfNodes.push({
      id: pNodeId,
      type: "phaseGroup",
      position: { x: X_PHASE, y: phaseY },
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
      type: "default",
      style: edgeStyle(isPhaseSelected, isPhaseSelected),
    });

    currentY += blockHeight + GROUP_GAP;
  });

  const rootCenterY =
    phaseCenters.length > 0
      ? (phaseCenters[0] + phaseCenters[phaseCenters.length - 1]) / 2 - 32
      : 100;

  rfNodes.unshift({
    id: "root:node",
    type: "rootNode",
    position: { x: X_ROOT, y: rootCenterY },
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
  const pendingFit = useRef(true);
  // Nested levels the user opened or closed by clicking a node (reset per project)
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const openIdsRef = useRef<Set<string>>(new Set());

  const targetKey = project?.id || learningPath?.id || "";

  const groups: MapGroup[] = useMemo(
    () => (project ? projectTree(project, childProjects) : learningPath ? learningTree(learningPath) : []),
    [project, learningPath, childProjects]
  );

  const setFold = useCallback((key: string, open: boolean) => {
    setToggled((prev) => ({ ...prev, [key]: open }));
  }, []);

  useEffect(() => {
    if (!project && !learningPath) return;
    const built = buildTreeNodesAndEdges(
      project ? project.name : learningPath!.name,
      !project,
      project ? projectUsesPhases(project) : false,
      groups,
      selectedId,
      selectedPhaseId,
      toggled,
      setFold,
      project ? onOpenProject : undefined
    );
    openIdsRef.current = built.openIds;
    setNodes(built.nodes);
    setEdges(built.edges);
  }, [project, learningPath, groups, selectedId, selectedPhaseId, toggled, setFold, onOpenProject]);

  /** Fit the map to the panel, but never below a readable zoom: past that, anchor the root and pan. */
  const fitReadable = useCallback(() => {
    const el = wrapperRef.current;
    const current = getNodes();
    if (!el || current.length === 0) return;
    const bounds = getNodesBounds(current);
    if (bounds.width === 0 || bounds.height === 0) return;

    const w = el.clientWidth;
    const h = el.clientHeight;
    // Fit the width, not the tighter of width and height, so wide panels are used fully
    const widthZoom = (w - 2 * FIT_PADDING) / bounds.width;
    const zoom = Math.min(Math.max(widthZoom, MIN_READABLE_ZOOM), MAX_FIT_ZOOM);
    const contentW = bounds.width * zoom;
    const contentH = bounds.height * zoom;
    const x = contentW <= w - 2 * FIT_PADDING ? (w - contentW) / 2 - bounds.x * zoom : FIT_PADDING - bounds.x * zoom;
    const usableH = h - TOP_CLEARANCE - FIT_PADDING;
    const y =
      contentH <= usableH
        ? TOP_CLEARANCE + (usableH - contentH) / 2 - bounds.y * zoom
        : TOP_CLEARANCE - bounds.y * zoom;
    setViewport({ x, y, zoom }, { duration: 160 });
  }, [getNodes, getNodesBounds, setViewport]);

  // Refit whenever a different project or learning path is shown, once its nodes are measured
  useEffect(() => {
    pendingFit.current = true;
    setToggled({});
  }, [targetKey]);

  useEffect(() => {
    if (pendingFit.current && nodesInitialized && nodes.length > 0) {
      pendingFit.current = false;
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
          { duration: 220 }
        );
      });
    },
    [getNodes, getInternalNode, getNodesBounds, getViewport, setViewport]
  );

  /** Refit once every node of a changed layout has been measured (sizes live on internal nodes). */
  const fitWhenMeasured = useCallback(
    (tries = 0) => {
      requestAnimationFrame(() => {
        const all = getNodes();
        const measured =
          all.length > 0 &&
          all.every((n) => {
            const m = getInternalNode(n.id)?.measured;
            return Boolean(m?.width && m?.height);
          });
        if (!measured) {
          if (tries < 30) fitWhenMeasured(tries + 1);
          return;
        }
        fitReadable();
      });
    },
    [getNodes, getInternalNode, fitReadable]
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
      fitWhenMeasured();
    },
    [foldable, fitWhenMeasured]
  );

  // Refit when the panel is resized (dividers, window, sidebar)
  useEffect(() => {
    const el = wrapperRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    const observer = new ResizeObserver(() => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(fitReadable, 90);
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
        if (entry.comp.components?.length && !openIdsRef.current.has(node.id)) setFold(node.id, true);
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
    [project, learningPath, onSelectPhase, onSelectComponent, onSelectLearningItem, onOpenProject, panToSubtree, setFold]
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
        <Controls showInteractive={false} />
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
