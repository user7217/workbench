/**
 * Status icons that open the status menu for a project phase/component or a learning
 * topic/subtopic, wired to the matching core action and pending key. Each is followed by the
 * item's "add to Today's Work" toggle, so the two controls appear together everywhere.
 */
import { useWorkbench, ITEM_STATUSES, LEARNING_ITEM_STATUSES, pendingKeys } from "@workbench-core";
import { StatusMenu } from "./StatusMenu";
import { TodayToggle } from "./TodayToggle";

/** Status icon of a project phase/component that opens the status menu. */
export function ItemStatusMenu({
  projectId,
  item,
  testIdPrefix = "",
}: {
  projectId: string;
  item: { id: string; name: string; status?: string };
  testIdPrefix?: string;
}) {
  const wb = useWorkbench();
  return (
    <span className="status-with-today">
      <StatusMenu
        status={item.status || "not_started"}
        options={ITEM_STATUSES}
        busy={wb.isPending(pendingKeys.projectItem(projectId, item.id))}
        testId={`${testIdPrefix}status-menu-${item.id}`}
        subject={item.name}
        onSelect={(st) => wb.setItemStatus(projectId, item.id, st)}
      />
      <TodayToggle
        entry={{ kind: "project", id: projectId, item: item.id }}
        subject={item.name}
        testIdPrefix={testIdPrefix}
      />
    </span>
  );
}

/** Status icon of a learning topic/subtopic that opens the status menu. */
export function LearningItemStatusMenu({
  pathId,
  item,
  testIdPrefix = "",
}: {
  pathId: string;
  item: { id: string; name: string; status: "not_started" | "in_progress" | "done" };
  testIdPrefix?: string;
}) {
  const wb = useWorkbench();
  return (
    <span className="status-with-today">
      <StatusMenu
        status={item.status}
        options={LEARNING_ITEM_STATUSES}
        busy={wb.isPending(pendingKeys.learningItem(pathId, item.id))}
        testId={`${testIdPrefix}status-menu-${item.id}`}
        subject={item.name}
        onSelect={(st) => wb.setLearningItemStatus(pathId, item.id, st)}
      />
      <TodayToggle
        entry={{ kind: "learning", id: pathId, item: item.id }}
        subject={item.name}
        testIdPrefix={testIdPrefix}
      />
    </span>
  );
}
