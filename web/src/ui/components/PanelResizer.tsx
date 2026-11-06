/**
 * A draggable divider of the three-panel workspaces: "left" and "right" sit between the vertical
 * panels, "horizontal" between the diagram and the context panel below it. Double-click resets.
 */
import { type Resizer, useLook } from "../lookState";

export function PanelResizer({
  which,
  label,
  title,
}: {
  which: Resizer;
  label: string;
  title?: string;
}) {
  const { activeResizer, startResizing, resetPanelWidths } = useLook();
  const horizontal = which === "horizontal";
  const baseClass = horizontal ? "panel-resizer-horizontal" : "panel-resizer";
  return (
    <div
      role="separator"
      aria-orientation={horizontal ? "horizontal" : "vertical"}
      aria-label={label}
      title={title}
      className={`${baseClass} ${activeResizer === which ? "dragging" : ""}`}
      data-testid={`panel-resizer-${which}`}
      onMouseDown={(e) => startResizing(which, e)}
      onDoubleClick={resetPanelWidths}
    />
  );
}
