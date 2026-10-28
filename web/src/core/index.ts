/**
 * Workbench core — the fixed part of the dashboard (data, state, rules, API). A look (src/ui or any
 * folder chosen with WORKBENCH_UI) imports only from here and default-exports its view component.
 *
 * Public surface
 * - Types: Project, Phase, Component, ProjectRepo, LearningPath, Topic, Subtopic (generated schema
 *   types), SnapshotData, SyncStatus, Route, Selection, ExpandKind, WorkbenchState, WorkbenchView,
 *   WorkbenchActions, Workbench, ComponentEntry, ParentRef, DetailItem, TreeItem, TreeGroup.
 * - React: <WorkbenchProvider>, useWorkbench() -> Workbench (state + derived + actions in one
 *   object), useWorkbenchStore().
 * - Store: createWorkbench(options) -> {getState, subscribe, actions, start, stop}, selectView(state),
 *   pendingKeys (keys for isPending), initialState().
 * - Actions (on useWorkbench()): navigation/selection (navigate, selectProject, openProjectAt,
 *   selectLearningPath, clearComponentSelection, setExpanded, setFilter, setShowArchived), data
 *   (refresh, sync), status writes (setItemStatus, setProjectStatus, setLearningStatus,
 *   setLearningItemStatus), text edits — optimistic, revert on failure, resolve true/false —
 *   (updateProjectFields(projectId, {name?, description?}), updateItemFields(projectId, itemId,
 *   {name?, summary?, notes?, description?, items?}), updateLearningFields(pathId, {name?,
 *   description?}), updateLearningItemFields(pathId, itemId, {name?, summary?, notes?,
 *   description?})), structure edits — server-authoritative, pending key pendingKeys.structure —
 *   (addItem(projectId, {parentId, name, id?, status?, summary?, position?}) -> new id | null,
 *   moveItem(projectId, itemId, {parentId?, position}), deleteItem(projectId, itemId),
 *   setParent(projectId, {parent, under} | null)), dismissError, optimisticWrite.
 *   Failures set `lastError` ({message, detail?, seq}); `detail` is the server's reason.
 * - Statuses: ITEM_STATUSES (phases/components), LEARNING_ITEM_STATUSES (topics/subtopics),
 *   ENTITY_STATUSES (projects/learning paths), STATUS_LABELS.
 * - Derive (pure helpers): getProjectGroups, getGroupChildren, indexProjectComponents,
 *   countDescendants, locateProjectItem, getDependencies, getDependents, findItemStatus,
 *   withItemStatus, findLearningItem, withLearningItemStatus, buildParentIndex, buildChildIndex,
 *   childProjectsAt, isChildProject, isListedProject, ROOT_SLOT, projectUsesPhases,
 *   isNewDiscoveredProject, getItemShortDescription, sortByName, sortByRecent, matchesQuery, filterByQuery,
 *   getRelatedLearning, getRelatedProjects, getComponentDetailItems, getLearningSubtopicItems,
 *   projectTree, learningTree, PROJECT_NODE_PREFIX, parseProjectNodeId, mapProjectItem,
 *   mapLearningItem, locateItemPlacement, applyTextFields, readTextFields, changedTextFields, subtreeIds.
 * - API (rarely needed by a look): api.* functions, statusUrls, fieldUrls, structureUrls.
 */
export type { Project, Phase, Component, ProjectRepo } from "../types/project";
export type { LearningPath, Topic, Subtopic } from "../types/learning";

export * from "./statuses";
export * from "./derive";
export {
  createWorkbench,
  selectView,
  initialState,
  pendingKeys,
  indexProjects,
  componentIndexOf,
  type Route,
  type Selection,
  type ExpandKind,
  type ExpandState,
  type WorkbenchError,
  type WorkbenchState,
  type WorkbenchView,
  type WorkbenchActions,
  type WorkbenchStore,
  type OptimisticWrite,
  type CreateWorkbenchOptions,
  type ProjectIndexes,
  type ProjectFields,
  type ProjectItemFields,
  type LearningFields,
  type LearningItemFields,
  type AddItemInput,
  type MoveTarget,
  type ParentLink,
} from "./store";
export { WorkbenchProvider, useWorkbench, useWorkbenchStore, type Workbench, type WorkbenchProviderProps } from "./react";
export * as api from "./api";
export { statusUrls, fieldUrls, structureUrls, type SnapshotData, type SyncStatus, type WriteResult, type WorkbenchApi } from "./api";
