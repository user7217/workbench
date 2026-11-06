/** The centre list of a learning path: each topic with its subtopic cards and items on demand. */
import { useWorkbench, getItemShortDescription, type LearningPath } from "@workbench-core";
import { useLook } from "../lookState";
import { LearningItemStatusMenu } from "./ItemStatusMenu";
import { StatusNodeGlyph } from "./status";

// Concise Topics & Subtopics List (§3)
export function LearningTopicList({ learningPath: activeLp }: { learningPath: LearningPath }) {
  const wb = useWorkbench();
  const { selection, selectLearningPath, activeTopic } = wb;
  const { expandedSubtopicItems, setExpandedSubtopicItems } = useLook();
  const selectedSubtopicId = selection.subtopicId;
  const activePathId = activeLp.id;
  const topics = activeLp.topics || [];

  return (
    <div className="centre-phases-stack">
      {topics.map((top) => {
        const isActiveTop = activeTopic?.id === top.id;
        const subs = top.subtopics || [];
        const topDesc = getItemShortDescription(top);

        return (
          <div
            key={top.id}
            className={`tier-1-phase-section ${isActiveTop ? "active-phase-card" : ""}`}
            data-testid={`learning-topic-${top.id}`}
          >
            <div
              className="tier-1-phase-header"
              onClick={() => selectLearningPath(activeLp.id, top.id, null)}
            >
              <div className="tier-1-header-main">
                <div className="tier-1-kicker-row">
                  <span className="tier-1-kicker-badge">TOPIC</span>
                  <LearningItemStatusMenu pathId={activeLp.id} item={top} />
                  <span className="tier-1-phase-title">{top.name}</span>
                </div>
                {topDesc && <span className="tier-1-phase-summary">{topDesc}</span>}
              </div>
            </div>

            {subs.length > 0 && (
              <div className="tier-2-subtopics-Indent">
                {subs.map((sub) => {
                  const isSubSelected = selectedSubtopicId === sub.id;
                  const isItemsOpen = !!expandedSubtopicItems[sub.id];
                  const childItems = wb.subtopicDetails(activePathId, sub);
                  const shortDesc = getItemShortDescription(sub);

                  return (
                    <div
                      key={sub.id}
                      className={`tier-2-subtopic-group ${isSubSelected ? "selected" : ""}`}
                      data-testid={`learning-subtopic-${sub.id}`}
                    >
                      <div
                        className="tier-2-subtopic-header"
                        onClick={(e) => {
                          e.stopPropagation();
                          selectLearningPath(activeLp.id, top.id, sub.id);
                        }}
                      >
                        <div className="tier-2-header-left">
                          <LearningItemStatusMenu pathId={activeLp.id} item={sub} />
                          <div className="tier-2-text-col">
                            <span className="tier-2-subtopic-title">{sub.name}</span>
                            {shortDesc && <p className="tier-2-subtopic-desc">{shortDesc}</p>}
                          </div>
                        </div>
                        {childItems.length > 0 && (
                          <button
                            type="button"
                            className="tree-chevron-btn sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              setExpandedSubtopicItems((prev) => ({
                                ...prev,
                                [sub.id]: !isItemsOpen,
                              }));
                            }}
                          >
                            {isItemsOpen ? "⌄" : "›"}
                          </button>
                        )}
                      </div>

                      {isItemsOpen && childItems.length > 0 && (
                        <div className="tier-3-items-list">
                          {childItems.map((item) => (
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
            )}
          </div>
        );
      })}
    </div>
  );
}
