/**
 * Pure, framework-free rules over the snapshot: project structure, child-project links, listing and
 * search rules, related items, dependency lookups and the tree used by maps. No React, no fetch.
 */
import type { Project, Phase, Component } from "../types/project";
import type { LearningPath, Topic, Subtopic } from "../types/learning";
import type { ItemStatus } from "./statuses";

// ---------------------------------------------------------------------------------------------
// Small shared rules
// ---------------------------------------------------------------------------------------------

/** False only when a project explicitly opts out of phases (it then has direct topics). */
export function projectUsesPhases(project: Project): boolean {
  return project.uses_phases !== false;
}

/** A discovered repo whose structure has not been populated yet. */
export function isNewDiscoveredProject(p: Project): boolean {
  return p.origin === "discovered" && getProjectGroups(p).length === 0;
}

/** One-line description of a phase/component/topic: its summary, else its description. */
export function getItemShortDescription(item: { summary?: string; description?: string }): string {
  return (item.summary || item.description || "").trim();
}

/** Case-insensitive sort by name (copy; input untouched). */
/**
 * Most recently changed first: by the git change time when known, else by the YAML `updated` date,
 * then by name so the order is stable.
 */
export function sortByRecent<T extends { id: string; name: string; updated?: string }>(
  items: T[],
  changedAt: Record<string, string> | undefined
): T[] {
  const key = (item: T) => changedAt?.[item.id] || item.updated || "";
  return [...items].sort((a, b) => {
    const ka = key(a);
    const kb = key(b);
    if (ka !== kb) return ka < kb ? 1 : -1;
    return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
  });
}

export function sortByName<T extends { name: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
}

/** Normalised search query: trimmed and lower-cased ("" = not searching). */
export function normalizeQuery(filterText: string): string {
  return filterText.trim().toLowerCase();
}

/** Search rule for projects and learning paths: name or description contains the query. */
export function matchesQuery(item: { name: string; description?: string }, query: string): boolean {
  if (!query) return true;
  return item.name.toLowerCase().includes(query) || (item.description || "").toLowerCase().includes(query);
}

/** Matching items, sorted by name. */
export function filterByQuery<T extends { name: string; description?: string }>(items: T[], query: string): T[] {
  return sortByName(items.filter((i) => matchesQuery(i, query)));
}

// ---------------------------------------------------------------------------------------------
// Project structure: phases (or direct topics) -> components -> nested components
// ---------------------------------------------------------------------------------------------

/** Phases (or direct topics) of a project, whichever the project actually uses. */
export function getProjectGroups(project: Project): Phase[] {
  if (project.uses_phases === false) {
    return project.topics && project.topics.length > 0 ? project.topics : project.phases || [];
  }
  return project.phases && project.phases.length > 0 ? project.phases : project.topics || [];
}

/** Top-level components of a phase (components, topics and subtopics are interchangeable). */
export function getGroupChildren(group: Phase): Component[] {
  return [...(group.components || []), ...(group.topics || []), ...(group.subtopics || [])];
}

/** A component anywhere in a project, with the phase it belongs to and its ancestor components. */
export interface ComponentEntry {
  comp: Component;
  phase: Phase;
  /** Ancestor components from the top-level one down to the direct parent (empty at top level). */
  ancestors: Component[];
}

/** Every component in a project, at any depth, keyed by id (ids are unique across a project). */
export function indexProjectComponents(project: Project): Map<string, ComponentEntry> {
  const index = new Map<string, ComponentEntry>();
  const visit = (comps: Component[], phase: Phase, ancestors: Component[]) => {
    for (const comp of comps) {
      if (!index.has(comp.id)) index.set(comp.id, { comp, phase, ancestors });
      if (comp.components?.length) visit(comp.components, phase, [...ancestors, comp]);
    }
  };
  for (const phase of getProjectGroups(project)) visit(getGroupChildren(phase), phase, []);
  return index;
}

/** Number of components below this one, at any depth. */
export function countDescendants(comp: Component): number {
  return (comp.components || []).reduce((n, c) => n + 1 + countDescendants(c), 0);
}

/** Where an id sits in a project: a phase, a component (with its phase), or nowhere. */
export type ProjectItemLocation =
  | { kind: "phase"; phase: Phase }
  | { kind: "component"; entry: ComponentEntry }
  | null;

export function locateProjectItem(project: Project, itemId: string): ProjectItemLocation {
  const phase = getProjectGroups(project).find((g) => g.id === itemId);
  if (phase) return { kind: "phase", phase };
  const entry = indexProjectComponents(project).get(itemId);
  return entry ? { kind: "component", entry } : null;
}

/** Where an item sits in its list: parent (null = the project's phases/topics), index and siblings. */
export interface ItemPlacement {
  parentId: string | null;
  index: number;
  siblings: (Phase | Component)[];
}

/** An item's place among its siblings, for reorder controls (move up/down = index -/+ 1). */
export function locateItemPlacement(project: Project, itemId: string): ItemPlacement | null {
  const groups = getProjectGroups(project);
  const gi = groups.findIndex((g) => g.id === itemId);
  if (gi >= 0) return { parentId: null, index: gi, siblings: groups };
  const entry = indexProjectComponents(project).get(itemId);
  if (!entry) return null;
  const parent = entry.ancestors[entry.ancestors.length - 1];
  const has = (l: Component[] | undefined) => (l || []).some((c) => c.id === itemId);
  const ph = entry.phase;
  const siblings = parent ? parent.components || [] : [ph.components, ph.topics, ph.subtopics].find(has) || [];
  return { parentId: parent ? parent.id : ph.id, index: siblings.findIndex((c) => c.id === itemId), siblings };
}

/** Components this one depends on (unknown ids are skipped). */
export function getDependencies(index: Map<string, ComponentEntry>, comp: Component): ComponentEntry[] {
  return (comp.depends_on || []).map((id) => index.get(id)).filter((x): x is ComponentEntry => Boolean(x));
}

/** Components that depend on this one ("used by"). */
export function getDependents(index: Map<string, ComponentEntry>, compId: string): ComponentEntry[] {
  const out: ComponentEntry[] = [];
  for (const entry of index.values()) {
    if ((entry.comp.depends_on || []).includes(compId)) out.push(entry);
  }
  return out;
}

function mapInComponents(
  comps: Component[] | undefined,
  itemId: string,
  fn: <T extends Phase | Component>(item: T) => T
): Component[] | undefined {
  return comps?.map((c) => {
    if (c.id === itemId) return fn(c);
    return c.components ? { ...c, components: mapInComponents(c.components, itemId, fn) } : c;
  });
}

function mapInGroups(
  groups: Phase[] | undefined,
  itemId: string,
  fn: <T extends Phase | Component>(item: T) => T
): Phase[] | undefined {
  return groups?.map((g) => {
    if (g.id === itemId) return fn(g);
    return {
      ...g,
      components: mapInComponents(g.components, itemId, fn),
      topics: mapInComponents(g.topics, itemId, fn),
      subtopics: mapInComponents(g.subtopics, itemId, fn),
    };
  });
}

/** Copy of the project with `fn` applied to one phase or component (any depth). */
export function mapProjectItem(
  project: Project,
  itemId: string,
  fn: <T extends Phase | Component>(item: T) => T
): Project {
  return {
    ...project,
    phases: mapInGroups(project.phases, itemId, fn),
    topics: mapInGroups(project.topics, itemId, fn),
  };
}

/** Copy of the project with one phase or component (any depth) set to the given status. */
export function withItemStatus(project: Project, itemId: string, status: ItemStatus): Project {
  return mapProjectItem(project, itemId, (item) => ({ ...item, status }));
}

/**
 * Text fields as the server stores them: an empty string (or empty `items` list) removes the optional
 * key, so a cleared field is missing from the snapshot. Read a missing field as "".
 */
export type TextFieldPatch = Record<string, string | string[] | undefined>;

/** Copy of `obj` with the patch applied using the server's rule (empty value -> key removed). */
export function applyTextFields<T extends object>(obj: T, patch: TextFieldPatch): T {
  const out = { ...obj } as Record<string, unknown>;
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    const empty = Array.isArray(value) ? value.length === 0 : value === "";
    if (empty && key !== "name") delete out[key];
    else out[key] = Array.isArray(value) ? [...value] : value;
  }
  return out as T;
}

/** Current value of each patched key on `obj` (missing -> "" or []), for undo and change detection. */
export function readTextFields(obj: object, keys: string[]): TextFieldPatch {
  const src = obj as Record<string, unknown>;
  const out: TextFieldPatch = {};
  for (const key of keys) {
    const v = src[key];
    out[key] = key === "items" ? (Array.isArray(v) ? [...(v as string[])] : []) : typeof v === "string" ? v : "";
  }
  return out;
}

/** The subset of `patch` that differs from the object's current values. */
export function changedTextFields(obj: object, patch: TextFieldPatch): TextFieldPatch {
  const keys = Object.keys(patch).filter((k) => patch[k] !== undefined);
  const current = readTextFields(obj, keys);
  const out: TextFieldPatch = {};
  for (const key of keys) {
    if (JSON.stringify(current[key]) !== JSON.stringify(patch[key])) out[key] = patch[key];
  }
  return out;
}

/** Ids of an item and everything nested under it (to exclude it from "Move to…" targets). */
export function subtreeIds(item: Phase | Component): Set<string> {
  const ids = new Set<string>([item.id]);
  const visit = (comps: Component[] | undefined) => {
    for (const c of comps || []) {
      ids.add(c.id);
      visit(c.components);
    }
  };
  // A phase's children may sit under components/topics/subtopics; a component's under components
  visit(getGroupChildren(item as Phase));
  return ids;
}

/** Current status of a phase or component (any depth), or undefined if the id is unknown. */
export function findItemStatus(project: Project, itemId: string): ItemStatus | undefined {
  for (const phase of getProjectGroups(project)) {
    if (phase.id === itemId) return phase.status || "not_started";
  }
  const entry = indexProjectComponents(project).get(itemId);
  return entry ? entry.comp.status || "not_started" : undefined;
}

// ---------------------------------------------------------------------------------------------
// Learning structure
// ---------------------------------------------------------------------------------------------

type LearningItemStatus = Topic["status"];

/** Topic or subtopic of a learning path, with the topic it belongs to. */
export function findLearningItem(lp: LearningPath, itemId: string): { item: Topic | Subtopic; topic: Topic } | null {
  for (const top of lp.topics || []) {
    if (top.id === itemId) return { item: top, topic: top };
    const sub = (top.subtopics || []).find((s) => s.id === itemId);
    if (sub) return { item: sub, topic: top };
  }
  return null;
}

/** Copy of the learning path with `fn` applied to one topic or subtopic. */
export function mapLearningItem(
  lp: LearningPath,
  itemId: string,
  fn: <T extends Topic | Subtopic>(item: T) => T
): LearningPath {
  return {
    ...lp,
    topics: (lp.topics || []).map((t) => {
      if (t.id === itemId) return fn(t);
      return { ...t, subtopics: (t.subtopics || []).map((s) => (s.id === itemId ? fn(s) : s)) };
    }),
  };
}

/** Copy of the learning path with one topic or subtopic set to the given status. */
export function withLearningItemStatus(lp: LearningPath, itemId: string, status: LearningItemStatus): LearningPath {
  return mapLearningItem(lp, itemId, (item) => ({ ...item, status }));
}

/** Markdown notes of a topic/subtopic ("" when none). */
export function getTopicMarkdown(markdowns: Record<string, string>, pathId: string, itemId: string): string {
  return markdowns[`${pathId}/${itemId}`] || "";
}

// ---------------------------------------------------------------------------------------------
// Parent/child projects. A child names its single parent (`parent`) and the phase or component it
// sits under (`under`). The link is structural only: statuses are never rolled up either way.
// ---------------------------------------------------------------------------------------------

export interface CrumbStep {
  id: string;
  name: string;
}

/** Where a child project sits inside its parent project. */
export interface ParentRef {
  parentId: string;
  parentName: string;
  /** Phase the child sits under (directly, or through a component); null if `under` is unset/unknown. */
  phase: CrumbStep | null;
  /** Component chain from top level down to the component named by `under` (empty when under a phase). */
  components: CrumbStep[];
}

/** Key used for children that name no (resolvable) `under` item: they sit at the parent's root. */
export const ROOT_SLOT = "";

/** A project counts as a child only when its parent actually exists in the snapshot. */
export function isChildProject(project: Project, byId: Map<string, Project>): boolean {
  return Boolean(project.parent && project.parent !== project.id && byId.has(project.parent));
}

function resolveUnder(parent: Project, under: string | null | undefined): Omit<ParentRef, "parentId" | "parentName"> {
  if (!under) return { phase: null, components: [] };
  const phase = getProjectGroups(parent).find((g) => g.id === under);
  if (phase) return { phase: { id: phase.id, name: phase.name }, components: [] };
  const entry = indexProjectComponents(parent).get(under);
  if (!entry) return { phase: null, components: [] };
  return {
    phase: { id: entry.phase.id, name: entry.phase.name },
    components: [...entry.ancestors, entry.comp].map((c) => ({ id: c.id, name: c.name })),
  };
}

/** child project id -> where it sits in its parent. */
export function buildParentIndex(projects: Project[]): Map<string, ParentRef> {
  const byId = new Map(projects.map((p) => [p.id, p]));
  const parentOf = new Map<string, ParentRef>();
  for (const child of projects) {
    if (!isChildProject(child, byId)) continue;
    const parent = byId.get(child.parent!)!;
    parentOf.set(child.id, {
      parentId: parent.id,
      parentName: parent.name,
      ...resolveUnder(parent, child.under),
    });
  }
  return parentOf;
}

/** parent id -> (slot id -> child projects sorted by name). */
export type ChildIndex = Map<string, Map<string, Project[]>>;

/**
 * parent project id -> (slot id -> child projects). The slot is the phase or component id the
 * child sits under, or ROOT_SLOT when `under` is unset or does not resolve inside the parent.
 */
export function buildChildIndex(projects: Project[], parentOf: Map<string, ParentRef>): ChildIndex {
  const index: ChildIndex = new Map();
  const sorted = sortByName(projects);
  for (const child of sorted) {
    const ref = parentOf.get(child.id);
    if (!ref) continue;
    const slot = ref.components.length ? ref.components[ref.components.length - 1].id : ref.phase?.id ?? ROOT_SLOT;
    const slots = index.get(ref.parentId) ?? new Map<string, Project[]>();
    slots.set(slot, [...(slots.get(slot) ?? []), child]);
    index.set(ref.parentId, slots);
  }
  return index;
}

/** Child projects sitting under one phase/component (or ROOT_SLOT) of a parent project. */
export function childProjectsAt(index: ChildIndex, parentId: string, slotId: string): Project[] {
  return index.get(parentId)?.get(slotId) ?? [];
}

/**
 * Project lists (Home, Building tree, sidebar) show every top-level project, but a child project
 * only while it is active; otherwise it lives inside its parent. A search shows every match.
 */
export function isListedProject(project: Project, byId: Map<string, Project>, searching: boolean): boolean {
  if (searching) return true;
  return !isChildProject(project, byId) || project.status === "active";
}

// ---------------------------------------------------------------------------------------------
// Related items
// ---------------------------------------------------------------------------------------------

/** Learning paths that list this project in `related_projects`, sorted by name. */
export function getRelatedLearning(project: Project, learning: LearningPath[]): LearningPath[] {
  return sortByName(learning.filter((l) => (l.related_projects || []).includes(project.id)));
}

/** A learning path's related projects; `project` is undefined when the id is not in the snapshot. */
export function getRelatedProjects(
  lp: LearningPath,
  byId: Map<string, Project>
): { id: string; project: Project | undefined }[] {
  return (lp.related_projects || []).map((id) => ({ id, project: byId.get(id) }));
}

// ---------------------------------------------------------------------------------------------
// Level-3 detail rows
// ---------------------------------------------------------------------------------------------

/** One detail row under a component or learning subtopic (items, notes, deps, links, takeaways). */
export interface DetailItem {
  id: string;
  kindLabel: string;
  text: string;
  status: string;
  /** Set for dependency rows: the component the row points to. */
  targetComp?: ComponentEntry;
  url?: string;
}

/** Detail rows of a component, derived from its metadata without duplicating data. */
export function getComponentDetailItems(comp: Component, allComps: Map<string, ComponentEntry>): DetailItem[] {
  const items: DetailItem[] = [];
  const own = comp.status || "not_started";
  for (const [idx, text] of (comp.items || []).entries()) {
    items.push({ id: `${comp.id}-item-${idx}`, kindLabel: "Item", text, status: own });
  }
  if (comp.notes) {
    items.push({ id: `${comp.id}-note`, kindLabel: "Note", text: comp.notes, status: own });
  }
  for (const depId of comp.depends_on || []) {
    const found = allComps.get(depId);
    if (found) {
      items.push({
        id: `${comp.id}-dep-${depId}`,
        kindLabel: "Prerequisite",
        text: found.comp.name,
        status: found.comp.status || "not_started",
        targetComp: found,
      });
    }
  }
  for (const entry of getDependents(allComps, comp.id)) {
    items.push({
      id: `${comp.id}-usedby-${entry.comp.id}`,
      kindLabel: "Downstream Consumer",
      text: entry.comp.name,
      status: entry.comp.status || "not_started",
      targetComp: entry,
    });
  }
  for (const lnk of comp.links || []) {
    items.push({ id: `${comp.id}-link-${lnk.label}`, kindLabel: "Reference", text: lnk.label, status: own, url: lnk.url });
  }
  for (const ref of comp.refs || []) {
    items.push({ id: `${comp.id}-ref-${ref}`, kindLabel: "Verified Commit", text: ref, status: "done" });
  }
  if (items.length === 0) {
    const desc = getItemShortDescription(comp);
    if (desc) items.push({ id: `${comp.id}-spec`, kindLabel: "Specification", text: desc, status: own });
  }
  return items;
}

/** Study rows of a learning subtopic: markdown bullet points, else its summary. */
export function getLearningSubtopicItems(sub: Subtopic, markdownText: string): DetailItem[] {
  const items: DetailItem[] = [];
  if (markdownText) {
    markdownText
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.startsWith("- ") || l.startsWith("* "))
      .map((l) => l.slice(2).trim())
      .forEach((b, idx) => items.push({ id: `${sub.id}-md-${idx}`, kindLabel: "Key Takeaway", text: b, status: sub.status }));
  }
  if (items.length === 0) {
    const desc = getItemShortDescription(sub);
    if (desc) items.push({ id: `${sub.id}-concept`, kindLabel: "Core Concept", text: desc, status: sub.status });
  }
  return items;
}

// ---------------------------------------------------------------------------------------------
// Tree for maps: phases -> components (nested) -> child projects (with namespaced ids)
// ---------------------------------------------------------------------------------------------

/** Ids of a child-project node start with this; items inside it are `${prefix}${projectId}::${itemId}`. */
export const PROJECT_NODE_PREFIX = "project:";

export interface TreeItem {
  id: string;
  name: string;
  status: string;
  children?: TreeItem[];
  /** Set for a child project node; its id is PROJECT_NODE_PREFIX + project id */
  isProject?: boolean;
  /** Item inside a child project (its node id is namespaced so it can't clash with the parent's ids) */
  foreign?: { projectId: string; itemId: string };
  /** Open by default when its parent is shown (a child project's phases) */
  defaultOpen?: boolean;
}

export interface TreeGroup extends TreeItem {
  children: TreeItem[];
}

/** Split a namespaced child-project node id into its project id and (optional) item id. */
export function parseProjectNodeId(nodeId: string): { projectId: string; itemId?: string } | null {
  if (!nodeId.startsWith(PROJECT_NODE_PREFIX)) return null;
  const [projectId, itemId] = nodeId.slice(PROJECT_NODE_PREFIX.length).split("::");
  return { projectId, itemId };
}

function foreignItems(projectId: string, comps: Component[]): TreeItem[] {
  return comps.map((c) => ({
    id: `${PROJECT_NODE_PREFIX}${projectId}::${c.id}`,
    name: c.name,
    status: c.status || "not_started",
    foreign: { projectId, itemId: c.id },
    children: c.components?.length ? foreignItems(projectId, c.components) : undefined,
  }));
}

function childProjectItems(slotId: string, childProjects?: Map<string, Project[]>): TreeItem[] {
  return (childProjects?.get(slotId) ?? []).map((cp) => {
    const groups: TreeItem[] = getProjectGroups(cp).map((g) => {
      const comps = foreignItems(cp.id, getGroupChildren(g));
      return {
        id: `${PROJECT_NODE_PREFIX}${cp.id}::${g.id}`,
        name: g.name,
        status: g.status || "not_started",
        foreign: { projectId: cp.id, itemId: g.id },
        defaultOpen: true,
        children: comps.length ? comps : undefined,
      };
    });
    return {
      id: PROJECT_NODE_PREFIX + cp.id,
      name: cp.name,
      status: cp.status,
      isProject: true,
      children: groups.length ? groups : undefined,
    };
  });
}

function componentItems(comps: Component[], childProjects?: Map<string, Project[]>): TreeItem[] {
  return comps.map((c) => {
    const children = [...componentItems(c.components ?? [], childProjects), ...childProjectItems(c.id, childProjects)];
    return {
      id: c.id,
      name: c.name,
      status: c.status || "not_started",
      children: children.length ? children : undefined,
    };
  });
}

/**
 * A project as a tree: phases -> components -> nested components, with child projects (and their
 * own phases/components, ids namespaced) under the slot they sit in.
 * `childProjects` is this project's entry of the ChildIndex (slot id -> children).
 */
export function projectTree(project: Project, childProjects?: Map<string, Project[]>): TreeGroup[] {
  return getProjectGroups(project).map((ph) => ({
    id: ph.id,
    name: ph.name,
    status: ph.status || "not_started",
    children: [...componentItems(getGroupChildren(ph), childProjects), ...childProjectItems(ph.id, childProjects)],
  }));
}

/** A learning path as a tree: topics -> subtopics. */
export function learningTree(lp: LearningPath): TreeGroup[] {
  return (lp.topics || []).map((t) => ({
    id: t.id,
    name: t.name,
    status: t.status,
    children: (t.subtopics || []).map((s) => ({ id: s.id, name: s.name, status: s.status })),
  }));
}
