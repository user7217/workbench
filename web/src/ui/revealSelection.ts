// ---------------------------------------------------------------------------------------------
// Keep the selected item in view in the list panels (Navigation and Contents)
// ---------------------------------------------------------------------------------------------
//
// Rows that stand for a phase/topic or a component/subtopic carry
// `data-reveal-id="<project or path id>/<item id>"`. When the selection changes (from the map,
// from another panel, or from opening an item by link), every panel that shows the selected row
// scrolls it into view. The core has already expanded the row's parents by then.
import { useEffect } from "react";

/** The attribute value a row of `itemId` in `ownerId` (project or learning path) carries. */
export function revealId(ownerId: string, itemId: string): string {
  return `${ownerId}/${itemId}`;
}

/** The nearest ancestor of `el` that scrolls vertically. */
function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let node = el.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if ((overflowY === "auto" || overflowY === "scroll") && node.scrollHeight > node.clientHeight) {
      return node;
    }
  }
  return null;
}

/**
 * Scroll `el` into view inside its own scrolling panel only. `scrollIntoView` would also move
 * every other scrollable ancestor, including ones the layout keeps at overflow: hidden.
 */
function revealInPanel(el: HTMLElement) {
  const box = scrollParent(el);
  if (!box) return;
  const r = el.getBoundingClientRect();
  const b = box.getBoundingClientRect();
  if (r.height === 0) return; // inside a panel that is hidden (a tab on a narrow screen)
  const fullyVisible = r.top >= b.top && r.bottom <= b.bottom;
  // A tall row (a whole phase block) only needs its top in view
  const topVisible = r.height > b.height && r.top >= b.top && r.top < b.bottom - 40;
  if (fullyVisible || topVisible) return;
  // A short row lands a third of the way down; a tall one near the top
  const offset = r.height > b.height * 0.6 ? 12 : b.height / 3 - r.height / 2;
  box.scrollTo({ top: box.scrollTop + (r.top - b.top) - offset, behavior: "smooth" });
}

/**
 * Scroll the rows of the selected item into view whenever the selection changes.
 * `ownerId` is the open project or learning path; `itemId` the selected item (null = none).
 * `visiblePanel` is the panel tab shown on a narrow screen: a row in a hidden tab cannot be
 * scrolled to, so it is revealed again when its tab is opened.
 */
export function useRevealSelection(
  ownerId: string | null | undefined,
  itemId: string | null,
  visiblePanel?: string,
) {
  // biome-ignore lint/correctness/useExhaustiveDependencies: visiblePanel only re-runs the reveal
  useEffect(() => {
    if (!ownerId || !itemId) return;
    const selector = `[data-reveal-id="${CSS.escape(revealId(ownerId, itemId))}"]`;
    // Two frames: the first lets React render newly expanded parents, the second has layout
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => {
        for (const el of document.querySelectorAll<HTMLElement>(selector)) revealInPanel(el);
      });
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, [ownerId, itemId, visiblePanel]);
}
