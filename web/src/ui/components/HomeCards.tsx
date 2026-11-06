/**
 * The Home cards of the default look: one per project and one per learning path, with a
 * "Read more" toggle for long descriptions and a chip per phase/topic that opens it.
 */
import {
  useWorkbench,
  getProjectGroups,
  getItemShortDescription,
  isNewDiscoveredProject,
  type Project,
  type LearningPath,
} from "@workbench-core";
import { useLook } from "../lookState";
import { KindBadge, StatusNodeGlyph } from "./status";

// Project card for Home (no status badge, except New on unpopulated discovered repos)
export function UnifiedProjectCard({ project: p }: { project: Project }) {
  const { selectProject } = useWorkbench();
  const { expandedCardDescs, setExpandedCardDescs } = useLook();
  const isNew = isNewDiscoveredProject(p);
  const repoNames = (p.repos || []).map((r) => r.name).join(", ");
  const groups = getProjectGroups(p);
  const isDescExpanded = !!expandedCardDescs[p.id];
  const hasLongDesc = (p.description || "").length > 110;
  const suggestionCount = (p.suggestions || []).length;

  return (
    <div
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

        {(isNew || suggestionCount > 0) && (
          <div className="unified-card-status">
            {suggestionCount > 0 && (
              <span
                className="badge-suggestions"
                data-testid={`suggestions-badge-${p.id}`}
                title="Suggested next steps waiting in this project's inbox"
              >
                {suggestionCount} {suggestionCount === 1 ? "suggestion" : "suggestions"}
              </span>
            )}
            {isNew && (
              <span className="badge-new" data-testid={`new-badge-${p.id}`}>
                New
              </span>
            )}
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
}

// Learning card for Home (no status badge)
export function UnifiedLearningCard({ learningPath: lp }: { learningPath: LearningPath }) {
  const { selectLearningPath } = useWorkbench();
  const { expandedCardDescs, setExpandedCardDescs } = useLook();
  const topics = lp.topics || [];
  const isDescExpanded = !!expandedCardDescs[lp.id];
  const hasLongDesc = (lp.description || "").length > 110;

  return (
    <div
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
}
