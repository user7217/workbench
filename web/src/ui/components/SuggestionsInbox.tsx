/**
 * The project's suggestions inbox in the default look: suggested next steps filed by an agent, each
 * with Keep (adds it to the tree as a not-started item) and Dismiss. Both are optimistic for the
 * row; a refused keep (e.g. its parent item was deleted) puts the row back and shows `lastError`.
 */
import {
  useWorkbench,
  locateProjectItem,
  pendingKeys,
  projectUsesPhases,
  type Project,
  type Suggestion,
} from "@workbench-core";

/** "under <item name>", "new phase"/"new topic", or a note that the target is gone. */
function placementLabel(project: Project, suggestion: Suggestion): { text: string; missing: boolean } {
  if (!suggestion.under) {
    return { text: projectUsesPhases(project) ? "new phase" : "new topic", missing: false };
  }
  const loc = locateProjectItem(project, suggestion.under);
  if (!loc) return { text: `under ${suggestion.under} (no longer exists)`, missing: true };
  const name = loc.kind === "phase" ? loc.phase.name : loc.entry.comp.name;
  return { text: `under ${name}`, missing: false };
}

export function SuggestionsInbox({ project }: { project: Project }) {
  const wb = useWorkbench();
  const suggestions = project.suggestions ?? [];
  if (suggestions.length === 0) return null;

  return (
    <div className="related-refs-block suggestions-inbox" data-testid="suggestions-inbox">
      <span className="related-refs-label">Inbox · {suggestions.length}</span>
      {suggestions.map((s) => {
        const busy = wb.isPending(pendingKeys.suggestion(project.id, s.id));
        const where = placementLabel(project, s);
        return (
          <div key={s.id} className="suggestion-row" data-testid={`suggestion-${s.id}`}>
            <div className="suggestion-text">
              <span className="suggestion-name">{s.name}</span>
              {s.summary && <span className="suggestion-summary">{s.summary}</span>}
              <span
                className={where.missing ? "suggestion-where missing" : "suggestion-where"}
                data-testid={`suggestion-where-${s.id}`}
              >
                {where.text}
              </span>
            </div>
            <div className="item-actions">
              <button
                type="button"
                className="item-action-btn text"
                data-testid={`suggestion-keep-${s.id}`}
                disabled={busy || where.missing}
                title={where.missing ? "Its target item no longer exists; dismiss it instead" : "Add to the project"}
                onClick={() => void wb.keepSuggestion(project.id, s.id)}
              >
                Keep
              </button>
              <button
                type="button"
                className="item-action-btn text danger"
                data-testid={`suggestion-dismiss-${s.id}`}
                disabled={busy}
                title="Drop this suggestion"
                onClick={() => void wb.dismissSuggestion(project.id, s.id)}
              >
                Dismiss
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
