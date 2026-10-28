import React, { useEffect, useRef, useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import {
  useWorkbench,
  getProjectGroups,
  getGroupChildren,
  getItemShortDescription,
  isNewDiscoveredProject,
  projectUsesPhases,
  ROOT_SLOT,
  STATUS_LABELS,
  ITEM_STATUSES,
  LEARNING_ITEM_STATUSES,
  ENTITY_STATUSES,
  pendingKeys,
  type Project,
  type Phase,
  type Component,
  type LearningPath,
  type DetailItem,
} from "@workbench-core";
import { VisualGraph } from "./components/VisualGraph";
import { KindBadge, PhaseNumberBadge, StatusNodeGlyph } from "./components/status";
import { StatusMenu } from "./components/StatusMenu";
import { AddItemInline, EditableField } from "./components/Editing";
import { ItemStructureActions, ParentControl } from "./components/StructureControls";

// ---------------------------------------------------------------------------------------------
// Layout preferences (look-only state, persisted in localStorage)
// ---------------------------------------------------------------------------------------------

const VIEW_STORAGE_KEY = "workbench_project_view_mode";
const PANEL_WIDTHS_STORAGE_KEY = "workbench_panel_widths_v1";

const DEFAULT_NAV_WIDTH = 276;
const MIN_NAV_WIDTH = 200;
const MAX_NAV_WIDTH = 440;

const DEFAULT_CONTENTS_WIDTH = 450;
const MIN_CONTENTS_WIDTH = 310;
const MAX_CONTENTS_WIDTH = 760;

const DEFAULT_DIAGRAM_HEIGHT = 360;
const MIN_DIAGRAM_HEIGHT = 160;
const MAX_DIAGRAM_HEIGHT = 620;

interface PanelWidths {
  navWidth: number;
  contentsWidth: number;
  diagramHeight: number;
}

function clampVal(val: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Math.round(val)));
}

function readStoredViewMode(): "list" | "visual" {
  try {
    const val = window.localStorage.getItem(VIEW_STORAGE_KEY);
    if (val === "visual" || val === "list") return val;
  } catch {
    // Ignore localStorage errors
  }
  return "list";
}

function writeStoredViewMode(mode: "list" | "visual"): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, mode);
  } catch {
    // Ignore localStorage errors
  }
}

function readStoredPanelWidths(): PanelWidths {
  try {
    const raw = window.localStorage.getItem(PANEL_WIDTHS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (
        typeof parsed?.navWidth === "number" &&
        typeof parsed?.contentsWidth === "number"
      ) {
        const diagH =
          typeof parsed?.diagramHeight === "number"
            ? clampVal(parsed.diagramHeight, MIN_DIAGRAM_HEIGHT, MAX_DIAGRAM_HEIGHT)
            : DEFAULT_DIAGRAM_HEIGHT;
        return {
          navWidth: clampVal(parsed.navWidth, MIN_NAV_WIDTH, MAX_NAV_WIDTH),
          contentsWidth: clampVal(
            parsed.contentsWidth,
            MIN_CONTENTS_WIDTH,
            MAX_CONTENTS_WIDTH
          ),
          diagramHeight: diagH,
        };
      }
    }
  } catch {
    // Ignore localStorage errors
  }
  return {
    navWidth: DEFAULT_NAV_WIDTH,
    contentsWidth: DEFAULT_CONTENTS_WIDTH,
    diagramHeight: DEFAULT_DIAGRAM_HEIGHT,
  };
}

function writeStoredPanelWidths(widths: PanelWidths): void {
  try {
    window.localStorage.setItem(PANEL_WIDTHS_STORAGE_KEY, JSON.stringify(widths));
  } catch {
    // Ignore localStorage errors
  }
}

/** The current Workbench look. All data, selection and writes come from useWorkbench(). */
export function App() {
  const wb = useWorkbench();
  const {
    snapshot,
    syncStatus,
    route,
    selection,
    filterText,
    setFilter,
    showArchived: buildingArchivedOpen,
    setShowArchived,
    isExpanded: expandedIn,
    setExpanded,
    isPending,
    lastError,
    dismissError,
    childIndex,
    filteredLearning,
    listedProjects,
    recentProjects,
    recentLearning,
    sidebarProjects,
    sidebarLearning,
    childProjectsAt,
    selectProject,
    selectLearningPath,
    openProjectAt,
    navigate,
    clearComponentSelection,
  } = wb;
  const selectedPhaseId = selection.phaseId;
  const selectedComponentId = selection.componentId;
  const selectedTopicId = selection.topicId;
  const selectedSubtopicId = selection.subtopicId;

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [viewMode, setViewMode] = useState<"list" | "visual">(readStoredViewMode);

  // Resizable 3-panel widths & visual/context height with localStorage persistence (§2, §5)
  const [panelWidths, setPanelWidths] = useState<PanelWidths>(readStoredPanelWidths);
  const [activeResizer, setActiveResizer] = useState<
    "left" | "right" | "horizontal" | null
  >(null);
  const dragStartRef = useRef<{
    which: "left" | "right" | "horizontal";
    startX: number;
    startY: number;
    startNav: number;
    startContents: number;
    startDiagramHeight: number;
  } | null>(null);

  // Look-only disclosure state (selection-driven expand sets live in the core store)
  const [sidebarExpandedProjects, setSidebarExpandedProjects] = useState<Record<string, boolean>>({});
  const [expandedCardDescs, setExpandedCardDescs] = useState<Record<string, boolean>>({});
  const [collapsedPhases, setCollapsedPhases] = useState<Record<string, boolean>>({});
  const [expandedSubtopicItems, setExpandedSubtopicItems] = useState<Record<string, boolean>>({});
  const [expandedContextSubtopics, setExpandedContextSubtopics] = useState<Record<string, boolean>>({});

  // Responsive active panel for narrower viewports (§9)
  const [mobilePanel, setMobilePanel] = useState<"nav" | "contents" | "visual">("contents");

  const [errorExpanded, setErrorExpanded] = useState(false);

  const filterInputRef = useRef<HTMLInputElement | null>(null);

  const toggleProjectViewMode = () => {
    const next = viewMode === "list" ? "visual" : "list";
    setViewMode(next);
    writeStoredViewMode(next);
  };

  // A failed write shows a toast for 4s (a new failure restarts the timer)
  const toastError = lastError?.message ?? null;
  useEffect(() => {
    if (!lastError) return;
    const timer = setTimeout(dismissError, 4000);
    return () => clearTimeout(timer);
  }, [lastError, dismissError]);

  // Handle draggable vertical & horizontal divider mouse events (§2, §5)
  useEffect(() => {
    if (!activeResizer) return;

    if (activeResizer === "horizontal") {
      document.body.classList.add("is-resizing-horizontal");
    } else {
      document.body.classList.add("is-resizing-panels");
    }

    function onMouseMove(e: MouseEvent) {
      const drag = dragStartRef.current;
      if (!drag) return;
      if (drag.which === "left") {
        const deltaX = e.clientX - drag.startX;
        const nextNav = clampVal(
          drag.startNav + deltaX,
          MIN_NAV_WIDTH,
          MAX_NAV_WIDTH
        );
        setPanelWidths((prev) => {
          const updated = { ...prev, navWidth: nextNav };
          writeStoredPanelWidths(updated);
          return updated;
        });
      } else if (drag.which === "right") {
        const deltaX = e.clientX - drag.startX;
        const nextContents = clampVal(
          drag.startContents + deltaX,
          MIN_CONTENTS_WIDTH,
          MAX_CONTENTS_WIDTH
        );
        setPanelWidths((prev) => {
          const updated = { ...prev, contentsWidth: nextContents };
          writeStoredPanelWidths(updated);
          return updated;
        });
      } else if (drag.which === "horizontal") {
        const deltaY = e.clientY - drag.startY;
        const nextDiagH = clampVal(
          drag.startDiagramHeight + deltaY,
          MIN_DIAGRAM_HEIGHT,
          MAX_DIAGRAM_HEIGHT
        );
        setPanelWidths((prev) => {
          const updated = { ...prev, diagramHeight: nextDiagH };
          writeStoredPanelWidths(updated);
          return updated;
        });
      }
    }

    function onMouseUp() {
      dragStartRef.current = null;
      setActiveResizer(null);
      document.body.classList.remove("is-resizing-panels");
      document.body.classList.remove("is-resizing-horizontal");
    }

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      document.body.classList.remove("is-resizing-panels");
      document.body.classList.remove("is-resizing-horizontal");
    };
  }, [activeResizer]);

  const startResizing = (
    which: "left" | "right" | "horizontal",
    e: React.MouseEvent
  ) => {
    e.preventDefault();
    dragStartRef.current = {
      which,
      startX: e.clientX,
      startY: e.clientY,
      startNav: panelWidths.navWidth,
      startContents: panelWidths.contentsWidth,
      startDiagramHeight: panelWidths.diagramHeight,
    };
    setActiveResizer(which);
  };

  const resetPanelWidths = useCallback(() => {
    const defaults: PanelWidths = {
      navWidth: DEFAULT_NAV_WIDTH,
      contentsWidth: DEFAULT_CONTENTS_WIDTH,
      diagramHeight: DEFAULT_DIAGRAM_HEIGHT,
    };
    setPanelWidths(defaults);
    writeStoredPanelWidths(defaults);
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        clearComponentSelection();
        return;
      }
      if (
        e.key === "/" &&
        document.activeElement !== filterInputRef.current &&
        !(document.activeElement instanceof HTMLInputElement) &&
        !(document.activeElement instanceof HTMLTextAreaElement)
      ) {
        e.preventDefault();
        filterInputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [clearComponentSelection]);

  /** Status icon of a project phase/component that opens the status menu. */
  const renderItemStatusMenu = (
    projectId: string,
    item: { id: string; name: string; status?: string },
    testIdPrefix = ""
  ) => (
    <StatusMenu
      status={item.status || "not_started"}
      options={ITEM_STATUSES}
      busy={isPending(pendingKeys.projectItem(projectId, item.id))}
      testId={`${testIdPrefix}status-menu-${item.id}`}
      subject={item.name}
      onSelect={(st) => wb.setItemStatus(projectId, item.id, st)}
    />
  );

  /** Status icon of a learning topic/subtopic that opens the status menu. */
  const renderLearningItemStatusMenu = (
    pathId: string,
    item: { id: string; name: string; status: "not_started" | "in_progress" | "done" },
    testIdPrefix = ""
  ) => (
    <StatusMenu
      status={item.status}
      options={LEARNING_ITEM_STATUSES}
      busy={isPending(pendingKeys.learningItem(pathId, item.id))}
      testId={`${testIdPrefix}status-menu-${item.id}`}
      subject={item.name}
      onSelect={(st) => wb.setLearningItemStatus(pathId, item.id, st)}
    />
  );

  /** An inspector section with a label when it has content; otherwise just its "+ Add …" control. */
  const renderSection = (label: string, hasContent: boolean, field: React.ReactNode) =>
    hasContent ? (
      <div className="inspector-section-box">
        <span className="inspector-field-label">{label}</span>
        {field}
      </div>
    ) : (
      field
    );

  const renderInlineComponents = (ownerId: string, comps: Component[], depth: number): React.ReactNode =>
    comps.length === 0 ? null : (
      <ul className="child-overview-list">
        {comps.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              className="child-overview-item"
              style={{ paddingLeft: 8 + depth * 14 }}
              title={`Open ${c.name} in its project`}
              onClick={(e) => {
                e.stopPropagation();
                openProjectAt(ownerId, c.id);
              }}
            >
              <StatusNodeGlyph status={c.status || "not_started"} size={12} />
              <span>{c.name}</span>
            </button>
            {renderInlineComponents(ownerId, c.components ?? [], depth + 1)}
            {renderChildProjectRows(ownerId, c.id)}
          </li>
        ))}
      </ul>
    );

  /** The child project's own phases and components, shown inside the parent. */
  const renderChildOverview = (child: Project) => {
    const groups = getProjectGroups(child);
    return (
      <div className="child-overview" data-testid={`child-overview-${child.id}`} onClick={(e) => e.stopPropagation()}>
        {groups.length === 0 && <div className="child-overview-empty">No phases yet</div>}
        {groups.map((g) => (
          <div key={g.id} className="child-overview-group">
            <button
              type="button"
              className="child-overview-item group"
              title={`Open ${g.name} in its project`}
              onClick={() => openProjectAt(child.id, g.id)}
            >
              <StatusNodeGlyph status={g.status || "not_started"} size={12} />
              <span>{g.name}</span>
            </button>
            {renderInlineComponents(child.id, getGroupChildren(g), 1)}
            {renderChildProjectRows(child.id, g.id)}
          </div>
        ))}
      </div>
    );
  };

  const renderChildProjectRow = (child: Project, testIdPrefix = "") => {
    const isOpen = expandedIn("childProjects", child.id);
    return (
    <div key={child.id} className="child-project-block">
    <div
      className={`child-project-row ${isOpen ? "open" : ""}`}
      data-testid={`${testIdPrefix}child-project-${child.id}`}
      title={`${child.name} is its own project — click to ${isOpen ? "hide" : "show"} it here`}
      aria-expanded={isOpen}
      onClick={(e) => {
        e.stopPropagation();
        setExpanded("childProjects", child.id, !isOpen);
      }}
    >
      <span className="child-project-chevron" aria-hidden="true">{isOpen ? "⌄" : "›"}</span>
      <KindBadge status={child.status} isLearning={false} size={20} />
      <span className="child-project-name">{child.name}</span>
      <span className="child-project-status">
        <StatusNodeGlyph status={child.status} size={12} />
        {STATUS_LABELS[child.status] ?? child.status}
      </span>
      <button
        type="button"
        className="child-project-open"
        data-testid={`${testIdPrefix}child-project-open-${child.id}`}
        onClick={(e) => {
          e.stopPropagation();
          selectProject(child.id);
        }}
      >
        Open
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M7 17L17 7M8 7h9v9" />
        </svg>
      </button>
    </div>
    {isOpen && renderChildOverview(child)}
    </div>
    );
  };


  const renderChildProjectRows = (parentId: string, slotId: string, testIdPrefix = "") => {
    const kids = childProjectsAt(parentId, slotId);
    if (kids.length === 0) return null;
    return <div className="child-project-list">{kids.map((c) => renderChildProjectRow(c, testIdPrefix))}</div>;
  };


  const isCustomPanelWidths =
    panelWidths.navWidth !== DEFAULT_NAV_WIDTH ||
    panelWidths.contentsWidth !== DEFAULT_CONTENTS_WIDTH ||
    panelWidths.diagramHeight !== DEFAULT_DIAGRAM_HEIGHT;

  // Unified Project Card for Home (§1: status badges removed except New on unpopulated discovered repos)
  const renderUnifiedProjectCard = (p: Project) => {
    const isNew = isNewDiscoveredProject(p);
    const repoNames = (p.repos || []).map((r) => r.name).join(", ");
    const groups = getProjectGroups(p);
    const isDescExpanded = !!expandedCardDescs[p.id];
    const hasLongDesc = (p.description || "").length > 110;

    return (
      <div
        key={p.id}
        className="unified-card"
        data-testid={`project-card-${p.id}`}
        onClick={() => selectProject(p.id)}
      >
        <div className="unified-card-header">
          <div className="unified-card-title-group">
            <KindBadge status={p.status || "active"} isLearning={false} size={28} />
            <div className="unified-card-title-wrap">
              <span className="unified-card-title" title={p.name}>
                {p.name}
              </span>
              {repoNames && <span className="unified-card-meta">{repoNames}</span>}
            </div>
          </div>

          {isNew && (
            <div className="unified-card-status">
              <span className="badge-new" data-testid={`new-badge-${p.id}`}>
                New
              </span>
            </div>
          )}
        </div>

        {p.description && (
          <div className="unified-card-desc-wrap">
            <p className={isDescExpanded ? "unified-card-desc expanded" : "unified-card-desc"}>
              {p.description}
            </p>
            {hasLongDesc && (
              <button
                type="button"
                className="desc-expand-link"
                onClick={(e) => {
                  e.stopPropagation();
                  setExpandedCardDescs((prev) => ({ ...prev, [p.id]: !isDescExpanded }));
                }}
              >
                {isDescExpanded ? "Show less" : "Read more"}
              </button>
            )}
          </div>
        )}

        {groups.length > 0 && (
          <div className="unified-card-phases">
            {groups.map((ph) => (
              <span
                key={ph.id}
                className="unified-phase-chip"
                onClick={(e) => {
                  e.stopPropagation();
                  selectProject(p.id, ph.id, null);
                }}
                title={getItemShortDescription(ph) || ph.name}
              >
                <StatusNodeGlyph status={ph.status || "not_started"} />
                <span className="unified-phase-chip-text">{ph.name}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    );
  };

  // Unified Learning Card for Home (§1: status badges removed)
  const renderUnifiedLearningCard = (lp: LearningPath) => {
    const topics = lp.topics || [];
    const isDescExpanded = !!expandedCardDescs[lp.id];
    const hasLongDesc = (lp.description || "").length > 110;

    return (
      <div
        key={lp.id}
        className="unified-card"
        data-testid={`learning-card-${lp.id}`}
        onClick={() => selectLearningPath(lp.id)}
      >
        <div className="unified-card-header">
          <div className="unified-card-title-group">
            <KindBadge status={lp.status || "active"} isLearning={true} size={28} />
            <div className="unified-card-title-wrap">
              <span className="unified-card-title" title={lp.name}>
                {lp.name}
              </span>
            </div>
          </div>
        </div>

        {lp.description && (
          <div className="unified-card-desc-wrap">
            <p className={isDescExpanded ? "unified-card-desc expanded" : "unified-card-desc"}>
              {lp.description}
            </p>
            {hasLongDesc && (
              <button
                type="button"
                className="desc-expand-link"
                onClick={(e) => {
                  e.stopPropagation();
                  setExpandedCardDescs((prev) => ({ ...prev, [lp.id]: !isDescExpanded }));
                }}
              >
                {isDescExpanded ? "Show less" : "Read more"}
              </button>
            )}
          </div>
        )}

        {topics.length > 0 && (
          <div className="unified-card-phases">
            {topics.map((top) => (
              <span
                key={top.id}
                className="unified-phase-chip"
                onClick={(e) => {
                  e.stopPropagation();
                  selectLearningPath(lp.id, top.id, null);
                }}
                title={getItemShortDescription(top) || top.name}
              >
                <StatusNodeGlyph status={top.status} />
                <span className="unified-phase-chip-text">{top.name}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    );
  };

  // Home View — Unified project & learning grids without separate status sections (§2)
  const renderHomeView = () => {
    // While searching, show every match; otherwise non-active child projects stay inside their parent
    // Home cards show the most recently changed work first
    const visibleProjects = recentProjects.filter(
      (p) => buildingArchivedOpen || p.status !== "archived"
    );
    const hasArchived = listedProjects.some((p) => p.status === "archived");

    return (
      <div className="home-unified-container">
        <section className="domain-section" data-testid="section-building">
          <div className="domain-header-bar">
            <div className="domain-header-left">
              <span className="domain-header-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" style={{ stroke: "var(--accent)" }} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                  <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                  <line x1="12" y1="22.08" x2="12" y2="12" />
                </svg>
              </span>
              <h2 className="domain-title">Projects</h2>
            </div>

            {hasArchived && (
              <button
                type="button"
                className="archived-toggle"
                data-testid="building-archived-toggle"
                onClick={() => setShowArchived(!buildingArchivedOpen)}
              >
                {buildingArchivedOpen ? "Hide Archived" : "Show Archived"}
              </button>
            )}
          </div>

          <div className="unified-cards-grid" data-testid="unified-projects-grid">
            {visibleProjects.map(renderUnifiedProjectCard)}
          </div>
        </section>

        <section className="domain-section" data-testid="section-learning">
          <div className="domain-header-bar">
            <div className="domain-header-left">
              <span className="domain-header-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" style={{ stroke: "var(--accent)" }} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 10v6M2 10l10-5 10 5-10 5z" />
                  <path d="M6 12v5c3 3 9 3 12 0v-5" />
                </svg>
              </span>
              <h2 className="domain-title">Learning Paths</h2>
            </div>
          </div>

          <div className="unified-cards-grid" data-testid="unified-learning-grid">
            {recentLearning.map(renderUnifiedLearningCard)}
          </div>
        </section>
      </div>
    );
  };

  // Building Workspace — Three Coordinated Resizable Panels + Optional Phases + Adjustable Context Panel (§1–§5)
  const renderBuildingWorkspace = () => {
    // Same visibility rule as Home: non-active child projects are listed inside their parent
    const navProjects = listedProjects;
    const { activeProject, activePhase, activeParent: parentRef } = wb;

    const activeProjectId = activeProject?.id || "";
    const usesPhases = activeProject ? projectUsesPhases(activeProject) : true;
    const groups = activeProject ? getProjectGroups(activeProject) : [];
    const isEmptyDiscovered =
      activeProject ? isNewDiscoveredProject(activeProject) : false;

    const relatedLearning = activeProject ? wb.relatedLearning(activeProject) : [];

    const toggleNested = (compId: string, open: boolean) => setExpanded("nestedComponents", compId, !open);

    /** Collapsible "N parts" toggle for a component's nested components. */
    const renderNestedToggle = (comp: Component, testId: string) => {
      const n = (comp.components || []).length;
      if (n === 0) return null;
      const open = expandedIn("nestedComponents", comp.id);
      return (
        <button
          type="button"
          className={`nested-toggle-btn ${open ? "open" : ""}`}
          data-testid={testId}
          aria-expanded={open}
          aria-label={open ? `Hide nested parts of ${comp.name}` : `Show nested parts of ${comp.name}`}
          onClick={(e) => {
            e.stopPropagation();
            toggleNested(comp.id, open);
          }}
        >
          <span className="nested-toggle-caret" aria-hidden="true">
            {open ? "⌄" : "›"}
          </span>
          {n} {n === 1 ? "part" : "parts"}
        </button>
      );
    };

    /** Left tree: a component row, then (when open) its nested components and child projects. */
    const renderTreeComp = (p: Project, ph: Phase, comp: Component, depth: number, isSelectedProject: boolean): React.ReactNode => {
      const isCompActive = isSelectedProject && selectedComponentId === comp.id;
      const nested = comp.components || [];
      const kidProjects = childProjectsAt(p.id, comp.id);
      const hasMore = nested.length > 0 || kidProjects.length > 0;
      const open = expandedIn("nestedComponents", comp.id);
      return (
        <React.Fragment key={comp.id}>
          <div
            className={`tree-comp-row ${isCompActive ? "active" : ""}`}
            data-testid={`nav-tree-comp-${comp.id}`}
            style={depth > 0 ? { paddingLeft: 10 + depth * 14 } : undefined}
            onClick={(e) => {
              e.stopPropagation();
              selectProject(p.id, ph.id, comp.id);
            }}
          >
            {hasMore ? (
              <button
                type="button"
                className="tree-chevron-btn xs"
                data-testid={`nav-tree-nested-toggle-${comp.id}`}
                aria-expanded={open}
                aria-label={open ? "Collapse nested parts" : "Expand nested parts"}
                onClick={(e) => {
                  e.stopPropagation();
                  toggleNested(comp.id, open);
                }}
              >
                {open ? "⌄" : "›"}
              </button>
            ) : (
              <span className="tree-branch-tick">└</span>
            )}
            <StatusNodeGlyph status={comp.status || "not_started"} />
            <span className="tree-comp-name" title={comp.name}>
              {comp.name}
            </span>
            {hasMore && !open && nested.length > 0 && (
              <span className="tree-nested-count" title={`${nested.length} nested parts`}>
                {nested.length}
              </span>
            )}
          </div>
          {open && nested.map((c) => renderTreeComp(p, ph, c, depth + 1, isSelectedProject))}
          {open && kidProjects.map((c) => renderTreeChildProject(c, depth + 1))}
        </React.Fragment>
      );
    };

    /** Left tree: a child project listed inside its parent. */
    const renderTreeChildProject = (child: Project, depth: number) => (
      <div
        key={`child:${child.id}`}
        className={`tree-comp-row tree-child-project-row ${child.id === activeProject?.id ? "active" : ""}`}
        data-testid={`nav-tree-child-project-${child.id}`}
        style={{ paddingLeft: 10 + depth * 14 }}
        title={`${child.name} — its own project (${STATUS_LABELS[child.status] ?? child.status})`}
        onClick={(e) => {
          e.stopPropagation();
          selectProject(child.id);
        }}
      >
        <span className="tree-branch-tick">└</span>
        <KindBadge status={child.status} isLearning={false} size={16} />
        <span className="tree-comp-name">{child.name}</span>
      </div>
    );

    /** Level-3 detail rows (items, notes, dependencies, links) of a component. */
    const renderComponentItems = (projectId: string, items: DetailItem[], testId?: string, withLinks = false) => (
      <div className="tier-3-items-list" data-testid={testId}>
        {items.map((item) => (
          <div
            key={item.id}
            className={`tier-3-item-row ${item.targetComp ? "clickable" : ""}`}
            onClick={(e) => {
              if (item.targetComp) {
                e.stopPropagation();
                selectProject(projectId, item.targetComp.phase.id, item.targetComp.comp.id);
              }
            }}
          >
            <span className="tier-3-branch-line">└</span>
            <StatusNodeGlyph status={item.status} />
            <span className="tier-3-item-kind">{item.kindLabel}:</span>
            {withLinks && item.url ? (
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="tier-3-item-text"
                onClick={(e) => e.stopPropagation()}
              >
                {item.text} ↗
              </a>
            ) : (
              <span className="tier-3-item-text">{item.text}</span>
            )}
          </div>
        ))}
      </div>
    );

    /** Centre pane: one component card, its child projects, and (when open) its nested components. */
    const renderCentreComp = (projectId: string, ph: Phase, comp: Component, depth: number): React.ReactNode => {
      const isCompSelected = selectedComponentId === comp.id;
      const isItemsOpen = !!expandedSubtopicItems[comp.id];
      const childItems = wb.componentDetails(activeProject!, comp);
      const shortDesc = getItemShortDescription(comp);
      const nested = comp.components || [];
      const nestedOpen = expandedIn("nestedComponents", comp.id);

      return (
        <div
          key={comp.id}
          className={`tier-2-subtopic-group ${isCompSelected ? "selected" : ""} ${depth > 0 ? "nested" : ""}`}
          data-testid={`component-row-${comp.id}`}
        >
          <div
            className="tier-2-subtopic-header"
            onClick={() => selectProject(projectId, ph.id, comp.id)}
          >
            <div className="tier-2-header-left">
              {renderItemStatusMenu(projectId, comp)}
              <div className="tier-2-text-col">
                <span className="tier-2-subtopic-title">{comp.name}</span>
                {shortDesc && <p className="tier-2-subtopic-desc">{shortDesc}</p>}
              </div>
            </div>
            <div className="tier-2-header-actions">
              {renderNestedToggle(comp, `nested-toggle-${comp.id}`)}
              {childItems.length > 0 && (
                <button
                  type="button"
                  className="tree-chevron-btn sm"
                  data-testid={`subtopic-expand-${comp.id}`}
                  aria-label={isItemsOpen ? "Hide subtopic items" : "Show subtopic items"}
                  title={isItemsOpen ? "Hide items" : "Show related items"}
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpandedSubtopicItems((prev) => ({ ...prev, [comp.id]: !isItemsOpen }));
                  }}
                >
                  {isItemsOpen ? "⌄" : "›"}
                </button>
              )}
            </div>
          </div>

          {/* LEVEL 3: Individual Items shown only when explicitly requested (§3) */}
          {isItemsOpen && childItems.length > 0 && renderComponentItems(projectId, childItems, undefined, true)}

          {renderChildProjectRows(projectId, comp.id)}

          {nestedOpen && nested.length > 0 && (
            <div className="nested-comp-children" data-testid={`nested-children-${comp.id}`}>
              {nested.map((c) => renderCentreComp(projectId, ph, c, depth + 1))}
            </div>
          )}
        </div>
      );
    };

    /** Right context pane: same structure as the centre list, with its own test ids. */
    const renderContextComp = (projectId: string, ph: Phase, comp: Component, depth: number): React.ReactNode => {
      const isSubSelected = selectedComponentId === comp.id;
      const isExpanded = !!expandedContextSubtopics[comp.id];
      const items = wb.componentDetails(activeProject!, comp);
      const shortDesc = getItemShortDescription(comp);
      const nested = comp.components || [];
      const nestedOpen = expandedIn("nestedComponents", comp.id);

      return (
        <div
          key={comp.id}
          className={`tier-2-subtopic-group ${isSubSelected ? "selected" : ""} ${depth > 0 ? "nested" : ""}`}
        >
          <div
            className={`tier-2-subtopic-header ${isSubSelected ? "selected" : ""}`}
            data-testid={`visual-context-subtopic-${comp.id}`}
            onClick={() => selectProject(projectId, ph.id, comp.id)}
          >
            <div className="tier-2-header-left">
              {renderItemStatusMenu(projectId, comp, "context-")}
              <div className="tier-2-text-col">
                <span className="tier-2-subtopic-title">{comp.name}</span>
                {shortDesc && <p className="tier-2-subtopic-desc">{shortDesc}</p>}
              </div>
            </div>
            <div className="tier-2-header-actions">
              {renderNestedToggle(comp, `context-nested-toggle-${comp.id}`)}
              {items.length > 0 && (
                <button
                  type="button"
                  className="tree-chevron-btn sm"
                  data-testid={`visual-context-expand-${comp.id}`}
                  aria-label={isExpanded ? "Collapse subtopic items" : "Expand subtopic items"}
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpandedContextSubtopics((prev) => ({ ...prev, [comp.id]: !isExpanded }));
                  }}
                >
                  {isExpanded ? "⌄" : "›"}
                </button>
              )}
            </div>
          </div>

          {isExpanded && items.length > 0 &&
            renderComponentItems(projectId, items, `visual-context-items-${comp.id}`)}

          {renderChildProjectRows(projectId, comp.id, "visual-context-")}

          {nestedOpen && nested.length > 0 && (
            <div className="nested-comp-children">
              {nested.map((c) => renderContextComp(projectId, ph, c, depth + 1))}
            </div>
          )}
        </div>
      );
    };

    const activeComponentEntry = wb.activeComponent;
    const structureBusy = activeProject ? isPending(pendingKeys.structure(activeProject.id)) : false;
    const compFieldsBusy =
      activeProject && activeComponentEntry
        ? isPending(pendingKeys.itemFields(activeProject.id, activeComponentEntry.comp.id))
        : false;

    return (
      <div className="three-panel-workspace" data-testid="building-three-panel-workspace">
        {/* Responsive Panel Switcher for narrower viewports */}
        <div className="responsive-panel-switcher">
          <button
            type="button"
            className={`resp-tab-btn ${mobilePanel === "nav" ? "active" : ""}`}
            onClick={() => setMobilePanel("nav")}
          >
            Navigation
          </button>
          <button
            type="button"
            className={`resp-tab-btn ${mobilePanel === "contents" ? "active" : ""}`}
            onClick={() => setMobilePanel("contents")}
          >
            Contents
          </button>
          <button
            type="button"
            className={`resp-tab-btn ${mobilePanel === "visual" ? "active" : ""}`}
            onClick={() => setMobilePanel("visual")}
          >
            Visual Map
          </button>
        </div>

        {/* PANEL A: Project / Topic / Subtopic Navigation Tree (Left) */}
        <section
          className={`workspace-panel panel-a-nav ${mobilePanel === "nav" ? "resp-active" : ""}`}
          style={{
            width: panelWidths.navWidth,
            minWidth: MIN_NAV_WIDTH,
            maxWidth: MAX_NAV_WIDTH,
          }}
          data-testid="building-panel-nav"
        >
          <div className="panel-sticky-header">
            <span className="panel-header-title">
              {usesPhases ? "Projects & Phases" : "Projects & Topics"}
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {isCustomPanelWidths && (
                <button
                  type="button"
                  className="ghost-sm-btn"
                  data-testid="reset-panel-widths"
                  onClick={resetPanelWidths}
                  title="Reset panel layout to defaults"
                >
                  Reset widths
                </button>
              )}
              {navProjects.some((p) => p.status === "archived") && (
                <button
                  type="button"
                  className="ghost-sm-btn"
                  data-testid="building-archived-toggle"
                  onClick={() => setShowArchived(!buildingArchivedOpen)}
                >
                  {buildingArchivedOpen ? "Hide Archived" : "Show Archived"}
                </button>
              )}
            </div>
          </div>

          <div className="panel-independent-scroll" data-testid="panel-a-scroll">
            {navProjects
              .filter((p) => buildingArchivedOpen || p.status !== "archived")
              .map((p) => {
                const isSelected = p.id === activeProjectId;
                const isExpanded = expandedIn("projects", p.id, isSelected);
                const pGroups = getProjectGroups(p);
                const pUsesPhases = projectUsesPhases(p);
                const isNew = isNewDiscoveredProject(p);
                const rootChildren = childProjectsAt(p.id, ROOT_SLOT);
                const hasTree = pGroups.length > 0 || rootChildren.length > 0;

                return (
                  <div
                    key={p.id}
                    className={`tree-project-block ${isSelected ? "selected" : ""}`}
                    data-testid={`nav-tree-project-${p.id}`}
                  >
                    <div
                      className={`tree-project-row ${isSelected ? "active" : ""}`}
                      data-testid={`project-card-${p.id}`}
                      onClick={() => selectProject(p.id)}
                    >
                      <button
                        type="button"
                        className="tree-chevron-btn"
                        aria-label={isExpanded ? "Collapse project" : "Expand project"}
                        onClick={(e) => {
                          e.stopPropagation();
                          setExpanded("projects", p.id, !isExpanded);
                        }}
                      >
                        {hasTree ? (isExpanded ? "⌄" : "›") : "·"}
                      </button>
                      <KindBadge status={p.status || "active"} isLearning={false} size={22} />
                      <span className="tree-project-name" title={p.name}>
                        {p.name}
                      </span>
                      {isNew && (
                        <span className="badge-new" data-testid={`new-badge-${p.id}`}>
                          New
                        </span>
                      )}
                    </div>

                    {isExpanded && hasTree && (
                      <div className="tree-phases-children">
                        {rootChildren.map((c) => renderTreeChildProject(c, 0))}
                        {pGroups.map((ph, idx) => {
                          const isPhaseActive =
                            isSelected && activePhase?.id === ph.id;
                          const isPhaseExpanded = expandedIn("phases", ph.id, isPhaseActive);
                          const comps = getGroupChildren(ph);
                          const phaseChildren = childProjectsAt(p.id, ph.id);
                          const hasPhaseKids = comps.length > 0 || phaseChildren.length > 0;

                          return (
                            <div key={ph.id} className="tree-phase-group">
                              <div
                                className={`tree-phase-row ${
                                  isPhaseActive && !selectedComponentId ? "active" : ""
                                }`}
                                data-testid={`nav-tree-phase-${ph.id}`}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  selectProject(p.id, ph.id, null);
                                }}
                              >
                                <button
                                  type="button"
                                  className="tree-chevron-btn sm"
                                  aria-label={
                                    isPhaseExpanded
                                      ? "Collapse subtopics"
                                      : "Expand subtopics"
                                  }
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setExpanded("phases", ph.id, !isPhaseExpanded);
                                  }}
                                >
                                  {hasPhaseKids
                                    ? isPhaseExpanded
                                      ? "⌄"
                                      : "›"
                                    : "·"}
                                </button>
                                {pUsesPhases && (
                                  <PhaseNumberBadge index={idx} status={ph.status || "not_started"} size={18} />
                                )}
                                <span className="tree-phase-name" title={ph.name}>
                                  {ph.name}
                                </span>
                                <StatusNodeGlyph status={ph.status || "not_started"} />
                              </div>

                              {isPhaseExpanded && hasPhaseKids && (
                                <div className="tree-comp-children">
                                  {comps.map((comp) => renderTreeComp(p, ph, comp, 0, isSelected))}
                                  {phaseChildren.map((c) => renderTreeChildProject(c, 0))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
          </div>
        </section>

        {/* Draggable Divider 1 (Between Panel A and Panel B) */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize navigation and content panels"
          title="Drag to resize navigation and content panels (double-click to reset)"
          className={`panel-resizer ${activeResizer === "left" ? "dragging" : ""}`}
          data-testid="panel-resizer-left"
          onMouseDown={(e) => startResizing("left", e)}
          onDoubleClick={resetPanelWidths}
        />

        {/* PANEL B: Selected Project / Phase or Topic / Subtopic Contents (Centre) */}
        <section
          className={`workspace-panel panel-b-contents ${
            mobilePanel === "contents" ? "resp-active" : ""
          }`}
          style={{
            width: panelWidths.contentsWidth,
            minWidth: MIN_CONTENTS_WIDTH,
            maxWidth: MAX_CONTENTS_WIDTH,
          }}
          data-testid="building-panel-contents"
        >
          <div className="panel-sticky-header">
            <span className="panel-header-title">
              {activeComponentEntry
                ? `Subtopic · ${activeComponentEntry.comp.name}`
                : activePhase
                  ? `${usesPhases ? "Phase" : "Topic"} · ${activePhase.name}`
                  : activeProject?.name || "Project Contents"}
            </span>
            {activeProject && (activeProject.repos || []).length > 0 && (
              <span className="card-repos">
                {(activeProject.repos || []).map((r) => r.name).join(", ")}
              </span>
            )}
          </div>

          {activeProject ? (
            <div
              className="panel-independent-scroll"
              data-testid={`project-page-${activeProject.id}`}
            >
              {/* Project Summary Header — status badges removed per §1 */}
              <div className="centre-project-header" data-testid="project-detail-banner">
                <div className="centre-project-title-row">
                  <KindBadge status={activeProject.status || "active"} isLearning={false} size={32} />
                  <div className="centre-project-title-text">
                    <EditableField
                      id={activeProject.id}
                      field="name"
                      label="project name"
                      kind="line"
                      value={activeProject.name}
                      busy={isPending(pendingKeys.projectFields(activeProject.id))}
                      onSave={(v) => void wb.updateProjectFields(activeProject.id, { name: v as string })}
                    >
                      <h1 className="detail-title">{activeProject.name}</h1>
                    </EditableField>
                    {parentRef && (
                      <div className="project-breadcrumb" data-testid="project-breadcrumb">
                        Part of{" "}
                        <button
                          type="button"
                          data-testid="project-breadcrumb-parent"
                          onClick={() => {
                            const deepest = parentRef.components[parentRef.components.length - 1];
                            if (parentRef.phase) {
                              selectProject(parentRef.parentId, parentRef.phase.id, deepest?.id ?? null);
                            } else {
                              selectProject(parentRef.parentId);
                            }
                          }}
                        >
                          {parentRef.parentName}
                        </button>
                        {parentRef.phase && (
                          <>
                            <span className="crumb-sep">›</span>
                            {parentRef.phase.name}
                          </>
                        )}
                        {parentRef.components.map((c) => (
                          <React.Fragment key={c.id}>
                            <span className="crumb-sep">›</span>
                            {c.name}
                          </React.Fragment>
                        ))}
                      </div>
                    )}
                    <div className="header-tools-row">
                      <ParentControl project={activeProject} />
                    </div>
                  </div>
                  <div className="header-status-control">
                    <StatusMenu
                      variant="pill"
                      status={activeProject.status}
                      options={ENTITY_STATUSES}
                      busy={isPending(pendingKeys.project(activeProject.id))}
                      testId="project-status-control"
                      subject={activeProject.name}
                      onSelect={(st) => wb.setProjectStatus(activeProject.id, st)}
                    />
                  </div>
                </div>
                <EditableField
                  id={activeProject.id}
                  field="description"
                  label="description"
                  kind="text"
                  value={activeProject.description ?? ""}
                  busy={isPending(pendingKeys.projectFields(activeProject.id))}
                  onSave={(v) => void wb.updateProjectFields(activeProject.id, { description: v as string })}
                >
                  <p className="centre-project-desc">{activeProject.description}</p>
                </EditableField>
                {(activeProject.links || []).length > 0 && (
                  <div className="detail-links">
                    {(activeProject.links || []).map((lnk, i) => (
                      <a
                        key={i}
                        href={lnk.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="ext-link-pill"
                      >
                        {lnk.label} ↗
                      </a>
                    ))}
                  </div>
                )}

                {relatedLearning.length > 0 && (
                  <div className="related-refs" data-testid="related-learning">
                    <span className="related-refs-label">Related learning</span>
                    {relatedLearning.map((l) => (
                      <button
                        key={l.id}
                        type="button"
                        className="related-ref-chip"
                        data-testid={`related-learning-${l.id}`}
                        onClick={() => selectLearningPath(l.id)}
                        title={`Open learning path ${l.name}`}
                      >
                        <KindBadge status={l.status} isLearning={true} size={16} />
                        <span>{l.name}</span>
                      </button>
                    ))}
                  </div>
                )}

                {childProjectsAt(activeProject.id, ROOT_SLOT).length > 0 && (
                  <div className="related-refs-block">
                    <span className="related-refs-label">Sub-projects</span>
                    {renderChildProjectRows(activeProject.id, ROOT_SLOT)}
                  </div>
                )}

                {groups.length > 0 && (
                  <div className="phase-selector-strip">
                    {groups.map((ph, idx) => {
                      const isCurr = activePhase?.id === ph.id;
                      return (
                        <button
                          key={ph.id}
                          type="button"
                          className={`phase-pill-tab ${isCurr ? "active" : ""}`}
                          onClick={() => selectProject(activeProject.id, ph.id, null)}
                        >
                          <StatusNodeGlyph status={ph.status || "not_started"} />
                          <span>
                            {usesPhases ? `${idx + 1}. ${ph.name}` : ph.name}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Selected Item Detail Card (shown on demand when an item is explicitly opened per §3) */}
              {activeComponentEntry && (
                <div className="selected-item-detail-card" data-testid="inspector-panel">
                  <div className="inspector-header">
                    <div className="inspector-header-left">
                      <StatusNodeGlyph
                        status={activeComponentEntry.comp.status || "not_started"}
                      />
                      <div>
                        <span className="inspector-field-label">
                          Selected Subtopic · {activeComponentEntry.phase.name}
                          {activeComponentEntry.ancestors.map((a) => ` › ${a.name}`).join("")}
                        </span>
                        <EditableField
                          id={activeComponentEntry.comp.id}
                          field="name"
                          label="name"
                          kind="line"
                          value={activeComponentEntry.comp.name}
                          busy={compFieldsBusy}
                          onSave={(v) => void wb.updateItemFields(activeProject.id, activeComponentEntry.comp.id, { name: v as string })}
                        >
                          <h2 className="inspector-title" data-testid="inspector-title">
                            {activeComponentEntry.comp.name}
                          </h2>
                        </EditableField>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="inspector-close-btn"
                      onClick={() => {
                        clearComponentSelection();
                      }}
                      aria-label="Close subtopic detail"
                    >
                      ×
                    </button>
                  </div>

                  <div className="inspector-structure-row">
                    <ItemStructureActions project={activeProject} item={activeComponentEntry.comp} isPhase={false} />
                    <AddItemInline
                      testId={`add-part-${activeComponentEntry.comp.id}`}
                      label="Add part"
                      placeholder="Name of the nested part"
                      busy={structureBusy}
                      onAdd={async (name) => {
                        const { comp, phase } = activeComponentEntry;
                        const id = await wb.addItem(activeProject.id, { parentId: comp.id, name });
                        if (id) selectProject(activeProject.id, phase.id, id);
                        return id !== null;
                      }}
                    />
                  </div>

                  <EditableField
                    id={activeComponentEntry.comp.id}
                    field="summary"
                    label="summary"
                    kind="text"
                    value={activeComponentEntry.comp.summary ?? ""}
                    displayEmpty={!getItemShortDescription(activeComponentEntry.comp)}
                    busy={compFieldsBusy}
                    onSave={(v) => void wb.updateItemFields(activeProject.id, activeComponentEntry.comp.id, { summary: v as string })}
                  >
                    <div className="inspector-inset-card" data-testid="inspector-summary">
                      {getItemShortDescription(activeComponentEntry.comp)}
                    </div>
                  </EditableField>

                  {renderSection(
                    "Implementation Notes",
                    !!activeComponentEntry.comp.notes,
                    <EditableField
                      id={activeComponentEntry.comp.id}
                      field="notes"
                      label="notes"
                      kind="text"
                      value={activeComponentEntry.comp.notes ?? ""}
                      busy={compFieldsBusy}
                      onSave={(v) => void wb.updateItemFields(activeProject.id, activeComponentEntry.comp.id, { notes: v as string })}
                    >
                      <div className="inspector-inset-card" data-testid="inspector-notes">
                        {activeComponentEntry.comp.notes}
                      </div>
                    </EditableField>
                  )}

                  {renderSection(
                    "Checklist",
                    (activeComponentEntry.comp.items || []).length > 0,
                    <EditableField
                      id={activeComponentEntry.comp.id}
                      field="items"
                      label="checklist items"
                      kind="list"
                      value={activeComponentEntry.comp.items ?? []}
                      busy={compFieldsBusy}
                      onSave={(v) => void wb.updateItemFields(activeProject.id, activeComponentEntry.comp.id, { items: v as string[] })}
                    >
                      <ul className="inspector-items-list" data-testid="inspector-items">
                        {(activeComponentEntry.comp.items || []).map((it, i) => (
                          <li key={i}>{it}</li>
                        ))}
                      </ul>
                    </EditableField>
                  )}

                  {(() => {
                    const comp = activeComponentEntry.comp;
                    const dependsOn = wb.dependenciesOf(activeProject, comp);
                    const dependedBy = wb.dependentsOf(activeProject, comp.id);

                    return (
                      <div className="inspector-deps-grid">
                        <div className="inspector-section-box">
                          <span className="inspector-field-label">Prerequisites</span>
                          {dependsOn.length === 0 ? (
                            <span className="inspector-empty-text">None</span>
                          ) : (
                            <div className="inspector-dep-list">
                              {dependsOn.map((d) => (
                                <button
                                  key={d.comp.id}
                                  type="button"
                                  className="dep-chip"
                                  data-testid={`inspector-dep-${d.comp.id}`}
                                  onClick={() =>
                                    selectProject(
                                      activeProject.id,
                                      d.phase.id,
                                      d.comp.id
                                    )
                                  }
                                >
                                  <StatusNodeGlyph
                                    status={d.comp.status || "not_started"}
                                  />
                                  <span>{d.comp.name}</span>
                                </button>
                              ))}
                            </div>
                          )}
                        </div>

                        <div className="inspector-section-box">
                          <span className="inspector-field-label">Used By</span>
                          {dependedBy.length === 0 ? (
                            <span className="inspector-empty-text">None</span>
                          ) : (
                            <div className="inspector-dep-list">
                              {dependedBy.map((d) => (
                                <button
                                  key={d.comp.id}
                                  type="button"
                                  className="dep-chip"
                                  data-testid={`inspector-dep-by-${d.comp.id}`}
                                  onClick={() =>
                                    selectProject(
                                      activeProject.id,
                                      d.phase.id,
                                      d.comp.id
                                    )
                                  }
                                >
                                  <StatusNodeGlyph
                                    status={d.comp.status || "not_started"}
                                  />
                                  <span>{d.comp.name}</span>
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })()}
                </div>
              )}

              {isEmptyDiscovered ? (
                <div className="empty-discovered-card" data-testid="empty-discovered-state">
                  <p style={{ margin: "0 0 8px 0", color: "var(--text-secondary)" }}>
                    Discovered repository — structure has not been populated yet.
                  </p>
                  <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
                    Open a coding agent session in this repo and run{" "}
                    <code>/workbench</code> to populate its breakdown.
                  </p>
                </div>
              ) : (
                <div
                  className="centre-phases-stack"
                  data-testid={usesPhases ? "project-list-view" : "direct-topics-view"}
                >
                  {groups.map((ph, idx) => {
                    const isCollapsed = !!collapsedPhases[ph.id];
                    const isActivePh = activePhase?.id === ph.id;
                    const comps = getGroupChildren(ph);
                    const phDesc = getItemShortDescription(ph);
                    const phBusy = isPending(pendingKeys.itemFields(activeProject.id, ph.id));

                    return (
                      <div
                        key={ph.id}
                        className={`tier-1-phase-section ${
                          isActivePh ? "active-phase-card" : ""
                        }`}
                        data-testid={`phase-block-${ph.id}`}
                      >
                        {/* LEVEL 1: Phase (when uses_phases is true) or Direct Topic (when uses_phases is false) */}
                        <div
                          className="tier-1-phase-header"
                          onClick={() => selectProject(activeProject.id, ph.id, null)}
                        >
                          <div className="tier-1-header-main">
                            <div className="tier-1-kicker-row">
                              <span className="tier-1-kicker-badge">
                                {usesPhases
                                  ? `PHASE ${String(idx + 1).padStart(2, "0")}`
                                  : "TOPIC"}
                              </span>
                              {renderItemStatusMenu(activeProject.id, ph)}
                              <EditableField
                                id={ph.id}
                                field="name"
                                label={`${usesPhases ? "phase" : "topic"} name`}
                                kind="line"
                                value={ph.name}
                                busy={phBusy}
                                onSave={(v) => void wb.updateItemFields(activeProject.id, ph.id, { name: v as string })}
                              >
                                <span className="tier-1-phase-title">{ph.name}</span>
                              </EditableField>
                            </div>
                            <EditableField
                              id={ph.id}
                              field="summary"
                              label="summary"
                              kind="text"
                              value={ph.summary ?? ""}
                              displayEmpty={!phDesc}
                              addWhenEmpty={isActivePh}
                              busy={phBusy}
                              onSave={(v) => void wb.updateItemFields(activeProject.id, ph.id, { summary: v as string })}
                            >
                              <span className="tier-1-phase-summary">{phDesc}</span>
                            </EditableField>
                            {(isActivePh || ph.notes) && (
                              <EditableField
                                id={ph.id}
                                field="notes"
                                label="notes"
                                kind="text"
                                value={ph.notes ?? ""}
                                addWhenEmpty={isActivePh}
                                busy={phBusy}
                                onSave={(v) => void wb.updateItemFields(activeProject.id, ph.id, { notes: v as string })}
                              >
                                <span className="tier-1-phase-notes" data-testid={`phase-notes-${ph.id}`}>
                                  {ph.notes}
                                </span>
                              </EditableField>
                            )}
                          </div>
                          <button
                            type="button"
                            className="ghost-sm-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              setCollapsedPhases((prev) => ({
                                ...prev,
                                [ph.id]: !isCollapsed,
                              }));
                            }}
                          >
                            {isCollapsed ? "Expand" : "Collapse"}
                          </button>
                        </div>

                        {!isCollapsed && (comps.length > 0 || childProjectsAt(activeProject.id, ph.id).length > 0) && (
                          /* LEVEL 2: Concise Subtopic Cards (name + 1-2 line description; extra details on demand per §3) */
                          <div className="tier-2-subtopics-Indent">
                            {renderChildProjectRows(activeProject.id, ph.id)}
                            {comps.map((comp) => renderCentreComp(activeProject.id, ph, comp, 0))}
                          </div>
                        )}
                        <div className="phase-footer-row">
                          {!isCollapsed && (
                            <AddItemInline
                              testId={`add-component-${ph.id}`}
                              label="Add component"
                              placeholder="Component name"
                              busy={structureBusy}
                              onAdd={async (name) => {
                                const id = await wb.addItem(activeProject.id, { parentId: ph.id, name });
                                if (id) selectProject(activeProject.id, ph.id, id);
                                return id !== null;
                              }}
                            />
                          )}
                          <ItemStructureActions project={activeProject} item={ph} isPhase={true} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              <AddItemInline
                testId="add-phase"
                label={usesPhases ? "Add phase" : "Add topic"}
                placeholder={usesPhases ? "Phase name" : "Topic name"}
                busy={structureBusy}
                className="add-phase"
                onAdd={async (name) => {
                  const id = await wb.addItem(activeProject.id, { parentId: null, name });
                  if (id) selectProject(activeProject.id, id, null);
                  return id !== null;
                }}
              />
            </div>
          ) : (
            <div className="empty-pane-card">
              Select a project from the navigation panel.
            </div>
          )}
        </section>

        {/* Draggable Divider 2 (Between Panel B and Panel C) */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize content and diagram panels"
          title="Drag to resize content and diagram panels (double-click to reset)"
          className={`panel-resizer ${activeResizer === "right" ? "dragging" : ""}`}
          data-testid="panel-resizer-right"
          onMouseDown={(e) => startResizing("right", e)}
          onDoubleClick={resetPanelWidths}
        />

        {/* PANEL C: Visual Project Overview + Adjustable Contextual Panel Beneath It (§2, §3, §4) */}
        <section
          className={`workspace-panel panel-c-visual ${
            mobilePanel === "visual" ? "resp-active" : ""
          }`}
          data-testid="building-panel-visual"
        >
          <div className="panel-sticky-header">
            <span className="panel-header-title">Visual Overview & Topic Context</span>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {isCustomPanelWidths && (
                <button
                  type="button"
                  className="ghost-sm-btn"
                  data-testid="reset-context-height"
                  onClick={resetPanelWidths}
                >
                  Reset layout
                </button>
              )}
              {activeProject && !isEmptyDiscovered && (
                <div className="segmented-view-switch">
                  <span
                    className={`seg-indicator ${viewMode === "list" ? "active" : ""}`}
                  >
                    List View
                  </span>
                  <span
                    className={`seg-indicator ${viewMode === "visual" ? "active" : ""}`}
                  >
                    Visual View
                  </span>
                  <button
                    type="button"
                    className="primary-toggle"
                    data-testid="view-mode-toggle"
                    onClick={toggleProjectViewMode}
                  >
                    {viewMode === "list"
                      ? "Switch to visual view"
                      : "Switch to list view"}
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="panel-c-split-container">
            {activeProject && !isEmptyDiscovered ? (
              <>
                <div
                  className="panel-c-diagram-frame"
                  data-testid="panel-c-diagram-pane"
                  style={{
                    height: activePhase ? panelWidths.diagramHeight : "100%",
                    minHeight: MIN_DIAGRAM_HEIGHT,
                  }}
                >
                  <VisualGraph
                    project={activeProject}
                    selectedId={selectedComponentId || selectedPhaseId}
                    selectedPhaseId={activePhase?.id || null}
                    onSelectPhase={(phase) =>
                      selectProject(activeProject.id, phase.id, null)
                    }
                    onSelectComponent={(component, phase) =>
                      selectProject(activeProject.id, phase.id, component.id)
                    }
                    childProjects={childIndex.get(activeProject.id)}
                    onOpenProject={openProjectAt}
                  />
                </div>

                {activePhase && (
                  <>
                    {/* Draggable Horizontal Divider Between Visual Overview and Context Section (§2) */}
                    <div
                      role="separator"
                      aria-orientation="horizontal"
                      aria-label="Resize visual overview and contextual section"
                      title="Drag to resize visual overview and contextual section (double-click to reset)"
                      className={`panel-resizer-horizontal ${
                        activeResizer === "horizontal" ? "dragging" : ""
                      }`}
                      data-testid="panel-resizer-horizontal"
                      onMouseDown={(e) => startResizing("horizontal", e)}
                      onDoubleClick={resetPanelWidths}
                    />

                    {/* Independently Scrollable Contextual Panel Beneath Visual Overview (§2, §3, §13) */}
                    <div
                      className="visual-context-panel"
                      data-testid="visual-context-panel"
                    >
                      <div
                        data-testid="panel-c-scroll"
                        style={{
                          overflowY: "auto",
                          flex: 1,
                          minHeight: 0,
                          display: "flex",
                          flexDirection: "column",
                          gap: 10,
                        }}
                      >
                        <div className="visual-context-header">
                          <div className="visual-context-header-top">
                            <span className="tier-1-kicker-badge accent">
                              {usesPhases ? "SELECTED PHASE" : "SELECTED TOPIC"}
                            </span>
                            {renderItemStatusMenu(activeProject.id, activePhase, "context-")}
                          </div>
                          <h3
                            className="visual-context-title"
                            data-testid="visual-context-title"
                          >
                            {activePhase.name}
                          </h3>
                          {getItemShortDescription(activePhase) && (
                            <p
                              className="visual-context-desc"
                              data-testid="visual-context-desc"
                            >
                              {getItemShortDescription(activePhase)}
                            </p>
                          )}
                        </div>

                        {/* Concise Subtopics (name + 1-2 line description; items shown on demand per §3) */}
                        <div className="visual-context-subtopics">
                          {renderChildProjectRows(activeProject.id, activePhase.id, "visual-context-")}
                          {getGroupChildren(activePhase).map((comp) =>
                            renderContextComp(activeProject.id, activePhase, comp, 0)
                          )}
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </>
            ) : (
              <div className="empty-pane-card" style={{ margin: 16 }}>
                No visual diagram available for this project yet.
              </div>
            )}
          </div>
        </section>
      </div>
    );
  };

  // Learning Workspace — Three Coordinated Resizable Panels + Adjustable Context Panel (§1–§5)
  const renderLearningWorkspace = () => {
    const {
      activeLearningPath: activeLp,
      activeTopic,
      activeLearningItem,
      activeMarkdown: activeMd,
    } = wb;
    const activePathId = activeLp?.id || "";
    const topics = activeLp?.topics || [];
    const relatedProjects = activeLp ? wb.relatedProjects(activeLp) : [];
    const learningItemBusy =
      activeLp && activeLearningItem
        ? isPending(pendingKeys.learningItemFields(activeLp.id, activeLearningItem.id))
        : false;

    return (
      <div className="three-panel-workspace" data-testid="learning-three-panel-workspace">
        {/* PANEL A: Learning Navigation (Left) */}
        <section
          className={`workspace-panel panel-a-nav ${mobilePanel === "nav" ? "resp-active" : ""}`}
          style={{
            width: panelWidths.navWidth,
            minWidth: MIN_NAV_WIDTH,
            maxWidth: MAX_NAV_WIDTH,
          }}
          data-testid="learning-panel-nav"
        >
          <div className="panel-sticky-header">
            <span className="panel-header-title">Learning Paths</span>
          </div>

          <div className="panel-independent-scroll">
            {filteredLearning.map((lp) => {
              const isSelected = lp.id === activePathId;
              const isExpanded = expandedIn("learningPaths", lp.id, isSelected);
              const lpTopics = lp.topics || [];

              return (
                <div
                  key={lp.id}
                  className={`tree-project-block ${isSelected ? "selected" : ""}`}
                >
                  <div
                    className={`tree-project-row ${isSelected ? "active" : ""}`}
                    data-testid={`learning-card-${lp.id}`}
                    onClick={() => selectLearningPath(lp.id)}
                  >
                    <button
                      type="button"
                      className="tree-chevron-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setExpanded("learningPaths", lp.id, !isExpanded);
                      }}
                    >
                      {lpTopics.length > 0 ? (isExpanded ? "⌄" : "›") : "·"}
                    </button>
                    <KindBadge status={lp.status || "active"} isLearning={true} size={22} />
                    <span className="tree-project-name" title={lp.name}>
                      {lp.name}
                    </span>
                  </div>

                  {isExpanded && lpTopics.length > 0 && (
                    <div className="tree-phases-children">
                      {lpTopics.map((top) => {
                        const isTopActive =
                          isSelected && activeTopic?.id === top.id;
                        const isTopExpanded = expandedIn("topics", top.id, isTopActive);
                        const subs = top.subtopics || [];

                        return (
                          <div key={top.id} className="tree-phase-group">
                            <div
                              className={`tree-phase-row ${
                                isTopActive && !selectedSubtopicId ? "active" : ""
                              }`}
                              onClick={(e) => {
                                e.stopPropagation();
                                selectLearningPath(lp.id, top.id, null);
                              }}
                            >
                              <button
                                type="button"
                                className="tree-chevron-btn sm"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setExpanded("topics", top.id, !isTopExpanded);
                                }}
                              >
                                {subs.length > 0 ? (isTopExpanded ? "⌄" : "›") : "·"}
                              </button>
                              <span className="tree-phase-name" title={top.name}>
                                {top.name}
                              </span>
                              <StatusNodeGlyph status={top.status} />
                            </div>

                            {isTopExpanded && subs.length > 0 && (
                              <div className="tree-comp-children">
                                {subs.map((sub) => {
                                  const isSubActive =
                                    isSelected && selectedSubtopicId === sub.id;
                                  return (
                                    <div
                                      key={sub.id}
                                      className={`tree-comp-row ${
                                        isSubActive ? "active" : ""
                                      }`}
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        selectLearningPath(lp.id, top.id, sub.id);
                                      }}
                                    >
                                      <span className="tree-branch-tick">└</span>
                                      <StatusNodeGlyph status={sub.status} />
                                      <span
                                        className="tree-comp-name"
                                        title={sub.name}
                                      >
                                        {sub.name}
                                      </span>
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        {/* Draggable Divider 1 */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize navigation and content panels"
          className={`panel-resizer ${activeResizer === "left" ? "dragging" : ""}`}
          data-testid="panel-resizer-left"
          onMouseDown={(e) => startResizing("left", e)}
          onDoubleClick={resetPanelWidths}
        />

        {/* PANEL B: Selected Topic / Subtopic Contents & Status Controls (Centre) */}
        <section
          className={`workspace-panel panel-b-contents ${
            mobilePanel === "contents" ? "resp-active" : ""
          }`}
          style={{
            width: panelWidths.contentsWidth,
            minWidth: MIN_CONTENTS_WIDTH,
            maxWidth: MAX_CONTENTS_WIDTH,
          }}
          data-testid="learning-panel-contents"
        >
          <div className="panel-sticky-header">
            <span className="panel-header-title">
              {activeLearningItem
                ? `Study · ${activeLearningItem.name}`
                : activeLp?.name || "Topic Contents"}
            </span>
          </div>

          {activeLp ? (
            <div
              className="panel-independent-scroll"
              data-testid={`learning-page-${activeLp.id}`}
            >
              {/* Learning Path Header — status badges removed per §1 */}
              <div className="centre-project-header" data-testid="learning-detail-banner">
                <div className="centre-project-title-row">
                  <KindBadge status={activeLp.status || "active"} isLearning={true} size={32} />
                  <div className="centre-project-title-text">
                    <EditableField
                      id={activeLp.id}
                      field="name"
                      label="learning path name"
                      kind="line"
                      value={activeLp.name}
                      busy={isPending(pendingKeys.learningFields(activeLp.id))}
                      onSave={(v) => void wb.updateLearningFields(activeLp.id, { name: v as string })}
                    >
                      <h1 className="detail-title">{activeLp.name}</h1>
                    </EditableField>
                  </div>
                  <div className="header-status-control">
                    <StatusMenu
                      variant="pill"
                      status={activeLp.status}
                      options={ENTITY_STATUSES}
                      busy={isPending(pendingKeys.learning(activeLp.id))}
                      testId="learning-status-control"
                      subject={activeLp.name}
                      onSelect={(st) => wb.setLearningStatus(activeLp.id, st)}
                    />
                  </div>
                </div>
                <EditableField
                  id={activeLp.id}
                  field="description"
                  label="description"
                  kind="text"
                  value={activeLp.description ?? ""}
                  busy={isPending(pendingKeys.learningFields(activeLp.id))}
                  onSave={(v) => void wb.updateLearningFields(activeLp.id, { description: v as string })}
                >
                  <p className="centre-project-desc">{activeLp.description}</p>
                </EditableField>

                {relatedProjects.length > 0 && (
                  <div className="related-refs" data-testid="related-projects">
                    <span className="related-refs-label">Related projects</span>
                    {relatedProjects.map(({ id, project }) =>
                      project ? (
                        <button
                          key={id}
                          type="button"
                          className="related-ref-chip"
                          data-testid={`related-project-${id}`}
                          onClick={() => selectProject(id)}
                          title={`Open project ${project.name}`}
                        >
                          <KindBadge status={project.status} isLearning={false} size={16} />
                          <span>{project.name}</span>
                        </button>
                      ) : (
                        <span
                          key={id}
                          className="related-ref-chip missing"
                          data-testid={`related-project-${id}`}
                          title="This project is not in the dashboard"
                        >
                          <span>{id}</span>
                        </span>
                      )
                    )}
                  </div>
                )}

                {topics.length > 0 && (
                  <div className="phase-selector-strip">
                    {topics.map((top) => {
                      const isCurr = activeTopic?.id === top.id;
                      return (
                        <button
                          key={top.id}
                          type="button"
                          className={`phase-pill-tab ${isCurr ? "active" : ""}`}
                          onClick={() => selectLearningPath(activeLp.id, top.id, null)}
                        >
                          <StatusNodeGlyph status={top.status} />
                          <span>{top.name}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Active Study Item Detail Card */}
              {activeLearningItem && (
                <div className="selected-item-detail-card" data-testid="inspector-panel">
                  <div className="inspector-header">
                    <div className="inspector-header-left">
                      <StatusNodeGlyph status={activeLearningItem.status} />
                      <div>
                        <span className="inspector-field-label">Selected Study Item</span>
                        <EditableField
                          id={activeLearningItem.id}
                          field="name"
                          label="name"
                          kind="line"
                          value={activeLearningItem.name}
                          busy={learningItemBusy}
                          onSave={(v) => void wb.updateLearningItemFields(activeLp.id, activeLearningItem.id, { name: v as string })}
                        >
                          <h2 className="inspector-title" data-testid="inspector-title">
                            {activeLearningItem.name}
                          </h2>
                        </EditableField>
                      </div>
                    </div>

                    <div className="status-button-group" data-testid="status-button-group">
                      {(["not_started", "in_progress", "done"] as const).map((st) => (
                        <button
                          key={st}
                          type="button"
                          data-testid={`status-btn-${st}`}
                          className={
                            activeLearningItem.status === st
                              ? "status-btn active"
                              : "status-btn"
                          }
                          onClick={() =>
                            wb.setLearningItemStatus(
                              activeLp.id,
                              activeLearningItem.id,
                              st
                            )
                          }
                        >
                          {st.replace("_", " ")}
                        </button>
                      ))}
                    </div>
                  </div>

                  <EditableField
                    id={activeLearningItem.id}
                    field="summary"
                    label="summary"
                    kind="text"
                    value={activeLearningItem.summary ?? ""}
                    displayEmpty={!getItemShortDescription(activeLearningItem)}
                    busy={learningItemBusy}
                    onSave={(v) => void wb.updateLearningItemFields(activeLp.id, activeLearningItem.id, { summary: v as string })}
                  >
                    <div className="inspector-inset-card" data-testid="inspector-summary">
                      {getItemShortDescription(activeLearningItem)}
                    </div>
                  </EditableField>

                  {renderSection(
                    "Notes",
                    !!activeLearningItem.notes,
                    <EditableField
                      id={activeLearningItem.id}
                      field="notes"
                      label="notes"
                      kind="text"
                      value={activeLearningItem.notes ?? ""}
                      busy={learningItemBusy}
                      onSave={(v) => void wb.updateLearningItemFields(activeLp.id, activeLearningItem.id, { notes: v as string })}
                    >
                      <div className="inspector-inset-card" data-testid="inspector-notes">
                        {activeLearningItem.notes}
                      </div>
                    </EditableField>
                  )}

                  {activeMd && (
                    <div className="markdown-body" data-testid="inspector-markdown">
                      <ReactMarkdown>{activeMd}</ReactMarkdown>
                    </div>
                  )}
                </div>
              )}

              {/* Concise Topics & Subtopics List (§3) */}
              <div className="centre-phases-stack">
                {topics.map((top) => {
                  const isActiveTop = activeTopic?.id === top.id;
                  const subs = top.subtopics || [];
                  const topDesc = getItemShortDescription(top);

                  return (
                    <div
                      key={top.id}
                      className={`tier-1-phase-section ${
                        isActiveTop ? "active-phase-card" : ""
                      }`}
                      data-testid={`learning-topic-${top.id}`}
                    >
                      <div
                        className="tier-1-phase-header"
                        onClick={() => selectLearningPath(activeLp.id, top.id, null)}
                      >
                        <div className="tier-1-header-main">
                          <div className="tier-1-kicker-row">
                            <span className="tier-1-kicker-badge">TOPIC</span>
                            {renderLearningItemStatusMenu(activeLp.id, top)}
                            <span className="tier-1-phase-title">{top.name}</span>
                          </div>
                          {topDesc && (
                            <span className="tier-1-phase-summary">{topDesc}</span>
                          )}
                        </div>
                      </div>

                      {subs.length > 0 && (
                        <div className="tier-2-subtopics-Indent">
                          {subs.map((sub) => {
                            const isSubSelected = selectedSubtopicId === sub.id;
                            const isItemsOpen = !!expandedSubtopicItems[sub.id];
                            const childItems = wb.subtopicDetails(activePathId, sub);
                            const shortDesc = getItemShortDescription(sub);

                            return (
                              <div
                                key={sub.id}
                                className={`tier-2-subtopic-group ${
                                  isSubSelected ? "selected" : ""
                                }`}
                                data-testid={`learning-subtopic-${sub.id}`}
                              >
                                <div
                                  className="tier-2-subtopic-header"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    selectLearningPath(activeLp.id, top.id, sub.id);
                                  }}
                                >
                                  <div className="tier-2-header-left">
                                    {renderLearningItemStatusMenu(activeLp.id, sub)}
                                    <div className="tier-2-text-col">
                                      <span className="tier-2-subtopic-title">
                                        {sub.name}
                                      </span>
                                      {shortDesc && (
                                        <p className="tier-2-subtopic-desc">
                                          {shortDesc}
                                        </p>
                                      )}
                                    </div>
                                  </div>
                                  {childItems.length > 0 && (
                                    <button
                                      type="button"
                                      className="tree-chevron-btn sm"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setExpandedSubtopicItems((prev) => ({
                                          ...prev,
                                          [sub.id]: !isItemsOpen,
                                        }));
                                      }}
                                    >
                                      {isItemsOpen ? "⌄" : "›"}
                                    </button>
                                  )}
                                </div>

                                {isItemsOpen && childItems.length > 0 && (
                                  <div className="tier-3-items-list">
                                    {childItems.map((item) => (
                                      <div key={item.id} className="tier-3-item-row">
                                        <span className="tier-3-branch-line">└</span>
                                        <StatusNodeGlyph status={item.status} />
                                        <span className="tier-3-item-kind">
                                          {item.kindLabel}:
                                        </span>
                                        <span className="tier-3-item-text">
                                          {item.text}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="empty-pane-card">Select a learning path.</div>
          )}
        </section>

        {/* Draggable Divider 2 */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize content and diagram panels"
          className={`panel-resizer ${activeResizer === "right" ? "dragging" : ""}`}
          data-testid="panel-resizer-right"
          onMouseDown={(e) => startResizing("right", e)}
          onDoubleClick={resetPanelWidths}
        />

        {/* PANEL C: Curriculum Map + Adjustable Contextual Panel Beneath It (§2, §3, §13) */}
        <section
          className={`workspace-panel panel-c-visual ${
            mobilePanel === "visual" ? "resp-active" : ""
          }`}
          data-testid="learning-panel-visual"
        >
          <div className="panel-sticky-header">
            <span className="panel-header-title">Curriculum Map & Topic Context</span>
            {isCustomPanelWidths && (
              <button
                type="button"
                className="ghost-sm-btn"
                onClick={resetPanelWidths}
              >
                Reset layout
              </button>
            )}
          </div>

          <div className="panel-c-split-container">
            {activeLp ? (
              <>
                <div
                  className="panel-c-diagram-frame"
                  data-testid="panel-c-diagram-pane"
                  style={{
                    height: activeTopic ? panelWidths.diagramHeight : "100%",
                    minHeight: MIN_DIAGRAM_HEIGHT,
                  }}
                >
                  <VisualGraph
                    learningPath={activeLp}
                    selectedId={selectedSubtopicId || selectedTopicId}
                    selectedPhaseId={activeTopic?.id || null}
                    onSelectLearningItem={(item, parentTopic) => {
                      if (parentTopic && parentTopic.id !== item.id) {
                        selectLearningPath(activeLp.id, parentTopic.id, item.id);
                      } else {
                        selectLearningPath(activeLp.id, item.id, null);
                      }
                    }}
                  />
                </div>

                {activeTopic && (
                  <>
                    <div
                      role="separator"
                      aria-orientation="horizontal"
                      aria-label="Resize curriculum overview and contextual section"
                      className={`panel-resizer-horizontal ${
                        activeResizer === "horizontal" ? "dragging" : ""
                      }`}
                      data-testid="panel-resizer-horizontal"
                      onMouseDown={(e) => startResizing("horizontal", e)}
                      onDoubleClick={resetPanelWidths}
                    />

                    <div
                      className="visual-context-panel"
                      data-testid="visual-context-panel"
                    >
                      <div
                        data-testid="panel-c-scroll"
                        style={{
                          overflowY: "auto",
                          flex: 1,
                          minHeight: 0,
                          display: "flex",
                          flexDirection: "column",
                          gap: 10,
                        }}
                      >
                        <div className="visual-context-header">
                          <div className="visual-context-header-top">
                            <span className="tier-1-kicker-badge accent">
                              SELECTED TOPIC
                            </span>
                            {renderLearningItemStatusMenu(activeLp.id, activeTopic, "context-")}
                          </div>
                          <h3
                            className="visual-context-title"
                            data-testid="visual-context-title"
                          >
                            {activeTopic.name}
                          </h3>
                          {getItemShortDescription(activeTopic) && (
                            <p
                              className="visual-context-desc"
                              data-testid="visual-context-desc"
                            >
                              {getItemShortDescription(activeTopic)}
                            </p>
                          )}
                        </div>

                        <div className="visual-context-subtopics">
                          {(activeTopic.subtopics || []).map((sub) => {
                            const isSubSelected = selectedSubtopicId === sub.id;
                            const isExpanded = !!expandedContextSubtopics[sub.id];
                            const items = wb.subtopicDetails(activePathId, sub);
                            const shortDesc = getItemShortDescription(sub);

                            return (
                              <div
                                key={sub.id}
                                className={`tier-2-subtopic-group ${
                                  isSubSelected ? "selected" : ""
                                }`}
                              >
                                <div
                                  className={`tier-2-subtopic-header ${
                                    isSubSelected ? "selected" : ""
                                  }`}
                                  data-testid={`visual-context-subtopic-${sub.id}`}
                                  onClick={() =>
                                    selectLearningPath(
                                      activeLp.id,
                                      activeTopic.id,
                                      sub.id
                                    )
                                  }
                                >
                                  <div className="tier-2-header-left">
                                    {renderLearningItemStatusMenu(activeLp.id, sub, "context-")}
                                    <div className="tier-2-text-col">
                                      <span className="tier-2-subtopic-title">
                                        {sub.name}
                                      </span>
                                      {shortDesc && (
                                        <p className="tier-2-subtopic-desc">
                                          {shortDesc}
                                        </p>
                                      )}
                                    </div>
                                  </div>
                                  {items.length > 0 && (
                                    <button
                                      type="button"
                                      className="tree-chevron-btn sm"
                                      data-testid={`visual-context-expand-${sub.id}`}
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setExpandedContextSubtopics((prev) => ({
                                          ...prev,
                                          [sub.id]: !isExpanded,
                                        }));
                                      }}
                                    >
                                      {isExpanded ? "⌄" : "›"}
                                    </button>
                                  )}
                                </div>

                                {isExpanded && items.length > 0 && (
                                  <div
                                    className="tier-3-items-list"
                                    data-testid={`visual-context-items-${sub.id}`}
                                  >
                                    {items.map((item) => (
                                      <div key={item.id} className="tier-3-item-row">
                                        <span className="tier-3-branch-line">└</span>
                                        <StatusNodeGlyph status={item.status} />
                                        <span className="tier-3-item-kind">
                                          {item.kindLabel}:
                                        </span>
                                        <span className="tier-3-item-text">
                                          {item.text}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </>
            ) : null}
          </div>
        </section>
      </div>
    );
  };

  const isSplitWorkspace =
    route.page === "building" ||
    route.page === "project" ||
    route.page === "learning" ||
    route.page === "learning-path";

  return (
    <div className="workbench-layout">
      {/* Left Sidebar — Compact, Collapsible, Home / Learning / Building only (§3) */}
      <aside
        className={sidebarCollapsed ? "sidebar collapsed" : "sidebar"}
        data-testid="left-sidebar"
      >
        <div className="sidebar-top-row">
          <div className="wordmark" onClick={() => navigate({ page: "home" })}>
            <span className="wordmark-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" style={{ stroke: "var(--accent)" }} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="12 2 2 7 12 12 22 7 12 2" />
                <polyline points="2 17 12 22 22 17" />
                <polyline points="2 12 12 17 22 12" />
              </svg>
            </span>
            {!sidebarCollapsed && <span className="wordmark-text">Workbench</span>}
          </div>
          <button
            type="button"
            className="sidebar-collapse-btn"
            data-testid="sidebar-collapse-toggle"
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => setSidebarCollapsed((c) => !c)}
          >
            {sidebarCollapsed ? "»" : "«"}
          </button>
        </div>

        <nav className="nav-primary">
          <button
            type="button"
            data-testid="nav-home"
            className={route.page === "home" ? "nav-item active" : "nav-item"}
            onClick={() => navigate({ page: "home" })}
            title="Home"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              <polyline points="9 22 9 12 15 12 15 22" />
            </svg>
            {!sidebarCollapsed && <span>Home</span>}
          </button>

          <button
            type="button"
            data-testid="nav-learning"
            className={
              route.page === "learning" || route.page === "learning-path"
                ? "nav-item active"
                : "nav-item"
            }
            onClick={() => navigate({ page: "learning" })}
            title="Learning"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
              <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
            </svg>
            {!sidebarCollapsed && <span>Learning</span>}
          </button>

          <button
            type="button"
            data-testid="nav-building"
            className={
              route.page === "building" || route.page === "project"
                ? "nav-item active"
                : "nav-item"
            }
            onClick={() => navigate({ page: "building" })}
            title="Building"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
              <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
              <line x1="12" y1="22.08" x2="12" y2="12" />
            </svg>
            {!sidebarCollapsed && <span>Building</span>}
          </button>
        </nav>

        {!sidebarCollapsed && (
          <div className="sidebar-scroll-tree">
            <div className="sidebar-section-label">Projects</div>
            <div className="sidebar-shortcut-list">
              {sidebarProjects.map((p) => {
                const isProjActive = route.page === "project" && route.id === p.id;
                const isTreeOpen = !!sidebarExpandedProjects[p.id] || isProjActive;
                const pGroups = getProjectGroups(p);

                return (
                  <div key={p.id} className="sidebar-tree-group">
                    <div
                      className={
                        isProjActive
                          ? "sidebar-shortcut-row active"
                          : "sidebar-shortcut-row"
                      }
                    >
                      <button
                        type="button"
                        className="sidebar-expand-btn"
                        aria-label={
                          isTreeOpen ? "Collapse phases" : "Expand phases"
                        }
                        onClick={(e) => {
                          e.stopPropagation();
                          setSidebarExpandedProjects((prev) => ({
                            ...prev,
                            [p.id]: !isTreeOpen,
                          }));
                        }}
                      >
                        {pGroups.length > 0 ? (isTreeOpen ? "⌄" : "›") : "·"}
                      </button>
                      <button
                        type="button"
                        className={
                          isProjActive
                            ? "sidebar-shortcut active"
                            : "sidebar-shortcut"
                        }
                        onClick={() => selectProject(p.id)}
                        title={p.name}
                      >
                        <span className="sidebar-truncate">{p.name}</span>
                      </button>
                    </div>

                    {isTreeOpen && pGroups.length > 0 && (
                      <div className="sidebar-nested-phases">
                        {pGroups.map((ph) => {
                          const isPhSelected =
                            isProjActive && selectedPhaseId === ph.id;
                          return (
                            <button
                              key={ph.id}
                              type="button"
                              className={
                                isPhSelected
                                  ? "sidebar-phase-link active"
                                  : "sidebar-phase-link"
                              }
                              onClick={() => selectProject(p.id, ph.id, null)}
                              title={ph.name}
                            >
                              <span className="sidebar-truncate">{ph.name}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="sidebar-section-label" style={{ marginTop: 14 }}>
              Learning
            </div>
            <div className="sidebar-shortcut-list">
              {sidebarLearning.map((l) => {
                const isLpActive =
                  route.page === "learning-path" && route.id === l.id;
                return (
                  <button
                    key={l.id}
                    type="button"
                    className={
                      isLpActive
                        ? "sidebar-shortcut active"
                        : "sidebar-shortcut"
                    }
                    onClick={() => selectLearningPath(l.id)}
                    title={l.name}
                  >
                    <span className="sidebar-truncate">{l.name}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </aside>

      {/* Main Content Area */}
      <div className="main-column">
        <header className="top-search-header">
          <div className="search-box-wrapper">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" style={{ stroke: "var(--text-muted)" }} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              ref={filterInputRef}
              type="text"
              className="filter-input"
              placeholder="Filter projects and learning paths... (press /)"
              value={filterText}
              onChange={(e) => setFilter(e.target.value)}
              data-testid="filter-input"
            />
            {filterText && (
              <button
                type="button"
                className="clear-search-btn"
                onClick={() => setFilter("")}
                aria-label="Clear filter"
              >
                ×
              </button>
            )}
          </div>
        </header>

        <div className="content-and-inspector">
          <main
            className={isSplitWorkspace ? "main-scroll split-mode" : "main-scroll"}
          >
            {route.page === "home" && renderHomeView()}
            {route.page === "building" && renderBuildingWorkspace()}
            {route.page === "project" && renderBuildingWorkspace()}
            {route.page === "learning" && renderLearningWorkspace()}
            {route.page === "learning-path" && renderLearningWorkspace()}
          </main>
        </div>

        <footer className="footer-bar" data-testid="footer-bar">
          {syncStatus.last_error && (
            <div className="footer-amber-banner" data-testid="failed-sync-banner">
              <div>
                <span>
                  Latest update failed validation — showing previous version
                </span>
                <button
                  type="button"
                  onClick={() => setErrorExpanded(!errorExpanded)}
                  style={{ marginLeft: 8, padding: "2px 6px", fontSize: 11 }}
                  data-testid="toggle-error-details"
                >
                  {errorExpanded ? "Hide" : "Details"}
                </button>
              </div>
              {errorExpanded && (
                <div
                  style={{ fontSize: 11, color: "var(--danger)" }}
                  data-testid="error-details-box"
                >
                  Commit {syncStatus.last_error.commit.slice(0, 7)} (
                  {syncStatus.last_error.file}): {syncStatus.last_error.message}
                </div>
              )}
            </div>
          )}

          <div className="footer-main-row">
            <span>
              {snapshot.commit ? (
                <>
                  Synced · commit <code>{snapshot.commit.slice(0, 7)}</code>
                </>
              ) : (
                "Waiting for initial sync..."
              )}
            </span>
            <a
              href="#sync"
              onClick={(e) => {
                e.preventDefault();
                void wb.sync();
              }}
              data-testid="check-updates-button"
            >
              Check for updates
            </a>
          </div>
        </footer>
      </div>

      {toastError && (
        <div className="toast-error" data-testid="toast-error">
          {toastError}
        </div>
      )}
    </div>
  );
}
