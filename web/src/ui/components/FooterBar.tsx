/**
 * The footer: the failed-sync banner (with expandable details), the synced commit and the
 * "Check for updates" link.
 */
import { useState } from "react";
import { useWorkbench } from "@workbench-core";

export function FooterBar() {
  const wb = useWorkbench();
  const { snapshot, syncStatus } = wb;
  const [errorExpanded, setErrorExpanded] = useState(false);

  return (
    <footer className="footer-bar" data-testid="footer-bar">
      {syncStatus.last_error && (
        <div className="footer-amber-banner" data-testid="failed-sync-banner">
          <div>
            <span>Latest update failed validation — showing previous version</span>
            <button
              type="button"
              onClick={() => setErrorExpanded(!errorExpanded)}
              style={{ marginLeft: 8, padding: "2px 6px", fontSize: 11 }}
              data-testid="toggle-error-details"
            >
              {errorExpanded ? "Hide" : "Details"}
            </button>
          </div>
          {errorExpanded && (
            <div style={{ fontSize: 11, color: "var(--danger)" }} data-testid="error-details-box">
              Commit {syncStatus.last_error.commit.slice(0, 7)} ({syncStatus.last_error.file}):{" "}
              {syncStatus.last_error.message}
            </div>
          )}
        </div>
      )}

      <div className="footer-main-row">
        <span>
          {snapshot.commit ? (
            <>
              Synced · commit <code>{snapshot.commit.slice(0, 7)}</code>
            </>
          ) : (
            "Waiting for initial sync..."
          )}
        </span>
        <a
          href="#sync"
          onClick={(e) => {
            e.preventDefault();
            void wb.sync();
          }}
          data-testid="check-updates-button"
        >
          Check for updates
        </a>
      </div>
    </footer>
  );
}
