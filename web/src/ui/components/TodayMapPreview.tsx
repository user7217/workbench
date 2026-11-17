/**
 * A small map of a Today card's project or learning path: the card's steps in colour, its current
 * step highlighted, and the rest of the map greyed out. Shown beside the card while it is hovered,
 * or pinned by clicking the card; on a phone it opens as a sheet above the tab bar.
 */
import { useLayoutEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useWorkbench, type TodayGroup } from "@workbench-core";
import { VisualGraph } from "./VisualGraph";

const WIDTH = 460;
const HEIGHT = 340;
const GAP = 12;
const MARGIN = 8;

/** Where the preview goes: beside the card if there is room, else below or above it. */
function placeBeside(card: DOMRect): { left: number; top: number } {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const clampTop = (top: number) => Math.min(Math.max(MARGIN, top), vh - HEIGHT - MARGIN);
  const clampLeft = (left: number) => Math.min(Math.max(MARGIN, left), vw - WIDTH - MARGIN);
  if (card.right + GAP + WIDTH <= vw - MARGIN)
    return { left: card.right + GAP, top: clampTop(card.top) };
  if (card.left - GAP - WIDTH >= MARGIN)
    return { left: card.left - GAP - WIDTH, top: clampTop(card.top) };
  const below = card.bottom + GAP;
  const top = below + HEIGHT <= vh - MARGIN ? below : card.top - GAP - HEIGHT;
  return { left: clampLeft(card.left), top: clampTop(top) };
}

export function TodayMapPreview({
  group,
  anchor,
  pinned,
  onClose,
  onPointerEnter,
  onPointerLeave,
}: {
  group: TodayGroup;
  /** The card the preview belongs to, for its position. */
  anchor: HTMLElement | null;
  pinned: boolean;
  onClose: () => void;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
}) {
  const { snapshot, childIndex } = useWorkbench();
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    if (!anchor) return;
    const update = () => setPos(placeBeside(anchor.getBoundingClientRect()));
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [anchor]);

  const project =
    group.kind === "project" ? snapshot.projects.find((p) => p.id === group.id) : undefined;
  const learningPath =
    group.kind === "learning" ? snapshot.learning.find((lp) => lp.id === group.id) : undefined;

  // The card's steps; a child project's steps light up that child project's node
  const focusIds = useMemo(() => {
    const ids = new Set<string>();
    for (const step of group.steps) {
      ids.add(step.ownerId === group.id ? step.itemId : step.ownerId);
    }
    return ids;
  }, [group]);

  if (!project && !learningPath) return null;
  const now = group.now && group.now.ownerId === group.id ? group.now.itemId : null;

  return createPortal(
    <div
      className={`today-map-preview${pinned ? " is-pinned" : ""}`}
      role="dialog"
      aria-label={`Map of ${group.name}`}
      data-testid={`today-map-${group.id}`}
      style={
        pos
          ? { left: pos.left, top: pos.top, width: WIDTH, height: HEIGHT }
          : { visibility: "hidden" }
      }
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
    >
      <div className="today-map-preview-header">
        <span className="today-map-preview-title">{group.name}</span>
        <span className="today-map-preview-hint">Today's part in colour</span>
        {pinned && (
          <button
            type="button"
            className="today-map-preview-close"
            aria-label="Close the map"
            onClick={onClose}
          >
            ×
          </button>
        )}
      </div>
      <div className="today-map-preview-body">
        <VisualGraph
          project={project}
          learningPath={learningPath}
          selectedId={now}
          childProjects={project ? childIndex.get(project.id) : undefined}
          focusIds={focusIds}
          preview
        />
      </div>
    </div>,
    document.body,
  );
}
