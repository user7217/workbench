/**
 * The selected-item detail cards ("inspector") of the centre pane: one for a project component
 * (editable fields, structure controls, prerequisites / used by) and one for a learning
 * topic/subtopic (editable fields, status buttons, markdown notes).
 */
import type React from "react";
import ReactMarkdown from "react-markdown";
import { useWorkbench, getItemShortDescription, pendingKeys, type Project } from "@workbench-core";
import { AddItemInline, EditableField } from "./Editing";
import { StatusNodeGlyph } from "./status";
import { ItemStructureActions } from "./StructureControls";

/** An inspector section with a label when it has content; otherwise just its "+ Add …" control. */
function InspectorSection({
  label,
  hasContent,
  children,
}: {
  label: string;
  hasContent: boolean;
  children: React.ReactNode;
}) {
  return hasContent ? (
    <div className="inspector-section-box">
      <span className="inspector-field-label">{label}</span>
      {children}
    </div>
  ) : (
    children
  );
}

// Selected Item Detail Card (shown on demand when an item is explicitly opened)
export function ComponentInspector({ project: activeProject }: { project: Project }) {
  const wb = useWorkbench();
  const { isPending, selectProject, clearComponentSelection } = wb;
  const activeComponentEntry = wb.activeComponent;
  if (!activeComponentEntry) return null;
  const structureBusy = isPending(pendingKeys.structure(activeProject.id));
  const compFieldsBusy = isPending(
    pendingKeys.itemFields(activeProject.id, activeComponentEntry.comp.id),
  );

  return (
    <div className="selected-item-detail-card" data-testid="inspector-panel">
      <div className="inspector-header">
        <div className="inspector-header-left">
          <StatusNodeGlyph status={activeComponentEntry.comp.status || "not_started"} />
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
              onSave={(v) =>
                void wb.updateItemFields(activeProject.id, activeComponentEntry.comp.id, {
                  name: v as string,
                })
              }
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
        <ItemStructureActions
          project={activeProject}
          item={activeComponentEntry.comp}
          isPhase={false}
        />
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
        onSave={(v) =>
          void wb.updateItemFields(activeProject.id, activeComponentEntry.comp.id, {
            summary: v as string,
          })
        }
      >
        <div className="inspector-inset-card" data-testid="inspector-summary">
          {getItemShortDescription(activeComponentEntry.comp)}
        </div>
      </EditableField>

      <InspectorSection label="Implementation Notes" hasContent={!!activeComponentEntry.comp.notes}>
        <EditableField
          id={activeComponentEntry.comp.id}
          field="notes"
          label="notes"
          kind="text"
          value={activeComponentEntry.comp.notes ?? ""}
          busy={compFieldsBusy}
          onSave={(v) =>
            void wb.updateItemFields(activeProject.id, activeComponentEntry.comp.id, {
              notes: v as string,
            })
          }
        >
          <div className="inspector-inset-card" data-testid="inspector-notes">
            {activeComponentEntry.comp.notes}
          </div>
        </EditableField>
      </InspectorSection>

      <InspectorSection
        label="Checklist"
        hasContent={(activeComponentEntry.comp.items || []).length > 0}
      >
        <EditableField
          id={activeComponentEntry.comp.id}
          field="items"
          label="checklist items"
          kind="list"
          value={activeComponentEntry.comp.items ?? []}
          busy={compFieldsBusy}
          onSave={(v) =>
            void wb.updateItemFields(activeProject.id, activeComponentEntry.comp.id, {
              items: v as string[],
            })
          }
        >
          <ul className="inspector-items-list" data-testid="inspector-items">
            {(activeComponentEntry.comp.items || []).map((it, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: a fixed, display-only list (items may repeat)
              <li key={i}>{it}</li>
            ))}
          </ul>
        </EditableField>
      </InspectorSection>

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
                      onClick={() => selectProject(activeProject.id, d.phase.id, d.comp.id)}
                    >
                      <StatusNodeGlyph status={d.comp.status || "not_started"} />
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
                      onClick={() => selectProject(activeProject.id, d.phase.id, d.comp.id)}
                    >
                      <StatusNodeGlyph status={d.comp.status || "not_started"} />
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
  );
}

// Active Study Item Detail Card
export function LearningItemInspector() {
  const wb = useWorkbench();
  const {
    isPending,
    activeLearningPath: activeLp,
    activeLearningItem,
    activeMarkdown: activeMd,
  } = wb;
  if (!activeLp || !activeLearningItem) return null;
  const learningItemBusy = isPending(
    pendingKeys.learningItemFields(activeLp.id, activeLearningItem.id),
  );

  return (
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
              onSave={(v) =>
                void wb.updateLearningItemFields(activeLp.id, activeLearningItem.id, {
                  name: v as string,
                })
              }
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
              className={activeLearningItem.status === st ? "status-btn active" : "status-btn"}
              onClick={() => wb.setLearningItemStatus(activeLp.id, activeLearningItem.id, st)}
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
        onSave={(v) =>
          void wb.updateLearningItemFields(activeLp.id, activeLearningItem.id, {
            summary: v as string,
          })
        }
      >
        <div className="inspector-inset-card" data-testid="inspector-summary">
          {getItemShortDescription(activeLearningItem)}
        </div>
      </EditableField>

      <InspectorSection label="Notes" hasContent={!!activeLearningItem.notes}>
        <EditableField
          id={activeLearningItem.id}
          field="notes"
          label="notes"
          kind="text"
          value={activeLearningItem.notes ?? ""}
          busy={learningItemBusy}
          onSave={(v) =>
            void wb.updateLearningItemFields(activeLp.id, activeLearningItem.id, {
              notes: v as string,
            })
          }
        >
          <div className="inspector-inset-card" data-testid="inspector-notes">
            {activeLearningItem.notes}
          </div>
        </EditableField>
      </InspectorSection>

      {activeMd && (
        <div className="markdown-body" data-testid="inspector-markdown">
          <ReactMarkdown>{activeMd}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}
