/**
 * Component cards of the project workspace: the centre list's card (CentreComp) and the context
 * panel's card (ContextComp), each with its "N parts" toggle, level-3 detail rows on demand, child
 * projects and (when open) nested components.
 */
import {
  useWorkbench,
  getItemShortDescription,
  type Phase,
  type Component,
  type DetailItem,
} from "@workbench-core";
import { useLook } from "../lookState";
import { ChildProjectRows } from "./ChildProjects";
import { ItemStatusMenu } from "./ItemStatusMenu";
import { StatusNodeGlyph } from "./status";
import { revealId } from "../revealSelection";

/** Collapsible "N parts" toggle for a component's nested components. */
function NestedToggle({ comp, testId }: { comp: Component; testId: string }) {
  const { isExpanded: expandedIn, setExpanded } = useWorkbench();
  const n = (comp.components || []).length;
  if (n === 0) return null;
  const open = expandedIn("nestedComponents", comp.id);
  return (
    <button
      type="button"
      className={`nested-toggle-btn ${open ? "open" : ""}`}
      data-testid={testId}
      aria-expanded={open}
      aria-label={open ? `Hide nested parts of ${comp.name}` : `Show nested parts of ${comp.name}`}
      onClick={(e) => {
        e.stopPropagation();
        setExpanded("nestedComponents", comp.id, !open);
      }}
    >
      <span className="nested-toggle-caret" aria-hidden="true">
        {open ? "⌄" : "›"}
      </span>
      {n} {n === 1 ? "part" : "parts"}
    </button>
  );
}

/** Level-3 detail rows (items, notes, dependencies, links) of a component. */
function ComponentItems({
  projectId,
  items,
  testId,
  withLinks = false,
}: {
  projectId: string;
  items: DetailItem[];
  testId?: string;
  withLinks?: boolean;
}) {
  const { selectProject } = useWorkbench();
  return (
    <div className="tier-3-items-list" data-testid={testId}>
      {items.map((item) => (
        <div
          key={item.id}
          className={`tier-3-item-row ${item.targetComp ? "clickable" : ""}`}
          onClick={(e) => {
            if (item.targetComp) {
              e.stopPropagation();
              selectProject(projectId, item.targetComp.phase.id, item.targetComp.comp.id);
            }
          }}
        >
          <span className="tier-3-branch-line">└</span>
          <StatusNodeGlyph status={item.status} />
          <span className="tier-3-item-kind">{item.kindLabel}:</span>
          {withLinks && item.url ? (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="tier-3-item-text"
              onClick={(e) => e.stopPropagation()}
            >
              {item.text} ↗
            </a>
          ) : (
            <span className="tier-3-item-text">{item.text}</span>
          )}
        </div>
      ))}
    </div>
  );
}

/** Centre pane: one component card, its child projects, and (when open) its nested components. */
export function CentreComp({
  projectId,
  ph,
  comp,
  depth,
}: {
  projectId: string;
  ph: Phase;
  comp: Component;
  depth: number;
}) {
  const wb = useWorkbench();
  const { selection, activeProject, isExpanded: expandedIn, selectProject } = wb;
  const { expandedSubtopicItems, setExpandedSubtopicItems } = useLook();
  const isCompSelected = selection.componentId === comp.id;
  const isItemsOpen = !!expandedSubtopicItems[comp.id];
  const childItems = wb.componentDetails(activeProject!, comp);
  const shortDesc = getItemShortDescription(comp);
  const nested = comp.components || [];
  const nestedOpen = expandedIn("nestedComponents", comp.id);

  return (
    <div
      className={`tier-2-subtopic-group ${isCompSelected ? "selected" : ""} ${depth > 0 ? "nested" : ""}`}
      data-testid={`component-row-${comp.id}`}
      data-reveal-id={revealId(projectId, comp.id)}
    >
      <div
        className="tier-2-subtopic-header"
        onClick={() => selectProject(projectId, ph.id, comp.id)}
      >
        <div className="tier-2-header-left">
          <ItemStatusMenu projectId={projectId} item={comp} />
          <div className="tier-2-text-col">
            <span className="tier-2-subtopic-title">{comp.name}</span>
            {shortDesc && <p className="tier-2-subtopic-desc">{shortDesc}</p>}
          </div>
        </div>
        <div className="tier-2-header-actions">
          <NestedToggle comp={comp} testId={`nested-toggle-${comp.id}`} />
          {childItems.length > 0 && (
            <button
              type="button"
              className="tree-chevron-btn sm"
              data-testid={`subtopic-expand-${comp.id}`}
              aria-label={isItemsOpen ? "Hide subtopic items" : "Show subtopic items"}
              title={isItemsOpen ? "Hide items" : "Show related items"}
              onClick={(e) => {
                e.stopPropagation();
                setExpandedSubtopicItems((prev) => ({ ...prev, [comp.id]: !isItemsOpen }));
              }}
            >
              {isItemsOpen ? "⌄" : "›"}
            </button>
          )}
        </div>
      </div>

      {/* LEVEL 3: Individual Items shown only when explicitly requested */}
      {isItemsOpen && childItems.length > 0 && (
        <ComponentItems projectId={projectId} items={childItems} withLinks={true} />
      )}

      <ChildProjectRows parentId={projectId} slotId={comp.id} />

      {nestedOpen && nested.length > 0 && (
        <div className="nested-comp-children" data-testid={`nested-children-${comp.id}`}>
          {nested.map((c) => (
            <CentreComp key={c.id} projectId={projectId} ph={ph} comp={c} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Right context pane: same structure as the centre list, with its own test ids. */
export function ContextComp({
  projectId,
  ph,
  comp,
  depth,
}: {
  projectId: string;
  ph: Phase;
  comp: Component;
  depth: number;
}) {
  const wb = useWorkbench();
  const { selection, activeProject, isExpanded: expandedIn, selectProject } = wb;
  const { expandedContextSubtopics, setExpandedContextSubtopics } = useLook();
  const isSubSelected = selection.componentId === comp.id;
  const isExpanded = !!expandedContextSubtopics[comp.id];
  const items = wb.componentDetails(activeProject!, comp);
  const shortDesc = getItemShortDescription(comp);
  const nested = comp.components || [];
  const nestedOpen = expandedIn("nestedComponents", comp.id);

  return (
    <div
      className={`tier-2-subtopic-group ${isSubSelected ? "selected" : ""} ${depth > 0 ? "nested" : ""}`}
    >
      <div
        className={`tier-2-subtopic-header ${isSubSelected ? "selected" : ""}`}
        data-testid={`visual-context-subtopic-${comp.id}`}
        onClick={() => selectProject(projectId, ph.id, comp.id)}
      >
        <div className="tier-2-header-left">
          <ItemStatusMenu projectId={projectId} item={comp} testIdPrefix="context-" />
          <div className="tier-2-text-col">
            <span className="tier-2-subtopic-title">{comp.name}</span>
            {shortDesc && <p className="tier-2-subtopic-desc">{shortDesc}</p>}
          </div>
        </div>
        <div className="tier-2-header-actions">
          <NestedToggle comp={comp} testId={`context-nested-toggle-${comp.id}`} />
          {items.length > 0 && (
            <button
              type="button"
              className="tree-chevron-btn sm"
              data-testid={`visual-context-expand-${comp.id}`}
              aria-label={isExpanded ? "Collapse subtopic items" : "Expand subtopic items"}
              onClick={(e) => {
                e.stopPropagation();
                setExpandedContextSubtopics((prev) => ({ ...prev, [comp.id]: !isExpanded }));
              }}
            >
              {isExpanded ? "⌄" : "›"}
            </button>
          )}
        </div>
      </div>

      {isExpanded && items.length > 0 && (
        <ComponentItems
          projectId={projectId}
          items={items}
          testId={`visual-context-items-${comp.id}`}
        />
      )}

      <ChildProjectRows parentId={projectId} slotId={comp.id} testIdPrefix="visual-context-" />

      {nestedOpen && nested.length > 0 && (
        <div className="nested-comp-children">
          {nested.map((c) => (
            <ContextComp key={c.id} projectId={projectId} ph={ph} comp={c} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
