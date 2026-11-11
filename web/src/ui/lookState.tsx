/**
 * Look-only state shared by the pages of the default look: resizable panel sizes, the project view
 * mode, the dependency map's layout, the active panel on narrow viewports and the disclosure sets that are not selection-driven.
 * App owns it (so it survives page switches) and provides it; components read it with useLook().
 */
import type React from "react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  DEFAULT_CONTENTS_WIDTH,
  DEFAULT_DIAGRAM_HEIGHT,
  DEFAULT_NAV_WIDTH,
  MAX_CONTENTS_WIDTH,
  MAX_NAV_WIDTH,
  MIN_CONTENTS_WIDTH,
  MIN_DIAGRAM_HEIGHT,
  MIN_NAV_WIDTH,
  type MapLayout,
  type PanelWidths,
  clampVal,
  readStoredMapLayout,
  readStoredPanelWidths,
  readStoredViewMode,
  writeStoredMapLayout,
  writeStoredPanelWidths,
  writeStoredViewMode,
} from "./layoutPrefs";

export type { MapLayout } from "./layoutPrefs";

export type Resizer = "left" | "right" | "horizontal";
export type MobilePanel = "nav" | "contents" | "visual";
type Disclosure = Record<string, boolean>;
type SetDisclosure = React.Dispatch<React.SetStateAction<Disclosure>>;

export interface LookState {
  viewMode: "list" | "visual";
  toggleProjectViewMode: () => void;
  /** The dependency map's layout, remembered per device */
  mapLayout: MapLayout;
  setMapLayout: (layout: MapLayout) => void;
  panelWidths: PanelWidths;
  activeResizer: Resizer | null;
  startResizing: (which: Resizer, e: React.MouseEvent) => void;
  resetPanelWidths: () => void;
  isCustomPanelWidths: boolean;
  mobilePanel: MobilePanel;
  setMobilePanel: (panel: MobilePanel) => void;
  expandedCardDescs: Disclosure;
  setExpandedCardDescs: SetDisclosure;
  collapsedPhases: Disclosure;
  setCollapsedPhases: SetDisclosure;
  expandedSubtopicItems: Disclosure;
  setExpandedSubtopicItems: SetDisclosure;
  expandedContextSubtopics: Disclosure;
  setExpandedContextSubtopics: SetDisclosure;
}

const LookContext = createContext<LookState | null>(null);

export const LookProvider = LookContext.Provider;

/** The look-only state provided by App. */
export function useLook(): LookState {
  const look = useContext(LookContext);
  if (!look) throw new Error("useLook() must be used inside <LookProvider>");
  return look;
}

/**
 * The tallest the diagram can be: the height of the column holding it (its content box) minus the
 * horizontal divider, so the panel below can shrink to nothing. Measured when a drag starts.
 */
function maxDiagramHeightFor(divider: HTMLElement): number {
  const column = divider.parentElement;
  if (!column) return Number.POSITIVE_INFINITY;
  const style = window.getComputedStyle(column);
  const content =
    column.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  return Math.max(MIN_DIAGRAM_HEIGHT, Math.floor(content - divider.offsetHeight));
}

/** Creates the look-only state; App calls this once and provides the result. */
export function useLookState(): LookState {
  const [viewMode, setViewMode] = useState<"list" | "visual">(readStoredViewMode);
  const [mapLayout, setMapLayoutState] = useState<MapLayout>(readStoredMapLayout);

  // Resizable 3-panel widths & visual/context height with localStorage persistence
  const [panelWidths, setPanelWidths] = useState<PanelWidths>(readStoredPanelWidths);
  const [activeResizer, setActiveResizer] = useState<Resizer | null>(null);
  const dragStartRef = useRef<{
    which: Resizer;
    startX: number;
    startY: number;
    startNav: number;
    startContents: number;
    startDiagramHeight: number;
    maxDiagramHeight: number;
  } | null>(null);

  // Look-only disclosure state (selection-driven expand sets live in the core store)
  const [expandedCardDescs, setExpandedCardDescs] = useState<Disclosure>({});
  const [collapsedPhases, setCollapsedPhases] = useState<Disclosure>({});
  const [expandedSubtopicItems, setExpandedSubtopicItems] = useState<Disclosure>({});
  const [expandedContextSubtopics, setExpandedContextSubtopics] = useState<Disclosure>({});

  // Responsive active panel for narrower viewports
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>("contents");

  const toggleProjectViewMode = () => {
    const next = viewMode === "list" ? "visual" : "list";
    setViewMode(next);
    writeStoredViewMode(next);
  };

  const setMapLayout = useCallback((layout: MapLayout) => {
    setMapLayoutState(layout);
    writeStoredMapLayout(layout);
  }, []);

  // Handle draggable vertical & horizontal divider mouse events
  useEffect(() => {
    if (!activeResizer) return;

    if (activeResizer === "horizontal") {
      document.body.classList.add("is-resizing-horizontal");
    } else {
      document.body.classList.add("is-resizing-panels");
    }

    function onMouseMove(e: MouseEvent) {
      const drag = dragStartRef.current;
      if (!drag) return;
      if (drag.which === "left") {
        const deltaX = e.clientX - drag.startX;
        const nextNav = clampVal(drag.startNav + deltaX, MIN_NAV_WIDTH, MAX_NAV_WIDTH);
        setPanelWidths((prev) => {
          const updated = { ...prev, navWidth: nextNav };
          writeStoredPanelWidths(updated);
          return updated;
        });
      } else if (drag.which === "right") {
        const deltaX = e.clientX - drag.startX;
        const nextContents = clampVal(
          drag.startContents + deltaX,
          MIN_CONTENTS_WIDTH,
          MAX_CONTENTS_WIDTH,
        );
        setPanelWidths((prev) => {
          const updated = { ...prev, contentsWidth: nextContents };
          writeStoredPanelWidths(updated);
          return updated;
        });
      } else if (drag.which === "horizontal") {
        const deltaY = e.clientY - drag.startY;
        const nextDiagH = clampVal(
          drag.startDiagramHeight + deltaY,
          MIN_DIAGRAM_HEIGHT,
          drag.maxDiagramHeight,
        );
        setPanelWidths((prev) => {
          const updated = { ...prev, diagramHeight: nextDiagH };
          writeStoredPanelWidths(updated);
          return updated;
        });
      }
    }

    function onMouseUp() {
      dragStartRef.current = null;
      setActiveResizer(null);
      document.body.classList.remove("is-resizing-panels");
      document.body.classList.remove("is-resizing-horizontal");
    }

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      document.body.classList.remove("is-resizing-panels");
      document.body.classList.remove("is-resizing-horizontal");
    };
  }, [activeResizer]);

  const startResizing = (which: Resizer, e: React.MouseEvent) => {
    e.preventDefault();
    const maxDiagramHeight =
      which === "horizontal"
        ? maxDiagramHeightFor(e.currentTarget as HTMLElement)
        : Number.POSITIVE_INFINITY;
    dragStartRef.current = {
      which,
      startX: e.clientX,
      startY: e.clientY,
      startNav: panelWidths.navWidth,
      startContents: panelWidths.contentsWidth,
      // A stored height taller than the column is shown clamped (CSS max-height): start from that
      startDiagramHeight: Math.min(panelWidths.diagramHeight, maxDiagramHeight),
      maxDiagramHeight,
    };
    setActiveResizer(which);
  };

  const resetPanelWidths = useCallback(() => {
    const defaults: PanelWidths = {
      navWidth: DEFAULT_NAV_WIDTH,
      contentsWidth: DEFAULT_CONTENTS_WIDTH,
      diagramHeight: DEFAULT_DIAGRAM_HEIGHT,
    };
    setPanelWidths(defaults);
    writeStoredPanelWidths(defaults);
  }, []);

  const isCustomPanelWidths =
    panelWidths.navWidth !== DEFAULT_NAV_WIDTH ||
    panelWidths.contentsWidth !== DEFAULT_CONTENTS_WIDTH ||
    panelWidths.diagramHeight !== DEFAULT_DIAGRAM_HEIGHT;

  return {
    viewMode,
    toggleProjectViewMode,
    mapLayout,
    setMapLayout,
    panelWidths,
    activeResizer,
    startResizing,
    resetPanelWidths,
    isCustomPanelWidths,
    mobilePanel,
    setMobilePanel,
    expandedCardDescs,
    setExpandedCardDescs,
    collapsedPhases,
    setCollapsedPhases,
    expandedSubtopicItems,
    setExpandedSubtopicItems,
    expandedContextSubtopics,
    setExpandedContextSubtopics,
  };
}
