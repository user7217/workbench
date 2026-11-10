/**
 * Tabs that pick which of the three workspace panels is shown when the window is too narrow for
 * all of them (below 1080px, phones included). Hidden on wide screens by styles/responsive.css.
 */
import { useLook } from "../lookState";

const TABS = [
  { id: "nav", label: "Navigation" },
  { id: "contents", label: "Contents" },
  { id: "visual", label: "Visual Map" },
] as const;

export function PanelTabs() {
  const { mobilePanel, setMobilePanel } = useLook();
  return (
    <div className="responsive-panel-switcher" role="tablist" aria-label="Workspace panel">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={mobilePanel === tab.id}
          className={`resp-tab-btn ${mobilePanel === tab.id ? "active" : ""}`}
          data-testid={`panel-tab-${tab.id}`}
          onClick={() => setMobilePanel(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
