/** Home: the project and learning-path card grids, most recently changed first. */
import { useWorkbench } from "@workbench-core";
import { UnifiedLearningCard, UnifiedProjectCard } from "../components/HomeCards";

// Home View — Unified project & learning grids without separate status sections (§2)
export function HomePage() {
  const {
    showArchived: buildingArchivedOpen,
    setShowArchived,
    listedProjects,
    recentProjects,
    recentLearning,
  } = useWorkbench();
  // While searching, show every match; otherwise non-active child projects stay inside their parent
  // Home cards show the most recently changed work first
  const visibleProjects = recentProjects.filter(
    (p) => buildingArchivedOpen || p.status !== "archived",
  );
  const hasArchived = listedProjects.some((p) => p.status === "archived");

  return (
    <div className="home-unified-container">
      <section className="domain-section" data-testid="section-building">
        <div className="domain-header-bar">
          <div className="domain-header-left">
            <span className="domain-header-icon">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                style={{ stroke: "var(--accent)" }}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                <line x1="12" y1="22.08" x2="12" y2="12" />
              </svg>
            </span>
            <h2 className="domain-title">Projects</h2>
          </div>

          {hasArchived && (
            <button
              type="button"
              className="archived-toggle"
              data-testid="building-archived-toggle"
              onClick={() => setShowArchived(!buildingArchivedOpen)}
            >
              {buildingArchivedOpen ? "Hide Archived" : "Show Archived"}
            </button>
          )}
        </div>

        <div className="unified-cards-grid" data-testid="unified-projects-grid">
          {visibleProjects.map((p) => (
            <UnifiedProjectCard key={p.id} project={p} />
          ))}
        </div>
      </section>

      <section className="domain-section" data-testid="section-learning">
        <div className="domain-header-bar">
          <div className="domain-header-left">
            <span className="domain-header-icon">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                style={{ stroke: "var(--accent)" }}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M22 10v6M2 10l10-5 10 5-10 5z" />
                <path d="M6 12v5c3 3 9 3 12 0v-5" />
              </svg>
            </span>
            <h2 className="domain-title">Learning Paths</h2>
          </div>
        </div>

        <div className="unified-cards-grid" data-testid="unified-learning-grid">
          {recentLearning.map((lp) => (
            <UnifiedLearningCard key={lp.id} learningPath={lp} />
          ))}
        </div>
      </section>
    </div>
  );
}
