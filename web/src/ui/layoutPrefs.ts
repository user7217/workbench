// ---------------------------------------------------------------------------------------------
// Layout preferences (look-only state, persisted in localStorage)
// ---------------------------------------------------------------------------------------------

const VIEW_STORAGE_KEY = "workbench_project_view_mode";
const PANEL_WIDTHS_STORAGE_KEY = "workbench_panel_widths_v1";
const MAP_LAYOUT_STORAGE_KEY = "workbench_map_layout";

/** How the dependency map is drawn: phases in columns under the project, or flowing left to right. */
export type MapLayout = "vertical" | "horizontal";
export const DEFAULT_MAP_LAYOUT: MapLayout = "vertical";

export const DEFAULT_NAV_WIDTH = 276;
export const MIN_NAV_WIDTH = 200;
export const MAX_NAV_WIDTH = 440;

export const DEFAULT_CONTENTS_WIDTH = 450;
export const MIN_CONTENTS_WIDTH = 310;
export const MAX_CONTENTS_WIDTH = 760;

export const DEFAULT_DIAGRAM_HEIGHT = 360;
// A sliver, so the map's frame stays visible and React Flow never gets a zero-height container.
// There is no fixed maximum: a drag is bounded by the column's height (measured when it starts)
// and the frame's CSS max-height keeps a stored height inside a smaller window.
export const MIN_DIAGRAM_HEIGHT = 24;

export interface PanelWidths {
  navWidth: number;
  contentsWidth: number;
  diagramHeight: number;
}

export function clampVal(val: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Math.round(val)));
}

export function readStoredViewMode(): "list" | "visual" {
  try {
    const val = window.localStorage.getItem(VIEW_STORAGE_KEY);
    if (val === "visual" || val === "list") return val;
  } catch {
    // Ignore localStorage errors
  }
  return "list";
}

export function writeStoredViewMode(mode: "list" | "visual"): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, mode);
  } catch {
    // Ignore localStorage errors
  }
}

export function readStoredMapLayout(): MapLayout {
  try {
    const val = window.localStorage.getItem(MAP_LAYOUT_STORAGE_KEY);
    if (val === "vertical" || val === "horizontal") return val;
  } catch {
    // Ignore localStorage errors
  }
  return DEFAULT_MAP_LAYOUT;
}

export function writeStoredMapLayout(layout: MapLayout): void {
  try {
    window.localStorage.setItem(MAP_LAYOUT_STORAGE_KEY, layout);
  } catch {
    // Ignore localStorage errors
  }
}

export function readStoredPanelWidths(): PanelWidths {
  try {
    const raw = window.localStorage.getItem(PANEL_WIDTHS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (typeof parsed?.navWidth === "number" && typeof parsed?.contentsWidth === "number") {
        const diagH =
          typeof parsed?.diagramHeight === "number"
            ? clampVal(parsed.diagramHeight, MIN_DIAGRAM_HEIGHT, Number.POSITIVE_INFINITY)
            : DEFAULT_DIAGRAM_HEIGHT;
        return {
          navWidth: clampVal(parsed.navWidth, MIN_NAV_WIDTH, MAX_NAV_WIDTH),
          contentsWidth: clampVal(parsed.contentsWidth, MIN_CONTENTS_WIDTH, MAX_CONTENTS_WIDTH),
          diagramHeight: diagH,
        };
      }
    }
  } catch {
    // Ignore localStorage errors
  }
  return {
    navWidth: DEFAULT_NAV_WIDTH,
    contentsWidth: DEFAULT_CONTENTS_WIDTH,
    diagramHeight: DEFAULT_DIAGRAM_HEIGHT,
  };
}

export function writeStoredPanelWidths(widths: PanelWidths): void {
  try {
    window.localStorage.setItem(PANEL_WIDTHS_STORAGE_KEY, JSON.stringify(widths));
  } catch {
    // Ignore localStorage errors
  }
}
