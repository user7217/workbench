/**
 * The "add to Today's Work" control shown beside a status: a small sun that is filled while the
 * item is on today's list. Adding an item brings everything nested under it along (the core works
 * that out), so the control only names the item itself.
 */
import { useWorkbench, type TodayEntry } from "@workbench-core";

export function TodayIcon({ filled, size = 14 }: { filled?: boolean; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

export function TodayToggle({
  entry,
  subject,
  variant = "icon",
  testIdPrefix = "",
}: {
  entry: TodayEntry;
  /** Name of the item, for the tooltip and screen readers. */
  subject: string;
  /** "icon" sits beside a status glyph; "pill" is the labelled button in page headers. */
  variant?: "icon" | "pill";
  testIdPrefix?: string;
}) {
  const wb = useWorkbench();
  if (!wb.todayEnabled) return null; // Today's Work is turned off (Settings menu)
  const on = wb.isInToday(entry);
  const label = on
    ? `Remove ${subject} from Today's Work`
    : `Add ${subject} and everything under it to Today's Work`;
  return (
    <button
      type="button"
      className={`today-toggle today-toggle-${variant}${on ? " is-on" : ""}`}
      data-testid={`${testIdPrefix}today-toggle-${entry.item ?? entry.id}`}
      aria-pressed={on}
      aria-label={label}
      title={label}
      onClick={(e) => {
        e.stopPropagation();
        void (on ? wb.removeFromToday(entry) : wb.addToToday(entry));
      }}
    >
      <TodayIcon filled={on} size={variant === "pill" ? 13 : 14} />
      {variant === "pill" && <span>Today</span>}
    </button>
  );
}
