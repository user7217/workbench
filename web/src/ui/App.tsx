import { useEffect, useRef } from "react";
import { useWorkbench } from "@workbench-core";
import { FooterBar } from "./components/FooterBar";
import { SearchHeader } from "./components/SearchHeader";
import { Sidebar } from "./components/Sidebar";
import { LookProvider, useLookState } from "./lookState";
import { HomePage } from "./pages/HomePage";
import { LearningPage } from "./pages/LearningPage";
import { ProjectPage } from "./pages/ProjectPage";

/**
 * The current Workbench look. All data, selection and writes come from useWorkbench(); look-only
 * layout state comes from useLookState() and reaches the pages through <LookProvider>.
 */
export function App() {
  const { route, lastError, dismissError, clearComponentSelection } = useWorkbench();
  const look = useLookState();

  const filterInputRef = useRef<HTMLInputElement | null>(null);

  // A failed write shows a toast for 4s (a new failure restarts the timer)
  const toastError = lastError?.message ?? null;
  useEffect(() => {
    if (!lastError) return;
    const timer = setTimeout(dismissError, 4000);
    return () => clearTimeout(timer);
  }, [lastError, dismissError]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        clearComponentSelection();
        return;
      }
      if (
        e.key === "/" &&
        document.activeElement !== filterInputRef.current &&
        !(document.activeElement instanceof HTMLInputElement) &&
        !(document.activeElement instanceof HTMLTextAreaElement)
      ) {
        e.preventDefault();
        filterInputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [clearComponentSelection]);

  const isSplitWorkspace =
    route.page === "building" ||
    route.page === "project" ||
    route.page === "learning" ||
    route.page === "learning-path";

  return (
    <LookProvider value={look}>
      <div className="workbench-layout">
        <Sidebar />

        {/* Main Content Area */}
        <div className="main-column">
          <SearchHeader inputRef={filterInputRef} />

          <div className="content-and-inspector">
            <main className={isSplitWorkspace ? "main-scroll split-mode" : "main-scroll"}>
              {route.page === "home" && <HomePage />}
              {route.page === "building" && <ProjectPage />}
              {route.page === "project" && <ProjectPage />}
              {route.page === "learning" && <LearningPage />}
              {route.page === "learning-path" && <LearningPage />}
            </main>
          </div>

          <FooterBar />
        </div>

        {toastError && (
          <div className="toast-error" data-testid="toast-error">
            {toastError}
          </div>
        )}
      </div>
    </LookProvider>
  );
}
