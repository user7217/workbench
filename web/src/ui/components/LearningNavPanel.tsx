/** Panel A of the learning workspace: the tree of learning paths, their topics and subtopics. */
import { useWorkbench } from "@workbench-core";
import { MAX_NAV_WIDTH, MIN_NAV_WIDTH } from "../layoutPrefs";
import { useLook } from "../lookState";
import { KindBadge, StatusNodeGlyph } from "./status";

// PANEL A: Learning Navigation (Left)
export function LearningNavPanel() {
  const {
    selection,
    isExpanded: expandedIn,
    setExpanded,
    filteredLearning,
    selectLearningPath,
    activeLearningPath: activeLp,
    activeTopic,
  } = useWorkbench();
  const { panelWidths, mobilePanel } = useLook();
  const selectedSubtopicId = selection.subtopicId;
  const activePathId = activeLp?.id || "";

  return (
    <section
      className={`workspace-panel panel-a-nav ${mobilePanel === "nav" ? "resp-active" : ""}`}
      style={{
        width: panelWidths.navWidth,
        minWidth: MIN_NAV_WIDTH,
        maxWidth: MAX_NAV_WIDTH,
      }}
      data-testid="learning-panel-nav"
    >
      <div className="panel-sticky-header">
        <span className="panel-header-title">Learning Paths</span>
      </div>

      <div className="panel-independent-scroll">
        {filteredLearning.map((lp) => {
          const isSelected = lp.id === activePathId;
          const isExpanded = expandedIn("learningPaths", lp.id, isSelected);
          const lpTopics = lp.topics || [];

          return (
            <div key={lp.id} className={`tree-project-block ${isSelected ? "selected" : ""}`}>
              <div
                className={`tree-project-row ${isSelected ? "active" : ""}`}
                data-testid={`learning-card-${lp.id}`}
                onClick={() => selectLearningPath(lp.id)}
              >
                <button
                  type="button"
                  className="tree-chevron-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpanded("learningPaths", lp.id, !isExpanded);
                  }}
                >
                  {lpTopics.length > 0 ? (isExpanded ? "⌄" : "›") : "·"}
                </button>
                <KindBadge status={lp.status || "active"} isLearning={true} size={22} />
                <span className="tree-project-name" title={lp.name}>
                  {lp.name}
                </span>
              </div>

              {isExpanded && lpTopics.length > 0 && (
                <div className="tree-phases-children">
                  {lpTopics.map((top) => {
                    const isTopActive = isSelected && activeTopic?.id === top.id;
                    const isTopExpanded = expandedIn("topics", top.id, isTopActive);
                    const subs = top.subtopics || [];

                    return (
                      <div key={top.id} className="tree-phase-group">
                        <div
                          className={`tree-phase-row ${
                            isTopActive && !selectedSubtopicId ? "active" : ""
                          }`}
                          onClick={(e) => {
                            e.stopPropagation();
                            selectLearningPath(lp.id, top.id, null);
                          }}
                        >
                          <button
                            type="button"
                            className="tree-chevron-btn sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              setExpanded("topics", top.id, !isTopExpanded);
                            }}
                          >
                            {subs.length > 0 ? (isTopExpanded ? "⌄" : "›") : "·"}
                          </button>
                          <span className="tree-phase-name" title={top.name}>
                            {top.name}
                          </span>
                          <StatusNodeGlyph status={top.status} />
                        </div>

                        {isTopExpanded && subs.length > 0 && (
                          <div className="tree-comp-children">
                            {subs.map((sub) => {
                              const isSubActive = isSelected && selectedSubtopicId === sub.id;
                              return (
                                <div
                                  key={sub.id}
                                  className={`tree-comp-row ${isSubActive ? "active" : ""}`}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    selectLearningPath(lp.id, top.id, sub.id);
                                  }}
                                >
                                  <span className="tree-branch-tick">└</span>
                                  <StatusNodeGlyph status={sub.status} />
                                  <span className="tree-comp-name" title={sub.name}>
                                    {sub.name}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
