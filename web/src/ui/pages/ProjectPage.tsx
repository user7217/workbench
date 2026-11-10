/**
 * The project workspace ("building" / "project" routes): navigation tree, the selected project's
 * contents with the component inspector, and the visual map with its context panel.
 */
import {
  useWorkbench,
  isNewDiscoveredProject,
  projectUsesPhases,
  pendingKeys,
} from "@workbench-core";
import { AddItemInline } from "../components/Editing";
import { ComponentInspector } from "../components/Inspector";
import { PanelResizer } from "../components/PanelResizer";
import { PanelTabs } from "../components/PanelTabs";
import { PhaseList } from "../components/PhaseList";
import { ProjectHeader } from "../components/ProjectHeader";
import { ProjectNavPanel } from "../components/ProjectNavPanel";
import { ProjectVisualPanel } from "../components/ProjectVisualPanel";
import { MAX_CONTENTS_WIDTH, MIN_CONTENTS_WIDTH } from "../layoutPrefs";
import { useLook } from "../lookState";

// Building Workspace — Three Coordinated Resizable Panels + Optional Phases + Adjustable Context Panel
export function ProjectPage() {
  const wb = useWorkbench();
  const { isPending, selectProject, activeProject, activePhase } = wb;
  const { panelWidths, mobilePanel } = useLook();
  const usesPhases = activeProject ? projectUsesPhases(activeProject) : true;
  const isEmptyDiscovered = activeProject ? isNewDiscoveredProject(activeProject) : false;
  const activeComponentEntry = wb.activeComponent;
  const structureBusy = activeProject ? isPending(pendingKeys.structure(activeProject.id)) : false;

  return (
    <div className="three-panel-workspace" data-testid="building-three-panel-workspace">
      <PanelTabs />

      <ProjectNavPanel />

      {/* Draggable Divider 1 (Between Panel A and Panel B) */}
      <PanelResizer
        which="left"
        label="Resize navigation and content panels"
        title="Drag to resize navigation and content panels (double-click to reset)"
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
            <ProjectHeader project={activeProject} />

            <ComponentInspector project={activeProject} />

            {isEmptyDiscovered ? (
              <div className="empty-discovered-card" data-testid="empty-discovered-state">
                <p style={{ margin: "0 0 8px 0", color: "var(--text-secondary)" }}>
                  Discovered repository — structure has not been populated yet.
                </p>
                <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
                  Open a coding agent session in this repo and run <code>/workbench</code> to
                  populate its breakdown.
                </p>
              </div>
            ) : (
              <PhaseList project={activeProject} />
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
          <div className="empty-pane-card">Select a project from the navigation panel.</div>
        )}
      </section>

      {/* Draggable Divider 2 (Between Panel B and Panel C) */}
      <PanelResizer
        which="right"
        label="Resize content and diagram panels"
        title="Drag to resize content and diagram panels (double-click to reset)"
      />

      <ProjectVisualPanel />
    </div>
  );
}
