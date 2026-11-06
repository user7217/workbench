/**
 * Panel C of the learning workspace: the curriculum map and, when a topic is selected, a
 * resizable context panel beneath it with that topic's subtopics.
 */
import { useWorkbench, getItemShortDescription } from "@workbench-core";
import { MIN_DIAGRAM_HEIGHT } from "../layoutPrefs";
import { useLook } from "../lookState";
import { LearningItemStatusMenu } from "./ItemStatusMenu";
import { PanelResizer } from "./PanelResizer";
import { StatusNodeGlyph } from "./status";
import { VisualGraph } from "./VisualGraph";

// PANEL C: Curriculum Map + Adjustable Contextual Panel Beneath It
export function LearningVisualPanel() {
  const wb = useWorkbench();
  const { selection, selectLearningPath, activeLearningPath: activeLp, activeTopic } = wb;
  const {
    panelWidths,
    isCustomPanelWidths,
    resetPanelWidths,
    mobilePanel,
    expandedContextSubtopics,
    setExpandedContextSubtopics,
  } = useLook();
  const selectedTopicId = selection.topicId;
  const selectedSubtopicId = selection.subtopicId;
  const activePathId = activeLp?.id || "";

  return (
    <section
      className={`workspace-panel panel-c-visual ${mobilePanel === "visual" ? "resp-active" : ""}`}
      data-testid="learning-panel-visual"
    >
      <div className="panel-sticky-header">
        <span className="panel-header-title">Curriculum Map & Topic Context</span>
        {isCustomPanelWidths && (
          <button type="button" className="ghost-sm-btn" onClick={resetPanelWidths}>
            Reset layout
          </button>
        )}
      </div>

      <div className="panel-c-split-container">
        {activeLp ? (
          <>
            <div
              className="panel-c-diagram-frame"
              data-testid="panel-c-diagram-pane"
              style={{
                height: activeTopic ? panelWidths.diagramHeight : "100%",
                minHeight: MIN_DIAGRAM_HEIGHT,
              }}
            >
              <VisualGraph
                learningPath={activeLp}
                selectedId={selectedSubtopicId || selectedTopicId}
                selectedPhaseId={activeTopic?.id || null}
                onSelectLearningItem={(item, parentTopic) => {
                  if (parentTopic && parentTopic.id !== item.id) {
                    selectLearningPath(activeLp.id, parentTopic.id, item.id);
                  } else {
                    selectLearningPath(activeLp.id, item.id, null);
                  }
                }}
              />
            </div>

            {activeTopic && (
              <>
                <PanelResizer
                  which="horizontal"
                  label="Resize curriculum overview and contextual section"
                />

                <div className="visual-context-panel" data-testid="visual-context-panel">
                  <div
                    data-testid="panel-c-scroll"
                    style={{
                      overflowY: "auto",
                      flex: 1,
                      minHeight: 0,
                      display: "flex",
                      flexDirection: "column",
                      gap: 10,
                    }}
                  >
                    <div className="visual-context-header">
                      <div className="visual-context-header-top">
                        <span className="tier-1-kicker-badge accent">SELECTED TOPIC</span>
                        <LearningItemStatusMenu
                          pathId={activeLp.id}
                          item={activeTopic}
                          testIdPrefix="context-"
                        />
                      </div>
                      <h3 className="visual-context-title" data-testid="visual-context-title">
                        {activeTopic.name}
                      </h3>
                      {getItemShortDescription(activeTopic) && (
                        <p className="visual-context-desc" data-testid="visual-context-desc">
                          {getItemShortDescription(activeTopic)}
                        </p>
                      )}
                    </div>

                    <div className="visual-context-subtopics">
                      {(activeTopic.subtopics || []).map((sub) => {
                        const isSubSelected = selectedSubtopicId === sub.id;
                        const isExpanded = !!expandedContextSubtopics[sub.id];
                        const items = wb.subtopicDetails(activePathId, sub);
                        const shortDesc = getItemShortDescription(sub);

                        return (
                          <div
                            key={sub.id}
                            className={`tier-2-subtopic-group ${isSubSelected ? "selected" : ""}`}
                          >
                            <div
                              className={`tier-2-subtopic-header ${
                                isSubSelected ? "selected" : ""
                              }`}
                              data-testid={`visual-context-subtopic-${sub.id}`}
                              onClick={() =>
                                selectLearningPath(activeLp.id, activeTopic.id, sub.id)
                              }
                            >
                              <div className="tier-2-header-left">
                                <LearningItemStatusMenu
                                  pathId={activeLp.id}
                                  item={sub}
                                  testIdPrefix="context-"
                                />
                                <div className="tier-2-text-col">
                                  <span className="tier-2-subtopic-title">{sub.name}</span>
                                  {shortDesc && <p className="tier-2-subtopic-desc">{shortDesc}</p>}
                                </div>
                              </div>
                              {items.length > 0 && (
                                <button
                                  type="button"
                                  className="tree-chevron-btn sm"
                                  data-testid={`visual-context-expand-${sub.id}`}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setExpandedContextSubtopics((prev) => ({
                                      ...prev,
                                      [sub.id]: !isExpanded,
                                    }));
                                  }}
                                >
                                  {isExpanded ? "⌄" : "›"}
                                </button>
                              )}
                            </div>

                            {isExpanded && items.length > 0 && (
                              <div
                                className="tier-3-items-list"
                                data-testid={`visual-context-items-${sub.id}`}
                              >
                                {items.map((item) => (
                                  <div key={item.id} className="tier-3-item-row">
                                    <span className="tier-3-branch-line">└</span>
                                    <StatusNodeGlyph status={item.status} />
                                    <span className="tier-3-item-kind">{item.kindLabel}:</span>
                                    <span className="tier-3-item-text">{item.text}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}
