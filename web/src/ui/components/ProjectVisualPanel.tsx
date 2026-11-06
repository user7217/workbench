/**
 * Panel C of the project workspace: the visual map of the project and, when a phase is selected,
 * a resizable context panel beneath it with that phase's component cards.
 */
import {
  useWorkbench,
  getGroupChildren,
  getItemShortDescription,
  isNewDiscoveredProject,
  projectUsesPhases,
} from "@workbench-core";
import { MIN_DIAGRAM_HEIGHT } from "../layoutPrefs";
import { useLook } from "../lookState";
import { ChildProjectRows } from "./ChildProjects";
import { ContextComp } from "./ComponentCards";
import { ItemStatusMenu } from "./ItemStatusMenu";
import { PanelResizer } from "./PanelResizer";
import { VisualGraph } from "./VisualGraph";

// PANEL C: Visual Project Overview + Adjustable Contextual Panel Beneath It
export function ProjectVisualPanel() {
  const { selection, childIndex, selectProject, openProjectAt, activeProject, activePhase } =
    useWorkbench();
  const {
    viewMode,
    toggleProjectViewMode,
    panelWidths,
    isCustomPanelWidths,
    resetPanelWidths,
    mobilePanel,
  } = useLook();
  const selectedPhaseId = selection.phaseId;
  const selectedComponentId = selection.componentId;
  const usesPhases = activeProject ? projectUsesPhases(activeProject) : true;
  const isEmptyDiscovered = activeProject ? isNewDiscoveredProject(activeProject) : false;

  return (
    <section
      className={`workspace-panel panel-c-visual ${mobilePanel === "visual" ? "resp-active" : ""}`}
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
              <span className={`seg-indicator ${viewMode === "list" ? "active" : ""}`}>
                List View
              </span>
              <span className={`seg-indicator ${viewMode === "visual" ? "active" : ""}`}>
                Visual View
              </span>
              <button
                type="button"
                className="primary-toggle"
                data-testid="view-mode-toggle"
                onClick={toggleProjectViewMode}
              >
                {viewMode === "list" ? "Switch to visual view" : "Switch to list view"}
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
                onSelectPhase={(phase) => selectProject(activeProject.id, phase.id, null)}
                onSelectComponent={(component, phase) =>
                  selectProject(activeProject.id, phase.id, component.id)
                }
                childProjects={childIndex.get(activeProject.id)}
                onOpenProject={openProjectAt}
              />
            </div>

            {activePhase && (
              <>
                {/* Draggable Horizontal Divider Between Visual Overview and Context Section */}
                <PanelResizer
                  which="horizontal"
                  label="Resize visual overview and contextual section"
                  title="Drag to resize visual overview and contextual section (double-click to reset)"
                />

                {/* Independently Scrollable Contextual Panel Beneath Visual Overview */}
                <div className="visual-context-panel" data-testid="visual-context-panel">
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
                        <ItemStatusMenu
                          projectId={activeProject.id}
                          item={activePhase}
                          testIdPrefix="context-"
                        />
                      </div>
                      <h3 className="visual-context-title" data-testid="visual-context-title">
                        {activePhase.name}
                      </h3>
                      {getItemShortDescription(activePhase) && (
                        <p className="visual-context-desc" data-testid="visual-context-desc">
                          {getItemShortDescription(activePhase)}
                        </p>
                      )}
                    </div>

                    {/* Concise Subtopics (name + 1-2 line description; items shown on demand) */}
                    <div className="visual-context-subtopics">
                      <ChildProjectRows
                        parentId={activeProject.id}
                        slotId={activePhase.id}
                        testIdPrefix="visual-context-"
                      />
                      {getGroupChildren(activePhase).map((comp) => (
                        <ContextComp
                          key={comp.id}
                          projectId={activeProject.id}
                          ph={activePhase}
                          comp={comp}
                          depth={0}
                        />
                      ))}
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
  );
}
