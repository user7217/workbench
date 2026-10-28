/**
 * Inline editing primitives of the default look: one open editor at a time (EditSession), a
 * single-field editor (EditableField), an inline "+ Add …" input (AddItemInline) and a modal
 * Dialog. They hold only UI state; saving goes through the core actions passed in as callbacks.
 *
 * Keys: Esc cancels; Enter saves a single-line field; Cmd/Ctrl+Enter saves a textarea.
 */
import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { createPortal } from "react-dom";

// ---------------------------------------------------------------------------------------------
// Edit session: which editor (if any) is open
// ---------------------------------------------------------------------------------------------

interface EditSession {
  activeKey: string | null;
  open: (key: string) => void;
  close: () => void;
}

const EditSessionContext = createContext<EditSession>({ activeKey: null, open: () => {}, close: () => {} });

/** Only one editor is open at a time, so `save-edit` / `cancel-edit` are unique on the page. */
export function EditSessionProvider({ children }: { children: ReactNode }) {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const value: EditSession = { activeKey, open: setActiveKey, close: () => setActiveKey(null) };
  return <EditSessionContext.Provider value={value}>{children}</EditSessionContext.Provider>;
}

export const useEditSession = () => useContext(EditSessionContext);

const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

/** Keyboard rules shared by every editor. */
function editorKeys(multiline: boolean, onSave: () => void, onCancel: () => void) {
  return (e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (e.key === "Escape") {
      // Keep the page's own Escape (close the inspector) from firing too
      e.preventDefault();
      e.stopPropagation();
      onCancel();
    } else if (e.key === "Enter" && (!multiline || e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      onSave();
    }
  };
}

function SaveCancel({ onSave, onCancel, busy, saveLabel = "Save" }: {
  onSave: () => void;
  onCancel: () => void;
  busy?: boolean;
  saveLabel?: string;
}) {
  return (
    <div className="edit-actions">
      <button type="button" className="edit-btn primary" data-testid="save-edit" disabled={busy} onClick={onSave}>
        {busy ? "Saving…" : saveLabel}
      </button>
      <button type="button" className="edit-btn" data-testid="cancel-edit" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// EditableField
// ---------------------------------------------------------------------------------------------

export type FieldKind = "line" | "text" | "list";

interface EditableFieldProps {
  /** Item id; with `field` forms the test id `edit-<field>-<id>`. */
  id: string;
  field: string;
  /** Human name of the field, e.g. "summary" (used in labels). */
  label: string;
  /** line = single-line input, text = textarea, list = textarea with one entry per line. */
  kind: FieldKind;
  value: string | string[];
  /** Persist the new value (the core write is optimistic, so the editor closes right away). */
  onSave: (value: string | string[]) => void;
  /** A write to this item is in flight: the edit affordance is disabled. */
  busy?: boolean;
  /** The current display of the value; when empty, an "+ Add <label>" button is shown instead. */
  children?: ReactNode;
  /** Show the "+ Add …" button when the value is empty (otherwise nothing is shown). */
  addWhenEmpty?: boolean;
  /** Whether there is nothing to display (default: the value is empty). Lets a fallback display show. */
  displayEmpty?: boolean;
  className?: string;
}

const toText = (v: string | string[]) => (Array.isArray(v) ? v.join("\n") : v);
const isEmpty = (v: string | string[]) => (Array.isArray(v) ? v.length === 0 : v.trim() === "");

/** A field shown as-is with a small pencil; editing swaps it for an input with Save / Cancel. */
export function EditableField({
  id,
  field,
  label,
  kind,
  value,
  onSave,
  busy = false,
  children,
  addWhenEmpty = true,
  displayEmpty,
  className = "",
}: EditableFieldProps) {
  const session = useEditSession();
  const key = `field:${id}:${field}`;
  const editing = session.activeKey === key;
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (editing) {
      setDraft(toText(value));
      requestAnimationFrame(() => inputRef.current?.focus());
    }
    // Reset the draft only when the editor opens
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const save = () => {
    if (busy) return;
    const next =
      kind === "list"
        ? draft.split("\n").map((l) => l.trim()).filter(Boolean)
        : kind === "line"
          ? draft.trim()
          : draft.replace(/\s+$/, "");
    session.close();
    onSave(next);
  };
  const onKeyDown = editorKeys(kind !== "line", save, session.close);

  if (editing) {
    const common = {
      className: `edit-input ${kind === "line" ? "" : "multiline"}`,
      value: draft,
      disabled: busy,
      "aria-label": `Edit ${label}`,
      "data-testid": `edit-input-${field}-${id}`,
      onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
      onKeyDown,
      onClick: stop,
    };
    return (
      <div className={`inline-editor ${className}`} onClick={stop}>
        {kind === "line" ? (
          <input {...common} ref={(el) => (inputRef.current = el)} />
        ) : (
          <textarea
            {...common}
            ref={(el) => (inputRef.current = el)}
            rows={kind === "list" ? 4 : 3}
            placeholder={kind === "list" ? "One entry per line" : undefined}
          />
        )}
        <div className="edit-footer">
          <span className="edit-hint">{kind === "line" ? "Enter to save · Esc to cancel" : "⌘/Ctrl+Enter to save · Esc to cancel"}</span>
          <SaveCancel onSave={save} onCancel={session.close} busy={busy} />
        </div>
      </div>
    );
  }

  const pencil = (
    <button
      type="button"
      className="edit-pencil"
      data-testid={`edit-${field}-${id}`}
      aria-label={`Edit ${label}`}
      title={`Edit ${label}`}
      disabled={busy}
      onClick={(e) => {
        e.stopPropagation();
        session.open(key);
      }}
    >
      ✎
    </button>
  );

  if (displayEmpty ?? isEmpty(value)) {
    if (!addWhenEmpty) return null;
    return (
      <button
        type="button"
        className="edit-add-empty"
        data-testid={`edit-${field}-${id}`}
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();
          session.open(key);
        }}
      >
        + Add {label}
      </button>
    );
  }

  return (
    <div className={`editable ${className}`}>
      <div className="editable-content">{children}</div>
      {pencil}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// AddItemInline
// ---------------------------------------------------------------------------------------------

interface AddItemInlineProps {
  testId: string;
  /** Button text, e.g. "Add component". */
  label: string;
  placeholder?: string;
  busy?: boolean;
  /** Create the item; resolves true when created (the input then closes). */
  onAdd: (name: string) => Promise<boolean>;
  className?: string;
}

/** "+ Add …" button that turns into a name input; Enter adds, Esc cancels. Not optimistic. */
export function AddItemInline({ testId, label, placeholder, busy = false, onAdd, className = "" }: AddItemInlineProps) {
  const session = useEditSession();
  const key = `add:${testId}`;
  const editing = session.activeKey === key;
  const [name, setName] = useState("");

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    if (await onAdd(trimmed)) {
      setName("");
      session.close();
    }
  };

  if (!editing) {
    return (
      <button
        type="button"
        className={`add-item-btn ${className}`}
        data-testid={testId}
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();
          setName("");
          session.open(key);
        }}
      >
        + {label}
      </button>
    );
  }
  return (
    <div className={`inline-editor add-item-editor ${className}`} onClick={stop}>
      <input
        className="edit-input"
        autoFocus
        value={name}
        disabled={busy}
        placeholder={placeholder ?? "Name"}
        aria-label={label}
        data-testid={`${testId}-input`}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={editorKeys(false, () => void submit(), session.close)}
      />
      <SaveCancel onSave={() => void submit()} onCancel={session.close} busy={busy} saveLabel="Add" />
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Dialog
// ---------------------------------------------------------------------------------------------

interface DialogProps {
  title: string;
  testId: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

/** Small centred modal; Esc or a click on the backdrop closes it. */
export function Dialog({ title, testId, onClose, children, footer }: DialogProps) {
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return createPortal(
    <div className="dialog-backdrop" onMouseDown={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
        onMouseDown={stop}
        onClick={stop}
      >
        <h3 className="dialog-title">{title}</h3>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-footer">{footer}</div>}
      </div>
    </div>,
    document.body
  );
}
