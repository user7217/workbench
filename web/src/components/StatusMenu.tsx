import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { STATUS_LABELS, StatusNodeGlyph } from "./status";

export const ITEM_STATUSES = ["not_started", "in_progress", "done", "parked"] as const;
export const LEARNING_ITEM_STATUSES = ["not_started", "in_progress", "done"] as const;
export const ENTITY_STATUSES = ["planned", "active", "paused", "done", "archived"] as const;

interface StatusMenuProps<S extends string> {
  status: string;
  options: readonly S[];
  onSelect: (status: S) => void;
  /** True while a save is in flight: the trigger is disabled and shows a busy state. */
  busy?: boolean;
  testId: string;
  /** "icon" = the status glyph itself is the trigger; "pill" = glyph + label + caret (headers). */
  variant?: "icon" | "pill";
  /** What is being changed, for the accessible name (e.g. the item's name). */
  subject?: string;
  size?: number;
}

const MENU_WIDTH = 150;

/**
 * Click a status to change it: a small menu of the allowed statuses, positioned with `fixed` so
 * scrolling panels never clip it. All clicks stop propagating so rows underneath are not selected.
 */
export function StatusMenu<S extends string>({
  status,
  options,
  onSelect,
  busy = false,
  testId,
  variant = "icon",
  subject,
  size = 14,
}: StatusMenuProps<S>) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const label = STATUS_LABELS[status] ?? status;

  const close = useCallback(() => setOpen(false), []);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const menuHeight = options.length * 28 + 10;
    const below = rect.bottom + 4;
    const top = below + menuHeight > window.innerHeight ? Math.max(4, rect.top - menuHeight - 4) : below;
    const left = Math.min(Math.max(4, rect.left), window.innerWidth - MENU_WIDTH - 4);
    setPos({ top, left });
  }, [open, options.length]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      close();
    };
    // Capture phase so Escape closes only the menu, not the open inspector behind it
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
        triggerRef.current?.focus();
      }
    };
    const onScroll = (e: Event) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      close();
    };
    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", close);
    };
  }, [open, close]);

  useEffect(() => {
    if (open && pos) menuRef.current?.querySelector<HTMLButtonElement>("button[aria-checked='true']")?.focus();
  }, [open, pos]);

  useEffect(() => {
    if (busy) setOpen(false);
  }, [busy]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`status-menu-trigger ${variant}${busy ? " busy" : ""}${open ? " open" : ""}`}
        data-testid={testId}
        disabled={busy}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-busy={busy}
        aria-label={`Status: ${label}${subject ? ` — change status of ${subject}` : " — change status"}`}
        title={busy ? "Saving…" : `${label} — click to change`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        <StatusNodeGlyph status={status} size={size} />
        {variant === "pill" && (
          <>
            <span className="status-menu-label">{busy ? "Saving…" : label}</span>
            <span className="status-menu-caret" aria-hidden="true">
              ⌄
            </span>
          </>
        )}
      </button>
      {open && pos && (
        <div
          ref={menuRef}
          className="status-menu-popover"
          role="menu"
          data-testid={`${testId}-menu`}
          style={{ top: pos.top, left: pos.left, width: MENU_WIDTH }}
          onClick={(e) => e.stopPropagation()}
          >
          {options.map((opt) => (
            <button
              key={opt}
              type="button"
              role="menuitemradio"
              aria-checked={opt === status}
              className={`status-menu-option${opt === status ? " current" : ""}`}
              data-testid={`status-option-${opt}`}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                if (opt !== status) onSelect(opt);
              }}
            >
              <StatusNodeGlyph status={opt} />
              <span>{STATUS_LABELS[opt] ?? opt}</span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}
