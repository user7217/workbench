/**
 * Today's Work: turns the stored list of chosen roots (a project, a phase or topic, any nested
 * part, a learning path or one of its topics) into ordered steps, grouped by the project or
 * learning path they were chosen from. Each group names its own step to do now and the one after
 * it; the groups themselves are not ranked, because work moves between projects freely.
 *
 * Only the roots are stored (`snapshot.today`). Everything else is worked out here from the
 * current data, so a part added to a chosen topic later shows up by itself.
 *
 * Order: a part comes after the parts nested under it and after everything it depends on
 * (`depends_on`), so the list can be worked from top to bottom. An unfinished prerequisite that
 * sits outside the chosen topic is pulled in just before the part that needs it.
 */
import type { SnapshotData } from "./api";
import { type ChildIndex, childProjectsAt, getGroupChildren, getProjectGroups } from "./derive";
import type { LearningPath, Subtopic, Topic } from "../types/learning";
import type { Component, Phase, Project } from "../types/project";

export type TodayKind = "project" | "learning";

/** One chosen root, as stored. `item` absent or null means the whole project or learning path. */
export interface TodayEntry {
  kind: TodayKind;
  id: string;
  item?: string | null;
  /** ISO date the entry was first added (set by the server). */
  added?: string;
}

export interface TodayData {
  /** The local calendar day (YYYY-MM-DD) the list was last confirmed for; null if never set. */
  day: string | null;
  entries: TodayEntry[];
}

export const EMPTY_TODAY: TodayData = { day: null, entries: [] };

/** Most roots the server stores. */
export const MAX_TODAY_ENTRIES = 60;

/**
 * Where a step stands in the run. `now` and `next` are the two the working-memory view colours;
 * `blocked` is an unfinished step that waits on others: its nested parts are not all done, or
 * something it depends on is not; `parked` parts are skipped.
 */
export type TodayStepState = "done" | "now" | "next" | "later" | "blocked" | "parked";

export interface TodayStep {
  /** Unique within a plan: `${kind}:${ownerId}/${itemId}`. */
  key: string;
  kind: TodayKind;
  /** The project or learning path the item belongs to (a child project's own id for its parts). */
  ownerId: string;
  ownerName: string;
  itemId: string;
  name: string;
  summary: string;
  status: string;
  /** Names of the items this one is nested under, outermost first (without the owner). */
  trail: string[];
  /** True when the item has nested parts: it is finished by finishing them, then marking it done. */
  hasParts: boolean;
  /** Set when the item is here only because this other item depends on it. */
  neededBy?: string;
  /** Key of the stored entry that brought the step in. */
  entryKey: string;
  state: TodayStepState;
}

/** Everything chosen from one project or learning path: its own ordered steps, now and next. */
export interface TodayGroup {
  /** `${kind}:${id}` of the project or learning path. */
  key: string;
  kind: TodayKind;
  id: string;
  name: string;
  /** Status of the project or path itself (active, paused, ...). */
  status: string;
  /** The stored entries that belong to this group, in stored order. */
  entries: TodayEntry[];
  /** True when the whole project or path was chosen, not single items of it. */
  whole: boolean;
  steps: TodayStep[];
  now: TodayStep | null;
  next: TodayStep | null;
  /** Steps that count towards the day: everything except parked ones. */
  total: number;
  done: number;
}

export interface TodayPlan {
  /** One group per project or learning path with entries, in the order they were first added. */
  groups: TodayGroup[];
  /** Stored entries whose project, path or item no longer exists. */
  missing: TodayEntry[];
}

export function entryKey(entry: TodayEntry): string {
  return `${entry.kind}:${entry.id}/${entry.item ?? ""}`;
}

export function sameEntry(a: TodayEntry, b: TodayEntry): boolean {
  return entryKey(a) === entryKey(b);
}

export function stepKey(kind: TodayKind, ownerId: string, itemId: string): string {
  return `${kind}:${ownerId}/${itemId}`;
}

/** The viewer's local calendar day as YYYY-MM-DD. */
export function localDay(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function todayOf(snapshot: SnapshotData): TodayData {
  return snapshot.today ?? EMPTY_TODAY;
}

/**
 * Whether the server offers Today's Work. It can be turned off per install (`workbench today off`,
 * the desktop app's menu, WORKBENCH_TODAY); a look then shows no Today tab and no add buttons.
 * A server from before the switch existed has it on exactly when it sends a `today` list.
 */
export function todayEnabled(snapshot: SnapshotData): boolean {
  return snapshot.features ? snapshot.features.today !== false : snapshot.today !== undefined;
}

export function hasEntry(today: TodayData, entry: TodayEntry): boolean {
  return today.entries.some((e) => sameEntry(e, entry));
}

/** The list with `entry` added at the end (unchanged if it is already there). */
export function withEntry(today: TodayData, entry: TodayEntry): TodayEntry[] {
  return hasEntry(today, entry) ? today.entries : [...today.entries, entry];
}

export function withoutEntry(today: TodayData, entry: TodayEntry): TodayEntry[] {
  return today.entries.filter((e) => !sameEntry(e, entry));
}

// ---------------------------------------------------------------------------------------------
// Expanding roots into steps
// ---------------------------------------------------------------------------------------------

type ProjectNode = Phase | Component;
type LearningNode = Topic | Subtopic;

interface Draft extends Omit<TodayStep, "state"> {
  /** Keys of the steps nested directly under this one. */
  partKeys: string[];
  /** Keys of the steps this one depends on (`depends_on`) that are part of the plan. */
  needKeys: string[];
}

function partsOf(node: ProjectNode): Component[] {
  // A phase keeps its parts in components/topics/subtopics; a component in components
  return getGroupChildren(node as Phase);
}

function findProjectNode(
  project: Project,
  itemId: string,
): { node: ProjectNode; trail: string[] } | null {
  const walk = (
    nodes: ProjectNode[],
    trail: string[],
  ): { node: ProjectNode; trail: string[] } | null => {
    for (const node of nodes) {
      if (node.id === itemId) return { node, trail };
      const found = walk(partsOf(node), [...trail, node.name]);
      if (found) return found;
    }
    return null;
  };
  return walk(getProjectGroups(project), []);
}

interface Collector {
  drafts: Draft[];
  seen: Set<string>;
  /** Prerequisites that are already done are left out, unless named here (finished today). */
  keepDone: ReadonlySet<string>;
  childIndex: ChildIndex;
}

function collectProjectNode(
  c: Collector,
  project: Project,
  node: ProjectNode,
  trail: string[],
  root: string,
  neededBy?: string,
): string | null {
  const key = stepKey("project", project.id, node.id);
  if (c.seen.has(key)) return key;
  c.seen.add(key); // before descending, so a dependency cycle cannot loop

  // What this part depends on comes first, wherever it sits in the project
  const needKeys: string[] = [];
  for (const depId of (node as Component).depends_on ?? []) {
    const dep = findProjectNode(project, depId);
    if (!dep) continue;
    const depKey = stepKey("project", project.id, depId);
    if ((dep.node.status ?? "not_started") === "done" && !c.keepDone.has(depKey)) continue;
    collectProjectNode(c, project, dep.node, dep.trail, root, node.name);
    needKeys.push(depKey);
  }

  const partKeys: string[] = [];
  const inside = [...trail, node.name];
  for (const part of partsOf(node)) {
    const partKey = collectProjectNode(c, project, part, inside, root);
    if (partKey) partKeys.push(partKey);
  }
  // Child projects attached under this part are part of it too
  for (const child of childProjectsAt(c.childIndex, project.id, node.id)) {
    for (const group of getProjectGroups(child)) {
      const partKey = collectProjectNode(c, child, group, [], root);
      if (partKey) partKeys.push(partKey);
    }
  }

  c.drafts.push({
    key,
    kind: "project",
    ownerId: project.id,
    ownerName: project.name,
    itemId: node.id,
    name: node.name,
    summary: node.summary ?? "",
    status: node.status ?? "not_started",
    trail,
    hasParts: partKeys.length > 0,
    neededBy,
    entryKey: root,
    partKeys,
    needKeys,
  });
  return key;
}

function collectLearningNode(
  c: Collector,
  path: LearningPath,
  node: LearningNode,
  trail: string[],
  root: string,
): string {
  const key = stepKey("learning", path.id, node.id);
  if (c.seen.has(key)) return key;
  c.seen.add(key);
  const partKeys = ((node as Topic).subtopics ?? []).map((sub) =>
    collectLearningNode(c, path, sub, [...trail, node.name], root),
  );
  c.drafts.push({
    key,
    kind: "learning",
    ownerId: path.id,
    ownerName: path.name,
    itemId: node.id,
    name: node.name,
    summary: (node as { summary?: string }).summary ?? "",
    status: node.status ?? "not_started",
    trail,
    hasParts: partKeys.length > 0,
    entryKey: root,
    partKeys,
    needKeys: [],
  });
  return key;
}

function findLearningNode(
  path: LearningPath,
  itemId: string,
): { node: LearningNode; trail: string[] } | null {
  for (const topic of path.topics ?? []) {
    if (topic.id === itemId) return { node: topic, trail: [] };
    const sub = (topic.subtopics ?? []).find((s) => s.id === itemId);
    if (sub) return { node: sub, trail: [topic.name] };
  }
  return null;
}

/** Collects one stored entry; false when what it points at no longer exists. */
function collectEntry(c: Collector, entry: TodayEntry, snapshot: SnapshotData): boolean {
  const root = entryKey(entry);
  if (entry.kind === "project") {
    const project = snapshot.projects.find((p) => p.id === entry.id);
    if (!project) return false;
    if (!entry.item) {
      for (const group of getProjectGroups(project))
        collectProjectNode(c, project, group, [], root);
      return true;
    }
    const found = findProjectNode(project, entry.item);
    if (!found) return false;
    collectProjectNode(c, project, found.node, found.trail, root);
    return true;
  }
  const path = snapshot.learning.find((lp) => lp.id === entry.id);
  if (!path) return false;
  if (!entry.item) {
    for (const topic of path.topics ?? []) collectLearningNode(c, path, topic, [], root);
    return true;
  }
  const found = findLearningNode(path, entry.item);
  if (!found) return false;
  collectLearningNode(c, path, found.node, found.trail, root);
  return true;
}

// ---------------------------------------------------------------------------------------------
// Now and next
// ---------------------------------------------------------------------------------------------

/**
 * Marks one step of an ordered list `now` and one `next`.
 *
 * `now` is the first step that can be worked on and is already in progress; if none is in
 * progress, the first that can be worked on. So starting a step (setting it in progress) is how a
 * different step is put first. `next` is the step the same rule picks once `now` is done.
 */
function markSteps(drafts: Draft[]): TodayStep[] {
  const byKey = new Map(drafts.map((d) => [d.key, d]));

  /** The step to work on, optionally pretending one more step is done. */
  const pick = (alsoDone?: string): { step: Draft | null; workable: (d: Draft) => boolean } => {
    const status = (d: Draft) => (d.key === alsoDone ? "done" : d.status);
    const finished = (d: Draft) => status(d) === "done" || status(d) === "parked";
    // Everything nested under a step, at any depth, is finished (a done part can hold open ones)
    const partsSettled = (d: Draft): boolean =>
      d.partKeys.every((k) => {
        const part = byKey.get(k);
        return !part || (finished(part) && partsSettled(part));
      });
    // What a step depends on has to be done first (a parked prerequisite keeps it waiting)
    const needsMet = (d: Draft) =>
      d.needKeys.every((k) => {
        const need = byKey.get(k);
        return !need || status(need) === "done";
      });
    const workable = (d: Draft) => !finished(d) && partsSettled(d) && needsMet(d);
    const open = drafts.filter(workable);
    return { step: open.find((d) => d.status === "in_progress") ?? open[0] ?? null, workable };
  };

  const { step: nowDraft, workable } = pick();
  // Next is whatever would be picked once the current step is done, so finishing the current
  // step always turns the step shown as next into the current one
  const nextDraft = nowDraft ? pick(nowDraft.key).step : null;

  return drafts.map(({ partKeys: _parts, needKeys: _needs, ...draft }) => ({
    ...draft,
    state:
      draft.status === "done"
        ? "done"
        : draft.status === "parked"
          ? "parked"
          : draft.key === nowDraft?.key
            ? "now"
            : draft.key === nextDraft?.key
              ? "next"
              : workable(byKey.get(draft.key) as Draft)
                ? "later"
                : "blocked",
  }));
}

function ownerOf(
  snapshot: SnapshotData,
  entry: TodayEntry,
): { name: string; status: string } | null {
  const owner =
    entry.kind === "project"
      ? snapshot.projects.find((p) => p.id === entry.id)
      : snapshot.learning.find((lp) => lp.id === entry.id);
  return owner ? { name: owner.name, status: owner.status } : null;
}

/**
 * Today's entries as one group per project or learning path, each with its own ordered steps and
 * its own `now` and `next` (see `markSteps`). Groups keep the order their first entry was added in
 * and are otherwise equal: nothing ranks one project above another.
 *
 * `keepDone` names prerequisites that were seen unfinished earlier today, so that finishing one
 * shows it as a done step instead of making it disappear.
 */
export function buildTodayPlan(
  snapshot: SnapshotData,
  childIndex: ChildIndex,
  keepDone: ReadonlySet<string> = new Set(),
): TodayPlan {
  const missing: TodayEntry[] = [];
  const byOwner = new Map<string, TodayEntry[]>();
  for (const entry of todayOf(snapshot).entries) {
    const key = `${entry.kind}:${entry.id}`;
    byOwner.set(key, [...(byOwner.get(key) ?? []), entry]);
  }

  const groups: TodayGroup[] = [];
  for (const [key, entries] of byOwner) {
    const owner = ownerOf(snapshot, entries[0]);
    if (!owner) {
      missing.push(...entries);
      continue;
    }
    // Each group collects on its own: a part reached from two projects shows up under both
    const c: Collector = { drafts: [], seen: new Set(), keepDone, childIndex };
    const found = entries.filter((entry) => {
      const ok = collectEntry(c, entry, snapshot);
      if (!ok) missing.push(entry);
      return ok;
    });
    if (found.length === 0) continue;

    const steps = markSteps(c.drafts);
    const counted = steps.filter((s) => s.state !== "parked");
    groups.push({
      key,
      kind: entries[0].kind,
      id: entries[0].id,
      name: owner.name,
      status: owner.status,
      entries: found,
      whole: found.some((e) => !e.item),
      steps,
      now: steps.find((s) => s.state === "now") ?? null,
      next: steps.find((s) => s.state === "next") ?? null,
      total: counted.length,
      done: counted.filter((s) => s.state === "done").length,
    });
  }
  return { groups, missing };
}

/** True when any group still has a step that can be worked on. */
export function todayHasWork(plan: TodayPlan): boolean {
  return plan.groups.some((g) => g.now !== null);
}

/** Entries that still have unfinished steps; the rest are finished and can be cleared. */
export function unfinishedEntries(today: TodayData, plan: TodayPlan): TodayEntry[] {
  const openRoots = new Set(
    plan.groups
      .flatMap((g) => g.steps)
      .filter((s) => s.state !== "done" && s.state !== "parked")
      .map((s) => s.entryKey),
  );
  return today.entries.filter((e) => openRoots.has(entryKey(e)));
}

/** Display name of a stored entry, or null when what it points at is gone. */
export function entryLabel(
  snapshot: SnapshotData,
  entry: TodayEntry,
): { owner: string; item: string | null } | null {
  if (entry.kind === "project") {
    const project = snapshot.projects.find((p) => p.id === entry.id);
    if (!project) return null;
    if (!entry.item) return { owner: project.name, item: null };
    const found = findProjectNode(project, entry.item);
    return found ? { owner: project.name, item: found.node.name } : null;
  }
  const path = snapshot.learning.find((lp) => lp.id === entry.id);
  if (!path) return null;
  if (!entry.item) return { owner: path.name, item: null };
  const found = findLearningNode(path, entry.item);
  return found ? { owner: path.name, item: found.node.name } : null;
}
