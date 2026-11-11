/**
 * The left sidebar of the default look: wordmark, collapse toggle, Home / Today / Learning /
 * Building navigation and shortcut lists of projects (with their phases) and learning paths.
 * On a phone it becomes a tab bar along the bottom (styles/responsive.css).
 */
import { useState } from "react";
import { useWorkbench, getProjectGroups, todayHasWork } from "@workbench-core";
import { SettingsMenu } from "./SettingsMenu";
import { TodayIcon } from "./TodayToggle";

// Left Sidebar — Compact, Collapsible, Home / Learning / Building only
export function Sidebar() {
  const {
    route,
    selection,
    sidebarProjects,
    sidebarLearning,
    selectProject,
    selectLearningPath,
    navigate,
    today,
    todayEnabled,
    todayPlan,
  } = useWorkbench();
  // A dot, not a count: the tab says there is something to do without keeping score
  const hasWorkToday = today.entries.length > 0 && todayHasWork(todayPlan());
  const selectedPhaseId = selection.phaseId;

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  // Look-only disclosure state (selection-driven expand sets live in the core store)
  const [sidebarExpandedProjects, setSidebarExpandedProjects] = useState<Record<string, boolean>>(
    {},
  );

  return (
    <aside
      className={sidebarCollapsed ? "sidebar collapsed" : "sidebar"}
      data-testid="left-sidebar"
    >
      <div className="sidebar-top-row">
        <div className="wordmark" onClick={() => navigate({ page: "home" })}>
          <span className="wordmark-icon">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              style={{ stroke: "var(--accent)" }}
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polygon points="12 2 2 7 12 12 22 7 12 2" />
              <polyline points="2 17 12 22 22 17" />
              <polyline points="2 12 12 17 22 12" />
            </svg>
          </span>
          {!sidebarCollapsed && <span className="wordmark-text">Workbench</span>}
        </div>
        <button
          type="button"
          className="sidebar-collapse-btn"
          data-testid="sidebar-collapse-toggle"
          aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={() => setSidebarCollapsed((c) => !c)}
        >
          {sidebarCollapsed ? "»" : "«"}
        </button>
      </div>

      <nav className="nav-primary">
        <button
          type="button"
          data-testid="nav-home"
          className={route.page === "home" ? "nav-item active" : "nav-item"}
          onClick={() => navigate({ page: "home" })}
          title="Home"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
            <polyline points="9 22 9 12 15 12 15 22" />
          </svg>
          {!sidebarCollapsed && <span>Home</span>}
        </button>

        {todayEnabled && (
          <button
            type="button"
            data-testid="nav-today"
            className={route.page === "today" ? "nav-item active" : "nav-item"}
            onClick={() => navigate({ page: "today" })}
            title={hasWorkToday ? "Today's Work: there are steps to do" : "Today's Work"}
          >
            <TodayIcon size={14} />
            {!sidebarCollapsed && <span>Today</span>}
            {hasWorkToday && <span className="nav-dot" aria-hidden="true" />}
          </button>
        )}

        <button
          type="button"
          data-testid="nav-learning"
          className={
            route.page === "learning" || route.page === "learning-path"
              ? "nav-item active"
              : "nav-item"
          }
          onClick={() => navigate({ page: "learning" })}
          title="Learning"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
          </svg>
          {!sidebarCollapsed && <span>Learning</span>}
        </button>

        <button
          type="button"
          data-testid="nav-building"
          className={
            route.page === "building" || route.page === "project" ? "nav-item active" : "nav-item"
          }
          onClick={() => navigate({ page: "building" })}
          title="Building"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
            <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
            <line x1="12" y1="22.08" x2="12" y2="12" />
          </svg>
          {!sidebarCollapsed && <span>Building</span>}
        </button>
      </nav>

      {!sidebarCollapsed && (
        <div className="sidebar-scroll-tree">
          <div className="sidebar-section-label">Projects</div>
          <div className="sidebar-shortcut-list">
            {sidebarProjects.map((p) => {
              const isProjActive = route.page === "project" && route.id === p.id;
              const isTreeOpen = !!sidebarExpandedProjects[p.id] || isProjActive;
              const pGroups = getProjectGroups(p);

              return (
                <div key={p.id} className="sidebar-tree-group">
                  <div
                    className={
                      isProjActive ? "sidebar-shortcut-row active" : "sidebar-shortcut-row"
                    }
                  >
                    <button
                      type="button"
                      className="sidebar-expand-btn"
                      aria-label={isTreeOpen ? "Collapse phases" : "Expand phases"}
                      onClick={(e) => {
                        e.stopPropagation();
                        setSidebarExpandedProjects((prev) => ({
                          ...prev,
                          [p.id]: !isTreeOpen,
                        }));
                      }}
                    >
                      {pGroups.length > 0 ? (isTreeOpen ? "⌄" : "›") : "·"}
                    </button>
                    <button
                      type="button"
                      className={isProjActive ? "sidebar-shortcut active" : "sidebar-shortcut"}
                      onClick={() => selectProject(p.id)}
                      title={p.name}
                    >
                      <span className="sidebar-truncate">{p.name}</span>
                    </button>
                  </div>

                  {isTreeOpen && pGroups.length > 0 && (
                    <div className="sidebar-nested-phases">
                      {pGroups.map((ph) => {
                        const isPhSelected = isProjActive && selectedPhaseId === ph.id;
                        return (
                          <button
                            key={ph.id}
                            type="button"
                            className={
                              isPhSelected ? "sidebar-phase-link active" : "sidebar-phase-link"
                            }
                            onClick={() => selectProject(p.id, ph.id, null)}
                            title={ph.name}
                          >
                            <span className="sidebar-truncate">{ph.name}</span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="sidebar-section-label" style={{ marginTop: 14 }}>
            Learning
          </div>
          <div className="sidebar-shortcut-list">
            {sidebarLearning.map((l) => {
              const isLpActive = route.page === "learning-path" && route.id === l.id;
              return (
                <button
                  key={l.id}
                  type="button"
                  className={isLpActive ? "sidebar-shortcut active" : "sidebar-shortcut"}
                  onClick={() => selectLearningPath(l.id)}
                  title={l.name}
                >
                  <span className="sidebar-truncate">{l.name}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      <SettingsMenu collapsed={sidebarCollapsed} />
    </aside>
  );
}
