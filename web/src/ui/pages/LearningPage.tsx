/**
 * The learning workspace ("learning" / "learning-path" routes): navigation tree, the selected
 * path's contents with the study-item inspector, and the curriculum map with its context panel.
 */
import { useWorkbench } from "@workbench-core";
import { LearningItemInspector } from "../components/Inspector";
import { LearningHeader } from "../components/LearningHeader";
import { LearningNavPanel } from "../components/LearningNavPanel";
import { LearningTopicList } from "../components/LearningTopicList";
import { LearningVisualPanel } from "../components/LearningVisualPanel";
import { PanelResizer } from "../components/PanelResizer";
import { PanelTabs } from "../components/PanelTabs";
import { MAX_CONTENTS_WIDTH, MIN_CONTENTS_WIDTH } from "../layoutPrefs";
import { useLook } from "../lookState";
import { useRevealSelection } from "../revealSelection";

// Learning Workspace — Three Coordinated Resizable Panels + Adjustable Context Panel
export function LearningPage() {
  const { activeLearningPath: activeLp, activeLearningItem, selection } = useWorkbench();
  const { panelWidths, mobilePanel } = useLook();
  useRevealSelection(activeLp?.id, selection.subtopicId ?? selection.topicId, mobilePanel);

  return (
    <div className="three-panel-workspace" data-testid="learning-three-panel-workspace">
      <PanelTabs />

      <LearningNavPanel />

      {/* Draggable Divider 1 */}
      <PanelResizer which="left" label="Resize navigation and content panels" />

      {/* PANEL B: Selected Topic / Subtopic Contents & Status Controls (Centre) */}
      <section
        className={`workspace-panel panel-b-contents ${
          mobilePanel === "contents" ? "resp-active" : ""
        }`}
        style={{
          width: panelWidths.contentsWidth,
          minWidth: MIN_CONTENTS_WIDTH,
          maxWidth: MAX_CONTENTS_WIDTH,
        }}
        data-testid="learning-panel-contents"
      >
        <div className="panel-sticky-header">
          <span className="panel-header-title">
            {activeLearningItem
              ? `Study · ${activeLearningItem.name}`
              : activeLp?.name || "Topic Contents"}
          </span>
        </div>

        {activeLp ? (
          <div className="panel-independent-scroll" data-testid={`learning-page-${activeLp.id}`}>
            <LearningHeader learningPath={activeLp} />

            <LearningItemInspector />

            <LearningTopicList learningPath={activeLp} />
          </div>
        ) : (
          <div className="empty-pane-card">Select a learning path.</div>
        )}
      </section>

      {/* Draggable Divider 2 */}
      <PanelResizer which="right" label="Resize content and diagram panels" />

      <LearningVisualPanel />
    </div>
  );
}
