/**
 * Framework-agnostic dashboard store: the data, what is selected/expanded, pending writes, and the
 * actions that change them. Looks read it through `useWorkbench()` (core/react.ts); other frameworks
 * can use `createWorkbench()` + `subscribe` directly.
 *
 * Adding a write action: build it on `optimisticWrite()` — give it a pending key, the request, and
 * a pure `apply`/`revert` pair over the snapshot. It applies locally, POSTs, adopts the server's
 * snapshot/status, and on failure reverts only that change and sets `lastError`. Status and text
 * edits work this way. Structure edits (add/move/delete/parent) are NOT optimistic: they mark
 * `pendingKeys.structure(projectId)`, POST, and adopt the server's snapshot (ids, positions and
 * validation are the server's call); on failure nothing changed locally and `lastError` says why.
 */
import type { Project } from "../types/project";
import type { LearningPath, Topic, Subtopic } from "../types/learning";
import {
  EMPTY_SNAPSHOT,
  EMPTY_SYNC_STATUS,
  defaultApi,
  fieldUrls,
  statusUrls,
  structureUrls,
  type SnapshotData,
  type SyncStatus,
  type WorkbenchApi,
  type WriteResult,
} from "./api";
import {
  applyTextFields,
  buildChildIndex,
  changedTextFields,
  buildParentIndex,
  childProjectsAt,
  filterByQuery,
  findItemStatus,
  findLearningItem,
  getComponentDetailItems,
  getDependencies,
  getDependents,
  getLearningSubtopicItems,
  getProjectGroups,
  getRelatedLearning,
  getRelatedProjects,
  getTopicMarkdown,
  indexProjectComponents,
  isListedProject,
  learningTree,
  locateProjectItem,
  mapLearningItem,
  mapProjectItem,
  normalizeQuery,
  projectTree,
  readTextFields,
  sortByName,
  sortByRecent,
  withItemStatus,
  withLearningItemStatus,
  type ChildIndex,
  type ComponentEntry,
  type DetailItem,
  type ParentRef,
  type TextFieldPatch,
  type TreeGroup,
} from "./derive";
import type { EntityStatus, ItemStatus, LearningItemStatus } from "./statuses";
import type { Component, Phase } from "../types/project";

// ---------------------------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------------------------

export type Route =
  | { page: "home" }
  | { page: "building" }
  | { page: "learning" }
  | { page: "project"; id: string }
  | { page: "learning-path"; id: string };

export interface Selection {
  /** Phase (or direct topic) selected in the active project. */
  phaseId: string | null;
  /** Component (any depth) selected in the active project. */
  componentId: string | null;
  /** Topic selected in the active learning path. */
  topicId: string | null;
  /** Subtopic selected in the active learning path. */
  subtopicId: string | null;
}

/**
 * Expand/collapse sets that selection reveals or that are shared across panels. A missing id means
 * "use the look's default" (e.g. the selected project is open by default); see `isExpanded`.
 */
export type ExpandKind =
  | "projects"
  | "phases"
  | "nestedComponents"
  | "childProjects"
  | "learningPaths"
  | "topics";

export type ExpandState = Record<ExpandKind, Record<string, boolean>>;

export interface WorkbenchError {
  /** Full message for a toast: what failed plus the server's reason. */
  message: string;
  /** The server's reason alone (its `detail`), e.g. why a delete was refused. */
  detail?: string;
  /** Increments on every new error, so a look can re-show the same message. */
  seq: number;
}

export interface WorkbenchState {
  snapshot: SnapshotData;
  syncStatus: SyncStatus;
  route: Route;
  selection: Selection;
  expanded: ExpandState;
  /** Raw text of the search box. */
  filterText: string;
  /** Whether archived projects are shown in project lists. */
  showArchived: boolean;
  /** Writes in flight, keyed by `pendingKeys.*`; their controls should be disabled. */
  pending: Record<string, true>;
  /** Last failed write (reverted), or null. */
  lastError: WorkbenchError | null;
}

export const pendingKeys = {
  project: (projectId: string) => `project:${projectId}`,
  projectItem: (projectId: string, itemId: string) => `item:${projectId}:${itemId}`,
  learning: (pathId: string) => `learning:${pathId}`,
  learningItem: (pathId: string, itemId: string) => `learning-item:${pathId}:${itemId}`,
  /** Text edit of a project's own fields. */
  projectFields: (projectId: string) => `project-fields:${projectId}`,
  /** Text edit of a phase/component. */
  itemFields: (projectId: string, itemId: string) => `item-fields:${projectId}:${itemId}`,
  learningFields: (pathId: string) => `learning-fields:${pathId}`,
  learningItemFields: (pathId: string, itemId: string) => `learning-item-fields:${pathId}:${itemId}`,
  /** Any structure edit (add/move/delete/parent) of a project; one at a time per project. */
  structure: (projectId: string) => `structure:${projectId}`,
};

// Text-field shapes accepted by the fields endpoints. "" removes an optional field; name can't be "".
export interface ProjectFields {
  name?: string;
  description?: string;
}
export interface ProjectItemFields {
  name?: string;
  summary?: string;
  notes?: string;
  description?: string;
  /** Components only (not phases/topics). [] removes the list. */
  items?: string[];
}
export interface LearningFields {
  name?: string;
  description?: string;
}
export interface LearningItemFields {
  name?: string;
  summary?: string;
  notes?: string;
  description?: string;
}

/** A new phase/topic (parentId null) or component (parentId = a phase or component id). */
export interface AddItemInput {
  parentId: string | null;
  name: string;
  /** Explicit id (slug); default: derived from the name, made unique by the server. */
  id?: string;
  status?: ItemStatus;
  summary?: string;
  /** Index in the parent's list; default: append. */
  position?: number;
}

/** Where to move an item: `position` in its list, or under `parentId` (components only). */
export interface MoveTarget {
  /** Omit to reorder in place. Phases/topics can only be reordered. */
  parentId?: string;
  position: number;
}

/** A child project's place in its parent: the parent project id and the phase/component it sits under. */
export interface ParentLink {
  parent: string;
  under: string;
}

export function initialState(): WorkbenchState {
  return {
    snapshot: EMPTY_SNAPSHOT,
    syncStatus: EMPTY_SYNC_STATUS,
    route: { page: "home" },
    selection: { phaseId: null, componentId: null, topicId: null, subtopicId: null },
    expanded: { projects: {}, phases: {}, nestedComponents: {}, childProjects: {}, learningPaths: {}, topics: {} },
    filterText: "",
    showArchived: false,
    pending: {},
    lastError: null,
  };
}

// ---------------------------------------------------------------------------------------------
// Snapshot indexes (memoised per projects array)
// ---------------------------------------------------------------------------------------------

export interface ProjectIndexes {
  projectsById: Map<string, Project>;
  parentOf: Map<string, ParentRef>;
  childIndex: ChildIndex;
}

const indexCache = new WeakMap<Project[], ProjectIndexes>();

export function indexProjects(projects: Project[]): ProjectIndexes {
  let hit = indexCache.get(projects);
  if (!hit) {
    const parentOf = buildParentIndex(projects);
    hit = {
      projectsById: new Map(projects.map((p) => [p.id, p])),
      parentOf,
      childIndex: buildChildIndex(projects, parentOf),
    };
    indexCache.set(projects, hit);
  }
  return hit;
}

const componentIndexCache = new WeakMap<Project, Map<string, ComponentEntry>>();

/** indexProjectComponents, memoised per project object. */
export function componentIndexOf(project: Project): Map<string, ComponentEntry> {
  let hit = componentIndexCache.get(project);
  if (!hit) {
    hit = indexProjectComponents(project);
    componentIndexCache.set(project, hit);
  }
  return hit;
}

// ---------------------------------------------------------------------------------------------
// Derived view
// ---------------------------------------------------------------------------------------------

export interface WorkbenchView extends ProjectIndexes {
  /** Normalised search query ("" when not searching). */
  query: string;
  searching: boolean;
  /** Projects matching the search, sorted by name. */
  filteredProjects: Project[];
  /** Learning paths matching the search, sorted by name. */
  filteredLearning: LearningPath[];
  /** Matching projects that are listed (child projects only while active, unless searching). Includes archived. */
  listedProjects: Project[];
  /** listedProjects ordered most recently changed first (for Home cards). */
  recentProjects: Project[];
  /** filteredLearning ordered most recently changed first (for Home cards). */
  recentLearning: LearningPath[];
  /** Sidebar shortcuts: listed, non-archived projects (ignores the search). */
  sidebarProjects: Project[];
  /** Sidebar shortcuts: non-archived learning paths (ignores the search). */
  sidebarLearning: LearningPath[];

  /** Project shown on the Building/project page (route id, else first listed non-archived, else first). */
  activeProject: Project | undefined;
  /** Selected phase of the active project, defaulting to its first. */
  activePhase: Phase | null;
  /** Selected component of the active project (with phase + ancestors), or null. */
  activeComponent: ComponentEntry | null;
  /** Where the active project sits in its parent, if it is a child project. */
  activeParent: ParentRef | undefined;
  /** Learning path shown on the Learning page (route id, else first match, else first). */
  activeLearningPath: LearningPath | undefined;
  /** Selected topic of the active learning path, defaulting to its first. */
  activeTopic: Topic | null;
  /** Selected subtopic, else the active topic. */
  activeLearningItem: Topic | Subtopic | null;
  /** Markdown notes of the active learning item ("" when none). */
  activeMarkdown: string;

  /** True while a write with this key (see `pendingKeys`) is in flight. */
  isPending: (key: string) => boolean;
  /** Expanded state of an id in a set, or `fallback` when the user never toggled it. */
  isExpanded: (kind: ExpandKind, id: string, fallback?: boolean) => boolean;
  /** Child projects under a phase/component id (or ROOT_SLOT) of a parent project. */
  childProjectsAt: (parentId: string, slotId: string) => Project[];
  /** Every component of a project at any depth, by id (memoised). */
  componentIndex: (project: Project) => Map<string, ComponentEntry>;
  /** Components the given component depends on. */
  dependenciesOf: (project: Project, comp: Component) => ComponentEntry[];
  /** Components that depend on the given component. */
  dependentsOf: (project: Project, compId: string) => ComponentEntry[];
  /** Level-3 detail rows of a component. */
  componentDetails: (project: Project, comp: Component) => DetailItem[];
  /** Level-3 study rows of a learning subtopic. */
  subtopicDetails: (pathId: string, sub: Subtopic) => DetailItem[];
  /** Markdown notes of a topic/subtopic. */
  topicMarkdown: (pathId: string, itemId: string) => string;
  /** Learning paths related to a project, sorted. */
  relatedLearning: (project: Project) => LearningPath[];
  /** Projects related to a learning path (project undefined when missing). */
  relatedProjects: (lp: LearningPath) => { id: string; project: Project | undefined }[];
  /** Map tree of a project, including its child projects. */
  projectTree: (project: Project) => TreeGroup[];
  /** Map tree of a learning path. */
  learningTree: (lp: LearningPath) => TreeGroup[];
}

const viewCache = new WeakMap<WorkbenchState, WorkbenchView>();

/** Everything a look needs that follows from the state. Memoised per state object. */
export function selectView(state: WorkbenchState): WorkbenchView {
  const cached = viewCache.get(state);
  if (cached) return cached;

  const { snapshot, route, selection } = state;
  const indexes = indexProjects(snapshot.projects);
  const { projectsById, childIndex, parentOf } = indexes;
  const query = normalizeQuery(state.filterText);
  const searching = query !== "";
  const filteredProjects = filterByQuery(snapshot.projects, query);
  const filteredLearning = filterByQuery(snapshot.learning, query);
  const listedProjects = filteredProjects.filter((p) => isListedProject(p, projectsById, searching));

  const requestedProjectId = route.page === "project" ? route.id : undefined;
  const activeProject =
    snapshot.projects.find((p) => p.id === requestedProjectId) ||
    listedProjects.find((p) => p.status !== "archived") ||
    snapshot.projects[0];
  const groups = activeProject ? getProjectGroups(activeProject) : [];
  const activePhase = groups.find((ph) => ph.id === selection.phaseId) || groups[0] || null;
  const activeComps = activeProject ? componentIndexOf(activeProject) : new Map<string, ComponentEntry>();
  const activeComponent = selection.componentId ? activeComps.get(selection.componentId) || null : null;

  const requestedPathId = route.page === "learning-path" ? route.id : undefined;
  const activeLearningPath =
    snapshot.learning.find((l) => l.id === requestedPathId) || filteredLearning[0] || snapshot.learning[0];
  const topics = activeLearningPath?.topics || [];
  const activeTopic = topics.find((t) => t.id === selection.topicId) || topics[0] || null;
  let activeLearningItem: Topic | Subtopic | null = activeTopic;
  if (selection.subtopicId) {
    for (const t of topics) {
      const s = (t.subtopics || []).find((sub) => sub.id === selection.subtopicId);
      if (s) {
        activeLearningItem = s;
        break;
      }
    }
  }

  const view: WorkbenchView = {
    ...indexes,
    query,
    searching,
    filteredProjects,
    filteredLearning,
    listedProjects,
    recentProjects: sortByRecent(listedProjects, snapshot.updated_at?.projects),
    recentLearning: sortByRecent(filteredLearning, snapshot.updated_at?.learning),
    sidebarProjects: sortByName(
      snapshot.projects.filter((p) => p.status !== "archived" && isListedProject(p, projectsById, false))
    ),
    sidebarLearning: sortByName(snapshot.learning.filter((l) => l.status !== "archived")),
    activeProject,
    activePhase,
    activeComponent,
    activeParent: activeProject ? parentOf.get(activeProject.id) : undefined,
    activeLearningPath,
    activeTopic,
    activeLearningItem,
    activeMarkdown:
      activeLearningItem && activeLearningPath
        ? getTopicMarkdown(snapshot.topic_markdowns, activeLearningPath.id, activeLearningItem.id)
        : "",
    isPending: (key) => Boolean(state.pending[key]),
    isExpanded: (kind, id, fallback = false) => {
      const v = state.expanded[kind][id];
      return v !== undefined ? v : fallback;
    },
    childProjectsAt: (parentId, slotId) => childProjectsAt(childIndex, parentId, slotId),
    componentIndex: componentIndexOf,
    dependenciesOf: (project, comp) => getDependencies(componentIndexOf(project), comp),
    dependentsOf: (project, compId) => getDependents(componentIndexOf(project), compId),
    componentDetails: (project, comp) => getComponentDetailItems(comp, componentIndexOf(project)),
    subtopicDetails: (pathId, sub) =>
      getLearningSubtopicItems(sub, getTopicMarkdown(snapshot.topic_markdowns, pathId, sub.id)),
    topicMarkdown: (pathId, itemId) => getTopicMarkdown(snapshot.topic_markdowns, pathId, itemId),
    relatedLearning: (project) => getRelatedLearning(project, snapshot.learning),
    relatedProjects: (lp) => getRelatedProjects(lp, projectsById),
    projectTree: (project) => projectTree(project, childIndex.get(project.id)),
    learningTree,
  };
  viewCache.set(state, view);
  return view;
}

// ---------------------------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------------------------

export interface OptimisticWrite {
  /** Pending key (see `pendingKeys`); a second write with the same key is ignored while one runs. */
  key: string;
  /** The server call. Resolves with the server's result, rejects with an Error whose message is shown. */
  request: (api: WorkbenchApi) => Promise<WriteResult>;
  /** Local change applied immediately. */
  apply: (snapshot: SnapshotData) => SnapshotData;
  /** Undo of exactly that change, applied to the then-current snapshot on failure. */
  revert: (snapshot: SnapshotData) => SnapshotData;
  /** Message shown on failure (the server's detail is appended). */
  failureMessage?: string;
}

export interface WorkbenchActions {
  /** Go to a page; project/learning-path routes behave like selectProject/selectLearningPath. */
  navigate: (route: Route) => void;
  /**
   * Open a project. phaseId: undefined = first phase, null = none. compId selects a component (any
   * depth) and reveals it (its phase and ancestors expand). A child project also reveals its spot in
   * the parent.
   */
  selectProject: (projectId: string, phaseId?: string | null, compId?: string | null) => void;
  /** Open a project focused on one of its phases or components, by id (or just the project). */
  openProjectAt: (projectId: string, itemId?: string) => void;
  /** Open a learning path. topicId: undefined = first topic, null = none. subtopicId reveals its topic. */
  selectLearningPath: (pathId: string, topicId?: string | null, subtopicId?: string | null) => void;
  /** Deselect the selected component (closes its detail). */
  clearComponentSelection: () => void;
  /** Set one id's expanded state in a set. */
  setExpanded: (kind: ExpandKind, id: string, open: boolean) => void;
  setFilter: (text: string) => void;
  setShowArchived: (show: boolean) => void;
  /** Reload snapshot + sync status from the server. */
  refresh: () => Promise<void>;
  /** Ask the server to sync now, then refresh. */
  sync: () => Promise<void>;
  /** Status of a project phase or component (any depth). No-op if unchanged or unknown. */
  setItemStatus: (projectId: string, itemId: string, status: ItemStatus) => void;
  /** Status of a project. No-op if unchanged. */
  setProjectStatus: (projectId: string, status: EntityStatus) => void;
  /** Status of a learning path. No-op if unchanged. */
  setLearningStatus: (pathId: string, status: EntityStatus) => void;
  /** Status of a learning topic or subtopic (always posted, even if unchanged). */
  setLearningItemStatus: (pathId: string, itemId: string, status: LearningItemStatus) => void;
  /** Clear `lastError` (e.g. when its toast times out). */
  dismissError: () => void;
  /** Name/description of a project (optimistic). Resolves true on success or when nothing changed. */
  updateProjectFields: (projectId: string, fields: ProjectFields) => Promise<boolean>;
  /** Text fields of a phase or component, any depth (optimistic). */
  updateItemFields: (projectId: string, itemId: string, fields: ProjectItemFields) => Promise<boolean>;
  /** Name/description of a learning path (optimistic). */
  updateLearningFields: (pathId: string, fields: LearningFields) => Promise<boolean>;
  /** Text fields of a learning topic or subtopic (optimistic). */
  updateLearningItemFields: (pathId: string, itemId: string, fields: LearningItemFields) => Promise<boolean>;
  /** Add a phase/topic or component (not optimistic). Resolves to the new item id, or null on failure. */
  addItem: (projectId: string, input: AddItemInput) => Promise<string | null>;
  /** Reorder an item, or move a component under another phase/component (not optimistic). */
  moveItem: (projectId: string, itemId: string, target: MoveTarget) => Promise<boolean>;
  /** Permanently delete a phase/component and everything under it (not optimistic). */
  deleteItem: (projectId: string, itemId: string) => Promise<boolean>;
  /** Attach a project under a parent project's phase/component, or detach it with null (not optimistic). */
  setParent: (projectId: string, link: ParentLink | null) => Promise<boolean>;
  /** The shared "apply locally, POST, adopt server result, revert on failure" pattern. */
  optimisticWrite: (write: OptimisticWrite) => Promise<boolean>;
}

export interface WorkbenchStore {
  getState: () => WorkbenchState;
  subscribe: (listener: () => void) => () => void;
  actions: WorkbenchActions;
  /** Load data now and start polling (if configured). Called by WorkbenchProvider on mount. */
  start: () => void;
  /** Stop polling. */
  stop: () => void;
}

export interface CreateWorkbenchOptions {
  api?: WorkbenchApi;
  /** Re-fetch the snapshot every N ms; 0/undefined = only on start and after writes/syncs. */
  pollIntervalMs?: number;
  initialState?: Partial<WorkbenchState>;
}

const mapProject = (projectId: string, fn: (p: Project) => Project) => (s: SnapshotData): SnapshotData => ({
  ...s,
  projects: s.projects.map((p) => (p.id === projectId ? fn(p) : p)),
});

const mapLearningPath =
  (pathId: string, fn: (l: LearningPath) => LearningPath) =>
  (s: SnapshotData): SnapshotData => ({
    ...s,
    learning: s.learning.map((l) => (l.id === pathId ? fn(l) : l)),
  });

const STATUS_FAILURE = "Could not save status update — reverted change.";
const FIELDS_FAILURE = "Could not save edit — reverted change.";

export function createWorkbench(options: CreateWorkbenchOptions = {}): WorkbenchStore {
  const api = options.api ?? defaultApi;
  let state: WorkbenchState = { ...initialState(), ...options.initialState };
  const listeners = new Set<() => void>();
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let errorSeq = 0;

  const getState = () => state;
  const setState = (update: (s: WorkbenchState) => WorkbenchState) => {
    const next = update(state);
    if (next === state) return;
    state = next;
    listeners.forEach((l) => l());
  };
  const subscribe = (listener: () => void) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  };

  const expand = (s: WorkbenchState, kind: ExpandKind, ids: string[]): WorkbenchState => {
    if (ids.length === 0) return s;
    const set = { ...s.expanded[kind] };
    for (const id of ids) set[id] = true;
    return { ...s, expanded: { ...s.expanded, [kind]: set } };
  };

  const selectProject: WorkbenchActions["selectProject"] = (projectId, phaseId, compId) => {
    setState((s0) => {
      const { projectsById, parentOf } = indexProjects(s0.snapshot.projects);
      const proj = projectsById.get(projectId);
      const groups = proj ? getProjectGroups(proj) : [];
      const defaultPhase = phaseId !== undefined ? phaseId : groups.length > 0 ? groups[0].id : null;

      let s: WorkbenchState = {
        ...s0,
        route: { page: "project", id: projectId },
        selection: { ...s0.selection, phaseId: defaultPhase, componentId: compId ?? null },
      };
      s = expand(s, "projects", [projectId]);

      // A child project is listed inside its parent: reveal that spot in the navigation tree
      const ref = parentOf.get(projectId);
      if (ref) {
        s = expand(s, "projects", [ref.parentId]);
        if (ref.phase) s = expand(s, "phases", [ref.phase.id]);
        s = expand(s, "nestedComponents", ref.components.map((c) => c.id));
      }

      if (compId && proj) {
        const entry = componentIndexOf(proj).get(compId);
        if (entry) {
          s = { ...s, selection: { ...s.selection, phaseId: entry.phase.id } };
          s = expand(s, "phases", [entry.phase.id]);
          s = expand(s, "nestedComponents", entry.ancestors.map((a) => a.id));
        }
      } else if (phaseId && proj) {
        const ph = groups.find((p) => p.id === phaseId);
        if (ph) s = expand(s, "phases", [ph.id]);
      }
      return s;
    });
  };

  const selectLearningPath: WorkbenchActions["selectLearningPath"] = (pathId, topicId, subtopicId) => {
    setState((s0) => {
      const lp = s0.snapshot.learning.find((l) => l.id === pathId);
      const defaultTopic =
        topicId !== undefined ? topicId : lp?.topics && lp.topics.length > 0 ? lp.topics[0].id : null;
      let s: WorkbenchState = {
        ...s0,
        route: { page: "learning-path", id: pathId },
        selection: { ...s0.selection, topicId: defaultTopic, subtopicId: subtopicId ?? null },
      };
      s = expand(s, "learningPaths", [pathId]);

      if (subtopicId && lp) {
        const top = (lp.topics || []).find((t) => (t.subtopics || []).some((sub) => sub.id === subtopicId));
        if (top) {
          s = { ...s, selection: { ...s.selection, topicId: top.id } };
          s = expand(s, "topics", [top.id]);
        }
      } else if (topicId && lp) {
        const top = (lp.topics || []).find((t) => t.id === topicId);
        if (top) s = expand(s, "topics", [top.id]);
      }
      return s;
    });
  };

  const openProjectAt: WorkbenchActions["openProjectAt"] = (projectId, itemId) => {
    const proj = indexProjects(state.snapshot.projects).projectsById.get(projectId);
    if (!proj || !itemId) return selectProject(projectId);
    const loc = locateProjectItem(proj, itemId);
    if (loc?.kind === "phase") return selectProject(projectId, itemId, null);
    if (loc?.kind === "component") return selectProject(projectId, loc.entry.phase.id, itemId);
    return selectProject(projectId);
  };

  const navigate: WorkbenchActions["navigate"] = (route) => {
    if (route.page === "project") return selectProject(route.id);
    if (route.page === "learning-path") return selectLearningPath(route.id);
    setState((s) => ({ ...s, route }));
  };

  const setPending = (key: string, on: boolean) =>
    setState((s) => {
      const pending = { ...s.pending };
      if (on) pending[key] = true;
      else delete pending[key];
      return { ...s, pending };
    });

  const setError = (message: string, detail?: string) =>
    setState((s) => ({ ...s, lastError: { message, detail, seq: ++errorSeq } }));

  const errorDetail = (err: unknown) => (err instanceof Error && err.message ? err.message : undefined);

  const adopt = (result: WriteResult) =>
    setState((s) => ({
      ...s,
      snapshot: result.snapshot ?? s.snapshot,
      syncStatus: result.status ?? s.syncStatus,
    }));

  const optimisticWrite: WorkbenchActions["optimisticWrite"] = async (write) => {
    if (state.pending[write.key]) return false;
    setPending(write.key, true);
    setState((s) => ({ ...s, snapshot: write.apply(s.snapshot) }));
    try {
      adopt(await write.request(api));
      return true;
    } catch (err) {
      setState((s) => ({ ...s, snapshot: write.revert(s.snapshot) }));
      const detail = errorDetail(err);
      setError(`${write.failureMessage ?? "Could not save — reverted change."}${detail ? ` ${detail}` : ""}`, detail);
      return false;
    } finally {
      setPending(write.key, false);
    }
  };

  const statusWrite = (
    key: string,
    url: string,
    status: string,
    apply: OptimisticWrite["apply"],
    revert: OptimisticWrite["revert"]
  ) =>
    void optimisticWrite({
      key,
      request: (a) => a.postJson(url, { status }),
      apply,
      revert,
      failureMessage: STATUS_FAILURE,
    });

  const setItemStatus: WorkbenchActions["setItemStatus"] = (projectId, itemId, status) => {
    const project = indexProjects(state.snapshot.projects).projectsById.get(projectId);
    const prev = project ? findItemStatus(project, itemId) : undefined;
    if (!prev || prev === status) return;
    statusWrite(
      pendingKeys.projectItem(projectId, itemId),
      statusUrls.projectItem(projectId, itemId),
      status,
      mapProject(projectId, (p) => withItemStatus(p, itemId, status)),
      mapProject(projectId, (p) => withItemStatus(p, itemId, prev))
    );
  };

  const setProjectStatus: WorkbenchActions["setProjectStatus"] = (projectId, status) => {
    const prev = indexProjects(state.snapshot.projects).projectsById.get(projectId)?.status;
    if (!prev || prev === status) return;
    statusWrite(
      pendingKeys.project(projectId),
      statusUrls.project(projectId),
      status,
      mapProject(projectId, (p) => ({ ...p, status })),
      mapProject(projectId, (p) => ({ ...p, status: prev }))
    );
  };

  const setLearningStatus: WorkbenchActions["setLearningStatus"] = (pathId, status) => {
    const prev = state.snapshot.learning.find((l) => l.id === pathId)?.status;
    if (!prev || prev === status) return;
    statusWrite(
      pendingKeys.learning(pathId),
      statusUrls.learning(pathId),
      status,
      mapLearningPath(pathId, (l) => ({ ...l, status })),
      mapLearningPath(pathId, (l) => ({ ...l, status: prev }))
    );
  };

  const setLearningItemStatus: WorkbenchActions["setLearningItemStatus"] = (pathId, itemId, status) => {
    const lp = state.snapshot.learning.find((l) => l.id === pathId);
    const prev = lp ? findLearningItem(lp, itemId)?.item.status : undefined;
    statusWrite(
      pendingKeys.learningItem(pathId, itemId),
      statusUrls.learningItem(pathId, itemId),
      status,
      mapLearningPath(pathId, (l) => withLearningItemStatus(l, itemId, status)),
      prev ? mapLearningPath(pathId, (l) => withLearningItemStatus(l, itemId, prev)) : (s) => s
    );
  };

  // --- Text fields (optimistic) ---

  /**
   * POST only the fields that differ from `current` (null = target unknown -> no-op). `patchSnap`
   * applies a field patch to the snapshot; the same function with the old values is the revert.
   */
  const fieldsWrite = (
    key: string,
    url: string,
    current: object | undefined,
    fields: TextFieldPatch,
    patchSnap: (patch: TextFieldPatch) => (s: SnapshotData) => SnapshotData
  ): Promise<boolean> => {
    if (!current) return Promise.resolve(false);
    const changed = changedTextFields(current, fields);
    if (Object.keys(changed).length === 0) return Promise.resolve(true);
    if (changed.name !== undefined && String(changed.name).trim() === "") {
      setError("Name can't be empty.", "Name can't be empty.");
      return Promise.resolve(false);
    }
    const previous = readTextFields(current, Object.keys(changed));
    return optimisticWrite({
      key,
      request: (a) => a.postJson(url, changed),
      apply: patchSnap(changed),
      revert: patchSnap(previous),
      failureMessage: FIELDS_FAILURE,
    });
  };

  const findProject = (projectId: string) => indexProjects(state.snapshot.projects).projectsById.get(projectId);
  const findPath = (pathId: string) => state.snapshot.learning.find((l) => l.id === pathId);

  const updateProjectFields: WorkbenchActions["updateProjectFields"] = (projectId, fields) =>
    fieldsWrite(
      pendingKeys.projectFields(projectId),
      fieldUrls.project(projectId),
      findProject(projectId),
      { ...fields },
      (patch) => mapProject(projectId, (p) => applyTextFields(p, patch))
    );

  const updateItemFields: WorkbenchActions["updateItemFields"] = (projectId, itemId, fields) => {
    const project = findProject(projectId);
    const loc = project ? locateProjectItem(project, itemId) : null;
    const item = loc ? (loc.kind === "phase" ? loc.phase : loc.entry.comp) : undefined;
    return fieldsWrite(
      pendingKeys.itemFields(projectId, itemId),
      fieldUrls.projectItem(projectId, itemId),
      item,
      { ...fields },
      (patch) => mapProject(projectId, (p) => mapProjectItem(p, itemId, (it) => applyTextFields(it, patch)))
    );
  };

  const updateLearningFields: WorkbenchActions["updateLearningFields"] = (pathId, fields) =>
    fieldsWrite(
      pendingKeys.learningFields(pathId),
      fieldUrls.learning(pathId),
      findPath(pathId),
      { ...fields },
      (patch) => mapLearningPath(pathId, (l) => applyTextFields(l, patch))
    );

  const updateLearningItemFields: WorkbenchActions["updateLearningItemFields"] = (pathId, itemId, fields) => {
    const lp = findPath(pathId);
    return fieldsWrite(
      pendingKeys.learningItemFields(pathId, itemId),
      fieldUrls.learningItem(pathId, itemId),
      lp ? findLearningItem(lp, itemId)?.item : undefined,
      { ...fields },
      (patch) => mapLearningPath(pathId, (l) => mapLearningItem(l, itemId, (it) => applyTextFields(it, patch)))
    );
  };

  // --- Structure (server-authoritative) ---

  /** Mark the project's structure key, POST, adopt the server snapshot; null on failure (lastError set). */
  const structureWrite = async (
    projectId: string,
    request: (a: WorkbenchApi) => Promise<WriteResult>,
    failureMessage: string
  ): Promise<WriteResult | null> => {
    const key = pendingKeys.structure(projectId);
    if (state.pending[key]) return null;
    setPending(key, true);
    try {
      const result = await request(api);
      adopt(result);
      return result;
    } catch (err) {
      const detail = errorDetail(err);
      setError(`${failureMessage}${detail ? ` ${detail}` : ""}`, detail);
      return null;
    } finally {
      setPending(key, false);
    }
  };

  const addItem: WorkbenchActions["addItem"] = async (projectId, input) => {
    const body: Record<string, unknown> = { parent_id: input.parentId, name: input.name };
    if (input.id !== undefined) body.id = input.id;
    if (input.status !== undefined) body.status = input.status;
    if (input.summary !== undefined) body.summary = input.summary;
    if (input.position !== undefined) body.position = input.position;
    const result = await structureWrite(
      projectId,
      (a) => a.postJson(structureUrls.add(projectId), body),
      "Could not add item."
    );
    return result?.item_id ?? null;
  };

  const moveItem: WorkbenchActions["moveItem"] = async (projectId, itemId, target) => {
    const body: Record<string, unknown> = { position: target.position };
    if (target.parentId !== undefined) body.parent_id = target.parentId;
    const result = await structureWrite(
      projectId,
      (a) => a.postJson(structureUrls.move(projectId, itemId), body),
      "Could not move item."
    );
    return result !== null;
  };

  const deleteItem: WorkbenchActions["deleteItem"] = async (projectId, itemId) => {
    const result = await structureWrite(
      projectId,
      (a) => a.postJson(structureUrls.remove(projectId, itemId), { confirm: true }),
      "Could not delete item."
    );
    if (result === null) return false;
    // Drop a selection that pointed into what was deleted
    setState((s) => {
      if (s.route.page !== "project" || s.route.id !== projectId) return s;
      const project = indexProjects(s.snapshot.projects).projectsById.get(projectId);
      const { phaseId, componentId } = s.selection;
      const gone = (id: string | null) => id !== null && (!project || !locateProjectItem(project, id));
      if (!gone(phaseId) && !gone(componentId)) return s;
      return {
        ...s,
        selection: {
          ...s.selection,
          phaseId: gone(phaseId) ? null : phaseId,
          componentId: gone(componentId) ? null : componentId,
        },
      };
    });
    return true;
  };

  const setParent: WorkbenchActions["setParent"] = async (projectId, link) => {
    const result = await structureWrite(
      projectId,
      (a) =>
        a.postJson(structureUrls.parent(projectId), {
          parent: link ? link.parent : null,
          under: link ? link.under : null,
        }),
      link ? "Could not attach project." : "Could not detach project."
    );
    return result !== null;
  };

  const refresh = async () => {
    const { snapshot, status } = await api.fetchAll();
    setState((s) => ({ ...s, snapshot: snapshot ?? s.snapshot, syncStatus: status ?? s.syncStatus }));
  };

  const sync = async () => {
    if (await api.triggerSync()) await refresh();
  };

  const actions: WorkbenchActions = {
    navigate,
    selectProject,
    openProjectAt,
    selectLearningPath,
    clearComponentSelection: () =>
      setState((s) => ({ ...s, selection: { ...s.selection, componentId: null } })),
    setExpanded: (kind, id, open) =>
      setState((s) => ({ ...s, expanded: { ...s.expanded, [kind]: { ...s.expanded[kind], [id]: open } } })),
    setFilter: (filterText) => setState((s) => ({ ...s, filterText })),
    setShowArchived: (showArchived) => setState((s) => ({ ...s, showArchived })),
    refresh,
    sync,
    setItemStatus,
    setProjectStatus,
    setLearningStatus,
    setLearningItemStatus,
    updateProjectFields,
    updateItemFields,
    updateLearningFields,
    updateLearningItemFields,
    addItem,
    moveItem,
    deleteItem,
    setParent,
    dismissError: () => setState((s) => (s.lastError ? { ...s, lastError: null } : s)),
    optimisticWrite,
  };

  return {
    getState,
    subscribe,
    actions,
    start: () => {
      void refresh();
      if (options.pollIntervalMs && options.pollIntervalMs > 0 && !pollTimer) {
        pollTimer = setInterval(() => void refresh(), options.pollIntervalMs);
      }
    },
    stop: () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
    },
  };
}

export type { SnapshotData, SyncStatus, WriteResult, WorkbenchApi };
