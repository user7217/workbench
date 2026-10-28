/**
 * A deliberately tiny alternative look for Workbench: a two-column page that uses nothing but
 * `useWorkbench()` from the core. Build or run it with
 *
 *   WORKBENCH_UI=examples/minimal-look npm run dev     (or npm run build)
 *
 * Copy this folder to start your own look. The only contract: index.tsx default-exports a component.
 */
import { useState } from "react";
import {
  useWorkbench,
  getGroupChildren,
  getProjectGroups,
  pendingKeys,
  ENTITY_STATUSES,
  ITEM_STATUSES,
  LEARNING_ITEM_STATUSES,
  STATUS_LABELS,
  type Component,
} from "@workbench-core";
import "./minimal.css";

/** A plain <select> of statuses; disabled while that item's write is in flight. */
function StatusSelect<S extends string>(props: {
  value: string;
  options: readonly S[];
  busy: boolean;
  onChange: (s: S) => void;
}) {
  return (
    <select
      className={`status s-${props.value}`}
      value={props.value}
      disabled={props.busy}
      onChange={(e) => props.onChange(e.target.value as S)}
    >
      {props.options.map((s) => (
        <option key={s} value={s}>
          {STATUS_LABELS[s] ?? s}
        </option>
      ))}
    </select>
  );
}

export default function MinimalLook() {
  const wb = useWorkbench();
  const project = wb.route.page === "project" ? wb.activeProject : undefined;
  const path = wb.route.page === "learning-path" ? wb.activeLearningPath : undefined;
  const [draft, setDraft] = useState<string | null>(null);

  // Components at any depth, indented by nesting level
  const renderComponents = (projectId: string, comps: Component[], depth: number): JSX.Element[] =>
    comps.flatMap((c) => [
      <li key={c.id} style={{ marginLeft: depth * 18 }}>
        <StatusSelect
          value={c.status ?? "not_started"}
          options={ITEM_STATUSES}
          busy={wb.isPending(pendingKeys.projectItem(projectId, c.id))}
          onChange={(s) => wb.setItemStatus(projectId, c.id, s)}
        />
        <span>{c.name}</span>
        {c.summary && <small>{c.summary}</small>}
      </li>,
      ...renderComponents(projectId, c.components ?? [], depth + 1),
    ]);

  return (
    <div className="mini">
      <aside>
        <h1>Workbench</h1>
        <input placeholder="Search…" value={wb.filterText} onChange={(e) => wb.setFilter(e.target.value)} />
        <h2>Projects</h2>
        {wb.listedProjects.filter((p) => p.status !== "archived").map((p) => (
          <button key={p.id} className={p.id === project?.id ? "on" : ""} onClick={() => (setDraft(null), wb.selectProject(p.id))}>
            {p.name} <em>{STATUS_LABELS[p.status]}</em>
          </button>
        ))}
        <h2>Learning</h2>
        {wb.filteredLearning.map((l) => (
          <button key={l.id} className={l.id === path?.id ? "on" : ""} onClick={() => wb.selectLearningPath(l.id)}>
            {l.name} <em>{STATUS_LABELS[l.status]}</em>
          </button>
        ))}
      </aside>

      <main>
        {wb.lastError && (
          <p className="error" onClick={wb.dismissError}>
            {wb.lastError.message}
          </p>
        )}

        {project && (
          <>
            <header>
              <h2>{project.name}</h2>
              <StatusSelect
                value={project.status}
                options={ENTITY_STATUSES}
                busy={wb.isPending(pendingKeys.project(project.id))}
                onChange={(s) => wb.setProjectStatus(project.id, s)}
              />
            </header>
            {/* The one text edit: the project description (optimistic; reverts on failure) */}
            {draft === null ? (
              <p className="desc" onClick={() => setDraft(project.description ?? "")} title="Click to edit">
                {project.description || "Add a description…"}
              </p>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void wb.updateProjectFields(project.id, { description: draft });
                  setDraft(null);
                }}
              >
                <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} />
                <button type="submit">Save</button>
                <button type="button" onClick={() => setDraft(null)}>
                  Cancel
                </button>
              </form>
            )}
            {getProjectGroups(project).map((ph) => (
              <section key={ph.id}>
                <h3>
                  <StatusSelect
                    value={ph.status ?? "not_started"}
                    options={ITEM_STATUSES}
                    busy={wb.isPending(pendingKeys.projectItem(project.id, ph.id))}
                    onChange={(s) => wb.setItemStatus(project.id, ph.id, s)}
                  />
                  {ph.name}
                </h3>
                <ul>{renderComponents(project.id, getGroupChildren(ph), 0)}</ul>
              </section>
            ))}
          </>
        )}

        {path && (
          <>
            <header>
              <h2>{path.name}</h2>
            </header>
            <ul>
              {(path.topics ?? []).flatMap((t) => [t, ...(t.subtopics ?? [])]).map((item) => (
                <li key={item.id}>
                  <StatusSelect
                    value={item.status}
                    options={LEARNING_ITEM_STATUSES}
                    busy={wb.isPending(pendingKeys.learningItem(path.id, item.id))}
                    onChange={(s) => wb.setLearningItemStatus(path.id, item.id, s)}
                  />
                  <span>{item.name}</span>
                </li>
              ))}
            </ul>
          </>
        )}

        {!project && !path && <p className="hint">Pick a project or learning path.</p>}
      </main>
    </div>
  );
}
