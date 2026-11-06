/**
 * The learning path header of the centre pane: editable name and description, status pill,
 * related projects and the topic selector strip.
 */
import { useWorkbench, ENTITY_STATUSES, pendingKeys, type LearningPath } from "@workbench-core";
import { EditableField } from "./Editing";
import { StatusMenu } from "./StatusMenu";
import { KindBadge, StatusNodeGlyph } from "./status";

// Learning Path Header — status badges removed per §1
export function LearningHeader({ learningPath: activeLp }: { learningPath: LearningPath }) {
  const wb = useWorkbench();
  const { isPending, selectProject, selectLearningPath, activeTopic } = wb;
  const topics = activeLp.topics || [];
  const relatedProjects = wb.relatedProjects(activeLp);

  return (
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
            ),
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
  );
}
