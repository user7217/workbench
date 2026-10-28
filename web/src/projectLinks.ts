import type { Project } from "./types/project";
import { getProjectGroups, indexProjectComponents } from "./componentTree";

/**
 * Parent/child projects. A child names its single parent (`parent`) and the phase or component it
 * sits under (`under`). The link is structural only: statuses are never rolled up either way.
 */

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

/**
 * parent project id -> (slot id -> child projects). The slot is the phase or component id the
 * child sits under, or ROOT_SLOT when `under` is unset or does not resolve inside the parent.
 */
export function buildChildIndex(
  projects: Project[],
  parentOf: Map<string, ParentRef>
): Map<string, Map<string, Project[]>> {
  const index = new Map<string, Map<string, Project[]>>();
  const sorted = [...projects].sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  for (const child of sorted) {
    const ref = parentOf.get(child.id);
    if (!ref) continue;
    const slot = ref.components.length
      ? ref.components[ref.components.length - 1].id
      : ref.phase?.id ?? ROOT_SLOT;
    const slots = index.get(ref.parentId) ?? new Map<string, Project[]>();
    slots.set(slot, [...(slots.get(slot) ?? []), child]);
    index.set(ref.parentId, slots);
  }
  return index;
}

/**
 * Project lists (Home, Building tree, sidebar) show every top-level project, but a child project
 * only while it is active; otherwise it lives inside its parent. A search shows every match.
 */
export function isListedProject(project: Project, byId: Map<string, Project>, searching: boolean): boolean {
  if (searching) return true;
  return !isChildProject(project, byId) || project.status === "active";
}
