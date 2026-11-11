/**
 * Panel A of the project workspace: the navigation tree of projects, their phases (or topics),
 * components with nested parts, and child projects listed inside their parent.
 */
import {
  useWorkbench,
  getProjectGroups,
  getGroupChildren,
  isNewDiscoveredProject,
  projectUsesPhases,
  ROOT_SLOT,
  STATUS_LABELS,
  type Project,
  type Phase,
  type Component,
} from "@workbench-core";
import { MAX_NAV_WIDTH, MIN_NAV_WIDTH } from "../layoutPrefs";
import { useLook } from "../lookState";
import { KindBadge, PhaseNumberBadge, StatusNodeGlyph } from "./status";
import { revealId } from "../revealSelection";

/** Left tree: a child project listed inside its parent. */
function TreeChildProject({ child, depth }: { child: Project; depth: number }) {
  const { activeProject, selectProject } = useWorkbench();
  return (
    <div
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
}

/** Left tree: a component row, then (when open) its nested components and child projects. */
function TreeComp({
  p,
  ph,
  comp,
  depth,
  isSelectedProject,
}: {
  p: Project;
  ph: Phase;
  comp: Component;
  depth: number;
  isSelectedProject: boolean;
}) {
  const {
    selection,
    isExpanded: expandedIn,
    setExpanded,
    childProjectsAt,
    selectProject,
  } = useWorkbench();
  const isCompActive = isSelectedProject && selection.componentId === comp.id;
  const nested = comp.components || [];
  const kidProjects = childProjectsAt(p.id, comp.id);
  const hasMore = nested.length > 0 || kidProjects.length > 0;
  const open = expandedIn("nestedComponents", comp.id);
  return (
    <>
      <div
        className={`tree-comp-row ${isCompActive ? "active" : ""}`}
        data-testid={`nav-tree-comp-${comp.id}`}
        data-reveal-id={revealId(p.id, comp.id)}
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
              setExpanded("nestedComponents", comp.id, !open);
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
      {open &&
        nested.map((c) => (
          <TreeComp
            key={c.id}
            p={p}
            ph={ph}
            comp={c}
            depth={depth + 1}
            isSelectedProject={isSelectedProject}
          />
        ))}
      {open &&
        kidProjects.map((c) => (
          <TreeChildProject key={`child:${c.id}`} child={c} depth={depth + 1} />
        ))}
    </>
  );
}

// PANEL A: Project / Topic / Subtopic Navigation Tree (Left)
export function ProjectNavPanel() {
  const wb = useWorkbench();
  const {
    selection,
    showArchived: buildingArchivedOpen,
    setShowArchived,
    isExpanded: expandedIn,
    setExpanded,
    listedProjects,
    childProjectsAt,
    selectProject,
    activeProject,
    activePhase,
  } = wb;
  const { panelWidths, isCustomPanelWidths, resetPanelWidths, mobilePanel } = useLook();
  const selectedComponentId = selection.componentId;
  // Same visibility rule as Home: non-active child projects are listed inside their parent
  const navProjects = listedProjects;
  const activeProjectId = activeProject?.id || "";
  const usesPhases = activeProject ? projectUsesPhases(activeProject) : true;

  return (
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
                    {rootChildren.map((c) => (
                      <TreeChildProject key={`child:${c.id}`} child={c} depth={0} />
                    ))}
                    {pGroups.map((ph, idx) => {
                      const isPhaseActive = isSelected && activePhase?.id === ph.id;
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
                            data-reveal-id={revealId(p.id, ph.id)}
                            onClick={(e) => {
                              e.stopPropagation();
                              selectProject(p.id, ph.id, null);
                            }}
                          >
                            <button
                              type="button"
                              className="tree-chevron-btn sm"
                              aria-label={
                                isPhaseExpanded ? "Collapse subtopics" : "Expand subtopics"
                              }
                              onClick={(e) => {
                                e.stopPropagation();
                                setExpanded("phases", ph.id, !isPhaseExpanded);
                              }}
                            >
                              {hasPhaseKids ? (isPhaseExpanded ? "⌄" : "›") : "·"}
                            </button>
                            {pUsesPhases && (
                              <PhaseNumberBadge
                                index={idx}
                                status={ph.status || "not_started"}
                                size={18}
                              />
                            )}
                            <span className="tree-phase-name" title={ph.name}>
                              {ph.name}
                            </span>
                            <StatusNodeGlyph status={ph.status || "not_started"} />
                          </div>

                          {isPhaseExpanded && hasPhaseKids && (
                            <div className="tree-comp-children">
                              {comps.map((comp) => (
                                <TreeComp
                                  key={comp.id}
                                  p={p}
                                  ph={ph}
                                  comp={comp}
                                  depth={0}
                                  isSelectedProject={isSelected}
                                />
                              ))}
                              {phaseChildren.map((c) => (
                                <TreeChildProject key={`child:${c.id}`} child={c} depth={0} />
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
    </section>
  );
}
