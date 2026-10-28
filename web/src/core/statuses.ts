/** Allowed statuses per level, and their human labels. Shared by the core and every look. */

/** Project phases and components (any depth). */
export const ITEM_STATUSES = ["not_started", "in_progress", "done", "parked"] as const;
/** Learning topics and subtopics. */
export const LEARNING_ITEM_STATUSES = ["not_started", "in_progress", "done"] as const;
/** Projects and learning paths. */
export const ENTITY_STATUSES = ["planned", "active", "paused", "done", "archived"] as const;

export type ItemStatus = (typeof ITEM_STATUSES)[number];
export type LearningItemStatus = (typeof LEARNING_ITEM_STATUSES)[number];
export type EntityStatus = (typeof ENTITY_STATUSES)[number];

export const STATUS_LABELS: Record<string, string> = {
  planned: "Planned",
  done: "Done",
  in_progress: "In progress",
  not_started: "Not started",
  parked: "Parked",
  paused: "Paused",
  active: "Active",
  archived: "Archived",
};
