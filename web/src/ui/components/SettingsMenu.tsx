/**
 * The Settings menu at the bottom of the sidebar (a tab of the tab bar on a phone). It holds the
 * two preferences of the dashboard:
 * - the dependency map's layout, a look-only choice kept in this browser, since a phone and a
 *   laptop suit different layouts;
 * - Today's Work on or off, saved as `enabled` in today.yaml, so it applies to every device and
 *   server that reads the same data repository.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useWorkbench } from "@workbench-core";
import { useLook } from "../lookState";

const MENU_WIDTH = 296;

function GearIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
    </svg>
  );
}

export function SettingsMenu({ collapsed }: { collapsed: boolean }) {
  const wb = useWorkbench();
  const look = useLook();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  // Desktop: open beside the button, bottom-aligned with it. The phone layout places it with CSS.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const left = Math.min(rect.right + 8, window.innerWidth - MENU_WIDTH - 8);
    setPos({ left: Math.max(8, left), bottom: Math.max(8, window.innerHeight - rect.bottom) });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      close();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("resize", close);
    };
  }, [open, close]);

  const todayOn = wb.todayEnabled;

  return (
    <div className="settings-menu">
      <button
        ref={triggerRef}
        type="button"
        className={open ? "nav-item settings-trigger open" : "nav-item settings-trigger"}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={collapsed ? "Settings" : undefined}
        data-testid="settings-button"
        onClick={() => setOpen((v) => !v)}
      >
        <GearIcon />
        {!collapsed && <span>Settings</span>}
      </button>

      {open && (
        <div
          ref={menuRef}
          className="settings-popover"
          role="dialog"
          aria-label="Settings"
          data-testid="settings-menu"
          style={pos ? { left: pos.left, bottom: pos.bottom, width: MENU_WIDTH } : undefined}
        >
          <div className="settings-row">
            <div className="settings-text">
              <span className="settings-label" id="settings-map-layout">
                Map layout
              </span>
              <span className="settings-hint">Kept in this browser</span>
            </div>
            <fieldset className="settings-segmented" aria-labelledby="settings-map-layout">
              {(["vertical", "horizontal"] as const).map((layout) => (
                <button
                  key={layout}
                  type="button"
                  aria-pressed={look.mapLayout === layout}
                  className={look.mapLayout === layout ? "is-on" : undefined}
                  data-testid={`settings-map-${layout}`}
                  onClick={() => look.setMapLayout(layout)}
                >
                  {layout === "vertical" ? "Vertical" : "Horizontal"}
                </button>
              ))}
            </fieldset>
          </div>

          <div className="settings-row">
            <div className="settings-text">
              <span className="settings-label" id="settings-today">
                Today's Work
              </span>
              <span className="settings-hint">Applies on every device</span>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={todayOn}
              aria-labelledby="settings-today"
              className={todayOn ? "settings-switch is-on" : "settings-switch"}
              data-testid="settings-today-switch"
              onClick={() => void wb.setTodayEnabled(!todayOn)}
            >
              <span className="settings-switch-knob" aria-hidden="true" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
