import type { Project, Phase, Component } from "./types/project";

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

type ItemStatus = NonNullable<Component["status"]>;

function setStatusInComponents(
  comps: Component[] | undefined,
  itemId: string,
  status: ItemStatus
): Component[] | undefined {
  return comps?.map((c) => {
    if (c.id === itemId) return { ...c, status };
    return c.components ? { ...c, components: setStatusInComponents(c.components, itemId, status) } : c;
  });
}

function setStatusInGroups(groups: Phase[] | undefined, itemId: string, status: ItemStatus): Phase[] | undefined {
  return groups?.map((g) => {
    if (g.id === itemId) return { ...g, status };
    return {
      ...g,
      components: setStatusInComponents(g.components, itemId, status),
      topics: setStatusInComponents(g.topics, itemId, status),
      subtopics: setStatusInComponents(g.subtopics, itemId, status),
    };
  });
}

/** Copy of the project with one phase or component (any depth) set to the given status. */
export function withItemStatus(project: Project, itemId: string, status: ItemStatus): Project {
  return {
    ...project,
    phases: setStatusInGroups(project.phases, itemId, status),
    topics: setStatusInGroups(project.topics, itemId, status),
  };
}

/** Current status of a phase or component (any depth), or undefined if the id is unknown. */
export function findItemStatus(project: Project, itemId: string): ItemStatus | undefined {
  for (const phase of getProjectGroups(project)) {
    if (phase.id === itemId) return phase.status || "not_started";
  }
  const entry = indexProjectComponents(project).get(itemId);
  return entry ? entry.comp.status || "not_started" : undefined;
}
