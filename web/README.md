# Workbench web UI — build your own look

The dashboard is split into a **core** that never changes and a **look** you can replace. This page
explains the split, gets a custom look running in a few minutes, and documents everything a look can
use.

![The default look: Home with projects and learning paths](docs/images/default-home.png)

## 1. Core vs look

| | Folder | What it owns |
|---|---|---|
| **Core** (fixed) | `src/core/` | Data loading, polling, every HTTP call (`api.ts`), the rules (`derive.ts`: project structure, child projects, listing/search, dependencies), allowed statuses (`statuses.ts`), state + actions (`store.ts`), the React binding (`react.ts`). No styling. |
| **Look** (swappable) | `src/ui/` by default | Layout, components, CSS. Reads and changes everything through `useWorkbench()`. |

Why: the rules about *what* the dashboard shows (which projects are listed, what a status may be,
how a write is saved and rolled back) are the part that has to stay correct. Keeping them in one
tested, framework-light place means a new look can be as different as you like (a single column, a
kanban, a terminal-style page) without re-implementing or breaking them.

**The one contract:** a look is a folder whose `index.tsx` default-exports a React component.
`main.tsx` mounts it inside `<WorkbenchProvider>`, so the component calls `useWorkbench()` from
`@workbench-core` and nothing else is required. Pick the folder with `WORKBENCH_UI=<path relative to
web/>` at dev or build time (default `src/ui`).

```tsx
// my-look/index.tsx — the smallest valid look
import { useWorkbench } from "@workbench-core";

export default function MyLook() {
  const wb = useWorkbench();
  return <ul>{wb.listedProjects.map((p) => <li key={p.id}>{p.name}</li>)}</ul>;
}
```

## 2. Quick start

```bash
cd web
npm install
cp -r examples/minimal-look examples/my-look       # any folder under web/ works
```

Start the demo API (artificial `demo-*` data in a throw-away folder) from the **repository root**:

```bash
WORKBENCH_PORT=8441 uv run python demo/run_demo.py
```

Run your look against it with hot reload (the dev server proxies `/api` to `WORKBENCH_API`):

```bash
cd web
WORKBENCH_UI=examples/my-look WORKBENCH_API=http://127.0.0.1:8441 npm run dev   # http://localhost:5173
```

Type-check and build it:

```bash
npm run typecheck:look                              # type-checks src/ and examples/ (tsconfig.looks.json)
WORKBENCH_UI=examples/my-look npm run build         # writes web/dist
```

The Python server serves whatever is in `web/dist` for every non-`/api` path, so after a build,
restarting the server (`make demo`, `demo/run_demo.py`, or your real server) serves your look. Run
`npm run build` without `WORKBENCH_UI` to go back to the default look.

![The minimal example look: one column per project, status selects and an editable description](docs/images/minimal-look.png)

`examples/minimal-look` (about 170 lines plus its own CSS) lists projects and learning paths, shows
a project's phases and nested components with status menus, and edits the project description.

## 3. The `useWorkbench()` reference

`useWorkbench()` returns **one object**: state fields, derived values/helpers and actions. It
re-renders on every store change. Outside React, `createWorkbench(options)` returns
`{getState, subscribe, actions, start, stop}` and `selectView(state)` gives the derived values.

### State

| Field | Type | Meaning |
|---|---|---|
| `snapshot` | `{projects, learning, topic_markdowns, commit, synced_at}` | Everything the server knows. |
| `syncStatus` | `{last_good_commit, last_checked_at, last_error}` | Last sync of the data repo; `last_error` = a bad commit that was skipped. |
| `route` | `{page: "home" \| "building" \| "learning"}` or `{page: "project" \| "learning-path", id}` | Current page. |
| `selection` | `{phaseId, componentId, topicId, subtopicId}` | What is selected on the project / learning page. |
| `expanded` | `Record<ExpandKind, Record<id, boolean>>` | Open/closed sets: `projects`, `phases`, `nestedComponents`, `childProjects`, `learningPaths`, `topics`. |
| `filterText`, `showArchived` | `string`, `boolean` | Search box text; whether archived projects are shown. |
| `pending` | `Record<key, true>` | Writes in flight (see `pendingKeys`). |
| `lastError` | `{message, detail?, seq} \| null` | Last failed write. `detail` is the server's reason alone; `seq` increments per error. |

### Derived values

| Value | Meaning |
|---|---|
| `query`, `searching` | Normalised search text, and whether it is non-empty. |
| `filteredProjects`, `filteredLearning` | Matches of the search (name or description), sorted by name. |
| `listedProjects` | Projects a list should show (applies the child-project rule; includes archived — filter with `showArchived`). |
| `sidebarProjects`, `sidebarLearning` | Non-archived shortcuts, ignoring the search. |
| `activeProject`, `activePhase`, `activeComponent` | The open project, its selected phase (default: first) and component (`{comp, phase, ancestors}`). |
| `activeParent` | Where the open project sits in its parent: `{parentId, parentName, phase, components}`. |
| `activeLearningPath`, `activeTopic`, `activeLearningItem`, `activeMarkdown` | Same for learning paths; `activeMarkdown` = notes of the selected topic/subtopic. |
| `projectsById`, `parentOf`, `childIndex` | Indexes over the snapshot. |

### Helpers

| Helper | Returns |
|---|---|
| `isPending(key)` | Whether a write with that key is in flight; disable its control. |
| `isExpanded(kind, id, fallback?)` | Open state, or `fallback` if never toggled. |
| `childProjectsAt(parentId, slotId)` | Child projects under a phase/component (or `ROOT_SLOT`). |
| `componentIndex(project)` | `Map<id, {comp, phase, ancestors}>` of every component at any depth. |
| `dependenciesOf(project, comp)`, `dependentsOf(project, compId)` | "Prerequisites" and "used by". |
| `componentDetails(project, comp)`, `subtopicDetails(pathId, sub)` | Level-3 rows (items, notes, deps, links). |
| `topicMarkdown(pathId, itemId)` | Markdown notes of a topic/subtopic. |
| `relatedLearning(project)`, `relatedProjects(lp)` | Cross references. |
| `projectTree(project)`, `learningTree(lp)` | Trees for maps/diagrams (include child projects). |

Pure functions are exported too: `getProjectGroups`, `getGroupChildren`, `projectUsesPhases`,
`locateProjectItem`, `locateItemPlacement` (parent, index and siblings — for move up/down),
`subtreeIds` (an item and its descendants — exclude them as move targets), `countDescendants`,
`getItemShortDescription`, `sortByName`, `filterByQuery`, and more (see the comment in
`src/core/index.ts`).

### Actions

Navigation and view state:

| Action | Notes |
|---|---|
| `navigate(route)` | Project / learning-path routes behave like the two below. |
| `selectProject(projectId, phaseId?, compId?)` | `phaseId`: `undefined` = first, `null` = none. `compId` reveals the component (its phase and ancestors expand). |
| `openProjectAt(projectId, itemId?)` | Open a project focused on any phase or component id. |
| `selectLearningPath(pathId, topicId?, subtopicId?)` | Same rules for topics/subtopics. |
| `clearComponentSelection()`, `setExpanded(kind, id, open)`, `setFilter(text)`, `setShowArchived(show)` | |
| `refresh()`, `sync()` | Reload the snapshot; ask the server to pull the data repo first. |
| `dismissError()` | Clear `lastError`. |

Writes — **status** (optimistic):

| Action | Allowed statuses |
|---|---|
| `setProjectStatus(projectId, status)`, `setLearningStatus(pathId, status)` | `ENTITY_STATUSES`: planned, active, paused, done, archived |
| `setItemStatus(projectId, itemId, status)` — phase or component, any depth | `ITEM_STATUSES`: not_started, in_progress, done, parked |
| `setLearningItemStatus(pathId, itemId, status)` — topic or subtopic | `LEARNING_ITEM_STATUSES`: not_started, in_progress, done |

Writes — **text** (optimistic; each resolves `true` on success or when nothing changed, `false` on failure):

| Action | Fields |
|---|---|
| `updateProjectFields(projectId, fields)` | `{name?, description?}` |
| `updateItemFields(projectId, itemId, fields)` | `{name?, summary?, notes?, description?, items?: string[]}` — `items` only on components |
| `updateLearningFields(pathId, fields)` | `{name?, description?}` |
| `updateLearningItemFields(pathId, itemId, fields)` | `{name?, summary?, notes?, description?}` |

Only changed fields are sent. `""` (or `items: []`) removes an optional field, so a cleared field is
**missing** from the snapshot: read a missing field as `""`. A name can't be empty (rejected before
any request).

Writes — **structure** (projects only; *not* optimistic — they wait for the server's snapshot):

| Action | Notes |
|---|---|
| `addItem(projectId, {parentId, name, id?, status?, summary?, position?})` → `Promise<string \| null>` | `parentId: null` adds a phase (a topic when the project has `uses_phases: false`); a phase id adds a component; a component id adds a nested component. Resolves to the new id (the server makes it unique) so you can select it. |
| `moveItem(projectId, itemId, {parentId?, position})` → `Promise<boolean>` | Omit `parentId` to reorder in place. Components can move under any phase/component except themselves and their descendants; phases only reorder. |
| `deleteItem(projectId, itemId)` → `Promise<boolean>` | Permanent; removes everything nested. Refused (422) while a `depends_on` or a child project points into it — `lastError.detail` names them. Clears a selection that pointed into it. |
| `setParent(projectId, {parent, under} \| null)` → `Promise<boolean>` | Attach under a parent project's phase/component, or detach with `null`. |

### Pending keys

`pendingKeys.project(id)`, `.projectItem(id, itemId)`, `.learning(id)`, `.learningItem(id, itemId)`
(status writes); `.projectFields(id)`, `.itemFields(id, itemId)`, `.learningFields(id)`,
`.learningItemFields(id, itemId)` (text edits); `.structure(projectId)` (any structure edit of that
project — one at a time). A second write with a key that is already pending is ignored.

### The `optimisticWrite` pattern

Status and text edits are built on one action, also available to looks:

```ts
wb.optimisticWrite({
  key: pendingKeys.itemFields(projectId, itemId), // marks the write pending
  request: (api) => api.postJson(url, body),       // the server call
  apply: (snapshot) => /* the change, applied immediately */,
  revert: (snapshot) => /* exactly that change undone */,
  failureMessage: "Could not save edit — reverted change.",
}); // -> Promise<boolean>
```

It applies the change locally, POSTs, adopts the server's `snapshot`/`status` on success, and on
failure applies `revert` to the *then-current* snapshot (so unrelated changes that arrived meanwhile
survive) and sets `lastError`.

### Errors

Every failed write sets `lastError = {message, detail, seq}`. `message` is ready for a toast
(“Could not delete item. Cannot delete 'a': still referenced by depends_on (b -> a)”); `detail` is
the server's text alone, for showing inside a dialog. The default look shows a toast for 4 s and
calls `dismissError()`.

### Rules the core enforces

- **Child projects:** a project with `parent` (and optional `under`, a phase or component id of the
  parent) is a child only if the parent exists. Lists show a child only while it is `active` (or
  while searching); otherwise it appears inside its parent at `childProjectsAt(parent, under)`,
  or at `ROOT_SLOT` when `under` does not resolve. Statuses never roll up between parent and child.
- **Nesting:** a project has phases (or, with `uses_phases: false`, direct topics); a phase has
  components (`components`, `topics` and `subtopics` lists are treated alike); components nest to any
  depth.
- **Ids:** component ids are unique across a whole project, so every action takes just
  `(projectId, itemId)` for any depth. New ids are slugs derived from the name by the server.

## 4. Just want different colours?

You don't need a new look for that: the default look takes every colour, radius and easing from the
CSS variables at the top of `src/ui/styles.css` (`--bg-*`, `--text-*`, `--border*`, `--accent*`,
`--status-*`, `--danger`, `--radius-*`). Edit them and rebuild.

## 5. Data contract

- Schemas: [`../schema/project.schema.json`](../schema/project.schema.json) and
  [`../schema/learning.schema.json`](../schema/learning.schema.json) (TypeScript types in
  `src/types/` are generated from them with `npm run generate-types`).
- Server endpoints. Writes are `POST` with headers `X-Workbench: 1` and
  `Content-Type: application/json`. Success: `{"ok": true, "snapshot": …, "status": …}` (plus
  `"item_id"` for add). Errors: `{"detail": "…"}` with 400 bad input, 403 missing header, 404 unknown
  id, 409 rebase conflict, 415 content type, 422 validation/semantic, 500 push failure.

| Method | Path | Body |
|---|---|---|
| GET | `/api/snapshot` | — |
| GET | `/api/status` | — |
| POST | `/api/sync` | `{}` |
| POST | `/api/projects/{id}/status` | `{"status"}` |
| POST | `/api/projects/{id}/items/{item}/status` | `{"status"}` |
| POST | `/api/learning/{id}/status` | `{"status"}` |
| POST | `/api/learning/{id}/items/{item}/status` | `{"status"}` |
| POST | `/api/projects/{id}/fields` | `{"name"?, "description"?}` |
| POST | `/api/projects/{id}/items/{item}/fields` | `{"name"?, "summary"?, "notes"?, "description"?, "items"?}` |
| POST | `/api/learning/{id}/fields` | `{"name"?, "description"?}` |
| POST | `/api/learning/{id}/items/{item}/fields` | `{"name"?, "summary"?, "notes"?, "description"?}` |
| POST | `/api/projects/{id}/items` | `{"parent_id", "name", "id"?, "status"?, "summary"?, "position"?}` |
| POST | `/api/projects/{id}/items/{item}/move` | `{"parent_id"?, "position"}` |
| POST | `/api/projects/{id}/items/{item}/delete` | `{"confirm": true}` |
| POST | `/api/projects/{id}/parent` | `{"parent", "under"}` (both null = detach) |

A look never needs these directly — the actions above call them — but a non-React frontend can.

## 6. The default look, editing included

![A project page in the default look, with the visual map of phases and components](docs/images/default-project-map.png)

Every text field has a pencil (✎): Enter saves a single-line field, Cmd/Ctrl+Enter saves a
textarea, Esc cancels. Selected components get ↑ / ↓, **Move to…**, **Delete…** and **+ Add part**;
each phase has **+ Add component** and its own ↑ / ↓ / Delete…; **+ Add phase** sits under the last
phase; **Part of…** in the project header attaches the project to a parent or detaches it.

![Inline editing of a component summary, with the structure controls above it](docs/images/default-inline-editing.png)

Regenerate these screenshots (builds both looks, runs a temporary demo server, restores `web/dist`):

```bash
uv run python web/docs/screenshots.py     # from the repository root
```
