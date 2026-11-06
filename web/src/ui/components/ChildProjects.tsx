/**
 * Child projects listed inside their parent: a row per child (click to show its own phases and
 * components inline, "Open" to go to it), used in the centre list, the context panel and the
 * project header. Expansion is the core's "childProjects" set.
 */
import {
  useWorkbench,
  getProjectGroups,
  getGroupChildren,
  STATUS_LABELS,
  type Project,
  type Component,
} from "@workbench-core";
import { KindBadge, StatusNodeGlyph } from "./status";

function InlineComponents({
  ownerId,
  comps,
  depth,
}: {
  ownerId: string;
  comps: Component[];
  depth: number;
}) {
  const { openProjectAt } = useWorkbench();
  return comps.length === 0 ? null : (
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
          <InlineComponents ownerId={ownerId} comps={c.components ?? []} depth={depth + 1} />
          <ChildProjectRows parentId={ownerId} slotId={c.id} />
        </li>
      ))}
    </ul>
  );
}

/** The child project's own phases and components, shown inside the parent. */
function ChildOverview({ child }: { child: Project }) {
  const { openProjectAt } = useWorkbench();
  const groups = getProjectGroups(child);
  return (
    <div
      className="child-overview"
      data-testid={`child-overview-${child.id}`}
      onClick={(e) => e.stopPropagation()}
    >
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
          <InlineComponents ownerId={child.id} comps={getGroupChildren(g)} depth={1} />
          <ChildProjectRows parentId={child.id} slotId={g.id} />
        </div>
      ))}
    </div>
  );
}

function ChildProjectRow({ child, testIdPrefix = "" }: { child: Project; testIdPrefix?: string }) {
  const { isExpanded: expandedIn, setExpanded, selectProject } = useWorkbench();
  const isOpen = expandedIn("childProjects", child.id);
  return (
    <div className="child-project-block">
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
        <span className="child-project-chevron" aria-hidden="true">
          {isOpen ? "⌄" : "›"}
        </span>
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
          <svg
            width="10"
            height="10"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M7 17L17 7M8 7h9v9" />
          </svg>
        </button>
      </div>
      {isOpen && <ChildOverview child={child} />}
    </div>
  );
}

/** The child projects attached at one slot (a phase, a component or ROOT_SLOT) of a parent. */
export function ChildProjectRows({
  parentId,
  slotId,
  testIdPrefix = "",
}: {
  parentId: string;
  slotId: string;
  testIdPrefix?: string;
}) {
  const { childProjectsAt } = useWorkbench();
  const kids = childProjectsAt(parentId, slotId);
  if (kids.length === 0) return null;
  return (
    <div className="child-project-list">
      {kids.map((c) => (
        <ChildProjectRow key={c.id} child={c} testIdPrefix={testIdPrefix} />
      ))}
    </div>
  );
}
