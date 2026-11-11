/**
 * The centre list of a project: one section per phase (or direct topic) with its editable name,
 * summary and notes, its child projects and component cards, "+ Add component" and the phase's
 * structure controls.
 */
import {
  useWorkbench,
  getProjectGroups,
  getGroupChildren,
  getItemShortDescription,
  projectUsesPhases,
  pendingKeys,
  type Project,
} from "@workbench-core";
import { useLook } from "../lookState";
import { ChildProjectRows } from "./ChildProjects";
import { CentreComp } from "./ComponentCards";
import { AddItemInline, EditableField } from "./Editing";
import { ItemStatusMenu } from "./ItemStatusMenu";
import { ItemStructureActions } from "./StructureControls";
import { revealId } from "../revealSelection";

export function PhaseList({ project: activeProject }: { project: Project }) {
  const wb = useWorkbench();
  const { isPending, childProjectsAt, selectProject, activePhase } = wb;
  const { collapsedPhases, setCollapsedPhases } = useLook();
  const usesPhases = projectUsesPhases(activeProject);
  const groups = getProjectGroups(activeProject);
  const structureBusy = isPending(pendingKeys.structure(activeProject.id));

  return (
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
            className={`tier-1-phase-section ${isActivePh ? "active-phase-card" : ""}`}
            data-testid={`phase-block-${ph.id}`}
            data-reveal-id={revealId(activeProject.id, ph.id)}
          >
            {/* LEVEL 1: Phase (when uses_phases is true) or Direct Topic (when uses_phases is false) */}
            <div
              className="tier-1-phase-header"
              onClick={() => selectProject(activeProject.id, ph.id, null)}
            >
              <div className="tier-1-header-main">
                <div className="tier-1-kicker-row">
                  <span className="tier-1-kicker-badge">
                    {usesPhases ? `PHASE ${String(idx + 1).padStart(2, "0")}` : "TOPIC"}
                  </span>
                  <ItemStatusMenu projectId={activeProject.id} item={ph} />
                  <EditableField
                    id={ph.id}
                    field="name"
                    label={`${usesPhases ? "phase" : "topic"} name`}
                    kind="line"
                    value={ph.name}
                    busy={phBusy}
                    onSave={(v) =>
                      void wb.updateItemFields(activeProject.id, ph.id, {
                        name: v as string,
                      })
                    }
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
                  onSave={(v) =>
                    void wb.updateItemFields(activeProject.id, ph.id, {
                      summary: v as string,
                    })
                  }
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
                    onSave={(v) =>
                      void wb.updateItemFields(activeProject.id, ph.id, {
                        notes: v as string,
                      })
                    }
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

            {!isCollapsed &&
              (comps.length > 0 || childProjectsAt(activeProject.id, ph.id).length > 0) && (
                /* LEVEL 2: Concise Subtopic Cards (name + 1-2 line description; extra details on demand) */
                <div className="tier-2-subtopics-Indent">
                  <ChildProjectRows parentId={activeProject.id} slotId={ph.id} />
                  {comps.map((comp) => (
                    <CentreComp
                      key={comp.id}
                      projectId={activeProject.id}
                      ph={ph}
                      comp={comp}
                      depth={0}
                    />
                  ))}
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
                    const id = await wb.addItem(activeProject.id, {
                      parentId: ph.id,
                      name,
                    });
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
  );
}
