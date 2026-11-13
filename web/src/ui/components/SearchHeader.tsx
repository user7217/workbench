/** The top header with the project / learning-path filter box ("/" focuses it, see App). */
import type React from "react";
import { useEffect, useState } from "react";
import { useWorkbench } from "@workbench-core";

// The phone layout's breakpoint (styles/phone.css): no keyboard there, so no "/" hint
const PHONE_QUERY = "(max-width: 720px)";

function usePhoneLayout(): boolean {
  const [phone, setPhone] = useState(() => window.matchMedia(PHONE_QUERY).matches);
  useEffect(() => {
    const query = window.matchMedia(PHONE_QUERY);
    const onChange = () => setPhone(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return phone;
}

export function SearchHeader({ inputRef }: { inputRef: React.RefObject<HTMLInputElement> }) {
  const { filterText, setFilter } = useWorkbench();
  const phone = usePhoneLayout();
  return (
    <header className="top-search-header">
      <div className="search-box-wrapper">
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          style={{ stroke: "var(--text-muted)" }}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          className="filter-input"
          placeholder={
            phone
              ? "Filter projects and learning paths"
              : "Filter projects and learning paths... (press /)"
          }
          value={filterText}
          onChange={(e) => setFilter(e.target.value)}
          data-testid="filter-input"
        />
        {filterText && (
          <button
            type="button"
            className="clear-search-btn"
            onClick={() => setFilter("")}
            aria-label="Clear filter"
          >
            ×
          </button>
        )}
      </div>
    </header>
  );
}
