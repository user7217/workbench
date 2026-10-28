/**
 * Project structure controls of the default look: reorder (up/down), "Move to…", "Delete…" and the
 * project's "Part of…" link. Structure writes are server-authoritative: controls are disabled while
 * `pendingKeys.structure(projectId)` is pending and failures come back as `lastError`.
 */
import { useState } from "react";
import {
  useWorkbench,
  useWorkbenchStore,
  countDescendants,
  getGroupChildren,
  getProjectGroups,
  locateItemPlacement,
  pendingKeys,
  projectUsesPhases,
  subtreeIds,
  type Component,
  type Phase,
  type Project,
} from "@workbench-core";
import { Dialog } from "./Editing";

/** Every phase and component of a project, depth-first, with its depth (phase = 0). */
function flattenItems(project: Project): { item: Phase | Component; depth: number; isPhase: boolean }[] {
  const out: { item: Phase | Component; depth: number; isPhase: boolean }[] = [];
  const visit = (comps: Component[], depth: number) => {
    for (const c of comps) {
      out.push({ item: c, depth, isPhase: false });
      visit(c.components || [], depth + 1);
    }
  };
  for (const ph of getProjectGroups(project)) {
    out.push({ item: ph, depth: 0, isPhase: true });
    visit(getGroupChildren(ph), 1);
  }
  return out;
}

interface ItemStructureActionsProps {
  project: Project;
  item: Phase | Component;
  /** True for a phase/topic group (reorder only; no "Move to…"). */
  isPhase: boolean;
}

/** ↑ / ↓ / Move to… / Delete… for one phase or component. */
export function ItemStructureActions({ project, item, isPhase }: ItemStructureActionsProps) {
  const wb = useWorkbench();
  const [dialog, setDialog] = useState<"move" | "delete" | null>(null);
  const busy = wb.isPending(pendingKeys.structure(project.id));
  const placement = locateItemPlacement(project, item.id);
  if (!placement) return null;
  const { index, siblings } = placement;

  const reorder = (to: number) => void wb.moveItem(project.id, item.id, { position: to });

  return (
    <div className="item-actions" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className="item-action-btn"
        data-testid={`move-up-${item.id}`}
        title="Move up"
        aria-label={`Move ${item.name} up`}
        disabled={busy || index <= 0}
        onClick={() => reorder(index - 1)}
      >
        ↑
      </button>
      <button
        type="button"
        className="item-action-btn"
        data-testid={`move-down-${item.id}`}
        title="Move down"
        aria-label={`Move ${item.name} down`}
        disabled={busy || index >= siblings.length - 1}
        onClick={() => reorder(index + 1)}
      >
        ↓
      </button>
      {!isPhase && (
        <button
          type="button"
          className="item-action-btn text"
          data-testid={`move-to-${item.id}`}
          disabled={busy}
          onClick={() => setDialog("move")}
        >
          Move to…
        </button>
      )}
      <button
        type="button"
        className="item-action-btn text danger"
        data-testid={`delete-${item.id}`}
        disabled={busy}
        onClick={() => setDialog("delete")}
      >
        Delete…
      </button>
      {dialog === "move" && (
        <MoveToDialog project={project} item={item} parentId={placement.parentId} onClose={() => setDialog(null)} />
      )}
      {dialog === "delete" && (
        <DeleteDialog project={project} item={item} isPhase={isPhase} onClose={() => setDialog(null)} />
      )}
    </div>
  );
}

function MoveToDialog({ project, item, parentId, onClose }: {
  project: Project;
  item: Phase | Component;
  parentId: string | null;
  onClose: () => void;
}) {
  const wb = useWorkbench();
  const busy = wb.isPending(pendingKeys.structure(project.id));
  const excluded = subtreeIds(item);
  const targets = flattenItems(project).filter(({ item: t }) => !excluded.has(t.id));
  const groupLabel = projectUsesPhases(project) ? "phase" : "topic";

  const pick = async (target: Phase | Component) => {
    // Append at the end of the target's own components list
    const ok = await wb.moveItem(project.id, item.id, {
      parentId: target.id,
      position: (target.components || []).length,
    });
    if (!ok) return;
    onClose();
    // Reveal the item at its new place (expands the new ancestors)
    wb.openProjectAt(project.id, item.id);
  };

  return (
    <Dialog
      title={`Move “${item.name}” to…`}
      testId="move-dialog"
      onClose={onClose}
      footer={
        <button type="button" className="edit-btn" data-testid="cancel-move" onClick={onClose}>
          Cancel
        </button>
      }
    >
      <p className="dialog-text">Pick a {groupLabel} or component; it is added at the end of its parts.</p>
      <div className="move-target-list">
        {targets.map(({ item: t, depth, isPhase }) => (
          <button
            key={t.id}
            type="button"
            className={`move-target ${isPhase ? "phase" : ""}`}
            style={{ paddingLeft: 10 + depth * 14 }}
            data-testid={`move-target-${t.id}`}
            disabled={busy || t.id === parentId}
            title={t.id === parentId ? "Already here" : undefined}
            onClick={() => void pick(t)}
          >
            {isPhase ? `${groupLabel[0].toUpperCase()}${groupLabel.slice(1)} · ` : ""}
            {t.name}
            {t.id === parentId && <span className="move-target-note"> (current)</span>}
          </button>
        ))}
      </div>
    </Dialog>
  );
}

function DeleteDialog({ project, item, isPhase, onClose }: {
  project: Project;
  item: Phase | Component;
  isPhase: boolean;
  onClose: () => void;
}) {
  const wb = useWorkbench();
  const store = useWorkbenchStore();
  const [refusal, setRefusal] = useState<string | null>(null);
  const busy = wb.isPending(pendingKeys.structure(project.id));
  const nested = isPhase
    ? getGroupChildren(item as Phase).reduce((n, c) => n + 1 + countDescendants(c), 0)
    : countDescendants(item as Component);

  const confirm = async () => {
    setRefusal(null);
    if (await wb.deleteItem(project.id, item.id)) {
      onClose();
      return;
    }
    // Show the server's reason here instead of in the toast
    const err = store.getState().lastError;
    setRefusal(err?.detail ?? err?.message ?? "The server refused the delete.");
    wb.dismissError();
  };

  return (
    <Dialog
      title={`Delete “${item.name}”?`}
      testId="delete-dialog"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="edit-btn" data-testid="cancel-delete" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="edit-btn danger"
            data-testid="confirm-delete"
            disabled={busy}
            onClick={() => void confirm()}
          >
            {busy ? "Deleting…" : "Delete permanently"}
          </button>
        </>
      }
    >
      <p className="dialog-text">
        This permanently removes the {isPhase ? "group" : "component"}
        {nested > 0 ? ` and ${nested} nested ${nested === 1 ? "part" : "parts"}` : ""} from the project file. It
        cannot be undone from the dashboard.
      </p>
      {refusal && (
        <p className="dialog-error" data-testid="delete-error" role="alert">
          {refusal}
        </p>
      )}
    </Dialog>
  );
}

/** "Part of…" in the project header: attach this project under a parent's phase/component, or detach. */
export function ParentControl({ project }: { project: Project }) {
  const wb = useWorkbench();
  const [open, setOpen] = useState(false);
  const busy = wb.isPending(pendingKeys.structure(project.id));
  return (
    <>
      <button
        type="button"
        className="item-action-btn text"
        data-testid="parent-control"
        disabled={busy}
        title={project.parent ? "Change or remove the parent project" : "Attach to a parent project"}
        onClick={() => setOpen(true)}
      >
        {project.parent ? "Part of… (change)" : "Part of…"}
      </button>
      {open && <ParentDialog project={project} onClose={() => setOpen(false)} />}
    </>
  );
}

function ParentDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const wb = useWorkbench();
  const busy = wb.isPending(pendingKeys.structure(project.id));
  const candidates = wb.snapshot.projects
    .filter((p) => p.id !== project.id)
    .sort((a, b) => a.name.localeCompare(b.name));
  const [parentId, setParentId] = useState<string>(project.parent ?? candidates[0]?.id ?? "");
  const parent = wb.projectsById.get(parentId);
  const slots = parent ? flattenItems(parent) : [];
  const [under, setUnder] = useState<string>(project.under ?? "");
  const underValid = slots.some(({ item }) => item.id === under);

  const save = async (link: { parent: string; under: string } | null) => {
    if (await wb.setParent(project.id, link)) onClose();
  };

  return (
    <Dialog
      title={`“${project.name}” is part of…`}
      testId="parent-dialog"
      onClose={onClose}
      footer={
        <>
          {project.parent && (
            <button
              type="button"
              className="edit-btn danger"
              data-testid="parent-detach"
              disabled={busy}
              onClick={() => void save(null)}
            >
              Detach
            </button>
          )}
          <span className="dialog-footer-spacer" />
          <button type="button" className="edit-btn" data-testid="cancel-parent" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="edit-btn primary"
            data-testid="parent-save"
            disabled={busy || !parent || !underValid}
            onClick={() => void save({ parent: parentId, under })}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <label className="dialog-label">
        Parent project
        <select
          className="edit-input"
          data-testid="parent-project-select"
          value={parentId}
          onChange={(e) => {
            setParentId(e.target.value);
            setUnder("");
          }}
        >
          {candidates.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <label className="dialog-label">
        Sits under
        <select
          className="edit-input"
          data-testid="parent-under-select"
          value={underValid ? under : ""}
          onChange={(e) => setUnder(e.target.value)}
        >
          <option value="" disabled>
            Pick a phase or component…
          </option>
          {slots.map(({ item, depth }) => (
            <option key={item.id} value={item.id}>
              {"  ".repeat(depth)}
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <p className="dialog-text muted">The link is structural only: statuses never roll up.</p>
    </Dialog>
  );
}
