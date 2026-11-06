/**
 * The project summary header of the centre pane: editable name and description, "Part of"
 * breadcrumb and control, status pill, links, related learning, sub-projects, the suggestions
 * inbox and the phase selector strip.
 */
import React from "react";
import {
  useWorkbench,
  getProjectGroups,
  projectUsesPhases,
  ROOT_SLOT,
  ENTITY_STATUSES,
  pendingKeys,
  type Project,
} from "@workbench-core";
import { ChildProjectRows } from "./ChildProjects";
import { EditableField } from "./Editing";
import { StatusMenu } from "./StatusMenu";
import { KindBadge, StatusNodeGlyph } from "./status";
import { ParentControl } from "./StructureControls";
import { SuggestionsInbox } from "./SuggestionsInbox";

// Project Summary Header (no status badge)
export function ProjectHeader({ project: activeProject }: { project: Project }) {
  const wb = useWorkbench();
  const {
    isPending,
    childProjectsAt,
    selectProject,
    selectLearningPath,
    activePhase,
    activeParent: parentRef,
  } = wb;
  const usesPhases = projectUsesPhases(activeProject);
  const groups = getProjectGroups(activeProject);
  const relatedLearning = wb.relatedLearning(activeProject);

  return (
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
          {(activeProject.links || []).map((lnk) => (
            <a
              key={`${lnk.label}|${lnk.url}`}
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
          <ChildProjectRows parentId={activeProject.id} slotId={ROOT_SLOT} />
        </div>
      )}

      <SuggestionsInbox project={activeProject} />

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
                <span>{usesPhases ? `${idx + 1}. ${ph.name}` : ph.name}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
