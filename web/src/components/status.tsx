import React from "react";

/** CSS custom property holding the color for a status (palette lives in styles.css :root). */
export function statusColorVar(status: string | undefined): string {
  switch (status) {
    case "done":
      return "var(--status-done)";
    case "in_progress":
      return "var(--status-in-progress)";
    case "parked":
    case "paused":
      return "var(--status-parked)";
    case "active":
      return "var(--accent)";
    case "archived":
      return "var(--status-archived)";
    case "planned":
      return "var(--status-planned)";
    default:
      return "var(--status-not-started)";
  }
}

/** Same color mixed with transparency, for tinted backgrounds and borders. */
export function statusTint(status: string | undefined, percent: number): string {
  return `color-mix(in srgb, ${statusColorVar(status)} ${percent}%, transparent)`;
}

export const STATUS_LABELS: Record<string, string> = {
  planned: "Planned",
  done: "Done",
  in_progress: "In progress",
  not_started: "Not started",
  parked: "Parked",
  paused: "Paused",
  active: "Active",
  archived: "Archived",
};

/** One icon per status, so every icon in the UI means something. */
export function StatusNodeGlyph({ status, size = 14 }: { status: string; size?: number }) {
  const color = statusColorVar(status);
  const label = STATUS_LABELS[status] ?? STATUS_LABELS.not_started;
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 16 16",
    role: "img",
    "aria-label": label,
    style: { flexShrink: 0, display: "inline-block" } as React.CSSProperties,
  };

  if (status === "done") {
    return (
      <svg {...common}>
        <title>{label}</title>
        <circle cx="8" cy="8" r="7" style={{ fill: color }} />
        <path d="M4.8 8.2l2.1 2.1 4.3-4.6" fill="none" stroke="var(--bg-app)" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (status === "in_progress") {
    return (
      <svg {...common}>
        <title>{label}</title>
        <circle cx="8" cy="8" r="6.2" fill="none" style={{ stroke: color }} strokeWidth="1.6" />
        <path d="M8 3.6a4.4 4.4 0 0 1 0 8.8z" style={{ fill: color }} />
      </svg>
    );
  }
  if (status === "parked" || status === "paused") {
    return (
      <svg {...common}>
        <title>{label}</title>
        <circle cx="8" cy="8" r="6.2" fill="none" style={{ stroke: color }} strokeWidth="1.6" />
        <path d="M6.4 5.6v4.8M9.6 5.6v4.8" style={{ stroke: color }} strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  }
  if (status === "active") {
    return (
      <svg {...common}>
        <title>{label}</title>
        <circle cx="8" cy="8" r="6.2" fill="none" style={{ stroke: color }} strokeWidth="1.6" />
        <circle cx="8" cy="8" r="2.8" style={{ fill: color }} />
      </svg>
    );
  }
  if (status === "planned") {
    return (
      <svg {...common}>
        <title>{label}</title>
        <circle cx="8" cy="8" r="6.2" fill="none" style={{ stroke: color }} strokeWidth="1.6" strokeDasharray="2.6 2.2" />
      </svg>
    );
  }
  if (status === "archived") {
    return (
      <svg {...common}>
        <title>{label}</title>
        <rect x="2" y="3" width="12" height="3.2" rx="1" fill="none" style={{ stroke: color }} strokeWidth="1.4" />
        <path d="M3.2 6.2v6.3a1 1 0 0 0 1 1h7.6a1 1 0 0 0 1-1V6.2M6.5 9h3" fill="none" style={{ stroke: color }} strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <title>{label}</title>
      <circle cx="8" cy="8" r="6.2" fill="none" style={{ stroke: color }} strokeWidth="1.6" />
    </svg>
  );
}

/** Project or learning-path badge: the glyph says what it is, the color says its status. */
export function KindBadge({
  isLearning,
  status,
  size = 28,
}: {
  isLearning: boolean;
  status: string;
  size?: number;
}) {
  const color = statusColorVar(status);
  const glyph = size * 0.55;
  return (
    <div
      className="item-icon-badge"
      title={`${isLearning ? "Learning path" : "Project"} · ${STATUS_LABELS[status] ?? status}`}
      style={{
        width: size,
        height: size,
        borderRadius: size >= 30 ? 8 : 7,
        background: statusTint(status, 16),
        border: `1px solid ${statusTint(status, 38)}`,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      {isLearning ? (
        <svg width={glyph} height={glyph} viewBox="0 0 24 24" fill="none" style={{ stroke: color }} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2 5.5A1.5 1.5 0 0 1 3.5 4H9a3 3 0 0 1 3 3v13a2.5 2.5 0 0 0-2.5-2.5H3.5A1.5 1.5 0 0 1 2 16z" />
          <path d="M22 5.5A1.5 1.5 0 0 0 20.5 4H15a3 3 0 0 0-3 3v13a2.5 2.5 0 0 1 2.5-2.5h6a1.5 1.5 0 0 0 1.5-1.5z" />
        </svg>
      ) : (
        <svg width={glyph} height={glyph} viewBox="0 0 24 24" fill="none" style={{ stroke: color }} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" />
        </svg>
      )}
    </div>
  );
}

/** Numbered badge for a phase, colored by the phase's status. */
export function PhaseNumberBadge({ index, status, size = 22 }: { index: number; status: string; size?: number }) {
  return (
    <span
      style={{
        width: size,
        height: size,
        borderRadius: 6,
        background: statusTint(status, 18),
        border: `1px solid ${statusTint(status, 45)}`,
        color: statusColorVar(status),
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: size * 0.55,
        fontWeight: 700,
        fontVariantNumeric: "tabular-nums",
        flexShrink: 0,
      }}
    >
      {index + 1}
    </span>
  );
}
